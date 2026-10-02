"""
Main FastAPI application for the subtitle translator backend.

Provides:
- /ws       WebSocket for translating detected subtitle cues
- /ws/asr   WebSocket for live captions (tab audio -> streaming ASR -> MT)
- HTTP endpoints for health, stats, metrics, config and a debug page
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import orjson

from .config import settings
from .models import HealthResponse, SubtitleCue, TranslationCorrection
from .translation import get_pipeline, warmup_pipeline
from .translation.pipeline import audio_session_policy
from .cache import get_translation_memory, get_translation_cache
from .websocket_handler import websocket_endpoint, manager, format_result, ignore_cloud_keys, save_correction
from .translation.cloud_translator import cloud_receivers
from . import asr as asr_pkg
from . import obs
from .security import OriginGuard, allowed_origins, origin_allowed

logging.basicConfig(
    level=logging.DEBUG if settings.server.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

_start_time: Optional[float] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.time()
    logger.info("Starting subtitle translator backend (device=%s)...", settings.translation.device)
    obs.log("startup", "start", component="backend", device=settings.translation.device, log_file=str(obs.log_path()))
    _log_cloud_receivers()
    _log_optional_deps()
    t0 = time.perf_counter()
    try:
        await warmup_pipeline()
    except Exception as e:
        logger.error("MT warmup failed: %s", e)
        obs.log_exc("startup", e, component="mt_warmup", degraded="server starts without MT warmup")
    try:
        await asyncio.get_running_loop().run_in_executor(None, asr_pkg.warmup_asr)
    except Exception as e:
        logger.error("ASR warmup failed: %s", e)
        obs.log_exc("startup", e, component="asr_warmup", degraded="server starts without ASR warmup")
    logger.info("Warmup complete")
    try:
        tm = await get_translation_memory()
        await tm.sweep(settings.tm.retention_days)  # D3: machine rows unused for retention_days
    except Exception as e:
        logger.error("TM retention sweep failed: %s", e)
        obs.log_exc("tm_retention", e, api="process", degraded="old rows kept until the next start")
    # Read the pipeline without building it: a log line must not retry (or fail) its init.
    from .translation import pipeline as _pipeline_mod

    built = _pipeline_mod._pipeline
    obs.log("startup", "success", duration_ms=(time.perf_counter() - t0) * 1000, component="backend",
            mt_engines=list(built.base_translator.engines.keys()) if built is not None else None)
    yield
    logger.info("Shutting down...")
    obs.log("shutdown", "start", component="backend", uptime_s=round(time.time() - _start_time, 1))
    tm = await get_translation_memory()
    await tm.close()


def _log_cloud_receivers() -> None:
    """Once per start (D4): which provider, if any, will receive subtitle text."""
    receivers = cloud_receivers()
    if receivers:
        logger.warning("Cloud is ON: subtitle text will be sent to %s",
                       "; ".join(f"{r['provider']} ({r['host']}) for {r['use']}" for r in receivers))
    else:
        logger.info("Cloud is off: no subtitle text leaves this machine")
    obs.log("startup", "success", component="cloud", enabled=settings.cloud.enabled, receivers=receivers)


def _log_optional_deps() -> None:
    """Record which optional modules import in this venv."""
    import importlib.util

    for mod in ("ctranslate2", "sherpa_onnx", "torch", "huggingface_hub"):
        try:
            found = importlib.util.find_spec(mod) is not None
        except Exception:
            found = False
        if found:
            obs.log("dependency", "success", module=mod)
        else:
            obs.log("dependency", "skip", error_type="process", error_message=f"module {mod!r} not installed", module=mod)


class ObsMiddleware:
    """Sets the session ContextVar from X-Session-Id and logs every HTTP request
    as stage 'http' (except POST /obs, which logs its own rejects)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        sid = None
        for k, v in scope.get("headers") or []:
            if k == b"x-session-id":
                sid = v.decode("latin-1")[:64]
                break
        token = obs.session_id_var.set(sid)
        status: Dict[str, int] = {}

        async def send_wrap(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        path, method = scope.get("path", ""), scope.get("method", "")
        t0 = time.perf_counter()
        try:
            try:
                await self.app(scope, receive, send_wrap)
            except Exception as e:
                obs.log_exc("http", e, duration_ms=(time.perf_counter() - t0) * 1000, method=method, path=path)
                raise
            if path == "/obs":
                return
            code = status.get("code", 0)
            ms = (time.perf_counter() - t0) * 1000
            if code >= 400:
                obs.log("http", "fail", duration_ms=ms, error_type="input_invalid" if code < 500 else "unknown",
                        error_message=f"HTTP {code}", method=method, path=path, status=code)
            else:
                obs.log("http", "success", duration_ms=ms, method=method, path=path, status=code)
        finally:
            # reset only after the line above is written, so it carries the session
            obs.session_id_var.reset(token)


app = FastAPI(title="Subtitle Translator", description="Real-time local subtitle transcription and translation", version="2.0.0", lifespan=lifespan)
class _AllowedOrigins:
    """CORSMiddleware calls `origin in allow_origins`; answer from security.allowed_origins()
    so a --port override or a config change is honoured without rebuilding the app."""

    def __contains__(self, origin) -> bool:
        return isinstance(origin, str) and origin_allowed(origin)

    def __iter__(self):
        return iter(sorted(allowed_origins()))


# Order (outermost first): ObsMiddleware -> OriginGuard -> CORS -> app. The guard
# refuses foreign origins (403, WebSockets before the upgrade); CORS then only
# ever echoes an allowed origin, never "*".
app.add_middleware(CORSMiddleware, allow_origins=_AllowedOrigins(), allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "X-Session-Id"])
app.add_middleware(OriginGuard)
app.add_middleware(ObsMiddleware)


