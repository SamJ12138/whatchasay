"""
Streaming ASR package.

Public API used by main.py:
    get_engines()      -> {name: StreamingASREngine}
    create_session(cfg)-> StreamingASRSession
    asr_status()       -> dict
    warmup_asr()       -> None
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

from ..config import settings
from .engine import AsrEvent, AsrSession, StreamingASREngine, SAMPLE_RATE
from .session import SessionConfig, StreamingASRSession
from .sherpa_engine import SherpaZipformerEngine, SHERPA_AVAILABLE
from .lid import SpokenLanguageId

logger = logging.getLogger(__name__)

_engines: Optional[Dict[str, StreamingASREngine]] = None
_lid: Optional[SpokenLanguageId] = None


def get_engines() -> Dict[str, StreamingASREngine]:
    global _engines
    if _engines is None:
        _engines = {}
        cfg = settings.asr
        if SHERPA_AVAILABLE:
            _engines["sherpa-zipformer"] = SherpaZipformerEngine(
                models_root=cfg.models_dir,
                num_threads=cfg.num_threads,
                rule1_min_trailing_silence=cfg.rule1_min_trailing_silence,
                rule2_min_trailing_silence=cfg.rule2_min_trailing_silence,
                rule3_min_utterance_length=cfg.rule3_min_utterance_length,
            )
        # Optional engines register themselves only if their deps/keys exist.
        try:
            from .cloud_engine import make_cloud_engine

            cloud = make_cloud_engine()
            if cloud is not None:
                _engines["cloud"] = cloud
        except Exception as e:  # pragma: no cover
            logger.debug("Cloud ASR engine not available: %s", e)
        try:
            from .whisper_engine import WhisperAccuracyEngine, WHISPER_AVAILABLE

            if WHISPER_AVAILABLE:
                _engines["whisper"] = WhisperAccuracyEngine()
        except Exception as e:  # pragma: no cover
            logger.debug("Whisper engine not available: %s", e)
    return _engines


def get_lid() -> SpokenLanguageId:
    global _lid
    if _lid is None:
        _lid = SpokenLanguageId(settings.asr.models_dir, num_threads=settings.asr.num_threads)
    return _lid


def _lid_identify(audio: np.ndarray, allowed: List[str]) -> Optional[str]:
    lid = get_lid()
    if not lid.available():
        return None
    return lid.identify(audio, allowed)


def create_session(
    source_lang: str = "auto",
    target_langs: Optional[List[str]] = None,
    engine: str = "auto",
) -> StreamingASRSession:
    cfg = SessionConfig(
        source_lang=source_lang,
        target_langs=list(target_langs or settings.translation.target_languages),
        engine=engine if engine != "auto" else settings.asr.engine,
        allowed_langs=list(settings.asr.languages),
        lid_window_s=settings.asr.lid_window_s,
        partial_interval_ms=settings.asr.partial_interval_ms,
    )
    return StreamingASRSession(cfg, get_engines(), lid_identify=_lid_identify)


def asr_status() -> dict:
    engines = get_engines()
    return {
        "available": bool(engines),
        "default_engine": settings.asr.engine,
        "languages": settings.asr.languages,
        "engines": {name: eng.status() for name, eng in engines.items()},
    }


def warmup_asr() -> None:
    engines = get_engines()
    eng = engines.get("sherpa-zipformer")
    if eng is not None:
        eng.warmup(settings.asr.warmup_languages)


async def check_asr_available() -> bool:
    return bool(get_engines())


__all__ = [
    "AsrEvent",
    "AsrSession",
    "StreamingASREngine",
    "StreamingASRSession",
    "SessionConfig",
    "SAMPLE_RATE",
    "get_engines",
    "create_session",
    "asr_status",
    "warmup_asr",
    "check_asr_available",
]
