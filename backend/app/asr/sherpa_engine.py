"""
Local streaming ASR using sherpa-onnx Zipformer transducers (CPU).

These models are chunk-synchronous: every ~40 ms of audio fed in produces an
updated hypothesis with a fixed ~300-400 ms delay, and the built-in endpoint
detector closes an utterance after trailing silence. That is what makes a
sub-second subtitle possible; Whisper-family models cannot do this natively.

Model directories live under data/models/asr/<name>/ and are downloaded
automatically from the sherpa-onnx GitHub release on first use.
"""

from __future__ import annotations

import logging
import os
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .engine import AsrEvent, SAMPLE_RATE, pcm16_to_float32
from .. import obs

logger = logging.getLogger(__name__)

try:
    import sherpa_onnx  # type: ignore

    SHERPA_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when not installed
    sherpa_onnx = None
    SHERPA_AVAILABLE = False

RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


class ZipformerModelSpec:
    """Where a model lives and which files to load."""

    def __init__(
        self,
        name: str,
        encoder: str,
        decoder: str,
        joiner: str,
        tokens: str = "tokens.txt",
        modeling_unit: str = "",
        bpe_vocab: str = "",
        drop: Tuple[str, ...] = (),
        gated: str = "",
    ):
        self.name = name
        self.encoder = encoder
        self.decoder = decoder
        self.joiner = joiner
        self.tokens = tokens
        self.modeling_unit = modeling_unit
        self.bpe_vocab = bpe_vocab
        # files deleted after extraction (unused precisions; saves disk)
        self.drop = drop
        # non-empty: the backend never downloads this model; only
        # scripts/download_models.py does, after its notice (the value is the flag)
        self.gated = gated

    @property
    def url(self) -> str:
        return f"{RELEASE_BASE}{self.name}.tar.bz2"


# Mandarin (asr.zh_model). "bilingual" (default): Apache-2.0, Mandarin + English
# code-switching. "large": lower error rate on read Mandarin but declares no license
# upstream (NOTICE.md); only `scripts/download_models.py --mandarin-large
# --accept-mandarin-large-terms` downloads it, never the backend.
MANDARIN_MODELS: Dict[str, ZipformerModelSpec] = {
    "bilingual": ZipformerModelSpec(
        "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        encoder="encoder-epoch-99-avg-1.int8.onnx",
        decoder="decoder-epoch-99-avg-1.onnx",
        joiner="joiner-epoch-99-avg-1.int8.onnx",
        drop=("encoder-epoch-99-avg-1.onnx", "joiner-epoch-99-avg-1.onnx", "decoder-epoch-99-avg-1.int8.onnx"),
    ),
    "large": ZipformerModelSpec(
        "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30",
        encoder="encoder.int8.onnx",
        decoder="decoder.onnx",
        joiner="joiner.int8.onnx",
        gated="--mandarin-large --accept-mandarin-large-terms",
    ),
}

# Verified against the sherpa-onnx pretrained-model catalogue (Sept 2026).
DEFAULT_MODELS: Dict[str, ZipformerModelSpec] = {
    "en": ZipformerModelSpec(
        "sherpa-onnx-streaming-zipformer-en-2023-06-26",
        encoder="encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
        decoder="decoder-epoch-99-avg-1-chunk-16-left-128.onnx",
        joiner="joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx",
    ),
    "zh": MANDARIN_MODELS["bilingual"],
    "bn": ZipformerModelSpec(
        "sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09",
        encoder="encoder.onnx",
        decoder="decoder.onnx",
        joiner="joiner.onnx",
    ),
}


def model_present(spec: ZipformerModelSpec, models_root: Path) -> bool:
    return (Path(models_root) / spec.name / spec.tokens).exists()


