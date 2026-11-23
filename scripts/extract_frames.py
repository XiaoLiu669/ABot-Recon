"""Extract a fixed-FPS frame sequence from a video (ABot-Recon input prep).

Thin wrapper over the repo's own ``demo.extract_video_frames`` so the frame
naming / JPEG settings stay identical to what ``demo.py --video`` produces.

Example:
    python scripts/extract_frames.py --video 视频.mp4 --output-dir frames/ --fps 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cv2  # noqa: E402
from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402


def extract_at_fps(
    video_path: Path,
    output_dir: Path,
    *,
    fps: float | None = None,
    start: float = 0.0,
    end: float | None = None,
    jpeg_quality: int = 95,
) -> list[Path]:
    """Write ``00000000.jpg`` ... frames sampled at ``fps`` (None = every frame)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise ValueError(f"Could not open video: {video_path}")

    src_fps = float(capture.get(cv2.CAP_PROP_FPS)) or 0.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps is None or fps <= 0:
        step = 1
    else:
        step = max(1, int(round(src_fps / fps))) if src_fps > 0 else 1

    start_frame = int(round(start * src_fps)) if src_fps > 0 else 0
    end_frame = int(round(end * src_fps)) if (end is not None and src_fps > 0) else total

    selected: list[Path] = []
    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index >= start_frame and (index - start_frame) % step == 0:
                if end_frame is not None and index >= end_frame:
                    break
                rgb = np.ascontiguousarray(frame[:, :, ::-1])
                path = output_dir / f"{len(selected):08d}.jpg"
                Image.fromarray(rgb, mode="RGB").save(path, format="JPEG", quality=jpeg_quality)
                selected.append(path)
            index += 1
    finally:
        capture.release()

    if not selected:
        raise ValueError(f"No frames selected from {video_path}")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract frames for ABot-Recon")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=None, help="sampling fps (default: all frames)")
    parser.add_argument("--start", type=float, default=0.0, help="start time in seconds")
    parser.add_argument("--end", type=float, default=None, help="end time in seconds")
    parser.add_argument("--jpeg-quality", type=int, default=95)
    args = parser.parse_args()

    if not args.video.is_file():
        raise FileNotFoundError(args.video)

    paths = extract_at_fps(
        args.video,
        args.output_dir,
        fps=args.fps,
        start=args.start,
        end=args.end,
        jpeg_quality=args.jpeg_quality,
    )
    print(f"Extracted {len(paths)} frames -> {args.output_dir}")


if __name__ == "__main__":
    main()