# ============================================================================
# WebSocket: subtitle cues
# ============================================================================

@app.websocket("/ws")
async def ws_translate(websocket: WebSocket):
    await websocket_endpoint(websocket)


# ============================================================================
# WebSocket: live captions from tab audio
# ============================================================================

def _parse_query_list(value: Optional[str], default: List[str]) -> List[str]:
    if not value:
        return list(default)
    return [v.strip() for v in value.split(",") if v.strip()]


@app.websocket("/ws/asr")
async def ws_asr(websocket: WebSocket):
    """
    Protocol
    --------
    Client -> server:
      binary frames : 16 kHz mono int16 PCM. Optionally prefixed by an 8-byte
                      little-endian float64 capture timestamp (seconds, from the
                      extension's audio clock) when the frame length is odd*2+8;
                      the simple form is raw PCM only.
      text (JSON)   : {type:'config', source_lang, target_langs, engine}   (cloud_keys: ignored, D4)
                      {type:'stop'}   -> flush pending audio, keep connection
                      {type:'ping'}
    Server -> client (JSON):
      {type:'ready', status}
      {type:'lid', lang, source}
      {type:'partial', utterance_id, text, lang, t0, t1}
      {type:'final',   utterance_id, text, lang, t0, t1}
      {type:'translation', utterance_id, cue_id, revision, translations{lang:{lines,single_line,display_text}}, mt_ms}
      {type:'revision', ...same as translation with revision 2}
      {type:'error', error}
      {type:'pong'}
    """
    sid = (websocket.query_params.get("session_id") or "")[:64] or obs.new_session_id()
    obs.session_id_var.set(sid)  # every task created below inherits it
    conn_t0 = time.perf_counter()
    await websocket.accept()
    engines = asr_pkg.get_engines()
    if not engines:
        obs.log("ws_connection", "fail", error_type="process", error_message="no ASR engine available; closing 4000", path="/ws/asr")
        await websocket.send_text(orjson.dumps({"type": "error", "error": "No ASR engine available. Install sherpa-onnx (pip install sherpa-onnx)."}).decode())
        await websocket.close(code=4000)
        return

    q = websocket.query_params
    source_lang = q.get("source_lang", "auto")
    target_langs = _parse_query_list(q.get("target_langs"), settings.translation.target_languages)
    engine = q.get("engine", "auto")

    try:
        session = asr_pkg.create_session(source_lang=source_lang, target_langs=target_langs, engine=engine)
    except Exception as e:
        obs.log_exc("ws_connection", e, path="/ws/asr", source_lang=source_lang, phase="create_session")
        raise
    pipeline = await get_pipeline()
    # D3: this session's translations live in its own cache and die with it; the
    # persistent TM is read (corrections) but not written unless configured
    memory = audio_session_policy()
    logger.info("ASR WebSocket connected (source=%s targets=%s engine=%s)", source_lang, target_langs, engine)
    obs.log("ws_connection", "start", path="/ws/asr", source_lang=source_lang, target_langs=target_langs, engine=engine,
            session_id_from="client" if q.get("session_id") else "backend")
    recv_summary = obs.Summary("ws_receive", path="/ws/asr")
    asr_summary = obs.Summary("asr_chunk")

    send_lock = asyncio.Lock()

    async def send(msg: Dict[str, Any]) -> None:
        mtype = msg.get("type")
        try:
            async with send_lock:
                await websocket.send_text(orjson.dumps(msg).decode("utf-8"))
        except Exception as e:
            obs.log_exc("ws_reply", e, api="process", path="/ws/asr", msg_type=mtype, utterance_id=msg.get("utterance_id"))
            raise
        if mtype in ("final", "translation", "revision", "error", "ready", "reset", "lid"):
            obs.log("ws_reply", "success", path="/ws/asr", msg_type=mtype, utterance_id=msg.get("utterance_id"),
                    targets=list((msg.get("translations") or {}).keys()) or None)

    await send({"type": "ready", "status": {**session.status(), "engines": list(engines.keys())}})
    for m in session.startup_messages():
        await send(m)

    async def translate_final(msg: Dict[str, Any]) -> None:
        """Translate a final caption and push each target language as soon as it
        is ready (never blocks the audio loop). The extension merges per-cue."""
        text, lang = msg["text"], msg["lang"]
        t0 = time.time()
        targets = [t for t in session.config.target_langs if t != lang]
        if not targets:
            # nothing to translate: still tell the UI the caption is complete
            await send({"type": "translation", "utterance_id": msg["utterance_id"], "cue_id": "asr_%s" % msg["utterance_id"],
                        "revision": 1, "source_text": text, "source_lang": lang, "translations": {}, "mt_ms": 0, "server_ts": time.time()})
            return
        results: List[Any] = []

        async def one(target: str) -> None:
            cue = SubtitleCue(text=text, start_time=msg["t0"], end_time=msg["t1"], source_lang=lang)
            try:
                result = await pipeline.translate_cue(cue, [target], skip_post_edit=True, policy=memory)
            except Exception as e:
                obs.log_exc("translate", e, path="/ws/asr", utterance_id=msg["utterance_id"], target=target,
                            degraded="error message sent instead of a translation")
                await send({"type": "error", "error": f"translation failed ({target}): {e}"})
                return
            results.append(result)
            payload = format_result(result)
            await send({
                "type": "translation",
                "utterance_id": msg["utterance_id"],
                "cue_id": result.cue_id,
                "revision": 1,
                "source_text": text,
                "source_lang": lang,
                "translations": {target: payload["translations"][target]} if target in payload["translations"] else payload["translations"],
                "targets_pending": max(0, len(targets) - len(results)),
                "mt_ms": round((time.time() - t0) * 1000),
                "capture_ts": msg.get("capture_ts"),
                "server_ts": time.time(),
            })

        await asyncio.gather(*(one(t) for t in targets))

        if results and pipeline.refiner_enabled(targets):
            try:
                merged = results[0]
                for r in results[1:]:
                    merged.translations.update(r.translations)
                changed = await pipeline.refine_batch([merged], targets, policy=memory)
                for r in changed:
                    p = format_result(r)
                    await send({
                        "type": "revision", "utterance_id": msg["utterance_id"], "cue_id": r.cue_id, "revision": 2,
                        "source_text": text, "source_lang": lang, "translations": p["translations"], "server_ts": time.time(),
                    })
            except Exception as e:  # pragma: no cover
                logger.debug("refine failed: %s", e)
                obs.log_exc("refine", e, path="/ws/asr", utterance_id=msg["utterance_id"],
                            degraded="refine error logged at DEBUG only; fast translation stays")

    def _handle_events(msgs: List[Dict[str, Any]]) -> List[asyncio.Task]:
        tasks = []
        for m in msgs:
            if m.get("type") == "final":
                obs.log("segment", "success" if m.get("text") else "skip", utterance_id=m.get("utterance_id"),
                        lang=m.get("lang"), audio_s=round((m.get("t1") or 0) - (m.get("t0") or 0), 2),
                        text_len=len(m.get("text") or ""), confirmed=m.get("confirmed"))
            tasks.append(asyncio.create_task(send(m)))
            if m.get("type") == "final" and m.get("text"):
                tasks.append(asyncio.create_task(translate_final(m)))
        return tasks

    loop = asyncio.get_running_loop()
    close_code: Optional[int] = None
    try:
        while True:
            data = await websocket.receive()
            if data.get("type") == "websocket.disconnect":
                close_code = data.get("code")
                break
            if data.get("bytes") is not None:
                raw: bytes = data["bytes"]
                capture_ts = None
                if not raw:
                    obs.log("ws_receive", "skip", error_type="input_invalid", error_message="empty binary frame", path="/ws/asr")
                elif len(raw) % 2:
                    obs.log("asr_chunk", "fail", error_type="input_invalid", path="/ws/asr", bytes=len(raw),
                            error_message="odd byte count; last byte dropped by pcm16_to_float32, frame still decoded")
                # feed() is CPU work (~10 ms); run in the default executor so
                # sends/translations interleave.
                f0 = time.perf_counter()
                try:
                    msgs = await loop.run_in_executor(None, obs.run_in_context(session.feed, raw, capture_ts))
                except Exception as e:
                    obs.log_exc("asr_chunk", e, api="process", duration_ms=(time.perf_counter() - f0) * 1000,
                                bytes=len(raw), lang=session.lang)
                    raise
                feed_ms = (time.perf_counter() - f0) * 1000
                recv_summary.add(feed_ms, bytes=len(raw))
                asr_summary.add(feed_ms, events=len(msgs) if msgs else 0)
                if msgs:
                    _handle_events(msgs)
            elif data.get("text") is not None:
                try:
                    msg = orjson.loads(data["text"])
                except Exception as e:
                    obs.log_exc("ws_receive", e, path="/ws/asr", kind="text", error_type="parse")
                    await send({"type": "error", "error": "invalid JSON"})
                    continue
                mtype = msg.get("type")
                if mtype in ("config", "stop", "ping"):
                    obs.log("ws_receive", "success", path="/ws/asr", kind="text", msg_type=mtype,
                            keys=sorted(k for k in msg.keys() if k != "cloud_keys") + (["cloud_keys"] if "cloud_keys" in msg else []))
                else:
                    obs.log("ws_receive", "skip", error_type="input_invalid", path="/ws/asr", kind="text", msg_type=str(mtype)[:32],
                            error_message="unknown message type ignored without reply")
                if mtype == "config":
                    if "target_langs" in msg and msg["target_langs"]:
                        session.config.target_langs = list(msg["target_langs"])
                    if "cloud_keys" in msg:
                        ignore_cloud_keys(msg, path="/ws/asr")
                    if "source_lang" in msg and msg["source_lang"]:
                        for m in session.set_language(msg["source_lang"]):
                            _handle_events([m])
                    await send({"type": "config_updated", "status": session.status()})
                elif mtype == "stop":
                    _handle_events(session.flush())
                    await send({"type": "stopped", "status": session.status()})
                elif mtype == "ping":
                    await send({"type": "pong", "server_ts": time.time()})
    except WebSocketDisconnect as e:
        logger.info("ASR WebSocket disconnected")
        close_code = e.code
    except Exception as e:
        logger.error("ASR WebSocket error: %s", e, exc_info=settings.server.debug)
        obs.log_exc("ws_connection", e, path="/ws/asr", duration_ms=(time.perf_counter() - conn_t0) * 1000,
                    degraded="connection loop ended by exception")
    finally:
        session.close()
        if memory.cache is not None:
            memory.cache.clear()
        logger.info("ASR WebSocket closed: %s", session.status())
        recv_summary.flush()
        asr_summary.flush()
        st = session.status()
        if close_code is not None:
            normal = close_code in (1000, 1001, 1005)
            obs.log("ws_connection", "success" if normal else "fail", duration_ms=(time.perf_counter() - conn_t0) * 1000,
                    error_type=None if normal else "process",
                    error_message=None if normal else f"client closed with code {close_code}",
                    path="/ws/asr", close_code=close_code, frames=asr_summary.total, **st)


