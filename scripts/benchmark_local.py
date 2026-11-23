"""Measure ABot-Recon streaming throughput and peak VRAM on the local GPU.

The numbers are meant to be comparable with the "KITTI-02 streaming efficiency"
row of the upstream README (24.45 FPS / 6.71 GiB at 504x280 on an H100).

Two configurations are measured:

``camera_only``
    camera poses only -- the cheapest streaming configuration and the closest
    counterpart of the published "streaming efficiency" number.
``full_points_conf``
    camera poses + dense local point maps + confidence, i.e. what ``demo.py``
    produces for a full reconstruction.

Timing is reported two ways:

* *wall* -- end-to-end wall clock of the streaming call, including the per-frame
  CPU preprocessing (JPEG decode + resize + normalise + H2D copy).
* *GPU* -- per-frame CUDA events recorded around each frame's forward pass.
  This isolates the model forward from the CPU-side preprocessing.

Usage (from the repository root)::

    python scripts/benchmark_local.py \
        --checkpoint checkpoints/abot_recon.safetensors \
        --video path/to/video.mp4 \
        --frames 100 --fps 4
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

try:
    from abot_recon import ABotRecon
    from abot_recon.preprocessing import preprocess_image
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "Import failed. Run this from the repository root after "
        "`pip install -e '.[video]'` (the video extra provides opencv-python)."
    ) from exc

DTYPES = {"fp32": torch.float32, "fp16": torch.float16, "bf16": torch.bfloat16}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path("checkpoints/abot_recon.safetensors"),
        help="local checkpoint path",
    )
    parser.add_argument("--video", type=Path, required=True, help="input video file")
    parser.add_argument("--frames", type=int, default=100, help="number of frames to benchmark")
    parser.add_argument("--fps", type=float, default=4.0, help="sampling rate of the input video")
    parser.add_argument("--height", type=int, default=280)
    parser.add_argument("--width", type=int, default=504)
    parser.add_argument("--amp-dtype", choices=sorted(DTYPES), default="fp16")
    parser.add_argument("--attention-backend", choices=("auto", "paged", "sdpa"), default="sdpa")
    parser.add_argument("--work-dir", type=Path, default=Path("outputs/bench_frames"))
    parser.add_argument("--out-json", type=Path, default=Path("outputs/bench_local.json"))
    return parser.parse_args()


def extract_frames(video: Path, out_dir: Path, fps: float, count: int) -> list[Path]:
    """Sample frames from a video at ``fps`` and write them as JPEG files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.jpg"):
        stale.unlink()
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    if source_fps <= 0:
        capture.release()
        raise RuntimeError(f"Could not read frame rate from video: {video}")
    step = max(1, int(round(source_fps / fps)))
    paths: list[Path] = []
    index = 0
    while len(paths) < count:
        ok, frame = capture.read()
        if not ok:
            break
        if index % step == 0:
            rgb = np.ascontiguousarray(frame[:, :, ::-1])
            path = out_dir / f"{len(paths):08d}.jpg"
            Image.fromarray(rgb, mode="RGB").save(path, format="JPEG", quality=95)
            paths.append(path)
        index += 1
    capture.release()
    if not paths:
        raise RuntimeError("No frames extracted")
    return paths


class VramSampler(threading.Thread):
    """Sample the GPU memory usage of this process while a benchmark runs."""

    def __init__(self, interval: float = 0.02):
        super().__init__(daemon=True)
        self.interval = interval
        self.peak_bytes = 0
        # Not named ``_stop``: ``Thread._stop`` is used internally by ``join()``.
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                free, total = torch.cuda.mem_get_info()
                used = total - free
                if used > self.peak_bytes:
                    self.peak_bytes = used
            except Exception:
                pass
            self._halt.wait(self.interval)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=2.0)


