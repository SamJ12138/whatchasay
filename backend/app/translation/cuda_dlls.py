"""
Windows helper: make the CUDA runtime DLLs bundled with the torch wheel
visible to other CUDA-built extensions (llama.cpp, CTranslate2) so users do
not need a separate CUDA Toolkit install.

Call prepare() before importing llama_cpp / ctranslate2 on Windows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False


def prepare() -> None:
    global _done
    if _done or sys.platform != "win32":
        _done = True
        return
    _done = True
    candidates = []
    try:
        import torch  # noqa

        candidates.append(Path(torch.__file__).parent / "lib")
    except Exception:
        pass
    # pip "nvidia-*-cu12" wheels put DLLs under site-packages/nvidia/<pkg>/bin
    for p in sys.path:
        nv = Path(p) / "nvidia"
        if nv.is_dir():
            for sub in nv.iterdir():
                b = sub / "bin"
                if b.is_dir():
                    candidates.append(b)
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        candidates.append(Path(cuda_path) / "bin")
    for d in candidates:
        if d.is_dir():
            try:
                os.add_dll_directory(str(d))
            except Exception:
                pass