# ============================================================================
# HTTP endpoints
# ============================================================================

HEALTH_HTML = """<!DOCTYPE html><html><head><title>Subtitle Translator - Health</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:820px;margin:0 auto;padding:40px 20px;background:linear-gradient(135deg,#1a1a2e,#16213e);color:#eee;min-height:100vh}}
.c{{background:rgba(255,255,255,.05);border-radius:16px;padding:40px;box-shadow:0 8px 32px rgba(0,0,0,.3)}}
h1{{color:#00d4ff;margin-bottom:10px}} .sub{{color:#888;margin-bottom:30px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin:24px 0}}
.s{{background:rgba(0,212,255,.1);padding:16px;border-radius:12px;text-align:center}}
.v{{font-size:1.6em;font-weight:bold;color:#00d4ff}} .l{{font-size:.85em;color:#888;margin-top:4px}}
a{{color:#00d4ff}} .links a{{display:inline-block;margin-right:16px;padding:10px 18px;background:#00d4ff;color:#1a1a2e;text-decoration:none;border-radius:8px;font-weight:bold}}
code{{background:rgba(0,0,0,.3);padding:2px 6px;border-radius:4px}}
</style></head><body><div class="c">
<h1>Subtitle Translator</h1><p class="sub">Live captions and translation, local by default</p>
<div class="stats">
<div class="s"><div class="v">{uptime}</div><div class="l">Uptime</div></div>
<div class="s"><div class="v">{device}</div><div class="l">Device</div></div>
<div class="s"><div class="v">{mt}</div><div class="l">MT engines</div></div>
<div class="s"><div class="v">{asr}</div><div class="l">ASR languages</div></div>
<div class="s"><div class="v">{cache_entries}</div><div class="l">Cache entries</div></div>
</div>
<h3>Next steps</h3><ol>
<li>Load the <code>extension</code> folder at <code>chrome://extensions</code> (Developer mode &rarr; Load unpacked).</li>
<li>Open a video. With subtitles: translations appear automatically. Without: click the extension icon &rarr; <b>Live captions</b>.</li>
</ol>
<div class="links"><a href="/debug">Debug console</a><a href="/health/json">Health JSON</a><a href="/metrics">Metrics</a><a href="/config">Config</a></div>
</div></body></html>"""


