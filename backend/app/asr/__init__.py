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
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..config import settings
from .. import obs
from .engine import AsrEvent, AsrSession, StreamingASREngine, SAMPLE_RATE
from .session import SessionConfig, StreamingASRSession
from .sherpa_engine import SegmentRules, SherpaZipformerEngine, SHERPA_AVAILABLE, models_for
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
                rules=SegmentRules(split_gap_s=cfg.split_gap_s, split_gap_ratio=cfg.split_gap_ratio,
                                   split_min_piece_s=cfg.split_min_piece_s, max_segment_s=cfg.max_segment_s,
                                   max_segment_tokens=cfg.max_segment_tokens),
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
        _lid = SpokenLanguageId(settings.asr.models_dir, num_threads=settings.asr.num_threads,
                                min_confidence=settings.asr.lid_min_confidence)
    return _lid


def _lid_identify(audio: np.ndarray, allowed: List[str]) -> Tuple[Optional[str], Optional[float]]:
    lid = get_lid()
    if not lid.available():
        return None, None
    return lid.identify_scored(audio, allowed)


def create_session(
    source_lang: str = "auto",
    target_langs: Optional[List[str]] = None,
    engine: str = "auto",
    prior: Optional[Dict[str, float]] = None,
) -> StreamingASRSession:
    cfg = SessionConfig(
        source_lang=source_lang,
        target_langs=list(target_langs or settings.translation.target_languages),
        engine=engine if engine != "auto" else settings.asr.engine,
        allowed_langs=list(settings.asr.languages),
        lid_window_s=settings.asr.lid_window_s,
        lid_first_window_s=settings.asr.lid_first_window_s,
        lid_retry_step_s=settings.asr.lid_retry_step_s,
        partial_interval_ms=settings.asr.partial_interval_ms,
        draft_stable_partials=settings.asr.draft_stable_partials,
        draft_min_words=settings.asr.draft_min_words,
        draft_min_cjk_chars=settings.asr.draft_min_cjk_chars,
        draft_min_fraction=settings.asr.draft_min_fraction,
        prior=dict(prior or {}),
        lid_prior_threshold=settings.asr.lid_prior_threshold,
        lid_prior_floor_en=settings.asr.lid_prior_floor_en,
        parallel_window_s=settings.asr.parallel_window_s,
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
    lid = get_lid()
    if lid.available():
        t0 = time.perf_counter()
        try:
            lid.load()
            obs.log("startup", "success", duration_ms=(time.perf_counter() - t0) * 1000, component="lid_model")
        except Exception as e:
            logger.info("Spoken-language ID model not loaded at startup: %s", e)
            obs.log_exc("startup", e, api="process", event="skip", component="lid_model",
                        degraded="loaded (or downloaded) on the first Auto-detect session instead")


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