def models_for(zh_model: str, models_root: Path) -> Dict[str, ZipformerModelSpec]:
    """The per-language specs for this config. A configured "large" Mandarin model
    that is not on disk falls back to the bilingual one with one WARNING line."""
    models = dict(DEFAULT_MODELS)
    if zh_model != "bilingual":
        spec = MANDARIN_MODELS[zh_model]
        if model_present(spec, models_root):
            models["zh"] = spec
        else:
            msg = (f"Mandarin model {zh_model!r} ({spec.name}) is configured but not downloaded; using the bilingual "
                   f"model. Download it with: python scripts/download_models.py {spec.gated}")
            logger.warning(msg)
            obs.log("startup", "skip", error_type="input_invalid", component="asr_model", lang="zh",
                    error_message=msg, degraded="bilingual Mandarin model")
    return models


def _find_file(model_dir: Path, preferred: str, patterns: List[str]) -> Path:
    """Return preferred filename if present, else first match of patterns."""
    cand = model_dir / preferred
    if cand.exists():
        return cand
    for pat in patterns:
        matches = sorted(model_dir.glob(pat))
        if matches:
            return matches[0]
    raise FileNotFoundError(f"No file matching {preferred!r} or {patterns} in {model_dir}")


def ensure_model(spec: ZipformerModelSpec, models_root: Path, allow_gated: bool = False) -> Path:
    """Download + extract the model archive if the directory is missing. A gated
    model is only downloaded by scripts/download_models.py (allow_gated)."""
    model_dir = models_root / spec.name
    if (model_dir / spec.tokens).exists():
        return model_dir
    if spec.gated and not allow_gated:
        raise FileNotFoundError(f"{spec.name} is not downloaded; the backend does not download it. "
                                f"Run: python scripts/download_models.py {spec.gated}")

    models_root.mkdir(parents=True, exist_ok=True)
    archive = models_root / f"{spec.name}.tar.bz2"
    logger.info("Downloading ASR model %s ...", spec.url)
    urllib.request.urlretrieve(spec.url, archive)
    with tarfile.open(archive) as tf:
        tf.extractall(models_root)
    archive.unlink(missing_ok=True)
    for name in spec.drop:
        (model_dir / name).unlink(missing_ok=True)
    if not (model_dir / spec.tokens).exists():
        raise RuntimeError(f"Model archive for {spec.name} did not contain {spec.tokens}")
    logger.info("ASR model %s ready", spec.name)
    return model_dir


class SherpaSession:
    """One Zipformer stream with endpointing and partial/final emission."""

    def __init__(self, engine: "SherpaZipformerEngine", lang: str, recognizer):
        self.engine = engine
        self.lang = lang
        self.recognizer = recognizer
        self.stream = recognizer.create_stream()
        self._samples_fed = 0
        self._utterance_id = 0
        self._utt_start_sample = 0
        self._last_partial = ""
        self._closed = False

    @property
    def audio_clock(self) -> float:
        return self._samples_fed / SAMPLE_RATE

    def feed(self, pcm16: bytes) -> List[AsrEvent]:
        if self._closed or not pcm16:
            return []
        samples = pcm16_to_float32(pcm16)
        self.stream.accept_waveform(SAMPLE_RATE, samples)
        self._samples_fed += len(samples)

        rec = self.recognizer
        while rec.is_ready(self.stream):
            rec.decode_stream(self.stream)

        text = rec.get_result(self.stream).strip()
        events: List[AsrEvent] = []

        if rec.is_endpoint(self.stream):
            if text:
                events.append(self._make_event("final", text))
            rec.reset(self.stream)
            self._utterance_id += 1
            self._utt_start_sample = self._samples_fed
            self._last_partial = ""
        elif text and text != self._last_partial:
            self._last_partial = text
            events.append(self._make_event("partial", text))

        return events

    def flush(self) -> List[AsrEvent]:
        if self._closed:
            return []
        # Push a little silence so the decoder drains, then emit as final.
        import numpy as np

        self.stream.accept_waveform(SAMPLE_RATE, np.zeros(int(SAMPLE_RATE * 0.5), dtype="float32"))
        rec = self.recognizer
        while rec.is_ready(self.stream):
            rec.decode_stream(self.stream)
        text = rec.get_result(self.stream).strip()
        events = []
        if text:
            events.append(self._make_event("final", text))
        rec.reset(self.stream)
        self._utterance_id += 1
        self._utt_start_sample = self._samples_fed
        self._last_partial = ""
        return events

    def close(self) -> None:
        self._closed = True

    def _make_event(self, kind: str, text: str) -> AsrEvent:
        return AsrEvent(
            kind=kind,
            text=text,
            lang=self.lang,
            t0=self._utt_start_sample / SAMPLE_RATE,
            t1=self.audio_clock,
            utterance_id=self._utterance_id,
        )


