"""
Structured run log (observability only; never changes what the app does).

One JSON object per line in logs/run_<run_id>.jsonl:

    {ts, run_id, session_id, layer, stage, event, duration_ms,
     error_type, error_message, context}

run_id      one backend process lifetime (set at import).
session_id  one extension tab's connection. The extension generates it and
            sends it as the `session_id` query parameter of the WebSocket
            handshake and as the `X-Session-Id` header of HTTP requests. The
            backend puts it in a ContextVar, so every line logged while
            serving that connection carries it. Connections without one get
            a backend-assigned `srv-xxxxxxxx`.
layer       backend | subprocess | extension (extension lines arrive via
            POST /obs, see ingest_extension()).
event       start | success | fail | skip
error_type  input_invalid | external_api | parse | timeout | process | unknown
            input_invalid  bad or unsupported input (bad frame, bad JSON shape,
                           unsupported language pair, validation error)
            external_api   a remote service (Google/Azure/Groq/Gemini/Ollama)
                           failed or returned non-200
            parse          a response or message could not be decoded
            timeout        a deadline or connect/read timeout expired
            process        a local process or resource failed: llama-server
                           died or answered non-200, import/model file
                           missing, SQLite error, peer connection dropped
            unknown        anything else

Stage names are listed in docs/pipeline-stages.md.

The log directory is ./logs relative to the working directory (the backend is
always started from backend/), or SUBTITLE_OBS_DIR. Logging failures are
swallowed on purpose: the log must never break captions. Subtitle text is not
written; only lengths and ids.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

EVENTS = ("start", "success", "fail", "skip")
ERROR_TYPES = ("input_invalid", "external_api", "parse", "timeout", "process", "unknown")
LAYERS = ("backend", "subprocess", "extension")

RUN_ID = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
LOG_DIR = Path(os.environ.get("SUBTITLE_OBS_DIR", "logs"))

session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("obs_session_id", default=None)

_lock = threading.Lock()
_fh = None
_disabled = False
_MAX_MSG = 500
# API keys travel in URLs (Google `?key=`, Gemini `?key=`) and httpx puts the
# URL into its error messages.
_REDACT = [
    (re.compile(r"(key=)[^&\s'\"]+", re.I), r"\1REDACTED"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1REDACTED"),
    (re.compile(r"\b(AIza[0-9A-Za-z_\-]{20,}|gsk_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{20,})\b"), "REDACTED"),
]


def log_path() -> Path:
    return LOG_DIR / f"run_{RUN_ID}.jsonl"


def new_session_id(prefix: str = "srv") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def redact(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    for pat, repl in _REDACT:
        text = pat.sub(repl, text)
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _write(rec: Dict[str, Any]) -> None:
    global _fh, _disabled
    if _disabled:
        return
    try:
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock:
            if _fh is None:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                _fh = open(log_path(), "a", encoding="utf-8")
            _fh.write(line + "\n")
            _fh.flush()
    except Exception:
        _disabled = True


def log(
    stage: str,
    event: str,
    *,
    duration_ms: Optional[float] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    session_id: Optional[str] = None,
    layer: str = "backend",
    **context: Any,
) -> None:
    """Write one record. Never raises."""
    try:
        if error_type is not None and error_type not in ERROR_TYPES:
            error_type = "unknown"
        msg = redact(str(error_message))[:_MAX_MSG] if error_message is not None else None
        _write({
            "ts": _now_iso(),
            "run_id": RUN_ID,
            "session_id": session_id or session_id_var.get(),
            "layer": layer,
            "stage": stage,
            "event": event,
            "duration_ms": round(float(duration_ms), 1) if duration_ms is not None else None,
            "error_type": error_type,
            "error_message": msg,
            "context": {k: redact(v) for k, v in context.items()},
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


def classify(exc: BaseException, api: str = "external_api") -> str:
    """Map an exception to an error_type. `api` is the type used for HTTP and
    connection errors: external_api for cloud services, process for the local
    llama-server child."""
    import sqlite3

    name = type(exc).__name__
    mod = type(exc).__module__ or ""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in name:
        return "timeout"
    if isinstance(exc, json.JSONDecodeError) or name in ("JSONDecodeError", "UnicodeDecodeError"):
        return "parse"
    if mod.startswith("httpx") or mod.startswith("httpcore"):
        return api
    if isinstance(exc, (ImportError, sqlite3.Error, FileNotFoundError, PermissionError, ConnectionError, OSError)):
        return "process"
    if name in ("ValidationError", "RequestValidationError") or isinstance(exc, ValueError):
        return "input_invalid"
    if isinstance(exc, (KeyError, IndexError)):
        return "parse"
    if name in ("WebSocketDisconnect", "ClientDisconnected"):
        return "process"
    return "unknown"


def _exc_context(exc: BaseException) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {"exception": type(exc).__name__}
    resp = getattr(exc, "response", None)
    status = getattr(resp, "status_code", None)
    if status is not None:
        ctx["http_status"] = status
    return ctx


def log_exc(stage: str, exc: BaseException, *, api: str = "external_api", event: str = "fail",
            duration_ms: Optional[float] = None, error_type: Optional[str] = None, **context: Any) -> None:
    log(stage, event, duration_ms=duration_ms, error_type=error_type or classify(exc, api),
        error_message=str(exc) or type(exc).__name__, **_exc_context(exc), **context)


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------


class span:
    """Time a block and log success, or fail on exception (then re-raise).

        with obs.span("mt_call", engine="hymt") as sp:
            ...
            sp.set(n_out=3)                       # add context
            sp.skip("no engine", "input_invalid") # log skip instead of success
            sp.fail("parse", "empty output")      # log fail without an exception
    """

    def __init__(self, stage: str, *, api: str = "external_api", log_start: bool = False,
                 session_id: Optional[str] = None, layer: str = "backend", **context: Any):
        self.stage = stage
        self.api = api
        self.log_start = log_start
        self.session_id = session_id
        self.layer = layer
        self.context = context
        self._outcome: Optional[Tuple[str, Optional[str], Optional[str]]] = None
        self.t0 = 0.0

    def __enter__(self) -> "span":
        self.t0 = time.perf_counter()
        if self.log_start:
            log(self.stage, "start", session_id=self.session_id, layer=self.layer, **self.context)
        return self

    def set(self, **context: Any) -> None:
        self.context.update(context)

    def skip(self, reason: str, error_type: Optional[str] = None) -> None:
        self._outcome = ("skip", error_type, reason)

    def fail(self, error_type: str, message: str) -> None:
        self._outcome = ("fail", error_type, message)

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000

    def __exit__(self, exc_type, exc, tb) -> bool:
        ms = self.elapsed_ms
        if exc is not None:
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt, SystemExit)):
                log(self.stage, "skip", duration_ms=ms, error_message=f"cancelled ({type(exc).__name__})",
                    session_id=self.session_id, layer=self.layer, **self.context)
            else:
                log(self.stage, "fail", duration_ms=ms, error_type=classify(exc, self.api),
                    error_message=str(exc) or type(exc).__name__, session_id=self.session_id,
                    layer=self.layer, **_exc_context(exc), **self.context)
            return False
        if self._outcome is not None:
            event, et, msg = self._outcome
            log(self.stage, event, duration_ms=ms, error_type=et, error_message=msg,
                session_id=self.session_id, layer=self.layer, **self.context)
        else:
            log(self.stage, "success", duration_ms=ms, session_id=self.session_id, layer=self.layer, **self.context)
        return False


async def traced(stage: str, coro, *, api: str = "external_api", **context: Any):
    """Await `coro` inside a span. Used for fire-and-forget tasks so their
    failures reach the log (the exception is still re-raised)."""
    with span(stage, api=api, **context):
        return await coro


def run_in_context(fn, *args):
    """Return a zero-arg callable that runs fn(*args) in a copy of the current
    context. run_in_executor does not carry ContextVars into the pool thread."""
    ctx = contextvars.copy_context()
    return lambda: ctx.run(fn, *args)


# ---------------------------------------------------------------------------
# Per-frame summaries
# ---------------------------------------------------------------------------


class Summary:
    """For per-frame stages: collect durations and log one success line every
    `every` items (duration_ms = mean of the window; p50/p95/max in context)."""

    def __init__(self, stage: str, every: int = 100, **context: Any):
        self.stage = stage
        self.every = every
        self.context = context
        self._durations: List[float] = []
        self._extra: Dict[str, float] = {}
        self.total = 0

    def add(self, duration_ms: float, **counters: float) -> None:
        self._durations.append(duration_ms)
        for k, v in counters.items():
            self._extra[k] = self._extra.get(k, 0) + v
        self.total += 1
        if len(self._durations) >= self.every:
            self.flush()

    def flush(self) -> None:
        if not self._durations:
            return
        xs = sorted(self._durations)
        n = len(xs)
        log(self.stage, "success", duration_ms=statistics.fmean(xs), summary=True, count=n, total=self.total,
            p50_ms=round(xs[n // 2], 2), p95_ms=round(xs[min(n - 1, int(n * 0.95))], 2), max_ms=round(xs[-1], 2),
            **self._extra, **self.context)
        self._durations = []
        self._extra = {}


# ---------------------------------------------------------------------------
# Extension records (POST /obs)
# ---------------------------------------------------------------------------


def ingest_extension(records: Iterable[Any], header_session: Optional[str] = None) -> Tuple[int, int]:
    """Validate records sent by extension/obs.js and append them to this run's
    log with layer="extension". Returns (accepted, rejected)."""
    accepted = rejected = 0
    for r in records:
        if not isinstance(r, dict) or not isinstance(r.get("stage"), str) or r.get("event") not in EVENTS:
            rejected += 1
            continue
        ctx = r.get("context") if isinstance(r.get("context"), dict) else {}
        ctx = {str(k): redact(v) for k, v in list(ctx.items())[:40]}
        if r.get("run_id"):
            ctx["ext_instance"] = str(r["run_id"])[:64]
        dur = r.get("duration_ms")
        et = r.get("error_type")
        msg = r.get("error_message")
        _write({
            "ts": str(r.get("ts") or _now_iso())[:40],
            "run_id": RUN_ID,
            "session_id": str(r.get("session_id") or header_session or "")[:64] or None,
            "layer": "extension",
            "stage": r["stage"][:64],
            "event": r["event"],
            "duration_ms": round(float(dur), 1) if isinstance(dur, (int, float)) else None,
            "error_type": et if et in ERROR_TYPES else (None if et is None else "unknown"),
            "error_message": redact(str(msg))[:_MAX_MSG] if msg is not None else None,
            "context": ctx,
        })
        accepted += 1
    return accepted, rejected
