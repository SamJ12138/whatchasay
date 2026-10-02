"""
Spoken language identification (once per session).

whisper-tiny (the sherpa-onnx export) on the first seconds of voiced audio (the
session decides when: asr/session.py), run directly with onnxruntime so that its
per-language probabilities are visible:
the answer is the most probable language among the ones we can recognise
(renormalised over them), and only if it is at least `min_confidence`; otherwise
the session stays provisional. whisper's own unconstrained answer is often a
neighbour of Bengali (Hindi, Nepali: docs/observations.md A7), so it is logged
(stage `lid_scores`, top 3) but never used as is. The result locks the recognizer
for the session; the user's manual choice in the popup always overrides it.
"""

from __future__ import annotations

import logging
import tarfile
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import numpy as np

from .engine import SAMPLE_RATE
from .. import obs

logger = logging.getLogger(__name__)

try:
    import onnxruntime as ort  # type: ignore

    ORT_AVAILABLE = True
except ImportError:  # pragma: no cover
    ort = None
    ORT_AVAILABLE = False

LID_MODEL_NAME = "sherpa-onnx-whisper-tiny"
LID_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{LID_MODEL_NAME}.tar.bz2"
ENCODER, DECODER = "tiny-encoder.int8.onnx", "tiny-decoder.onnx"

# whisper's front end: 25 ms frames every 10 ms, 80 mel bins (Slaney scale and norm), windows of
# at most 30 s. The exported encoder takes any length, and its cost grows with it: the window is
# the audio plus PAD_S of silence (at least MIN_S), not always 30 s (0.14 s -> 0.015 s per call for
# 2.5 s of audio; on short audio the answer is at least as confident as with 30 s of padding).
N_FFT, HOP, N_MELS, CHUNK_S = 400, 160, 80, 30
PAD_S, MIN_S = 0.5, 2.0


def _hz_to_mel(f):
    f = np.asarray(f, dtype=np.float64)
    logstep = np.log(6.4) / 27.0
    return np.where(f >= 1000, 15 + np.log(np.maximum(f, 1e-10) / 1000) / logstep, f / (200.0 / 3))


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    logstep = np.log(6.4) / 27.0
    return np.where(m >= 15, 1000 * np.exp(logstep * (m - 15)), m * 200.0 / 3)


def _mel_filters() -> np.ndarray:
    fft = np.linspace(0, SAMPLE_RATE / 2, 1 + N_FFT // 2)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(0), _hz_to_mel(SAMPLE_RATE / 2), N_MELS + 2))
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fft[None, :]
    w = np.zeros((N_MELS, len(fft)))
    for i in range(N_MELS):
        w[i] = np.maximum(0, np.minimum(-ramps[i] / fdiff[i], ramps[i + 2] / fdiff[i + 1]))
    w *= (2.0 / (mel_f[2:N_MELS + 2] - mel_f[:N_MELS]))[:, None]
    return w.astype(np.float32)


_FILTERS = _mel_filters()
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)  # periodic Hann


