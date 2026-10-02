"""
Spoken language identification (once per session).

Uses sherpa-onnx's Whisper-tiny based SpokenLanguageIdentification on the
first few seconds of voiced audio, restricted to the languages we can
recognise. The result locks the recognizer for the session; the user's manual
choice in the popup always overrides it.
"""

from __future__ import annotations

import logging
import tarfile
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

from .engine import SAMPLE_RATE
from .. import obs

logger = logging.getLogger(__name__)

try:
    import sherpa_onnx  # type: ignore

    SHERPA_AVAILABLE = True
except ImportError:  # pragma: no cover
    sherpa_onnx = None
    SHERPA_AVAILABLE = False

LID_MODEL_NAME = "sherpa-onnx-whisper-tiny"
LID_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{LID_MODEL_NAME}.tar.bz2"


class SpokenLanguageId:
    def __init__(self, models_root: Path, num_threads: int = 2):
        self.models_root = Path(models_root)
        self.num_threads = num_threads
        self._slid = None

    def _ensure_model(self) -> Path:
        model_dir = self.models_root / LID_MODEL_NAME
        if (model_dir / "tiny-encoder.int8.onnx").exists():
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
        if not SHERPA_AVAILABLE:
            raise RuntimeError("sherpa-onnx not installed")
        model_dir = self._ensure_model()
        config = sherpa_onnx.SpokenLanguageIdentificationConfig(
            whisper=sherpa_onnx.SpokenLanguageIdentificationWhisperConfig(
                encoder=str(model_dir / "tiny-encoder.int8.onnx"),
                decoder=str(model_dir / "tiny-decoder.onnx"),
            ),
            num_threads=self.num_threads,
            debug=False,
            provider="cpu",
        )
        self._slid = sherpa_onnx.SpokenLanguageIdentification(config)
        logger.info("Spoken language ID model loaded")

    def available(self) -> bool:
        return SHERPA_AVAILABLE

    def identify(self, samples_f32, allowed: Optional[Iterable[str]] = None) -> Optional[str]:
        """Return an ISO-639-1 code (e.g. 'en', 'zh', 'bn') or None."""
        if not SHERPA_AVAILABLE:
            return None
        if self._slid is None:
            self._load()
        stream = self._slid.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples_f32)
        lang = self._slid.compute(stream)
        lang = (lang or "").lower()
        if allowed is not None and lang not in set(allowed):
            logger.info("LID returned %r which is outside %s", lang, list(allowed))
            obs.log("lid", "skip", error_type="input_invalid", detected=lang, allowed=list(allowed),
                    error_message=f"LID result {lang!r} outside allowed languages; returned None")
            return None
        return lang or None
