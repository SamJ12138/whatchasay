"""
Local streaming ASR using sherpa-onnx Zipformer transducers (CPU).

These models are chunk-synchronous: the hypothesis is updated once per decode
chunk (0.32 s for the English and Mandarin models, 0.64 s for the Bengali one),
and the built-in endpoint detector closes an utterance after trailing silence;
SherpaSession also ends a line at pauses and length limits (SegmentRules). That is what makes a
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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SegmentRules:
    """Where a line ends besides the recognizer's own endpoint (observations A9).

    Pause rule: the line ends before a word whose first token comes at least
    `split_gap_s` after the previous token, if the line so far spans at least
    `split_min_piece_s` and the gap is at least `split_gap_ratio` times the line's
    median token gap (0 = no ratio test). split_gap_s 0 = off.
    Length rule: a line spanning more than `max_segment_s`, or holding more than
    `max_segment_tokens` tokens, is cut at its widest gap between words (0 = off).
    """

    split_gap_s: float = 0.5
    split_gap_ratio: float = 2.5
    split_min_piece_s: float = 2.0
    max_segment_s: float = 6.0
    max_segment_tokens: int = 48


def _word_start(token: str) -> bool:
    """Does this token begin a word? The Zipformer models mark a word's first piece with
    a leading space (or the sentencepiece underline); a CJK character is a word of its own."""
    c = token[:1]
    return c in (" ", "▁") or "一" <= c <= "鿿" or "㐀" <= c <= "䶿"


def _text_of(tokens: List[str]) -> str:
    return "".join(tokens).replace("▁", " ").strip()


class SherpaSession:
    """One Zipformer stream: partial / final emission, the recognizer's endpointing, and
    lines closed at pauses and at the length limits (SegmentRules) without resetting the
    stream: the recognizer keeps its context, the tokens before the cut become a final,
    the ones after it the next line."""

    def __init__(self, engine: "SherpaZipformerEngine", lang: str, recognizer, rules: Optional[SegmentRules] = None):
        self.engine = engine
        self.lang = lang
        self.recognizer = recognizer
        self.rules = rules or SegmentRules()
        self.stream = recognizer.create_stream()
        self._samples_fed = 0
        self._utterance_id = 0
        self._line_t0 = 0.0     # audio clock where the open line starts
        self._cut = 0           # tokens of the recognizer's current segment already closed as finals
        self._seen = 0          # token count when the rules were last applied
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

        tokens, times = self._tokens()
        events: List[AsrEvent] = []
        if len(tokens) != self._seen:
            self._seen = len(tokens)
            events.extend(self._close_lines(tokens, times))

        if rec.is_endpoint(self.stream):
            events.extend(self._end_segment(tokens, times))
        else:
            text = _text_of(tokens[self._cut:])
            if text and text != self._last_partial:
                self._last_partial = text
                events.append(self._event("partial", tokens, times, self._cut, len(tokens)))
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
        tokens, times = self._tokens()
        return self._close_lines(tokens, times) + self._end_segment(tokens, times)

    def close(self) -> None:
        self._closed = True

    # ------------------------------------------------------------- segmentation

    def _tokens(self) -> Tuple[List[str], List[float]]:
        """Tokens of the recognizer's current segment with their audio-clock times
        (sherpa-onnx reports timestamps relative to the segment's start_time)."""
        r = self.recognizer.get_result_all(self.stream)
        tokens = list(r.tokens)
        times = [r.start_time + t for t in r.timestamps]
        n = min(len(tokens), len(times))
        return tokens[:n], times[:n]

    def _split_at(self, tokens: List[str], times: List[float]) -> Optional[int]:
        """Index of the token that starts the next line, or None while the open line goes on."""
        a, n, r = self._cut, len(tokens), self.rules
        starts = [k for k in range(a + 1, n) if _word_start(tokens[k])]
        if r.split_gap_s > 0:
            for k in starts:
                gap = times[k] - times[k - 1]
                if gap < r.split_gap_s or times[k - 1] - times[a] < r.split_min_piece_s:
                    continue
                inner = sorted(times[j] - times[j - 1] for j in range(a + 1, k))
                if r.split_gap_ratio > 0 and inner and gap < r.split_gap_ratio * inner[len(inner) // 2]:
                    continue
                return k
        too_long = (r.max_segment_s > 0 and n > a and times[n - 1] - times[a] > r.max_segment_s) or \
                   (r.max_segment_tokens > 0 and n - a > r.max_segment_tokens)
        if not too_long:
            return None
        # cut where the part before fits the limits: at the widest gap that leaves a real line
        # (split_min_piece_s) before it, else at the widest gap there is; among equals the latest
        fits = [k for k in starts if not ((r.max_segment_tokens > 0 and k - a > r.max_segment_tokens) or
                                          (r.max_segment_s > 0 and times[k - 1] - times[a] > r.max_segment_s))]
        real = [k for k in fits if times[k - 1] - times[a] >= r.split_min_piece_s]
        candidates = real or fits[1:] or fits  # never a one-word line while there is another place to cut
        if not candidates:
            return None
        return max(candidates, key=lambda k: (times[k] - times[k - 1], k))

    def _close_lines(self, tokens: List[str], times: List[float]) -> List[AsrEvent]:
        events: List[AsrEvent] = []
        while True:
            k = self._split_at(tokens, times)
            if k is None:
                return events
            events.append(self._event("final", tokens, times, self._cut, k))
            self._cut = k
            self._line_t0 = times[k]
            self._utterance_id += 1
            self._last_partial = ""

    def _end_segment(self, tokens: List[str], times: List[float]) -> List[AsrEvent]:
        """The recognizer's endpoint (or a flush): what is left of the segment is a final."""
        events = []
        if _text_of(tokens[self._cut:]):
            events.append(self._event("final", tokens, times, self._cut, len(tokens)))
        self.recognizer.reset(self.stream)
        self._utterance_id += 1
        self._line_t0 = self.audio_clock
        self._cut = 0
        self._seen = 0
        self._last_partial = ""
        return events

    def _event(self, kind: str, tokens: List[str], times: List[float], a: int, b: int) -> AsrEvent:
        """The line made of tokens[a:b]; a line closed by a rule ends with its last word,
        any other at the audio clock."""
        closed_by_rule = kind == "final" and b < len(tokens)
        return AsrEvent(
            kind=kind,
            text=_text_of(tokens[a:b]),
            lang=self.lang,
            t0=self._line_t0,
            t1=min(times[b - 1] + 0.2, times[b]) if closed_by_rule else self.audio_clock,
            utterance_id=self._utterance_id,
            first_word_t=times[a] if b > a else None,
            last_word_t=times[b - 1] if b > a else None,
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
        rule3_min_utterance_length: float = 30.0,
        rules: Optional[SegmentRules] = None,
    ):
        self.models_root = Path(models_root)
        self.models = models or DEFAULT_MODELS
        self.num_threads = num_threads
        self.rule1 = rule1_min_trailing_silence
        self.rule2 = rule2_min_trailing_silence
        self.rule3 = rule3_min_utterance_length
        self.rules = rules or SegmentRules()
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
        return SherpaSession(self, lang, self.get_recognizer(lang), rules=self.rules)