def _uptime_str() -> str:
    secs = time.time() - _start_time if _start_time else 0
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs / 60)}m"
    return f"{int(secs / 3600)}h"


@app.get("/health")
async def health_check():
    pipeline = await get_pipeline()
    cache = get_translation_cache()
    status = asr_pkg.asr_status()
    html = HEALTH_HTML.format(
        uptime=_uptime_str(),
        device=settings.translation.device,
        mt=", ".join(pipeline.base_translator.engines.keys()) or "none",
        asr=", ".join(status.get("languages", [])) if status.get("available") else "n/a",
        cache_entries=cache.stats["size"],
    )
    return HTMLResponse(content=html)


@app.get("/health/json", response_model=HealthResponse)
async def health_check_json():
    pipeline = await get_pipeline()
    cache = get_translation_cache()
    ollama_available = False
    if settings.refiner.enabled and settings.refiner.provider == "ollama":
        try:
            import httpx

            async with httpx.AsyncClient(timeout=1.0) as c:
                ollama_available = (await c.get(f"{settings.ollama.base_url}/api/tags")).status_code == 200
        except Exception as e:
            obs.log_exc("health_probe", e, target="ollama", degraded="reported as ollama_available=false")
            pass
    return HealthResponse(
        status="ok",
        models_loaded=bool(pipeline.base_translator.engines),
        ollama_available=ollama_available,
        cache_entries=cache.stats["size"],
        uptime_seconds=time.time() - _start_time if _start_time else 0,
        device=settings.translation.device,
        mt_engines=pipeline.base_translator.status(),
        asr=asr_pkg.asr_status(),
        refiner_enabled=settings.refiner.enabled,
        privacy=privacy_summary(),
        run_id=obs.RUN_ID,
    )


