"""StreamingASRSession logic with a fake engine (no models needed), plus a
real Zipformer streaming check when the model files are present."""

import time
from pathlib import Path
from typing import List

import numpy as np
import pytest

from app.asr.engine import AsrEvent, SAMPLE_RATE
from app.asr.session import SessionConfig, StreamingASRSession
from tests.conftest import MODELS_DIR, has_zipformer, zipformer_dir


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
    assert out[-1] == {"type": "lid", "lang": "zh", "source": "manual", "confirmed": True, "status": "manual"}
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
    eng = SherpaZipformerEngine(MODELS_DIR, num_threads=2)
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


# ---------------------------------------------------------------- Batch 4: honest LID status (A1-A3)


def _auto(lid, **kw):
    eng = FakeEngine()
    cfg = SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], lid_window_s=0.5,
                        lid_min_rms=0.001, partial_interval_ms=0, **kw)
    return StreamingASRSession(cfg, {"sherpa-zipformer": eng}, lid_identify=lid)


def _feed(s, n=80):
    msgs = []
    for _ in range(n):
        msgs.extend(s.feed(loud_frame()))
    return msgs


def test_captions_are_provisional_until_lid_confirms():
    s = _auto(lambda a, allowed: "en")
    start = s.startup_messages()
    assert start == [{"type": "lid", "lang": "en", "source": "provisional", "confirmed": False, "status": "provisional"}]
    first = s.feed(loud_frame())
    caps = [m for m in first if m["type"] in ("partial", "final")]
    assert caps and all(m["confirmed"] is False and m["lang_status"] == "provisional" for m in caps)
    msgs = _feed(s)
    lid = [m for m in msgs if m["type"] == "lid"]
    assert lid and lid[-1]["confirmed"] is True and lid[-1]["status"] == "confirmed"
    after = [m for m in msgs[msgs.index(lid[-1]):] if m["type"] in ("partial", "final")]
    assert after and all(m["confirmed"] is True and m["lang_status"] == "confirmed" for m in after)


def test_undecided_lid_is_reported_as_fallback_never_confirmed():
    s = _auto(lambda a, allowed: None)  # A2 / A3: nothing inside the allowed languages
    msgs = _feed(s, 120)
    lid = [m for m in msgs if m["type"] == "lid"]
    assert lid, "no lid message after LID gave up"
    assert all(m.get("confirmed") is not True for m in lid)
    assert lid[-1]["status"] == "fallback" and lid[-1]["source"] == "fallback" and lid[-1]["lang"] == "en"
    caps = [m for m in msgs if m["type"] in ("partial", "final")]
    assert caps and all(m["confirmed"] is False for m in caps)
    assert caps[-1]["lang_status"] == "fallback"
    assert s.status()["lang_confirmed"] is False


def test_lid_errors_are_reported_as_error_never_confirmed():
    def boom(audio, allowed):
        raise RuntimeError("whisper-tiny failed to load")

    s = _auto(boom)  # A1
    msgs = _feed(s, 120)
    lid = [m for m in msgs if m["type"] == "lid"]
    assert lid and all(m.get("confirmed") is not True for m in lid)
    assert lid[-1]["status"] == "error" and lid[-1]["error_type"] == "process"
    caps = [m for m in msgs if m["type"] in ("partial", "final")]
    assert caps[-1]["confirmed"] is False and caps[-1]["lang_status"] == "error"


def test_lid_model_missing_is_fallback_not_confirmed():
    s = _auto(None)
    msgs = _feed(s, 120)
    lid = [m for m in msgs if m["type"] == "lid"]
    assert lid and lid[-1]["status"] == "fallback" and lid[-1]["confirmed"] is False


def test_declared_language_is_manual():
    eng = FakeEngine()
    s = StreamingASRSession(SessionConfig(source_lang="bn"), {"sherpa-zipformer": eng}, lid_identify=None)
    caps = [m for m in s.feed(loud_frame()) if m["type"] in ("partial", "final")]
    assert caps and caps[0]["confirmed"] is True and caps[0]["lang_status"] == "manual"
