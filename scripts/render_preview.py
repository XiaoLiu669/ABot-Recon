"""Render a static preview of an ABot-Recon reconstruction (PNG).

Reads the colored PLY produced by scripts/export_reconstruction_ply.py and draws
a top-down point-cloud view with the camera trajectory, plus a side view.

Example:
    python scripts/render_preview.py --output-dir outputs/run
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def load_ply(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Minimal binary PLY reader for x/y/z float32 + rgb uchar vertices."""
    with path.open("rb") as handle:
        header = b""
        while True:
            line = handle.readline()
            header += line
            if line.strip() == b"end_header":
                break
        text = header.decode("ascii", errors="replace")
        fmt = "binary_little_endian"
        count = 0
        props: list[tuple[str, str]] = []
        in_vertex = False
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "format":
                fmt = parts[1]
            elif parts[0] == "element":
                in_vertex = parts[1] == "vertex"
                if in_vertex:
                    count = int(parts[2])
            elif parts[0] == "property" and in_vertex:
                props.append((parts[1], parts[2]))
        if fmt != "binary_little_endian":
            raise ValueError(f"only binary_little_endian PLY supported, got {fmt}")

        np_types = {
            "float": "<f4", "float32": "<f4", "double": "<f8", "float64": "<f8",
            "uchar": "u1", "uint8": "u1", "char": "i1", "int8": "i1",
            "ushort": "<u2", "uint16": "<u2", "short": "<i2", "int16": "<i2",
            "uint": "<u4", "uint32": "<u4", "int": "<i4", "int32": "<i4",
        }
        dtype = np.dtype([(name, np_types[typ]) for typ, name in props])
        data = np.frombuffer(handle.read(dtype.itemsize * count), dtype=dtype, count=count)

    names = [n for _, n in props]
    points = np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float64)
    if "red" in names:
        colors = np.stack([data["red"], data["green"], data["blue"]], axis=1) / 255.0
    else:
        colors = np.full((len(points), 3), 0.7)
    return points, colors


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a static ABot-Recon preview")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--radius", type=float, default=60.0, help="crop radius around the start")
    parser.add_argument("--max-points", type=int, default=600_000)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    ply = args.output_dir / "reconstruction.ply"
    poses_path = args.output_dir / "camera_poses.npy"
    if not ply.is_file():
        raise FileNotFoundError(ply)

    points, colors = load_ply(ply)
    poses = np.load(poses_path)
    traj = poses[:, :3, 3]

    mask = np.linalg.norm(points[:, [0, 2]] - traj[0, [0, 2]], axis=1) < args.radius
    sel = np.flatnonzero(mask)
    if len(sel) > args.max_points:
        sel = sel[np.linspace(0, len(sel) - 1, args.max_points).astype(int)]
    p = points[sel]
    c = colors[sel]

    fig, axes = plt.subplots(1, 2, figsize=(18, 9))

    ax = axes[0]
    ax.scatter(p[:, 0], p[:, 2], c=c, s=0.35, alpha=0.55, linewidths=0)
    ax.plot(traj[:, 0], traj[:, 2], "-", color="crimson", lw=1.8, label="camera path")
    ax.scatter([traj[0, 0]], [traj[0, 2]], c="limegreen", s=90, marker="o",
               edgecolors="k", linewidths=0.6, label="start", zorder=5)
    ax.scatter([traj[-1, 0]], [traj[-1, 2]], c="dodgerblue", s=140, marker="*",
               edgecolors="k", linewidths=0.6, label="end", zorder=5)
    ax.set_xlabel("X (m)")
    ax.set_ylabel("Z (m)")
    ax.set_title("Top-down view (X-Z) with camera trajectory")
    ax.set_aspect("equal")
    ax.legend(loc="best", fontsize=10)
    ax.grid(alpha=0.25)

    ax = axes[1]
    ax.scatter(p[:, 2], p[:, 1], c=c, s=0.35, alpha=0.55, linewidths=0)
    ax.set_xlabel("Z forward (m)")
    ax.set_ylabel("Y height (m)")
    ax.set_title("Side view (camera height)")
    ax.grid(alpha=0.25)

    out = args.output or (args.output_dir / "pointcloud_preview.png")
    fig.tight_layout()
    fig.savefig(out, dpi=110, facecolor="white")
    span = traj[:, [0, 2]].max(axis=0) - traj[:, [0, 2]].min(axis=0)
    print(f"points={len(points)} drawn={len(p)} trajectory_span={span[0]:.2f}x{span[1]:.2f} m")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