def privacy_summary() -> Dict[str, Any]:
    """What the backend keeps (D3) and what leaves the machine (D4)."""
    return {
        "tm": {
            "persist_audio_sessions": settings.tm.persist_audio_sessions,
            "persist_captions": settings.tm.persist_captions,
            "retention_days": settings.tm.retention_days,
        },
        "cloud": {"enabled": settings.cloud.enabled, "receivers": cloud_receivers()},
    }


class TranslateRequest(BaseModel):
    text: str
    source_lang: Optional[str] = None
    lang_hint: Optional[str] = None  # language to assume for short/ambiguous text (settings.lang_detect)
    target_languages: Optional[List[str]] = None
    skip_post_edit: bool = True


class TranslateResponse(BaseModel):
    source_text: str
    source_lang: str
    # per target: lines, single_line, status (ok | fallback | untranslated | error), engine, error_type, error
    translations: Dict[str, Dict[str, Any]]
    processing_time_ms: float
    notes: Optional[Dict[str, Any]] = None
    degraded: bool = False  # true when any target is not status "ok"


@app.post("/translate", response_model=TranslateResponse)
async def translate_http(request: TranslateRequest):
    pipeline = await get_pipeline()
    cue = SubtitleCue(text=request.text, start_time=0.0, end_time=5.0, source_lang=request.source_lang,
                      lang_hint=request.lang_hint)
    result = await pipeline.translate_cue(cue, target_languages=request.target_languages, skip_post_edit=request.skip_post_edit)
    payload = format_result(result)
    return TranslateResponse(
        source_text=result.source_text,
        source_lang=result.source_lang,
        translations={lang: {k: v for k, v in t.items() if k != "display_text"} for lang, t in payload["translations"].items()},
        processing_time_ms=result.processing_time_ms,
        notes=result.notes,
        degraded=payload["degraded"],
    )


