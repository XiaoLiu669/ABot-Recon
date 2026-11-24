"""ABot-Recon Web 应用的重建 worker（独立进程运行）。

以独立进程运行的原因：
  * 推理期间独占显存，进程退出即彻底释放；
  * 崩溃或被杀不会拖垮 Web 服务；
  * 用 ``PROGRESS`` / ``PHASE`` / ``LOG`` / ``DONE`` / ``ERROR`` 这几种行
    向父进程汇报进度，父进程只要逐行读 stdout 即可。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def emit(tag: str, **payload) -> None:
    print(json.dumps({"tag": tag, **payload}, ensure_ascii=False), flush=True)


def log(message: str) -> None:
    emit("LOG", message=message)


def phase(name: str, index: int = 0, total: int = 1) -> None:
    emit("PHASE", phase=name, index=index, total=total)


def progress(done: int, total: int) -> None:
    emit("PROGRESS", done=done, total=total)


def extract_frames(video: Path, frames_dir: Path, fps: float) -> list[Path]:
    """按 fps 从视频抽帧，落成 jpg（与 demo.py --fps 的行为一致）。"""
    import cv2
    import numpy as np
    from PIL import Image

    frames_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开视频：{video}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if source_fps <= 0:
        capture.release()
        raise RuntimeError(f"无法读取视频帧率：{video}")
    step = max(1, int(round(source_fps / fps)))
    paths: list[Path] = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % step == 0:
            rgb = np.ascontiguousarray(frame[:, :, ::-1])
            path = frames_dir / f"{len(paths):08d}.jpg"
            Image.fromarray(rgb, mode="RGB").save(path, format="JPEG", quality=95)
            paths.append(path)
        index += 1
    capture.release()
    if not paths:
        raise RuntimeError("没有从视频中抽到任何帧")
    log(f"源视频 {source_fps:.2f} fps，每 {step} 帧取 1 帧，共抽出 {len(paths)} 帧")
    return paths


def run_inference(
    checkpoint: Path,
    frames: list[Path],
    output_dir: Path,
    *,
    device: str,
    amp_dtype: str,
    attention_backend: str,
    confidence_threshold: float,
) -> None:
    import torch

    from abot_recon import ABotRecon

    phase("加载模型", 0, 3)
    started = time.perf_counter()
    model = ABotRecon.from_pretrained(
        checkpoint,
        device=device,
        amp_dtype=amp_dtype,
        attention_backend=attention_backend,
        loop_closure=False,
        max_frames=22_000,
    )
    log(f"模型已加载（{time.perf_counter() - started:.1f} s）")

    # 给逐帧推理加进度回报：包一层帧生成器，每 5 帧报一次。
    inner = model.model._frames
    total = len(frames)

    def counting_frames(paths):
        for index, tensor in enumerate(inner(paths)):
            if index % 5 == 0 or index + 1 == total:
                progress(index + 1, total)
            yield tensor

    model.model._frames = counting_frames

    phase("重建中", 1, 3)
    started = time.perf_counter()
    result = model.infer(
        frames,
        output_local_points=True,
        output_world_points=True,
        output_confidence=True,
        confidence_threshold=confidence_threshold,
        loop_closure=False,
        dense_output_indices=list(range(total)),
    )
    elapsed = time.perf_counter() - started
    log(f"推理完成：{total} 帧，用时 {elapsed:.1f} s（{elapsed / max(total, 1) * 1000:.1f} ms/帧）")

    phase("保存结果", 2, 3)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from demo import save_result

    save_result(output_dir, result, frames, list(range(total)))
    if torch.cuda.is_available():
        log(f"峰值显存（PyTorch 分配）{torch.cuda.max_memory_allocated() / 1024**3:.2f} GiB")


def run_export(command: list[str], label: str) -> None:
    log(f"{label} …")
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (completed.stdout or "").strip().splitlines()[-4:]
    for line in tail:
        log(f"  {line}")
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} 失败（退出码 {completed.returncode}）：\n{(completed.stderr or '')[-1200:]}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--amp-dtype", default="fp16")
    parser.add_argument("--attention-backend", default="sdpa")
    parser.add_argument("--confidence-threshold", type=float, default=0.01)
    parser.add_argument("--max-points", type=int, default=20_000_000)
    args = parser.parse_args()

    run_dir = args.run_dir
    output_dir = run_dir / "output"
    frames_dir = run_dir / "frames"
    chunks_dir = run_dir / "point_cloud_chunks"
    repo_root = Path(__file__).resolve().parents[1]

    # 上游网络层的 tqdm 会往 stdout 写进度条，污染这里的 JSON 行协议。
    os.environ.setdefault("ABOT_RECON_NO_TQDM", "1")

    try:
        phase("抽帧", 0, 3)
        frames = extract_frames(args.video, frames_dir, args.fps)

        run_inference(
            args.checkpoint,
            frames,
            output_dir,
            device=args.device,
            amp_dtype=args.amp_dtype,
            attention_backend=args.attention_backend,
            confidence_threshold=args.confidence_threshold,
        )

        phase("导出点云", 2, 3)
        run_export(
            [
                sys.executable,
                str(repo_root / "scripts" / "export_reconstruction_ply.py"),
                "--output", str(run_dir / "reconstruction.ply"),
                "--poses", str(output_dir / "camera_poses.npy"),
                "--points", str(output_dir / "world_points.pt"),
                "--colors", str(output_dir / "colors.pt"),
                "--points-frame", "world",
                "--metadata", str(output_dir / "metadata.json"),
                "--confidence", str(output_dir / "confidence.pt"),
                "--confidence-threshold", str(args.confidence_threshold),
                "--max-points", str(args.max_points),
                "--bev-output", str(run_dir / "trajectory_bev.png"),
            ],
            "导出 PLY / 轨迹俯视图",
        )

        phase("切分点云", 2, 3)
        run_export(
            [
                sys.executable,
                str(repo_root / "scripts" / "export_point_cloud_chunks.py"),
                "--input", str(run_dir / "reconstruction.ply"),
                "--poses", str(output_dir / "camera_poses.npy"),
                "--fps", str(args.fps),
                "--output-dir", str(chunks_dir),
            ],
            "切分点云分块",
        )

        manifest = json.loads((chunks_dir / "manifest.json").read_text(encoding="utf-8"))
        emit(
            "DONE",
            point_count=manifest.get("point_count"),
            chunk_count=manifest.get("chunk_count"),
            frames=len(frames),
        )
    except BaseException as error:  # noqa: BLE001 - 需要把失败原因传回服务端
        emit("ERROR", message=f"{type(error).__name__}: {error}")
        raise


if __name__ == "__main__":
    main()