def log_mel(samples) -> np.ndarray:
    """whisper's log-mel spectrogram of the audio plus PAD_S of silence, at least MIN_S and
    at most 30 s long (an even number of frames): (80, 200) for 1 s ... (80, 3000)."""
    audio = np.asarray(samples, dtype=np.float32)
    n = len(audio) + int(PAD_S * SAMPLE_RATE)
    n = min(CHUNK_S * SAMPLE_RATE, max(int(MIN_S * SAMPLE_RATE), -(-n // (2 * HOP)) * 2 * HOP))
    audio = np.pad(audio, (0, max(0, n - len(audio))))[:n]
    x = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode="reflect")
    frames = np.lib.stride_tricks.as_strided(x, (1 + (len(x) - N_FFT) // HOP, N_FFT), (x.strides[0] * HOP, x.strides[0]))
    power = np.abs(np.fft.rfft(frames * _WINDOW, axis=1)) ** 2
    lg = np.log10(np.maximum(_FILTERS @ power[:-1].T, 1e-10))
    lg = np.maximum(lg, lg.max() - 8.0)
    return ((lg + 4.0) / 4.0).astype(np.float32)


def language_tokens(meta: Dict[str, str]) -> Tuple[Dict[str, int], int]:
    """{language code: token id} and the start-of-transcript token, from the encoder's metadata."""
    toks = [int(t) for t in meta["all_language_tokens"].split(",") if t]
    codes = [c for c in meta["all_language_codes"].split(",") if c]
    return dict(zip(codes, toks)), int(meta["sot"])


def choose_language(probs: Dict[str, float], allowed: Iterable[str], floor: float) -> Tuple[Optional[str], dict]:
    """The most probable allowed language, renormalised over the allowed ones, if it reaches
    `floor`; else None (undecided). `info` carries what to log: the unconstrained top 3,
    the constrained distribution, the best candidate and its confidence."""
    top3 = [(c, round(float(p), 4)) for c, p in sorted(probs.items(), key=lambda kv: -kv[1])[:3]]
    sub = {c: float(probs.get(c, 0.0)) for c in allowed}
    total = sum(sub.values())
    info = {"top3": top3, "floor": floor, "constrained": {}, "best": None, "confidence": 0.0, "chosen": None}
    if total <= 0:
        return None, info
    constrained = {c: p / total for c, p in sub.items()}
    best = max(constrained, key=constrained.get)
    info.update(constrained={c: round(p, 4) for c, p in constrained.items()}, best=best,
                confidence=round(constrained[best], 4))
    if constrained[best] < floor:
        return None, info
    info["chosen"] = best
    return best, info


class SpokenLanguageId:
    def __init__(self, models_root: Path, num_threads: int = 2, min_confidence: float = 0.6):
        self.models_root = Path(models_root)
        self.num_threads = num_threads
        self.min_confidence = min_confidence
        self._enc = None
        self._dec = None
        self._lang_tokens: Dict[str, int] = {}
        self._sot = 0

    def _ensure_model(self) -> Path:
        model_dir = self.models_root / LID_MODEL_NAME
        if (model_dir / ENCODER).exists() and (model_dir / DECODER).exists():
            return model_dir
        self.models_root.mkdir(parents=True, exist_ok=True)
        archive = self.models_root / f"{LID_MODEL_NAME}.tar.bz2"
        logger.info("Downloading LID model %s ...", LID_URL)
        urllib.request.urlretrieve(LID_URL, archive)
        with tarfile.open(archive) as tf:
            tf.extractall(self.models_root)
        archive.unlink(missing_ok=True)
        return model_dir

    def _load(self):
        if not ORT_AVAILABLE:
            raise RuntimeError("onnxruntime not installed")
        model_dir = self._ensure_model()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.num_threads
        opts.inter_op_num_threads = 1
        self._enc = ort.InferenceSession(str(model_dir / ENCODER), opts, providers=["CPUExecutionProvider"])
        self._dec = ort.InferenceSession(str(model_dir / DECODER), opts, providers=["CPUExecutionProvider"])
        self._lang_tokens, self._sot = language_tokens(self._enc.get_modelmeta().custom_metadata_map)
        logger.info("Spoken language ID model loaded")

    def available(self) -> bool:
        return ORT_AVAILABLE

    def load(self) -> None:
        """Load the model now if it is on disk (startup warmup: about half a second that the
        first attempt of the first Auto-detect session would otherwise spend). A model that
        is not downloaded yet is left to the first use, as before."""
        if self._enc is not None:
            return
        model_dir = self.models_root / LID_MODEL_NAME
        if not ((model_dir / ENCODER).exists() and (model_dir / DECODER).exists()):
            raise FileNotFoundError(f"{LID_MODEL_NAME} is not in {self.models_root}")
        self._load()

    def probabilities(self, samples_f32) -> Dict[str, float]:
        """whisper-tiny's probability for each of its ~100 languages (softmax over the
        language tokens after start-of-transcript, as whisper's detect_language does)."""
        if self._enc is None:
            self._load()
        cross_k, cross_v = self._enc.run(None, {"mel": log_mel(samples_f32)[None]})
        cache = np.zeros((cross_k.shape[0], 1, 448, cross_k.shape[-1]), np.float32)
        logits = self._dec.run(None, {"tokens": np.array([[self._sot]], np.int64), "in_n_layer_self_k_cache": cache,
                                      "in_n_layer_self_v_cache": cache, "n_layer_cross_k": cross_k,
                                      "n_layer_cross_v": cross_v, "offset": np.array([0], np.int64)})[0][0, -1]
        codes = list(self._lang_tokens)
        lg = logits[[self._lang_tokens[c] for c in codes]].astype(np.float64)
        p = np.exp(lg - lg.max())
        p /= p.sum()
        return dict(zip(codes, p.tolist()))

    def identify(self, samples_f32, allowed: Optional[Iterable[str]] = None) -> Optional[str]:
        """Return an ISO-639-1 code (e.g. 'en', 'zh', 'bn') or None (undecided)."""
        if not self.available():
            return None
        probs = self.probabilities(samples_f32)
        allowed = list(allowed) if allowed is not None else list(probs)
        lang, info = choose_language(probs, allowed, self.min_confidence)
        if lang is None:
            logger.info("LID undecided: best allowed %r at %.2f < %.2f (whisper top 3: %s)",
                        info["best"], info["confidence"], self.min_confidence, info["top3"])
            obs.log("lid_scores", "skip", error_type="input_invalid", allowed=allowed, **info,
                    error_message=f"best allowed language below the confidence floor {self.min_confidence}")
        else:
            obs.log("lid_scores", "success", allowed=allowed, **info)
        return lang
