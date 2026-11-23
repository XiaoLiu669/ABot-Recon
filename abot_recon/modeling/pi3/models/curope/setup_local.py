"""Build the cuRoPE2D CUDA extension with an extra CUDA include path.

The upstream ``setup.py`` assumes a monolithic CUDA Toolkit layout where
``$CUDA_HOME/include`` contains ``cuda_runtime.h``. On this machine CUDA was
installed in component ("network") layout, so the CUDA runtime headers live in
a separate directory.

This wrapper reuses the upstream extension definition and only adds the
extra include directory; it does not modify the repository's ``setup.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

from setuptools import setup
from torch.utils.cpp_extension import BuildExtension, CUDAExtension

HERE = Path(__file__).resolve().parent

# Extra include directories for a componentized CUDA installation.
# Any directory listed here is used as-is; the caller is responsible for
# pointing at real directories (e.g. cuda_runtime.h, nv/target).
extra_includes = []
for env_name in ("CUDA_EXTRA_INCLUDE_DIRS",):
    raw = os.environ.get(env_name, "")
    extra_includes.extend(p for p in raw.split(os.pathsep) if p)
extra_includes = [p for p in extra_includes if Path(p).is_dir()]

extra_libs = []
for env_name in ("CUDA_EXTRA_LIB_DIRS",):
    raw = os.environ.get(env_name, "")
    extra_libs.extend(p for p in raw.split(os.pathsep) if p)
extra_libs = [p for p in extra_libs if Path(p).is_dir()]

if not extra_includes:
    raise SystemExit(
        "Set CUDA_EXTRA_INCLUDE_DIRS to the directories that contain "
        "cuda_runtime.h and nv/target "
        "(e.g. D:\\software\\cuda\\cuda_cudart\\cudart\\include;"
        "D:\\software\\cuda\\cuda_cccl\\thrust\\include)"
    )

setup(
    name="curope",
    ext_modules=[
        CUDAExtension(
            name="curope",
            sources=[
                str(HERE / "curope.cpp"),
                str(HERE / "kernels.cu"),
            ],
            include_dirs=extra_includes,
            library_dirs=extra_libs,
            extra_compile_args=dict(
                nvcc=["-O3", "--ptxas-options=-v"],
                cxx=["-O3"],
            ),
        )
    ],
    cmdclass={"build_ext": BuildExtension},
)
