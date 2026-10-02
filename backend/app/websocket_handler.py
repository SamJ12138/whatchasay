"""
WebSocket handler for the browser extension (/ws).

Handles:
- Connection management
- cue / batch / correction / config / ping messages
- Micro-batching of cues (30 ms window) for one model call
- Fast result delivery (revision 1) followed by an optional pushed
  'revision' message (revision 2) when the async refiner improves a line
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
import orjson

from .config import settings
from .models import MessageType, SubtitleCue, ConfigUpdate, TranslationCorrection, TranslationResult
from .translation import get_pipeline, TranslationPipeline
from .cache import get_translation_memory

logger = logging.getLogger(__name__)


def format_result(result: TranslationResult) -> Dict[str, Any]:
    """Wire format for a TranslationResult (shared by every sender)."""
    translations = {}
    for lang, trans in result.translations.items():
        translations[lang] = {
            "lines": trans.lines,
            "single_line": trans.single_line,
            "display_text": trans.display_text,
        }
    return {
        "cue_id": result.cue_id,
        "source_text": result.source_text,
        "source_lang": result.source_lang,
        "translations": translations,
        "from_cache": result.from_cache,
        "post_edited": result.post_edited,
        "revision": result.revision,
        "processing_time_ms": result.processing_time_ms,
        "notes": result.notes,
    }


def _envelope(msg_type: MessageType, correlation_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": msg_type.value,
        "correlation_id": correlation_id,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.connection_info: Dict[str, Dict[str, Any]] = {}

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        conn_id = str(uuid.uuid4())[:8]
        self.active_connections[conn_id] = websocket
        self.connection_info[conn_id] = {
            "connected_at": datetime.utcnow(),
            "messages_received": 0,
            "messages_sent": 0,
            "last_activity": datetime.utcnow(),
            # per-connection preferences (never mutate global settings for these)
            "target_languages": None,
        }
        logger.info("Client connected: %s", conn_id)
        return conn_id

    def disconnect(self, conn_id: str) -> None:
        self.active_connections.pop(conn_id, None)
        self.connection_info.pop(conn_id, None)
        logger.info("Client disconnected: %s", conn_id)

    async def send_message(self, conn_id: str, message: Dict[str, Any]) -> bool:
        websocket = self.active_connections.get(conn_id)
        if websocket is None:
            return False
        try:
            await websocket.send_text(orjson.dumps(message).decode("utf-8"))
            info = self.connection_info.get(conn_id)
            if info:
                info["messages_sent"] += 1
                info["last_activity"] = datetime.utcnow()
            return True
        except Exception as e:
            logger.error("Error sending to %s: %s", conn_id, e)
            return False

    async def broadcast(self, message: Dict[str, Any]) -> int:
        sent = 0
        for conn_id in list(self.active_connections.keys()):
            if await self.send_message(conn_id, message):
                sent += 1
        return sent

    @property
    def connection_count(self) -> int:
        return len(self.active_connections)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Delivery: fast result now, refined revision later
# ---------------------------------------------------------------------------

_inflight_refinements: Set[str] = set()


async def deliver_results(
    pipeline: TranslationPipeline,
    pairs: List[tuple],  # (conn_id, correlation_id, TranslationResult)
    target_languages: List[str],
    skip_refine: bool = False,
) -> None:
    for conn_id, correlation_id, result in pairs:
        await manager.send_message(conn_id, _envelope(MessageType.RESULT, correlation_id, format_result(result)))

    if skip_refine or not pipeline.refiner_enabled(target_languages):
        return
    candidates = [(c, k, r) for (c, k, r) in pairs if not r.from_cache and r.cue_id not in _inflight_refinements and not (r.notes or {}).get("error")]
    if not candidates:
        return
    for _, _, r in candidates:
        _inflight_refinements.add(r.cue_id)

    async def _refine():
        try:
            changed = await pipeline.refine_batch([r for _, _, r in candidates], target_languages)
            by_id = {r.cue_id: r for r in changed}
            for conn_id, correlation_id, r in candidates:
                new_r = by_id.get(r.cue_id)
                if new_r is not None:
                    await manager.send_message(conn_id, _envelope(MessageType.REVISION, correlation_id, format_result(new_r)))
        except Exception as e:  # pragma: no cover
            logger.warning("Refinement task failed: %s", e)
        finally:
            for _, _, r in candidates:
                _inflight_refinements.discard(r.cue_id)

    asyncio.create_task(_refine())


# ---------------------------------------------------------------------------
# Micro-batcher
# ---------------------------------------------------------------------------


@dataclass
class PendingCue:
    cue: SubtitleCue
    correlation_id: str
    conn_id: str
    received_at: float
    target_languages: Optional[List[str]] = None
    skip_post_edit: bool = False


class MicroBatcher:
    """Collect cues for a short window, then translate them in one call."""

    def __init__(self, pipeline: TranslationPipeline):
        self.pipeline = pipeline
        self.pending: deque = deque()
        self._lock = asyncio.Lock()
        self._batch_task: Optional[asyncio.Task] = None

    async def add_cue(self, pending: PendingCue) -> None:
        async with self._lock:
            self.pending.append(pending)
            if self._batch_task is None or self._batch_task.done():
                self._batch_task = asyncio.create_task(self._process_after_delay())

    async def _process_after_delay(self) -> None:
        await asyncio.sleep(settings.features.batch_window_ms / 1000.0)
        while True:
            async with self._lock:
                if not self.pending:
                    return
                # group by target-language set so one batch shares targets
                first = self.pending[0]
                key = tuple(first.target_languages or settings.translation.target_languages)
                batch: List[PendingCue] = []
                rest: deque = deque()
                while self.pending and len(batch) < settings.features.max_batch_size:
                    p = self.pending.popleft()
                    if tuple(p.target_languages or settings.translation.target_languages) == key:
                        batch.append(p)
                    else:
                        rest.append(p)
                self.pending.extendleft(reversed(rest))
            await self._process_batch(batch, list(key))

    async def _process_batch(self, batch: List[PendingCue], target_languages: List[str]) -> None:
        start = time.time()
        skip_refine = any(p.skip_post_edit for p in batch)
        try:
            results = await self.pipeline.translate_batch([p.cue for p in batch], target_languages, skip_refine)
            pairs = [(p.conn_id, p.correlation_id, r) for p, r in zip(batch, results)]
            await deliver_results(self.pipeline, pairs, target_languages, skip_refine)
        except Exception as e:
            logger.error("Batch processing error: %s", e)
            for p in batch:
                await manager.send_message(p.conn_id, _envelope(MessageType.ERROR, p.correlation_id, {"error": str(e)}))
        logger.debug("Micro-batch: %d cues in %.0f ms", len(batch), (time.time() - start) * 1000)


_micro_batcher: Optional[MicroBatcher] = None


async def get_micro_batcher() -> MicroBatcher:
    global _micro_batcher
    if _micro_batcher is None:
        _micro_batcher = MicroBatcher(await get_pipeline())
    return _micro_batcher


# ---------------------------------------------------------------------------
# Per-connection handler
# ---------------------------------------------------------------------------


class WebSocketHandler:
    def __init__(self, pipeline: TranslationPipeline, conn_id: str):
        self.pipeline = pipeline
        self.conn_id = conn_id
        self.handlers = {
            MessageType.BATCH: self._handle_batch,
            MessageType.CORRECTION: self._handle_correction,
            MessageType.CONFIG: self._handle_config,
            MessageType.PING: self._handle_ping,
        }

    async def handle_connection(self, websocket: WebSocket, conn_id: str) -> None:
        try:
            while True:
                data = await websocket.receive_text()
                info = manager.connection_info.get(conn_id)
                if info:
                    info["messages_received"] += 1
                    info["last_activity"] = datetime.utcnow()
                await self._process_message(conn_id, data)
        except WebSocketDisconnect:
            logger.info("Client %s disconnected", conn_id)
        except Exception as e:
            logger.error("Connection error for %s: %s", conn_id, e)

    def _conn_targets(self, payload: Dict[str, Any]) -> List[str]:
        info = manager.connection_info.get(self.conn_id) or {}
        return list(payload.get("target_languages") or info.get("target_languages") or settings.translation.target_languages)

    async def _process_message(self, conn_id: str, data: str) -> None:
        correlation_id = "unknown"
        try:
            raw = orjson.loads(data)
            msg_type = MessageType(raw.get("type", "cue"))
            correlation_id = raw.get("correlation_id", str(uuid.uuid4())[:8])
            payload = raw.get("payload", {})

            if msg_type == MessageType.CUE:
                await self._handle_cue(conn_id, correlation_id, payload)
                return

            handler = self.handlers.get(msg_type)
            if handler is None:
                await self._send_error(conn_id, correlation_id, f"Unknown message type: {msg_type}")
                return
            result = await handler(payload)
            await manager.send_message(conn_id, _envelope(MessageType.RESULT, correlation_id, result))

        except orjson.JSONDecodeError as e:
            await self._send_error(conn_id, correlation_id, f"Invalid JSON: {e}")
        except ValidationError as e:
            await self._send_error(conn_id, correlation_id, f"Validation error: {e}")
        except Exception as e:
            logger.exception("Processing error: %s", e)
            await self._send_error(conn_id, correlation_id, f"Error: {e}")

    async def _handle_cue(self, conn_id: str, correlation_id: str, payload: Dict) -> None:
        cue = SubtitleCue(**payload.get("cue", payload))
        target_languages = self._conn_targets(payload)
        skip_post_edit = bool(payload.get("skip_post_edit", False))

        if settings.features.batch_window_ms > 0:
            batcher = await get_micro_batcher()
            await batcher.add_cue(PendingCue(
                cue=cue, correlation_id=correlation_id, conn_id=conn_id,
                received_at=time.time(), target_languages=target_languages, skip_post_edit=skip_post_edit,
            ))
            return

        result = await self.pipeline.translate_cue(cue, target_languages, skip_post_edit)
        await deliver_results(self.pipeline, [(conn_id, correlation_id, result)], target_languages, skip_post_edit)

    async def _handle_batch(self, payload: Dict) -> Dict:
        cues = [SubtitleCue(**c) for c in payload.get("cues", [])]
        target_languages = self._conn_targets(payload)
        results = await self.pipeline.translate_batch(cues, target_languages, skip_post_edit=True)
        return {"results": [format_result(r) for r in results], "count": len(results)}

    async def _handle_correction(self, payload: Dict) -> Dict:
        try:
            correction = TranslationCorrection(**payload)
            tm = await get_translation_memory()
            source_lang = correction.source_lang
            if not source_lang or source_lang in ("auto", "unknown"):
                from .translation.language_detection import detect_language

                source_lang = detect_language(correction.source_text)[0]
            cache = self.pipeline._memory_cache
            for lang, corrected_text in correction.corrected_translation.items():
                original_text = correction.original_translation.get(lang, "")
                if corrected_text and corrected_text != original_text:
                    lines = corrected_text.split("\n") if "\n" in corrected_text else [corrected_text]
                    await tm.store_correction(
                        source_text=correction.source_text, source_lang=source_lang, target_lang=lang,
                        original_translation=original_text, corrected_translation=corrected_text, corrected_lines=lines,
                    )
            if cache is not None:
                cache.clear()  # drop stale memory entries so the correction wins immediately
            return {"status": "saved", "cue_id": correction.cue_id, "source_lang": source_lang}
        except Exception as e:
            logger.error("Correction error: %s", e)
            return {"status": "error", "error": str(e)}

    async def _handle_config(self, payload: Dict) -> Dict:
        try:
            update = ConfigUpdate(**payload)
            info = manager.connection_info.get(self.conn_id)

            if update.target_languages is not None:
                # per-connection, plus keep the global default for HTTP/debug callers
                if info is not None:
                    info["target_languages"] = list(update.target_languages)
                settings.translation.target_languages = list(update.target_languages)
            if update.use_post_editor is not None:
                settings.features.use_post_editor = update.use_post_editor
            if update.refiner_enabled is not None:
                settings.refiner.enabled = update.refiner_enabled
            if update.fast_mode is not None:
                settings.features.fast_mode = update.fast_mode
            if update.strict_meaning_lock is not None:
                settings.features.strict_meaning_lock = update.strict_meaning_lock
            if update.max_lines is not None:
                settings.translation.max_lines = update.max_lines
            if update.max_chars_en is not None:
                settings.translation.max_chars_en = update.max_chars_en
                settings.translation.max_chars_by_lang["en"] = update.max_chars_en
            if update.max_chars_zh is not None:
                settings.translation.max_chars_zh = update.max_chars_zh
                settings.translation.max_chars_by_lang["zh"] = update.max_chars_zh
            if update.max_chars_vi is not None:
                settings.translation.max_chars_vi = update.max_chars_vi
                settings.translation.max_chars_by_lang["vi"] = update.max_chars_vi
            if update.max_chars_by_lang:
                settings.translation.max_chars_by_lang.update(update.max_chars_by_lang)
            if update.cloud_keys:
                apply_cloud_keys(update.cloud_keys)

            return {
                "status": "updated",
                "current_config": {
                    "target_languages": settings.translation.target_languages,
                    "supported_languages": settings.translation.supported_languages,
                    "use_post_editor": settings.features.use_post_editor,
                    "refiner_enabled": settings.refiner.enabled,
                    "fast_mode": settings.features.fast_mode,
                    "max_lines": settings.translation.max_lines,
                    "max_chars_by_lang": settings.translation.max_chars_by_lang,
                    "mt_engines": list(self.pipeline.base_translator.engines.keys()),
                },
            }
        except Exception as e:
            logger.error("Config update error: %s", e)
            return {"status": "error", "error": str(e)}

    async def _handle_ping(self, payload: Dict) -> Dict:
        return {
            "type": "pong",
            "timestamp": datetime.utcnow().isoformat(),
            "stats": {
                "connections": manager.connection_count,
                "pipeline_stats": {
                    "total_requests": self.pipeline.stats.total_requests,
                    "cache_hits": self.pipeline.stats.cache_hits,
                    "avg_time_ms": self.pipeline.stats.avg_time_ms,
                    "p50_ms": self.pipeline.stats.percentile(50),
                    "p95_ms": self.pipeline.stats.percentile(95),
                },
            },
        }

    async def _send_error(self, conn_id: str, correlation_id: str, error: str) -> None:
        await manager.send_message(conn_id, _envelope(MessageType.ERROR, correlation_id, {"error": error}))


def apply_cloud_keys(keys: Dict[str, str]) -> None:
    """Apply API keys sent by the extension; rebuild engines that depend on them."""
    c, r = settings.cloud, settings.refiner
    changed_mt = False
    for k, v in (keys or {}).items():
        v = (v or "").strip()
        if k in ("google_api_key", "azure_translator_key", "azure_translator_region", "gladia_api_key", "elevenlabs_api_key") and getattr(c, k, None) != v:
            setattr(c, k, v)
            changed_mt = changed_mt or k in ("google_api_key", "azure_translator_key", "azure_translator_region")
        elif k in ("groq_api_key", "gemini_api_key"):
            setattr(r, k, v)
        elif k == "refiner_provider" and v:
            r.provider = v
        elif k == "mt_provider" and v:
            c.mt_provider = v
            changed_mt = True
        elif k == "asr_provider" and v:
            c.asr_provider = v
    if changed_mt:
        from .translation.base_translator import get_base_translator
        from .translation.cloud_translator import make_cloud_engine

        translator = get_base_translator()
        eng = make_cloud_engine()
        if eng is not None:
            translator.add_engine("cloud", eng)
        else:
            translator.engines.pop("cloud", None)


async def websocket_endpoint(websocket: WebSocket) -> None:
    conn_id = await manager.connect(websocket)
    try:
        pipeline = await get_pipeline()
        handler = WebSocketHandler(pipeline, conn_id)
        await handler.handle_connection(websocket, conn_id)
    finally:
        manager.disconnect(conn_id)
