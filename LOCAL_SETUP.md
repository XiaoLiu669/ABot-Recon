# Local setup notes — ABot-Recon on Windows + RTX 2080 Ti (sm_75)

This document records the changes made in this fork on top of
[`DingJunyao/ABot-Recon`](https://github.com/DingJunyao/ABot-Recon) `video`
branch (which is itself a fork of the upstream
[`amap-cvlab/ABot-Recon`](https://github.com/amap-cvlab/ABot-Recon), Apache-2.0).

Upstream code and licence are unchanged; every change listed below is
additive and backwards compatible (all new CLI options have defaults that
reproduce the previous behaviour).

## What was added

| File | Purpose |
|---|---|
| `demo.py` | `--amp-dtype`, `--fps`, `--keep-frames` options (see below) |
| `scripts/extract_frames.py` | Standalone frame extraction by sampling rate |
| `scripts/render_preview.py` | Static PNG preview of a reconstruction (top-down + side view) |
| `scripts/serve_point_cloud.ps1` | Serves the bundled web point-cloud viewer over HTTP |

### `--amp-dtype`

`demo.py` previously hard-coded the inference dtype to the `InferenceConfig`
default (`bf16`) and had no way to override it, while `abot_recon.cli` already
exposed `--amp-dtype`. The option is now available in `demo.py` as well.

This matters on pre-Ampere GPUs. Measured on an RTX 2080 Ti (sm_75) with
PyTorch 2.5.1+cu121 on Windows:

| dtype | FLASH_ATTENTION | EFFICIENT_ATTENTION | MATH |
|---|---|---|---|
| `fp16` | fails | **works** | works |
| `bf16` | fails | fails | works (slow) |

The Windows PyTorch build ships no FlashAttention CUDA backend, and the bf16
FlashAttention kernels only exist for sm_80+, so `bf16` silently drops to the
`MATH` backend — which is what makes the upstream blog report 4–30 s/frame.
With `--amp-dtype fp16` the memory-efficient kernel is used instead.

Accuracy impact was checked against an fp32 reference run on the same input:

```
camera_poses : max abs diff 8.48e-06   (relative 0.0008%)
local_points : max abs diff 0.00401    (relative 0.11%)
```

### `--fps`

`--stride` selects every N-th *source frame*, which makes the sampling density
depend on the input frame rate. `--fps` selects by wall-clock rate instead, so
the same command produces the same number of frames regardless of source fps.
`--fps` takes precedence over `--stride` for video input.

### `--keep-frames`

Video frames are extracted into a temporary directory by default and discarded
afterwards. `--keep-frames DIR` writes them to `DIR` instead, which makes it
possible to re-run inference on the same frames without decoding the video
again.

### `scripts/render_preview.py`

Renders a static preview of a finished reconstruction: a top-down (X-Z) view of
the point cloud with the camera trajectory overlaid, plus a side view showing
the height structure.

```bash
python scripts/render_preview.py --output-dir outputs/run --radius 60
```

### `scripts/serve_point_cloud.ps1`

`tools/point_cloud_viewer.html` fetches the manifest and the point-cloud
chunks over HTTP, so it cannot be opened directly from `file://`. This script
starts a local static server rooted at the repository and opens the viewer:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\serve_point_cloud.ps1 -OutputDir outputs\run
```

## Running on an RTX 2080 Ti (sm_75)

```bash
python demo.py \
  --video path/to/video.mp4 \
  --checkpoint checkpoints/abot_recon.safetensors \
  --output-dir outputs/run \
  --amp-dtype fp16 \
  --attention-backend sdpa \
  --fps 4 \
  --no-loop-closure \
  --save-world-points \
  --save-confidence
```

* `--attention-backend sdpa` — FlashInfer does not support sm_75 or Windows.
* `--amp-dtype fp16` — see the dtype table above.

Measured on an RTX 2080 Ti (22 GB), PyTorch 2.5.1+cu121, 280x504 input:

```
~345 ms/frame, peak 7.4 GB VRAM
```

The KV cache is a fixed 12-frame local window, so VRAM use does not grow with
sequence length.

## Known environment pitfalls

* **Two `pip` runs against the same venv** fail with
  `WinError 32` (file in use). Install serially.
* **`curl` on PowerShell** aborts when it writes the progress bar to stderr.
  Use `--silent --show-error` and redirect the output to a file.
* **`curl` cannot combine `-C -` (resume) with `-r` (range)** —
  `curl: option -r: is badly used here`. Implement resume by shifting the
  range start by the number of bytes already present.
* **Resuming a range download can silently misalign the file**: the length may
  be right while the bytes are wrong, and a `safetensors` header may still
  parse. Verify by sampling random offsets against the server with a `Range`
  request and comparing bytes.
* **Visual Studio `--installPath` with spaces** is truncated to `C:\Program` by
  `Start-Process -ArgumentList` unless the value is quoted. Use the call
  operator instead of `Start-Process`.
* **CUDA 12.1 + MSVC**: `host_config.h` accepts `_MSC_VER` in `[1910, 1940)`,
  and the MSVC 14.44 STL additionally requires CUDA >= 12.4 (`STL1002`).
  CUDA 12.1 therefore pairs with the MSVC 14.38 toolset (VS 2022 17.8).

## Licence

The upstream project is Apache-2.0 (`LICENSE`). The cuRoPE2D CUDA extension
carries its own `CC BY-NC-SA 4.0` header from Naver Corporation, as shipped
upstream. Model weights are governed by `MODEL_LICENSE.md`.