@app.get("/stats")
async def get_stats():
    pipeline = await get_pipeline()
    cache = get_translation_cache()
    tm = await get_translation_memory()
    s = pipeline.stats
    return {
        "pipeline": {
            "total_requests": s.total_requests, "cache_hits": s.cache_hits, "tm_hits": s.tm_hits,
            "translations": s.translations, "refinements": s.refinements, "errors": s.errors,
            "avg_time_ms": s.avg_time_ms, "p50_ms": s.percentile(50), "p95_ms": s.percentile(95),
        },
        "memory_cache": cache.stats,
        "translation_memory": await tm.get_stats(),
        "connections": manager.connection_count,
        "mt_engines": pipeline.base_translator.status(),
        "asr": asr_pkg.asr_status(),
    }


@app.get("/metrics")
async def get_metrics():
    pipeline = await get_pipeline()
    cache = get_translation_cache()
    s = pipeline.stats
    total = s.total_requests or 1
    return {
        "total_requests": s.total_requests,
        "avg_latency_ms": s.avg_time_ms,
        "p50_latency_ms": s.percentile(50),
        "p95_latency_ms": s.percentile(95),
        "p99_latency_ms": s.percentile(99),
        "cache_hit_rate_pct": round((s.cache_hits + s.tm_hits) / total * 100, 1),
        "refinement_rate_pct": round(s.refinements / total * 100, 1),
        "errors": s.errors,
        "batch_window_ms": settings.features.batch_window_ms,
        "memory_cache": {"size": cache.stats["size"], "max_size": cache.stats.get("max_size", settings.cache.memory_cache_size)},
        "connections": manager.connection_count,
        "mt_engines": pipeline.base_translator.status(),
    }


@app.get("/config")
async def get_config():
    pipeline = await get_pipeline()
    return {
        "target_languages": settings.translation.target_languages,
        "supported_languages": settings.translation.supported_languages,
        "asr_languages": settings.asr.languages,
        "max_lines": settings.translation.max_lines,
        "max_chars_by_lang": settings.translation.max_chars_by_lang,
        "refiner": {"enabled": settings.refiner.enabled, "provider": settings.refiner.provider, "deadline_s": settings.refiner.deadline_s},
        "device": settings.translation.device,
        "mt_engine_order": settings.mt.engine_order,
        "mt_engines": list(pipeline.base_translator.engines.keys()),
        "batch_window_ms": settings.features.batch_window_ms,
    }


@app.post("/config")
async def update_config(updates: Dict[str, Any]):
    if "target_languages" in updates:
        settings.translation.target_languages = list(updates["target_languages"])
    if "refiner_enabled" in updates:
        settings.refiner.enabled = bool(updates["refiner_enabled"])
    if "refiner_provider" in updates:
        settings.refiner.provider = str(updates["refiner_provider"])
    if "batch_window_ms" in updates:
        settings.features.batch_window_ms = int(updates["batch_window_ms"])
    if "cloud_keys" in updates:
        ignore_cloud_keys(updates, path="/config")
    return await get_config()


@app.post("/corrections")
async def post_correction(correction: TranslationCorrection):
    """A user correction (Alt+E) from a tab that has no caption-path /ws socket, i.e. a
    tab running only live captions; the extension's service worker posts it. Same
    storage as a /ws correction; running live sessions see it on their next sentence."""
    try:
        return await save_correction(correction, settings.lang_detect.default_hint, path="/corrections",
                                     pipeline=await get_pipeline())
    except Exception as e:
        obs.log_exc("tm_store", e, api="process", kind="correction", path="/corrections",
                    degraded="error sent to the extension")
        return JSONResponse(status_code=500, content={"status": "error", "error": f"correction not saved: {e}",
                                                      "error_type": obs.classify(e, "process")})


@app.post("/cache/clear")
async def clear_cache():
    get_translation_cache().clear()
    (await get_pipeline()).clear_context()
    return {"status": "cleared"}


@app.post("/tm/clear")
async def clear_translation_memory():
    """Delete everything the translation memory holds (D3): machine translations,
    corrections, glossary; the file is rebuilt so no deleted text stays on disk.
    Also empties the in-memory cache and the refiner context."""
    tm = await get_translation_memory()
    t0 = time.perf_counter()
    try:
        removed = await tm.clear()
    except Exception as e:
        obs.log_exc("tm_clear", e, api="process")
        return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})
    get_translation_cache().clear()
    (await get_pipeline()).clear_context()
    obs.log("tm_clear", "success", duration_ms=(time.perf_counter() - t0) * 1000, **removed)
    return {"status": "cleared", "removed": removed}