def benchmark(network, frames, output_keys, height, width, dtype, device, label):
    """Run one streaming pass and collect per-frame timings and VRAM peaks."""
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()

    events: list[tuple[torch.cuda.Event, torch.cuda.Event]] = []

    def frame_stream():
        for path in frames:
            with Image.open(path) as image:
                tensor, _ = preprocess_image(image, height=height, width=width)
            tensor = tensor.unsqueeze(0).unsqueeze(0)
            start = torch.cuda.Event(enable_timing=True)
            start.record()
            yield tensor.to(device=device, dtype=dtype, non_blocking=True)
            end = torch.cuda.Event(enable_timing=True)
            end.record()
            events.append((start, end))

    sampler = VramSampler()
    sampler.start()
    started = time.perf_counter()
    with torch.autocast("cuda", dtype=dtype):
        network.inference_stream_iter(
            frame_stream(),
            num_frames=len(frames),
            output_keys=output_keys,
            dense_output_indices=None,
        )
    torch.cuda.synchronize()
    wall = time.perf_counter() - started
    sampler.stop()

    per_frame_ms = [start.elapsed_time(end) for start, end in events]
    return {
        "label": label,
        "frames": len(frames),
        "wall_seconds": wall,
        "wall_ms_per_frame": wall * 1000.0 / len(frames),
        "wall_fps": len(frames) / wall,
        "gpu_ms_per_frame_mean": statistics.mean(per_frame_ms),
        "gpu_ms_per_frame_median": statistics.median(per_frame_ms),
        "gpu_ms_per_frame_min": min(per_frame_ms),
        "gpu_ms_per_frame_max": max(per_frame_ms),
        "gpu_fps_from_events": 1000.0 / statistics.mean(per_frame_ms),
        "torch_peak_allocated_gib": torch.cuda.max_memory_allocated() / 1024**3,
        "torch_peak_reserved_gib": torch.cuda.max_memory_reserved() / 1024**3,
        "process_peak_vram_gib": sampler.peak_bytes / 1024**3,
    }


def main() -> None:
    args = parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("This benchmark needs a CUDA device")
    capability = torch.cuda.get_device_capability(0)
    print(f"[bench] gpu           : {torch.cuda.get_device_name(0)}")
    print(f"[bench] capability    : sm_{capability[0]}{capability[1]}")
    print(f"[bench] torch        : {torch.__version__}  cuda {torch.version.cuda}")
    print(f"[bench] amp dtype    : {args.amp_dtype}   backend: {args.attention_backend}")
    print(f"[bench] input size   : {args.width}x{args.height} (WxH)")

    started = time.perf_counter()
    frames = extract_frames(args.video, args.work_dir, args.fps, args.frames)
    print(
        f"[bench] extracted {len(frames)} frames in {time.perf_counter()-started:.1f}s"
        f" -> {args.work_dir}"
    )

    model = ABotRecon.from_pretrained(
        args.checkpoint,
        device="cuda",
        amp_dtype=args.amp_dtype,
        attention_backend=args.attention_backend,
        loop_closure=False,
        max_frames=22_000,
    )
    network = model.model.network
    dtype = DTYPES[args.amp_dtype]
    torch.cuda.synchronize()

    weight_bytes = sum(p.numel() * p.element_size() for p in network.parameters())
    weight_bytes += sum(b.numel() * b.element_size() for b in network.buffers())
    free, total = torch.cuda.mem_get_info()
    print(f"[bench] model params  : {sum(p.numel() for p in network.parameters())/1e9:.3f} B")
    print(f"[bench] weights on GPU: {weight_bytes/1024**3:.3f} GiB")
    print(f"[bench] GPU total     : {total/1024**3:.2f} GiB")

    configs = [
        ("camera_only", ["camera_poses"]),
        ("full_points_conf", ["camera_poses", "local_points", "conf"]),
    ]
    results = []
    for label, keys in configs:
        print(f"\n[bench] === {label} ===")
        # Warm up first: the model uses a fixed 12-frame local window.
        benchmark(network, frames[:12], keys, args.height, args.width, dtype, "cuda", "warmup")
        result = benchmark(network, frames, keys, args.height, args.width, dtype, "cuda", label)
        results.append(result)
        for key, value in result.items():
            print(f"    {key:34s} = {value:.4f}" if isinstance(value, float) else f"    {key:34s} = {value}")

    summary = {
        "gpu_name": torch.cuda.get_device_name(0),
        "compute_capability": f"sm_{capability[0]}{capability[1]}",
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "amp_dtype": args.amp_dtype,
        "attention_backend": args.attention_backend,
        "input_resolution": f"{args.width}x{args.height}",
        "loop_closure": False,
        "frames": len(frames),
        "source_video": str(args.video),
        "weights_gib": weight_bytes / 1024**3,
        "results": results,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[bench] summary -> {args.out_json}")


if __name__ == "__main__":
    main()