class SherpaZipformerEngine:
    """Lazily loads one recognizer per language and hands out sessions."""

    name = "sherpa-zipformer"

    def __init__(
        self,
        models_root: Path,
        models: Optional[Dict[str, ZipformerModelSpec]] = None,
        num_threads: int = 2,
        rule1_min_trailing_silence: float = 1.2,
        rule2_min_trailing_silence: float = 0.6,
        rule3_min_utterance_length: float = 15.0,
    ):
        self.models_root = Path(models_root)
        self.models = models or DEFAULT_MODELS
        self.num_threads = num_threads
        self.rule1 = rule1_min_trailing_silence
        self.rule2 = rule2_min_trailing_silence
        self.rule3 = rule3_min_utterance_length
        self._recognizers: Dict[str, object] = {}
        self._lock = threading.Lock()

    def supports(self, lang: str) -> bool:
        return SHERPA_AVAILABLE and lang in self.models

    def status(self) -> dict:
        return {
            "engine": self.name,
            "available": SHERPA_AVAILABLE,
            "languages": sorted(self.models.keys()),
            "models": {lang: spec.name for lang, spec in self.models.items()},
            "loaded": sorted(self._recognizers.keys()),
        }

    def _load(self, lang: str):
        if not SHERPA_AVAILABLE:
            raise RuntimeError("sherpa-onnx is not installed (pip install sherpa-onnx)")
        spec = self.models[lang]
        model_dir = ensure_model(spec, self.models_root)
        encoder = _find_file(model_dir, spec.encoder, ["encoder*.int8.onnx", "encoder*.onnx"])
        decoder = _find_file(model_dir, spec.decoder, ["decoder*.onnx"])
        joiner = _find_file(model_dir, spec.joiner, ["joiner*.int8.onnx", "joiner*.onnx"])
        tokens = model_dir / spec.tokens

        t0 = time.time()
        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(tokens),
            encoder=str(encoder),
            decoder=str(decoder),
            joiner=str(joiner),
            num_threads=self.num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=self.rule1,
            rule2_min_trailing_silence=self.rule2,
            rule3_min_utterance_length=self.rule3,
            decoding_method="greedy_search",
            provider="cpu",
        )
        logger.info("Loaded Zipformer %s (%s) in %.1fs", lang, spec.name, time.time() - t0)
        return recognizer

    def get_recognizer(self, lang: str):
        with self._lock:
            if lang not in self._recognizers:
                self._recognizers[lang] = self._load(lang)
            return self._recognizers[lang]

    def warmup(self, langs: List[str]) -> None:
        for lang in langs:
            if self.supports(lang):
                t0 = time.perf_counter()
                try:
                    self.get_recognizer(lang)
                    obs.log("startup", "success", duration_ms=(time.perf_counter() - t0) * 1000, component="asr_model", lang=lang)
                except Exception as e:  # pragma: no cover
                    logger.warning("ASR warmup failed for %s: %s", lang, e)
                    obs.log_exc("startup", e, api="process", duration_ms=(time.perf_counter() - t0) * 1000,
                                component="asr_model", lang=lang, degraded="recognizer loads on first session instead")
            else:
                obs.log("startup", "skip", error_type="input_invalid", component="asr_model", lang=lang,
                        error_message=f"no streaming model for {lang!r}")

    def start_session(self, lang: str) -> SherpaSession:
        if not self.supports(lang):
            raise ValueError(f"Language {lang!r} not supported by {self.name}")
        return SherpaSession(self, lang, self.get_recognizer(lang))
