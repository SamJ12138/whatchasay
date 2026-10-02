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
from .. import obs
from .engine import AsrEvent, AsrSession, StreamingASREngine, SAMPLE_RATE
from .session import SessionConfig, StreamingASRSession
from .sherpa_engine import SherpaZipformerEngine, SHERPA_AVAILABLE, models_for
from .punctuation import Punctuator
from .lid import SpokenLanguageId

logger = logging.getLogger(__name__)

_engines: Optional[Dict[str, StreamingASREngine]] = None
_lid: Optional[SpokenLanguageId] = None
_punctuator: Optional[Punctuator] = None


def get_engines() -> Dict[str, StreamingASREngine]:
    global _engines
    if _engines is None:
        _engines = {}
        cfg = settings.asr
        if SHERPA_AVAILABLE:
            _engines["sherpa-zipformer"] = SherpaZipformerEngine(
                models_root=cfg.models_dir,
                models=models_for(cfg.zh_model, cfg.models_dir),
                num_threads=cfg.num_threads,
                rule1_min_trailing_silence=cfg.rule1_min_trailing_silence,
                rule2_min_trailing_silence=cfg.rule2_min_trailing_silence,
                rule3_min_utterance_length=cfg.rule3_min_utterance_length,
            )
        else:
            obs.log("startup", "skip", error_type="process", component="asr_engine", engine="sherpa-zipformer",
                    error_message="sherpa_onnx not importable")
        obs.log("startup", "success", component="asr_engines", engines=list(_engines.keys()))
    return _engines


def get_punctuator() -> Optional[Punctuator]:
    """English casing + punctuation for ASR text (asr.punctuation), or None when off."""
    global _punctuator
    if not settings.asr.punctuation:
        return None
    if _punctuator is None:
        _punctuator = Punctuator(settings.asr.punct_dir)
    return _punctuator


def _restore_text(text: str, lang: str, final: bool) -> str:
    p = get_punctuator()
    return p.restore(text, lang, log=final) if p is not None else text


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
    return StreamingASRSession(cfg, get_engines(), lid_identify=_lid_identify, restore_text=_restore_text)


def asr_status() -> dict:
    engines = get_engines()
    return {
        "available": bool(engines),
        "default_engine": settings.asr.engine,
        "languages": settings.asr.languages,
        "engines": {name: eng.status() for name, eng in engines.items()},
        "punctuation": get_punctuator().status() if get_punctuator() is not None else {"enabled": False},
    }


def warmup_asr() -> None:
    engines = get_engines()
    eng = engines.get("sherpa-zipformer")
    if eng is not None:
        eng.warmup(settings.asr.warmup_languages)
    p = get_punctuator()
    if p is not None and any(p.supports(l) for l in settings.asr.languages):
        p.load()


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
