"""
Tencent HY-MT1.5-1.8B served by llama.cpp's `llama-server` on the GPU.

A translation-specialised 1.8B model covering 36 languages including
English, Chinese and Bengali in every direction with no pivot. We run the
official llama.cpp Windows CUDA build as a child process and call its
OpenAI-compatible HTTP API on localhost (a few ms overhead). This avoids the
Python binding, whose prebuilt wheels either crash on CPUs without AVX-512
or predate the HunYuan architecture.

Binary:  backend/bin/llama/llama-server.exe  (auto-downloaded from the
         ggml-org/llama.cpp GitHub release if missing)
Model:   backend/data/models/mt/HY-MT1.5-1.8B-Q8_0.gguf (auto-downloaded)
CUDA:    the runtime DLLs bundled with the torch wheel are put on PATH for
         the child process, so no separate CUDA Toolkit is needed.

Prompt format from the model card:
    zh involved:  将以下文本翻译为{lang}，注意只需要输出翻译后的结果，不要额外解释：\n{text}
    otherwise:    Translate the following segment into {lang}, without additional explanation.\n{text}
"""

from __future__ import annotations

import atexit
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import List, Optional

import httpx

from ..config import settings, get_language_display_name

logger = logging.getLogger(__name__)

LLAMA_RELEASE = "b10909"
LLAMA_ZIP_URL = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_RELEASE}/llama-{LLAMA_RELEASE}-bin-win-cuda-12.4-x64.zip"
LLAMA_BIN_DIR = Path("bin/llama")
LLAMA_SERVER = LLAMA_BIN_DIR / ("llama-server.exe" if sys.platform == "win32" else "llama-server")

# Names the model was trained with (English names; Chinese names in zh prompt).
HYMT_LANG_NAMES = {
    "en": ("English", "英语"), "zh": ("Chinese", "中文"), "bn": ("Bengali", "孟加拉语"),
    "vi": ("Vietnamese", "越南语"), "ja": ("Japanese", "日语"), "ko": ("Korean", "韩语"),
    "es": ("Spanish", "西班牙语"), "fr": ("French", "法语"), "de": ("German", "德语"),
    "ru": ("Russian", "俄语"), "pt": ("Portuguese", "葡萄牙语"), "it": ("Italian", "意大利语"),
    "ar": ("Arabic", "阿拉伯语"), "hi": ("Hindi", "印地语"), "th": ("Thai", "泰语"),
    "id": ("Indonesian", "印尼语"), "ms": ("Malay", "马来语"), "tr": ("Turkish", "土耳其语"),
    "pl": ("Polish", "波兰语"), "nl": ("Dutch", "荷兰语"), "cs": ("Czech", "捷克语"),
    "uk": ("Ukrainian", "乌克兰语"), "ta": ("Tamil", "泰米尔语"), "te": ("Telugu", "泰卢固语"),
    "mr": ("Marathi", "马拉地语"), "ur": ("Urdu", "乌尔都语"), "fa": ("Persian", "波斯语"),
    "he": ("Hebrew", "希伯来语"),
}


def build_prompt(text: str, source_lang: str, target_lang: str) -> str:
    en_name, zh_name = HYMT_LANG_NAMES.get(target_lang, (get_language_display_name(target_lang), get_language_display_name(target_lang)))
    if "zh" in (source_lang, target_lang):
        return f"将以下文本翻译为{zh_name}，注意只需要输出翻译后的结果，不要额外解释：\n\n{text}"
    return f"Translate the following segment into {en_name}, without additional explanation.\n\n{text}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cuda_dll_dirs() -> List[str]:
    dirs = []
    try:
        import torch

        dirs.append(str(Path(torch.__file__).parent / "lib"))
    except Exception:
        pass
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        dirs.append(str(Path(cuda_path) / "bin"))
    return dirs


def ensure_llama_server() -> Path:
    """Download the llama.cpp release build if the binary is missing."""
    if LLAMA_SERVER.exists():
        return LLAMA_SERVER
    if sys.platform != "win32":
        raise RuntimeError("Automatic llama.cpp download is only implemented for Windows; install llama-server manually into bin/llama/")
    import urllib.request

    LLAMA_BIN_DIR.mkdir(parents=True, exist_ok=True)
    archive = LLAMA_BIN_DIR / "llama.zip"
    logger.info("Downloading llama.cpp %s (CUDA build) ...", LLAMA_RELEASE)
    urllib.request.urlretrieve(LLAMA_ZIP_URL, archive)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(LLAMA_BIN_DIR)
    archive.unlink(missing_ok=True)
    (LLAMA_BIN_DIR / "VERSION.txt").write_text(LLAMA_RELEASE)
    if not LLAMA_SERVER.exists():
        raise RuntimeError("llama-server binary missing after extraction")
    return LLAMA_SERVER


