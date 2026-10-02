"""Spoken-language ID with the real whisper-tiny model, through the real session's LID
buffering: the three sample WAVs and the YouTube clip of docs/live-test-youtube.md.
Three runs per input, starting 0, 0.3 and 0.7 s in (the live runs' start jitter);
all three must confirm the right language.

The clip's audio is not in the repository (a film scene, copyrighted): point
ST_LID_CLIP_WAV at a 16 kHz mono WAV of its first 20 s, or that case is skipped.
"""

import os
import wave
from pathlib import Path

import pytest

from tests.conftest import MODELS_DIR, has_zipformer, zipformer_dir
from tests.fakes import FakeASREngine

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (MODELS_DIR / "sherpa-onnx-whisper-tiny" / "tiny-encoder.int8.onnx").exists(),
                       reason="whisper-tiny LID model not downloaded"),
]

OFFSETS = (0.0, 0.3, 0.7)
CLIP = os.environ.get("ST_LID_CLIP_WAV")


def _cases():
    out = []
    for lang in ("en", "zh", "bn"):
        out.append(pytest.param(lang, lambda l=lang: zipformer_dir(l) / "test_wavs" / "0.wav", id=f"sample-{lang}",
                                marks=pytest.mark.skipif(not has_zipformer(lang), reason=f"{lang} model not downloaded")))
    out.append(pytest.param("bn", lambda: Path(CLIP), id="youtube-clip-bn",
                            marks=pytest.mark.skipif(not (CLIP and Path(CLIP).exists()), reason="ST_LID_CLIP_WAV not set")))
    return out


@pytest.fixture(scope="module")
def lid():
    from app.asr.lid import SpokenLanguageId

    return SpokenLanguageId(MODELS_DIR, min_confidence=0.6)


def _pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2), path
        return w.readframes(w.getnframes())


def _detect(lid, pcm: bytes):
    from app.asr.session import SessionConfig, StreamingASRSession

    s = StreamingASRSession(SessionConfig(source_lang="auto", target_langs=["en"], allowed_langs=["en", "zh", "bn"]),
                            {"sherpa-zipformer": FakeASREngine()}, lid_identify=lid.identify)
    msgs = []
    for i in range(0, len(pcm), 1280):  # 40 ms frames, as the extension sends them
        msgs += s.feed(pcm[i:i + 1280])
        if s.lang_status in ("confirmed", "fallback", "error"):
            break
    return [m for m in msgs if m["type"] == "lid"][-1]


@pytest.mark.parametrize("expected,path", _cases())
def test_three_runs_agree_on_the_right_language(lid, expected, path):
    pcm = _pcm(path())
    results = [_detect(lid, pcm[int(off * 16000) * 2:]) for off in OFFSETS]
    assert [(m["lang"], m["status"]) for m in results] == [(expected, "confirmed")] * 3, results