@app.get("/asr/status")
async def get_asr_status():
    return asr_pkg.asr_status()


@app.post("/obs")
async def ingest_obs(request: Request):
    """Extension log records (extension/obs.js). Body: {events:[...]} or [...].
    Fire-and-forget for the client; appended to this run's log with layer='extension'."""
    header_sid = request.headers.get("x-session-id")
    try:
        body = orjson.loads(await request.body())
    except Exception as e:
        obs.log_exc("obs_ingest", e, error_type="parse")
        return JSONResponse(status_code=400, content={"accepted": 0, "error": "invalid JSON"})
    events = body.get("events") if isinstance(body, dict) else body
    if not isinstance(events, list):
        obs.log("obs_ingest", "fail", error_type="input_invalid", error_message="body has no events list")
        return JSONResponse(status_code=400, content={"accepted": 0, "error": "expected {events:[...]}"})
    accepted, rejected = obs.ingest_extension(events[:1000], header_sid)
    if rejected:
        obs.log("obs_ingest", "fail", error_type="input_invalid", error_message=f"{rejected} malformed record(s) dropped",
                accepted=accepted, rejected=rejected)
    return {"accepted": accepted, "rejected": rejected}


@app.get("/tm/export")
async def export_corrections(format: str = Query("jsonl")):
    tm = await get_translation_memory()
    export_path = settings.data_dir / "corrections_export.jsonl"
    count = await tm.export_corrections(export_path)
    if count == 0:
        return {"status": "empty", "count": 0}
    with open(export_path, "r", encoding="utf-8") as f:
        content = f.read()
    return JSONResponse(content={"count": count, "data": content})


# ============================================================================
# Debug UI
# ============================================================================