LLAMA_AVAILABLE = LLAMA_SERVER.exists() or sys.platform == "win32"


class HyMTEngine:
    name = "hymt"

    def __init__(self, gguf_path: Optional[Path] = None):
        cfg = settings.mt
        self.gguf_path = Path(gguf_path or cfg.hymt_gguf)
        self.n_gpu_layers = cfg.hymt_gpu_layers if cfg.hymt_gpu_layers >= 0 else 99
        self.n_ctx = cfg.hymt_ctx
        self.max_tokens = cfg.hymt_max_tokens
        self.languages = set(cfg.hymt_languages)
        self.parallel = max(1, cfg.hymt_parallel)
        self.max_concurrency = self.parallel  # router may run this many requests at once
        self._proc: Optional[subprocess.Popen] = None
        self._port: Optional[int] = None
        self._client: Optional[httpx.Client] = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None
        self.stats = {"calls": 0, "total_ms": 0.0}
        atexit.register(self.shutdown)

    # -- process management ------------------------------------------------

    def _ensure_model(self) -> Path:
        if self.gguf_path.exists():
            return self.gguf_path
        from huggingface_hub import hf_hub_download

        logger.info("Downloading %s from %s ...", settings.mt.hymt_file, settings.mt.hymt_repo)
        self.gguf_path.parent.mkdir(parents=True, exist_ok=True)
        path = hf_hub_download(settings.mt.hymt_repo, settings.mt.hymt_file, local_dir=str(self.gguf_path.parent))
        return Path(path)

    def _start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        server = ensure_llama_server()
        model = self._ensure_model()
        self._port = _free_port()
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(_cuda_dll_dirs() + [env.get("PATH", "")])
        cmd = [
            str(server.resolve()), "-m", str(model.resolve()),
            "--host", "127.0.0.1", "--port", str(self._port),
            "-ngl", str(self.n_gpu_layers), "-c", str(self.n_ctx * self.parallel), "-np", str(self.parallel),
            "--no-webui", "--log-disable",
        ]
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        t0 = time.time()
        self._proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation)
        self._client = httpx.Client(base_url=f"http://127.0.0.1:{self._port}", timeout=20.0)
        deadline = time.time() + 120
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f"llama-server exited early with code {self._proc.returncode}")
            try:
                r = self._client.get("/health", timeout=1.0)
                if r.status_code == 200 and r.json().get("status") == "ok":
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("llama-server did not become healthy in time")
        logger.info("HY-MT1.5-1.8B ready via llama-server on port %d (%.1fs)", self._port, time.time() - t0)

    def shutdown(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def warmup(self) -> None:
        try:
            with self._lock:
                self._start()
            self.translate_batch_sync(["Hello"], "en", "zh")
        except Exception as e:
            self._load_error = str(e)
            logger.warning("HY-MT warmup failed: %s", e)

    # -- MTEngine ------------------------------------------------------------

    def supports(self, source_lang: str, target_lang: str) -> bool:
        if not LLAMA_AVAILABLE or self._load_error:
            return False
        return source_lang in self.languages and target_lang in self.languages

    def _one(self, text: str, source_lang: str, target_lang: str) -> str:
        prompt = build_prompt(text, source_lang, target_lang)
        r = self._client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": self.max_tokens,
                "temperature": 0.0,
                "top_p": 1.0,
                "repeat_penalty": 1.05,
                "cache_prompt": True,
            },
        )
        r.raise_for_status()
        result = r.json()["choices"][0]["message"]["content"].strip()
        # Defensive: keep the first line if the model adds commentary
        if "\n" in result and len(result) < 300:
            result = result.split("\n")[0].strip()
        return result

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        with self._lock:
            self._start()  # cheap when already running; serializes only the startup
        t0 = time.time()
        out = [self._one(t, source_lang, target_lang) for t in texts]
        ms = (time.time() - t0) * 1000
        self.stats["calls"] += len(texts)
        self.stats["total_ms"] += ms
        return out

    def status(self) -> dict:
        return {
            "engine": self.name,
            "available": LLAMA_AVAILABLE and not self._load_error,
            "loaded": self._proc is not None and self._proc.poll() is None,
            "model": self.gguf_path.name,
            "port": self._port,
            "error": self._load_error,
            "avg_ms": round(self.stats["total_ms"] / self.stats["calls"], 1) if self.stats["calls"] else None,
        }
