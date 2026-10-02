"""StreamingASRSession logic with a fake engine (no models needed), plus a
real Zipformer streaming check when the model files are present."""

import time
from pathlib import Path
from typing import List

import numpy as np
import pytest

from app.asr.engine import AsrEvent, SAMPLE_RATE
from app.asr.session import SessionConfig, StreamingASRSession
from tests.conftest import has_zipformer, zipformer_dir


class FakeSession:
    def __init__(self, lang):
        self.lang = lang
        self.fed = 0
        self.closed = False
        self.utt = 0

    def feed(self, pcm16: bytes) -> List[AsrEvent]:
        self.fed += len(pcm16) // 2
        secs = self.fed / SAMPLE_RATE
        # a partial every call, a final every 2 s of audio
        if int(secs * 10) % 20 == 0 and secs > 0:
            self.utt += 1
            return [AsrEvent("final", f"{self.lang} final {self.utt}", self.lang, 0, secs, utterance_id=self.utt)]
        return [AsrEvent("partial", f"{self.lang} partial {secs:.1f}", self.lang, 0, secs, utterance_id=self.utt)]

    def flush(self):
        return [AsrEvent("final", f"{self.lang} flushed", self.lang, 0, self.fed / SAMPLE_RATE)]

    def close(self):
        self.closed = True


class FakeEngine:
    name = "sherpa-zipformer"

    def __init__(self, langs=("en", "zh", "bn")):
        self.langs = set(langs)
        self.sessions: List[FakeSession] = []

    def supports(self, lang):
        return lang in self.langs

    def start_session(self, lang):
        s = FakeSession(lang)
        self.sessions.append(s)
        return s

    def status(self):
        return {}


def loud_frame(ms=40):
    n = int(SAMPLE_RATE * ms / 1000)
    return (np.sin(np.linspace(0, 200, n)) * 0.3 * 32767).astype("<i2").tobytes()


def test_explicit_language_starts_immediately_and_skips_lid():
    eng = FakeEngine()
    calls = []

    def lid(audio, allowed):
        calls.append(len(audio))
        return "bn"

    s = StreamingASRSession(SessionConfig(source_lang="en", lid_window_s=0.2), {"sherpa-zipformer": eng}, lid_identify=lid)
    assert s.lang == "en" and s.lang_confirmed
    msgs = []
    for _ in range(50):
        msgs.extend(s.feed(loud_frame()))
    assert calls == []  # LID never ran
    assert any(m["type"] == "partial" for m in msgs)
    assert all(m.get("lang") == "en" for m in msgs if m["type"] in ("partial", "final"))


def test_auto_mode_starts_provisionally_then_switches_and_replays():
    eng = FakeEngine()
    s = StreamingASRSession(
        SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], lid_window_s=0.5, lid_min_rms=0.001, partial_interval_ms=0),
        {"sherpa-zipformer": eng},
        lid_identify=lambda audio, allowed: "bn",
    )
    assert s.lang == "en" and not s.lang_confirmed  # provisional
    msgs = []
    for _ in range(40):  # 1.6 s
        msgs.extend(s.feed(loud_frame()))
    types = [m["type"] for m in msgs]
    assert types[0] == "lid" and msgs[0]["source"] == "provisional"
    # captions were flowing before LID finished
    first_partial = types.index("partial")
    reset_idx = types.index("reset")
    assert first_partial < reset_idx
    lid_msgs = [m for m in msgs if m["type"] == "lid" and m.get("confirmed")]
    assert lid_msgs and lid_msgs[0]["lang"] == "bn" and lid_msgs[0].get("switched")
    assert s.lang == "bn" and s.lang_confirmed
    assert eng.sessions[0].closed  # provisional recognizer closed
    # replayed audio reached the new recognizer (fed > frames after switch)
    assert eng.sessions[1].fed > 0
    assert s.stats["lid_switches"] == 1


def test_auto_mode_confirms_without_reset_when_guess_is_right():
    eng = FakeEngine()
    s = StreamingASRSession(
        SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], lid_window_s=0.5, lid_min_rms=0.001),
        {"sherpa-zipformer": eng},
        lid_identify=lambda audio, allowed: "en",
    )
    msgs = []
    for _ in range(40):
        msgs.extend(s.feed(loud_frame()))
    assert "reset" not in [m["type"] for m in msgs]
    assert s.lang == "en" and s.lang_confirmed and len(eng.sessions) == 1


def test_manual_override_restarts_recognizer():
    eng = FakeEngine()
    s = StreamingASRSession(SessionConfig(source_lang="auto"), {"sherpa-zipformer": eng}, lid_identify=lambda a, b: None)
    out = s.set_language("zh")
    assert out[-1] == {"type": "lid", "lang": "zh", "source": "manual", "confirmed": True}
    assert s.lang == "zh" and len(eng.sessions) == 2


@pytest.mark.slow
@pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded")
def test_real_zipformer_streams_partials_before_end():
    import soundfile as sf
    from app.asr.sherpa_engine import SherpaZipformerEngine

    wav = sorted((zipformer_dir("en") / "test_wavs").glob("*.wav"))[0]
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    assert sr == 16000
    eng = SherpaZipformerEngine(Path("data/models/asr"), num_threads=2)
    sess = eng.start_session("en")
    frame = int(sr * 0.04)
    first_partial_at = None
    feed_ms = []
    for i in range(0, len(audio), frame):
        pcm = (np.clip(audio[i:i + frame], -1, 1) * 32767).astype("<i2").tobytes()
        t = time.perf_counter()
        evs = sess.feed(pcm)
        feed_ms.append((time.perf_counter() - t) * 1000)
        if evs and first_partial_at is None:
            first_partial_at = (i + frame) / sr
    finals = sess.flush()
    text = " ".join(e.text for e in finals).upper()
    assert first_partial_at is not None and first_partial_at < 2.0
    assert "NIGHTFALL" in text or "LAMPS" in text
    # streaming budget: processing a 40 ms frame must take well under 40 ms
    assert sorted(feed_ms)[len(feed_ms) // 2] < 40