DEBUG_HTML = """<!DOCTYPE html><html><head><title>Subtitle Translator - Debug</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:20px;background:#1a1a2e;color:#eee}
h1{color:#00d4ff}.sec{background:#16213e;padding:20px;margin:20px 0;border-radius:8px}
.sec h2{margin-top:0;color:#00d4ff;border-bottom:1px solid #0f3460;padding-bottom:10px}
textarea,select,input{background:#0f3460;color:#eee;border:1px solid #00d4ff;border-radius:4px;padding:8px;font-family:inherit}
textarea{width:100%;height:80px;margin:10px 0}
button{background:#00d4ff;color:#1a1a2e;border:none;padding:10px 18px;cursor:pointer;border-radius:4px;font-weight:bold;margin-right:8px}
#result,#asr{white-space:pre-wrap;background:#0f3460;padding:15px;border-radius:4px;font-family:monospace;max-height:320px;overflow-y:auto}
.stat{display:inline-block;margin:8px 20px 8px 0;background:#0f3460;padding:10px 14px;border-radius:4px}
.v{font-size:22px;font-weight:bold;color:#00d4ff}.l{font-size:12px;color:#888}
#status{padding:10px;border-radius:4px;margin-bottom:20px}.ok{background:#0a4d0a}.bad{background:#4d0a0a}
</style></head><body>
<h1>Subtitle Translator Debug Console</h1>
<div id="status" class="bad">WebSocket: disconnected</div>
<div class="sec"><h2>Statistics</h2><div id="stats">Loading...</div></div>
<div class="sec"><h2>Test translation</h2>
<label>Source <select id="src"><option value="">auto</option><option>en</option><option>zh</option><option>bn</option></select></label>
<label>Targets <input id="tgt" value="en,zh,bn" size="12"></label>
<textarea id="input">Where did you put the keys?</textarea><br>
<button onclick="translateHttp()">Translate (HTTP)</button><button onclick="translateWs()">Translate (WebSocket)</button>
<h3>Result</h3><div id="result">-</div></div>
<div class="sec"><h2>Live captions (microphone test)</h2>
<label>Source <select id="asrSrc"><option value="auto">auto</option><option>en</option><option>zh</option><option>bn</option></select></label>
<button onclick="startAsr()">Start mic</button><button onclick="stopAsr()">Stop</button>
<div id="asr">-</div></div>
<div class="sec"><h2>Configuration</h2><div id="config">Loading...</div></div>
<script>
let ws=null,cid=0;
function connectWs(){ws=new WebSocket('ws://'+location.host+'/ws');
 ws.onopen=()=>{const s=document.getElementById('status');s.textContent='WebSocket: connected';s.className='ok';};
 ws.onclose=()=>{const s=document.getElementById('status');s.textContent='WebSocket: disconnected';s.className='bad';setTimeout(connectWs,3000);};
 ws.onmessage=e=>{const d=JSON.parse(e.data);document.getElementById('result').textContent=JSON.stringify(d.payload,null,2)+(d.type==='revision'?'\\n[revision 2]':'');};}
connectWs();
function targets(){return document.getElementById('tgt').value.split(',').map(s=>s.trim()).filter(Boolean);}
async function translateHttp(){const text=document.getElementById('input').value,src=document.getElementById('src').value||null;const t=performance.now();
 try{const r=await fetch('/translate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,source_lang:src,target_languages:targets()})});
 const d=await r.json();document.getElementById('result').textContent=JSON.stringify(d,null,2)+'\\nround-trip '+Math.round(performance.now()-t)+' ms';}catch(e){document.getElementById('result').textContent='Error: '+e;}}
function translateWs(){if(!ws||ws.readyState!==1)return;cid++;const src=document.getElementById('src').value||undefined;
 ws.send(JSON.stringify({type:'cue',correlation_id:'debug-'+cid,payload:{cue:{text:document.getElementById('input').value,start_time:0,end_time:5,source_lang:src},target_languages:targets()}}));}
let asrWs=null,ctx=null,node=null,stream=null;
async function startAsr(){const src=document.getElementById('asrSrc').value;const out=document.getElementById('asr');out.textContent='connecting...';
 asrWs=new WebSocket('ws://'+location.host+'/ws/asr?source_lang='+src+'&target_langs='+targets().join(','));asrWs.binaryType='arraybuffer';
 asrWs.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='partial'){out.dataset.partial='['+m.lang+' partial] '+m.text;}
  else if(m.type==='final'){out.dataset.log=(out.dataset.log||'')+'\\n[final] '+m.text;out.dataset.partial='';}
  else if(m.type==='translation'||m.type==='revision'){out.dataset.log+=' -> '+Object.entries(m.translations).map(([l,t])=>l+': '+t.single_line).join(' | ')+' ('+m.mt_ms+' ms'+(m.revision===2?', refined':'')+')';}
  else{out.dataset.log=(out.dataset.log||'')+'\\n'+JSON.stringify(m);}
  out.textContent=(out.dataset.log||'')+'\\n'+(out.dataset.partial||'');};
 stream=await navigator.mediaDevices.getUserMedia({audio:true});ctx=new AudioContext({sampleRate:16000});
 const srcNode=ctx.createMediaStreamSource(stream);node=ctx.createScriptProcessor(1024,1,1);
 node.onaudioprocess=ev=>{if(!asrWs||asrWs.readyState!==1)return;const f=ev.inputBuffer.getChannelData(0);const i16=new Int16Array(f.length);for(let i=0;i<f.length;i++){const s=Math.max(-1,Math.min(1,f[i]));i16[i]=s<0?s*32768:s*32767;}asrWs.send(i16.buffer);};
 srcNode.connect(node);node.connect(ctx.destination);}
function stopAsr(){if(asrWs){asrWs.send(JSON.stringify({type:'stop'}));setTimeout(()=>asrWs&&asrWs.close(),500);}if(node)node.disconnect();if(ctx)ctx.close();if(stream)stream.getTracks().forEach(t=>t.stop());}
async function loadStats(){try{const d=await (await fetch('/stats')).json();const p=d.pipeline;
 document.getElementById('stats').innerHTML=['total_requests','cache_hits','tm_hits','refinements'].map(k=>'<div class="stat"><div class="v">'+p[k]+'</div><div class="l">'+k+'</div></div>').join('')+
 '<div class="stat"><div class="v">'+p.p50_ms.toFixed(0)+' / '+p.p95_ms.toFixed(0)+' ms</div><div class="l">p50 / p95</div></div><div class="stat"><div class="v">'+d.connections+'</div><div class="l">connections</div></div>';}catch(e){}}
async function loadConfig(){try{const d=await (await fetch('/config')).json();document.getElementById('config').innerHTML='<pre>'+JSON.stringify(d,null,2)+'</pre>';}catch(e){}}
loadStats();loadConfig();setInterval(loadStats,5000);
</script></body></html>"""


@app.get("/debug", response_class=HTMLResponse)
async def debug_ui():
    return DEBUG_HTML


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"error": str(exc)})
