"""
Punctuation and casing for streaming ASR text, before translation.

The English Zipformer emits UPPERCASE words with no punctuation, and OPUS-MT
translates that badly (docs/asr-input-quality.md: en->zh turned "the squalid
quarter of the brothels" into "有质量的胶片", "quality film"). sherpa-onnx's online
punctuation model (a CNN-BiLSTM from Edge-Punct-Casing, Apache-2.0, 7.5 MB int8)
restores casing and punctuation in ~4 ms per sentence on one CPU thread.

Only English is restored: the measurement showed no consistent gain for Mandarin
and Bengali (and there is no Bengali model). The model lives in
data/models/punct/<name>/; scripts/download_models.py fetches it. The backend
never downloads it: if it is missing, text passes through unchanged and one
WARNING line says how to get it.
"""

from __future__ import annotations

import logging
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from .. import obs

logger = logging.getLogger(__name__)

PUNCT_MODEL = "sherpa-onnx-online-punct-en-2024-08-06"
PUNCT_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/{PUNCT_MODEL}.tar.bz2"
PUNCT_FILES = ("model.int8.onnx", "bpe.vocab")
PUNCT_DROP = ("model.onnx",)  # fp32 copy, unused
LANGUAGES = ("en",)


def model_dir(punct_dir: Path, name: str = PUNCT_MODEL) -> Path:
    return Path(punct_dir) / name


def download_model(punct_dir: Path) -> Path:
    """Fetch + extract the model (used by scripts/download_models.py only)."""
    d = model_dir(punct_dir)
    if all((d / f).exists() for f in PUNCT_FILES):
        return d
    root = Path(punct_dir)
    root.mkdir(parents=True, exist_ok=True)
    archive = root / f"{PUNCT_MODEL}.tar.bz2"
    urllib.request.urlretrieve(PUNCT_URL, archive)
    with tarfile.open(archive) as tf:
        tf.extractall(root)
    archive.unlink(missing_ok=True)
    for f in PUNCT_DROP:
        (d / f).unlink(missing_ok=True)
    missing = [f for f in PUNCT_FILES if not (d / f).exists()]
    if missing:
        raise RuntimeError(f"punctuation model archive {PUNCT_MODEL} did not contain {missing}")
    return d


class Punctuator:
    """Restores casing + punctuation for the languages it has a model for.

    restore() never raises: on any failure the input comes back unchanged (logged
    once per kind of failure, `punctuate` fail, degraded)."""

    def __init__(self, punct_dir: Path, name: str = PUNCT_MODEL, num_threads: int = 1):
        self.dir = model_dir(punct_dir, name)
        self.num_threads = num_threads
        self._model = None
        self._error: Optional[str] = None
        self._lock = threading.Lock()

    def supports(self, lang: str) -> bool:
        return lang in LANGUAGES

    def available(self) -> bool:
        return all((self.dir / f).exists() for f in PUNCT_FILES)

    def load(self) -> bool:
        """Load the model once; False (with one WARNING) when it cannot be used."""
        if self._model is not None:
            return True
        if self._error is not None:
            return False
        with self._lock:
            if self._model is not None:
                return True
            if self._error is not None:
                return False
            t0 = time.perf_counter()
            try:
                if not self.available():
                    raise FileNotFoundError(f"punctuation model not found in {self.dir}; "
                                            f"run: python scripts/download_models.py")
                import sherpa_onnx  # type: ignore

                cfg = sherpa_onnx.OnlinePunctuationConfig(model_config=sherpa_onnx.OnlinePunctuationModelConfig(
                    cnn_bilstm=str(self.dir / "model.int8.onnx"), bpe_vocab=str(self.dir / "bpe.vocab"),
                    num_threads=self.num_threads, provider="cpu"))
                self._model = sherpa_onnx.OnlinePunctuation(cfg)
            except Exception as e:
                self._error = str(e) or type(e).__name__
                logger.warning("English punctuation/casing is off: %s", self._error)
                obs.log_exc("startup", e, api="process", component="punctuation", model=self.dir.name,
                            degraded="English ASR text reaches the translator in UPPERCASE without punctuation")
                return False
            obs.log("startup", "success", duration_ms=(time.perf_counter() - t0) * 1000, component="punctuation",
                    model=self.dir.name)
            return True

    def restore(self, text: str, lang: str, log: bool = True) -> str:
        """Cased, punctuated text (English); anything else unchanged. log=False for
        partials (one run-log line per final is enough)."""
        if not text or not self.supports(lang) or not self.load():
            return text
        t0 = time.perf_counter()
        try:
            # The model was trained on lower-case input; upper-case input comes back as is.
            out = self._model.add_punctuation_with_case(text.lower()).strip()
        except Exception as e:
            obs.log_exc("punctuate", e, api="process", lang=lang, text_len=len(text), degraded="text kept as is")
            return text
        if log:
            obs.log("punctuate", "success", duration_ms=(time.perf_counter() - t0) * 1000, lang=lang, text_len=len(text))
        return out or text

    def status(self) -> dict:
        return {"model": self.dir.name, "languages": list(LANGUAGES), "available": self.available(),
                "loaded": self._model is not None, "error": self._error}
