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
         the child process, so no separate CUDA Toolkit is needed (the torch
         package is located with importlib, never imported: P9).

Lifecycle (translator_process in the run log):
  - the child's stdout+stderr go to one pipe drained by a thread; the last
    50 lines are attached to the fail line when it exits or dies on start (P1)
  - a request that finds the child dead or unreachable waits for it to exit
    (or stops it), restarts it and waits for /health, bounded by
    hymt_restart_timeout_s, then retries once; only then does the router fall
    back (P2)
  - a failed warmup is retried (hymt_warmup_attempts, doubling pause); if it
    still fails the engine is skipped for hymt_retry_initial_s, then tried
    again, the pause doubling up to hymt_retry_max_s (P3)

Prompt format from the model card:
    zh involved:  将以下文本翻译为{lang}，注意只需要输出翻译后的结果，不要额外解释：\n{text}
    otherwise:    Translate the following segment into {lang}, without additional explanation.\n{text}
"""

from __future__ import annotations

import atexit
import importlib.util
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from typing import List, Optional

import httpx

from ..config import settings, get_language_display_name
from .. import obs

logger = logging.getLogger(__name__)
STAGE = "translator_process"
STDERR_TAIL_LINES = 50

# The child is gone or not listening (crashed, killed, restarting): restart it.
# A read *timeout* is not here: a busy child is not restarted.
# Connect timeout: a localhost connect takes well under a millisecond, but Windows
# needs ~2 s to *refuse* one to a dead child's port; 0.5 s ends that wait early.
_CHILD_GONE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)
_CLIENT_TIMEOUT = httpx.Timeout(20.0, connect=0.5)


def _plog(event: str, **kw) -> None:
    """translator_process lines belong to the subprocess layer and to no tab session."""
    obs.log(STAGE, event, layer="subprocess", session_id="-", **kw)

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
    """CUDA runtime DLL dirs for the child's PATH. The torch wheel's lib dir is
    found with importlib.util.find_spec: importing torch here cost 1.2-2.5 s
    before every first spawn (P9)."""
    dirs = []
    try:
        spec = importlib.util.find_spec("torch")
        locations = list(spec.submodule_search_locations or []) if spec else []
        if not locations:
            raise ModuleNotFoundError("No module named 'torch'")
        lib = Path(locations[0]) / "lib"
        if lib.is_dir():
            dirs.append(str(lib))
    except Exception as e:
        _plog("skip", error_type="process", error_message=f"torch CUDA DLL dir not added to PATH: {e}", action="cuda_dlls")
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
    _plog("start", action="download", release=LLAMA_RELEASE)
    t0 = time.perf_counter()
    try:
        urllib.request.urlretrieve(LLAMA_ZIP_URL, archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(LLAMA_BIN_DIR)
        archive.unlink(missing_ok=True)
        (LLAMA_BIN_DIR / "VERSION.txt").write_text(LLAMA_RELEASE)
        if not LLAMA_SERVER.exists():
            raise RuntimeError("llama-server binary missing after extraction")
    except Exception as e:
        _plog("fail", duration_ms=(time.perf_counter() - t0) * 1000, error_type=obs.classify(e),
              error_message=str(e), action="download")
        raise
    _plog("success", duration_ms=(time.perf_counter() - t0) * 1000, action="download")
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
        self._spawns = 0
        self._stopping: set = set()  # pids the backend itself is stopping
        self.available = LLAMA_AVAILABLE
        self._tail: deque = deque(maxlen=STDERR_TAIL_LINES)  # last lines of the current child's output
        self._drain_thread: Optional[threading.Thread] = None
        self._t_start = 0.0
        self._retry_at = 0.0  # monotonic time before which a failed engine is skipped (P3)
        self._retry_backoff: Optional[float] = None
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

    @staticmethod
    def _alive(proc: Optional[subprocess.Popen]) -> bool:
        return proc is not None and proc.returncode is None and proc.poll() is None

    def _tail_lines(self) -> List[str]:
        """Last lines of the child's output (joins the drain thread briefly so a
        just-exited child's final lines are included)."""
        t = self._drain_thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)
        return list(self._tail)

    def _start(self, reason: Optional[str] = None, deadline_s: Optional[float] = None) -> None:
        if self._alive(self._proc):
            return
        if reason:
            action = "restart"
        elif self._proc is not None:
            action, reason = "restart", f"previous child (pid {self._proc.pid}) exited with code {self._proc.returncode}"
        elif self._spawns:
            action, reason = "restart", "previous start failed"
        else:
            action, reason = "spawn", "first use"
        self._spawns += 1
        self._t_start = time.perf_counter()
        _plog("start", action=action, reason=reason, spawn_no=self._spawns)
        try:
            self._start_child(deadline_s)
        except Exception as e:
            proc = self._proc
            code = proc.returncode if proc is not None else None
            msg = str(e)
            if "exited early" in msg:
                _plog("fail", error_type="process", error_message=msg, action="early_exit", exit_code=code, port=self._port,
                      stderr_tail=self._tail_lines())
            elif "did not become healthy" in msg:
                if self._alive(proc):  # do not leave an unhealthy child running
                    self._stopping.add(proc.pid)
                    proc.kill()
                _plog("fail", error_type="timeout", error_message=msg, action="health_timeout", port=self._port,
                      stderr_tail=self._tail_lines())
            else:
                _plog("fail", error_type=obs.classify(e, "process"), error_message=msg, action=action)
            raise

    def _watch(self, proc: subprocess.Popen, t_spawn: float, tail: deque, drain: threading.Thread) -> None:
        """Waiter thread (observation only): record the child's exit code and
        the last lines it printed."""
        try:
            code = proc.wait()
        except Exception:
            return
        if proc.pid in self._stopping:
            return  # shutdown() logs the exit it asked for
        drain.join(timeout=1.0)
        _plog("fail", error_type="process", error_message=f"llama-server exited on its own with code {code}",
              action="exit", exit_code=code, pid=proc.pid, lived_s=round(time.time() - t_spawn, 1),
              stderr_tail=list(tail))

    @staticmethod
    def _drain(proc: subprocess.Popen, tail: deque) -> None:
        """Read the child's output so the pipe never fills; keep the last lines."""
        try:
            for raw in iter(proc.stdout.readline, b""):
                tail.append(raw.decode("utf-8", "replace").rstrip()[:300])
        except Exception:
            pass

    def _command(self, port: int) -> List[str]:
        """llama-server command line (tests replace this with tests/fake_llama_server.py)."""
        server = ensure_llama_server()
        model = self._ensure_model()
        return [
            str(server.resolve()), "-m", str(model.resolve()),
            "--host", "127.0.0.1", "--port", str(port),
            "-ngl", str(self.n_gpu_layers), "-c", str(self.n_ctx * self.parallel), "-np", str(self.parallel),
            "--no-webui",  # logging stays on: the output is drained and its tail kept (P1)
        ]

    def _start_child(self, deadline_s: Optional[float] = None) -> None:
        self._port = _free_port()
        cmd = self._command(self._port)
        env = dict(os.environ)
        env["PATH"] = os.pathsep.join(_cuda_dll_dirs() + [env.get("PATH", "")])
        creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._tail = tail = deque(maxlen=STDERR_TAIL_LINES)
        t0 = time.time()
        pre_popen_ms = (time.perf_counter() - self._t_start) * 1000 if self._t_start else None
        self._proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, creationflags=creation)
        self._drain_thread = drain = threading.Thread(target=self._drain, args=(self._proc, tail),
                                                      name="llama-server-output", daemon=True)
        drain.start()
        threading.Thread(target=self._watch, args=(self._proc, t0, tail, drain), name="llama-server-watch", daemon=True).start()
        self._client = httpx.Client(base_url=f"http://127.0.0.1:{self._port}", timeout=_CLIENT_TIMEOUT)
        deadline = time.time() + (deadline_s if deadline_s is not None else settings.mt.hymt_health_timeout_s)
        polls = 0
        last_poll_error = None
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f"llama-server exited early with code {self._proc.returncode}")
            polls += 1
            try:
                r = self._client.get("/health", timeout=1.0)
                if r.status_code == 200 and r.json().get("status") == "ok":
                    break
                last_poll_error = f"HTTP {r.status_code}"
            except Exception as e:
                last_poll_error = type(e).__name__  # expected while the model loads
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("llama-server did not become healthy in time")
        logger.info("HY-MT1.5-1.8B ready via llama-server on port %d (%.1fs)", self._port, time.time() - t0)
        _plog("success", duration_ms=(time.time() - t0) * 1000, action="ready", port=self._port, pid=self._proc.pid,
              health_polls=polls, last_poll_error=last_poll_error, model=self.gguf_path.name, slots=self.parallel,
              pre_popen_ms=round(pre_popen_ms, 1) if pre_popen_ms is not None else None)

    def shutdown(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            self._stopping.add(proc.pid)
            _plog("start", action="stop", pid=proc.pid, reason="backend shutdown")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception as e:
                _plog("fail", error_type=obs.classify(e, "process"), error_message=f"terminate failed, killing: {e}",
                      action="stop", pid=proc.pid)
                try:
                    proc.kill()
                except Exception as e2:
                    _plog("fail", error_type="process", error_message=f"kill failed: {e2}", action="stop", pid=proc.pid)
                    pass
            if proc.returncode is not None:
                _plog("success", action="exit", exit_code=proc.returncode, pid=proc.pid, reason="stopped by backend")

    def warmup(self) -> None:
        """Start the child and translate "Hello". Retried hymt_warmup_attempts
        times with a doubling pause; if every attempt fails the engine is
        paused (supports() False) and tried again later (P3), not disabled for
        the life of the process."""
        t0 = time.perf_counter()
        attempts = max(1, int(settings.mt.hymt_warmup_attempts))
        pause = max(0.0, float(settings.mt.hymt_warmup_backoff_s))
        for attempt in range(1, attempts + 1):
            try:
                with self._lock:
                    self._start()
                self.translate_batch_sync(["Hello"], "en", "zh")
                self._mark_ok()
                _plog("success", duration_ms=(time.perf_counter() - t0) * 1000, action="warmup", attempt=attempt)
                return
            except Exception as e:
                last = e
                retry = attempt < attempts
                logger.warning("HY-MT warmup attempt %d/%d failed: %s", attempt, attempts, e)
                _plog("fail", duration_ms=(time.perf_counter() - t0) * 1000, error_type=obs.classify(e, "process"),
                      error_message=str(e), action="warmup", attempt=attempt, will_retry=retry,
                      degraded=(f"retrying in {pause:.1f}s" if retry else
                                "HY-MT paused; tried again after the retry backoff (supports() is False until then)"))
                if retry:
                    time.sleep(pause)
                    pause *= 2
        self._mark_failed(last)

    def _mark_failed(self, e: BaseException) -> None:
        """Pause the engine: skipped for _retry_backoff seconds, then tried again (doubling, capped)."""
        self._load_error = str(e) or type(e).__name__
        cfg = settings.mt
        self._retry_backoff = cfg.hymt_retry_initial_s if self._retry_backoff is None else min(self._retry_backoff * 2, cfg.hymt_retry_max_s)
        self._retry_at = time.monotonic() + self._retry_backoff

    def _mark_ok(self) -> None:
        self._load_error = None
        self._retry_backoff = None
        self._retry_at = 0.0

    # -- MTEngine ------------------------------------------------------------

    def supports(self, source_lang: str, target_lang: str) -> bool:
        if not self.available:
            return False
        if self._load_error and time.monotonic() < self._retry_at:
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
            dropped = result.count("\n")
            result = result.split("\n")[0].strip()
            obs.log("mt_call", "skip", error_type="parse", engine=self.name, src=source_lang, tgt=target_lang,
                    error_message=f"multi-line output truncated to the first line ({dropped} line break(s) dropped)",
                    degraded="later lines discarded")
        return result

    def _recover(self, err: BaseException) -> None:
        """The child is dead or unreachable (P2): wait for it to exit (or stop
        it), restart it and wait for /health, all within hymt_restart_timeout_s."""
        bound = max(1.0, float(settings.mt.hymt_restart_timeout_s))
        deadline = time.monotonic() + bound
        with self._lock:
            proc = self._proc
            if proc is not None and proc.returncode is None:
                try:
                    proc.wait(timeout=min(2.0, bound / 4))  # just died: let the OS finish reaping it
                except subprocess.TimeoutExpired:
                    self._stopping.add(proc.pid)  # alive but not answering: stop it
                    proc.kill()
                    proc.wait(timeout=5)
            self._start(reason=f"request failed: {type(err).__name__}: {err}"[:200],
                        deadline_s=max(0.5, deadline - time.monotonic()))

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        try:
            with self._lock:
                # cheap when already running; serializes only the startup. A restart on the
                # request path (an earlier child existed) is bounded like _recover (P2);
                # only the very first start gets the full model-load budget.
                self._start(deadline_s=settings.mt.hymt_restart_timeout_s if self._spawns else None)
        except Exception as e:
            self._mark_failed(e)
            raise
        t0 = time.time()
        try:
            out = [self._one(t, source_lang, target_lang) for t in texts]
        except _CHILD_GONE as e:
            logger.warning("llama-server unreachable (%s); restarting before any fallback", e)
            try:
                self._recover(e)
            except Exception as e2:
                self._mark_failed(e2)
                raise
            out = [self._one(t, source_lang, target_lang) for t in texts]  # one retry on the new child
        if self._load_error:
            self._mark_ok()
        ms = (time.time() - t0) * 1000
        self.stats["calls"] += len(texts)
        self.stats["total_ms"] += ms
        return out

    def status(self) -> dict:
        return {
            "engine": self.name,
            "available": self.available and not self._load_error,
            "loaded": self._proc is not None and self._proc.poll() is None,
            "model": self.gguf_path.name,
            "port": self._port,
            "error": self._load_error,
            "retry_in_s": round(max(0.0, self._retry_at - time.monotonic()), 1) if self._load_error else None,
            "avg_ms": round(self.stats["total_ms"] / self.stats["calls"], 1) if self.stats["calls"] else None,
        }
