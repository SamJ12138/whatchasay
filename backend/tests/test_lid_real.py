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


# ---------------------------------------------------------------- earlier confirmation (latency Batch 3)


def _decision(lid, pcm: bytes, **cfg):
    """(language, status, seconds of audio fed when the session decided, seconds of audio fed
    when the first text in the confirmed language came out)."""
    from app.asr.session import SessionConfig, StreamingASRSession
    from app.asr.sherpa_engine import SherpaZipformerEngine

    s = StreamingASRSession(SessionConfig(source_lang="auto", target_langs=["en"], allowed_langs=["en", "zh", "bn"],
                                          partial_interval_ms=0, **cfg),
                            {"sherpa-zipformer": SherpaZipformerEngine(MODELS_DIR)}, lid_identify=lid.identify)
    decided = first_text = None
    for i in range(0, len(pcm), 1280):
        clock = (i + 1280) / 2 / 16000
        for m in s.feed(pcm[i:i + 1280]):
            if first_text is None and m["type"] in ("partial", "final") and m["lang_status"] == "confirmed":
                first_text = clock
        if decided is None and s.lang_status != "provisional":
            decided = clock
        if decided is not None and first_text is not None:
            break
    return s.lang, s.lang_status, decided, first_text


OLD_SCHEDULE = dict(lid_first_window_s=2.5)  # no early attempts: the full window only, as before


@pytest.mark.skipif(not (CLIP and Path(CLIP).exists() and has_zipformer("bn")), reason="ST_LID_CLIP_WAV not set")
def test_the_live_clip_is_confirmed_earlier_with_the_same_result(lid):
    """The clip starts with one short sentence and then two seconds without speech. Before:
    Bengali confirmed after 3.9 s of audio, and the first Bengali text with it. Now: under
    2 s (first attempt at 1.4 s, the second half a second later), the same language."""
    pcm = _pcm(Path(CLIP))
    before = _decision(lid, pcm, **OLD_SCHEDULE)
    after = _decision(lid, pcm)
    assert before[:2] == after[:2] == ("bn", "confirmed")
    assert before[2] > 3.5 and after[2] < 2.2, (before, after)
    # the first subtitle in the confirmed language: the clip's first sentence, replayed
    assert before[3] > 3.5 and after[3] < 2.2, (before, after)


@pytest.mark.parametrize("lang", ["zh", "bn"])
def test_sample_speech_in_another_language_than_the_first_guess_is_confirmed_earlier(lid, lang):
    if not has_zipformer(lang):
        pytest.skip(f"{lang} model not downloaded")
    pcm = _pcm(zipformer_dir(lang) / "test_wavs" / "0.wav")
    before, after = _decision(lid, pcm, **OLD_SCHEDULE), _decision(lid, pcm)
    assert before[:2] == after[:2] == (lang, "confirmed")
    assert after[2] <= before[2] - 0.9, (before, after)


@pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded")
def test_english_the_first_guess_is_still_confirmed_on_the_full_window_only(lid):
    pcm = _pcm(zipformer_dir("en") / "test_wavs" / "0.wav")
    before, after = _decision(lid, pcm, **OLD_SCHEDULE), _decision(lid, pcm)
    assert before[:3] == after[:3] and after[:2] == ("en", "confirmed")


def test_one_language_id_call_on_a_short_window_is_fast(lid):
    """The window is no longer padded to 30 s: a call on 2.5 s of audio took 0.14 s (0.86 s
    in the live runs) and takes about 0.015 s."""
    import time

    import numpy as np

    audio = (0.1 * np.sin(2 * np.pi * 220 * np.arange(int(16000 * 2.5)) / 16000)).astype("float32")
    lid.probabilities(audio)  # load
    t0 = time.perf_counter()
    for _ in range(5):
        lid.probabilities(audio)
    assert (time.perf_counter() - t0) / 5 < 0.08
