"""ABot-Recon 本地 Web 应用：上传视频 -> 重建 -> 在浏览器里看 3D 结果。

一个 Flask 服务，负责三件事：
  1. 接收视频上传并落盘到 runs/<run_id>/；
  2. 以子进程方式跑 webapp/worker.py（推理 + 导出），把进度实时回传前端；
  3. 把 manifest / 点云分块 / 轨迹 / PLY 等产物通过 HTTP 提供给前端和
     已有的 tools/point_cloud_viewer.html。

启动：
    python webapp/server.py --port 8770
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

REPO_ROOT = Path(__file__).resolve().parents[1]
WEBAPP_DIR = Path(__file__).resolve().parent
DEFAULT_RUNS_DIR = WEBAPP_DIR / "runs"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GiB

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

STATE_LOCK = threading.Lock()
RUNS: dict[str, dict] = {}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def runs_dir() -> Path:
    return Path(os.environ.get("ABOT_RECON_RUNS_DIR", DEFAULT_RUNS_DIR))


def run_dir(run_id: str) -> Path:
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        abort(400)
    return runs_dir() / run_id


def read_state(run_id: str) -> dict:
    path = run_dir(run_id) / "run.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    with STATE_LOCK:
        return dict(RUNS.get(run_id, {}))


def write_state(run_id: str, state: dict) -> None:
    with STATE_LOCK:
        RUNS[run_id] = state
    path = run_dir(run_id) / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_log(run_id: str, line: str) -> None:
    state = read_state(run_id)
    entries = state.get("logs", [])
    entries.append(line)
    state["logs"] = entries[-400:]
    write_state(run_id, state)


def safe_name(name: str) -> str:
    keep = []
    for char in name:
        keep.append(char if (char.isalnum() or char in "-_.（）() 中文") else "_")
    cleaned = "".join(keep).strip("._") or "video.mp4"
    return cleaned[:120]


# --------------------------------------------------------------------------- #
# 任务执行
# --------------------------------------------------------------------------- #
def execute(run_id: str, options: dict) -> None:
    """在后台线程里跑 worker 子进程，并把它的输出写进 run.json。"""
    directory = run_dir(run_id)
    state = read_state(run_id)
    video_name = state.get("video_file") or "input.mp4"
    video = directory / video_name
    if not video.is_file():
        state["state"] = "failed"
        state["phase"] = "失败"
        state["error"] = f"找不到上传的视频文件：{video_name}"
        write_state(run_id, state)
        return
    checkpoint = Path(options.get("checkpoint") or (REPO_ROOT / "checkpoints" / "abot_recon.safetensors"))
    command = [
        sys.executable,
        str(WEBAPP_DIR / "worker.py"),
        "--run-dir", str(directory),
        "--video", str(video),
        "--checkpoint", str(checkpoint),
        "--fps", str(options.get("fps", 4)),
        "--device", options.get("device", "cuda"),
        "--amp-dtype", options.get("amp_dtype", "fp16"),
        "--attention-backend", options.get("attention_backend", "sdpa"),
        "--confidence-threshold", str(options.get("confidence_threshold", 0.01)),
        "--max-points", str(options.get("max_points", 20_000_000)),
    ]
    state = read_state(run_id)
    state.update(
        {
            "state": "running",
            "started_at": time.time(),
            "phase": "启动中",
            "progress_done": 0,
            "progress_total": 0,
            "error": None,
            "result": None,
            "options": options,
        }
    )
    write_state(run_id, state)

    process = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            append_log(run_id, line)
            continue
        tag = event.get("tag")
        current = read_state(run_id)
        if tag == "LOG":
            current.setdefault("logs", []).append(event.get("message", ""))
            current["logs"] = current["logs"][-400:]
        elif tag == "PHASE":
            current["phase"] = event.get("phase", "")
            current["phase_index"] = event.get("index", 0)
            current["phase_total"] = event.get("total", 1)
        elif tag == "PROGRESS":
            current["progress_done"] = event.get("done", 0)
            current["progress_total"] = event.get("total", 0)
        elif tag == "DONE":
            current["result"] = {
                "point_count": event.get("point_count"),
                "chunk_count": event.get("chunk_count"),
                "frames": event.get("frames"),
            }
            current["phase"] = "完成"
        elif tag == "ERROR":
            current["error"] = event.get("message", "未知错误")
        write_state(run_id, current)

    return_code = process.wait()
    current = read_state(run_id)
    current["finished_at"] = time.time()
    if return_code != 0 and not current.get("error"):
        current["error"] = f"worker 退出码 {return_code}"
    if current.get("error"):
        current["state"] = "failed"
        current["phase"] = "失败"
    else:
        current["state"] = "done"
        current["phase"] = "完成"
        current["progress_done"] = current.get("progress_total") or current.get("progress_done")
    current["duration"] = round(current["finished_at"] - current.get("started_at", current["finished_at"]), 1)
    write_state(run_id, current)


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> "object":
    return send_from_directory(WEBAPP_DIR, "index.html")


@app.get("/assets/<path:name>")
def assets(name: str) -> "object":
    return send_from_directory(WEBAPP_DIR / "static", name)


@app.get("/viewer")
def viewer() -> "object":
    return send_from_directory(REPO_ROOT / "tools", "point_cloud_viewer.html")


@app.get("/point_cloud_datasets.json")
def point_cloud_catalog() -> "object":
    """查看器会按自身 URL 相对地请求这个数据集目录文件，缺失时它会退化成 404。"""
    return send_from_directory(REPO_ROOT / "tools", "point_cloud_datasets.json")


@app.get("/pseudo_gaussian_datasets.json")
def pseudo_gaussian_catalog() -> "object":
    return send_from_directory(REPO_ROOT / "tools", "pseudo_gaussian_datasets.json")


@app.get("/api/config")
def config() -> "object":
    checkpoint = REPO_ROOT / "checkpoints" / "abot_recon.safetensors"
    gpu = None
    try:
        import torch

        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001
        gpu = None
    return jsonify(
        {
            "gpu": gpu,
            "checkpoint": str(checkpoint),
            "checkpoint_ready": checkpoint.is_file(),
            "repo_root": str(REPO_ROOT),
        }
    )


@app.post("/api/upload")
def upload() -> "object":
    if "video" not in request.files:
        return jsonify({"error": "缺少 video 文件字段"}), 400
    handle = request.files["video"]
    if not handle or not handle.filename:
        return jsonify({"error": "没有选择文件"}), 400
    run_id = uuid.uuid4().hex[:12]
    directory = run_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    original = safe_name(handle.filename)
    suffix = Path(original).suffix.lower() or ".mp4"
    stored = directory / f"input{suffix}"
    handle.save(stored)
    if not stored.is_file() or stored.stat().st_size == 0:
        shutil.rmtree(directory, ignore_errors=True)
        return jsonify({"error": "上传失败"}), 400
    state = {
        "run_id": run_id,
        "state": "uploaded",
        "phase": "已上传",
        "filename": original,
        "video_file": stored.name,
        "size": stored.stat().st_size,
        "created_at": time.time(),
        "logs": [],
        "error": None,
        "result": None,
    }
    write_state(run_id, state)
    return jsonify({"run_id": run_id, "filename": original, "size": stored.stat().st_size})


@app.post("/api/run")
def start_run() -> "object":
    payload = request.get_json(silent=True) or {}
    run_id = str(payload.get("run_id", ""))
    directory = run_dir(run_id)
    if not directory.is_dir():
        return jsonify({"error": "任务不存在"}), 404
    state = read_state(run_id)
    if state.get("state") == "running":
        return jsonify({"error": "任务正在运行"}), 409
    options = {
        "fps": float(payload.get("fps", 4) or 4),
        "amp_dtype": str(payload.get("amp_dtype", "fp16")),
        "attention_backend": str(payload.get("attention_backend", "sdpa")),
        "confidence_threshold": float(payload.get("confidence_threshold", 0.01) or 0.0),
        "max_points": int(payload.get("max_points", 20_000_000) or 20_000_000),
        "device": str(payload.get("device", "cuda")),
    }
    thread = threading.Thread(target=execute, args=(run_id, options), daemon=True)
    thread.start()
    return jsonify({"run_id": run_id, "started": True})


@app.get("/api/status/<run_id>")
def status(run_id: str) -> "object":
    state = read_state(run_id)
    if not state:
        return jsonify({"error": "任务不存在"}), 404
    state = dict(state)
    state["logs"] = state.get("logs", [])[-120:]
    return jsonify(state)


@app.get("/api/runs")
def list_runs() -> "object":
    items = []
    base = runs_dir()
    if base.is_dir():
        for directory in base.iterdir():
            if not directory.is_dir():
                continue
            state = read_state(directory.name)
            if state:
                items.append(
                    {
                        "run_id": state.get("run_id", directory.name),
                        "filename": state.get("filename"),
                        "state": state.get("state"),
                        "phase": state.get("phase"),
                        "created_at": state.get("created_at"),
                        "result": state.get("result"),
                    }
                )
    items.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    return jsonify({"runs": items})


@app.get("/files/<run_id>/<path:relative>")
def files(run_id: str, relative: str) -> "object":
    directory = run_dir(run_id)
    target = (directory / relative).resolve()
    try:
        target.relative_to(directory.resolve())
    except ValueError:
        abort(403)
    if not target.is_file():
        abort(404)
    response = send_file(target)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/delete/<run_id>")
def delete_run(run_id: str) -> "object":
    directory = run_dir(run_id)
    if directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
    with STATE_LOCK:
        RUNS.pop(run_id, None)
    return jsonify({"deleted": True})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    os.environ["ABOT_RECON_RUNS_DIR"] = str(args.runs_dir)
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    print(f"[webapp] 服务已启动： http://{args.host}:{args.port}")
    print(f"[webapp] 任务目录： {args.runs_dir}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
