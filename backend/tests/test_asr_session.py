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
    # the first recognizer stays (the parallel ones, Mandarin and Bengali, are stopped)
    assert s.lang == "en" and s.lang_confirmed and s.asr is eng.sessions[0] and not eng.sessions[0].closed
    assert all(x.closed for x in eng.sessions[1:])


def test_manual_override_restarts_recognizer():
    eng = FakeEngine()
    s = StreamingASRSession(SessionConfig(source_lang="auto"), {"sherpa-zipformer": eng}, lid_identify=lambda a, b: None)
    out = s.set_language("zh")
    assert out[-1] == {"type": "lid", "lang": "zh", "source": "manual", "confirmed": True, "status": "manual"}
    assert s.lang == "zh" and s.asr is eng.sessions[-1] and eng.sessions[-1].lang == "zh"
    assert all(x.closed for x in eng.sessions[:-1])     # the first and the parallel recognizers


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


# ---------------------------------------------------------------- A9: the detection buffer is replayed in frames


class SizeRecordingSession(FakeSession):
    def __init__(self, lang):
        super().__init__(lang)
        self.feeds = []

    def feed(self, pcm16: bytes):
        self.feeds.append(len(pcm16) // 2)
        return super().feed(pcm16)


class SizeRecordingEngine(FakeEngine):
    def start_session(self, lang):
        s = SizeRecordingSession(lang)
        self.sessions.append(s)
        return s


def test_the_detection_buffer_is_replayed_in_40_ms_frames_and_no_frame_twice():
    """The recognizer looks at its endpoint once per feed(): replayed as one block, the
    buffered seconds could not end a sentence (observations A9). And the frame that
    triggered detection is part of the buffer: it must not be fed again after the replay."""
    eng = SizeRecordingEngine()
    s = StreamingASRSession(
        SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], lid_window_s=1.0, lid_min_rms=0.001, partial_interval_ms=0,
                      parallel_window_s=0),  # the replay path (a switch after the parallel window)
        {"sherpa-zipformer": eng}, lid_identify=lambda audio, allowed: "bn")
    frames = 0
    while s.lang != "bn":
        s.feed(loud_frame())
        frames += 1
    new = eng.sessions[1]
    assert new.feeds and max(new.feeds) <= 640, new.feeds[:5]
    assert sum(new.feeds) == frames * 640, (sum(new.feeds), frames * 640)
    s.feed(loud_frame())
    assert sum(new.feeds) == (frames + 1) * 640


def test_sentences_inside_the_replayed_buffer_come_out_as_their_own_finals():
    """FakeSession ends an utterance every 2 s of audio it is fed, if feed() is called
    there: 3 s of buffer replayed in frames must produce the final at 2 s."""
    eng = FakeEngine()
    s = StreamingASRSession(
        SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], lid_window_s=3.0, lid_first_window_s=3.0,
                      lid_min_rms=0.001, partial_interval_ms=0),
        {"sherpa-zipformer": eng}, lid_identify=lambda audio, allowed: "bn")
    msgs = []
    while s.lang != "bn":
        msgs.extend(s.feed(loud_frame()))
    after = msgs[[m["type"] for m in msgs].index("reset"):]
    replayed_finals = [m["text"] for m in after if m["type"] == "final" and m["lang"] == "bn"]
    assert replayed_finals and replayed_finals[0] == "bn final 1", replayed_finals


# ---------------------------------------------------------------- earlier language confirmation


def _early(lid, **kw):
    """Auto-detect with the real schedule: first attempt after 1 s of voiced audio, then
    every 0.5 s, the full window at 2.5 s. Returns (session, engine, the lid calls)."""
    calls = []

    def identify(audio, allowed):
        calls.append(round(len(audio) / SAMPLE_RATE, 2))
        return lid(calls[-1])

    eng = FakeEngine()
    kw.setdefault("lid_min_rms", 0.001)
    cfg = SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], partial_interval_ms=0, **kw)
    return StreamingASRSession(cfg, {"sherpa-zipformer": eng}, lid_identify=identify), eng, calls


def _feed_until_decided(s, max_frames=200):
    """-> (messages, seconds of audio fed when the language status left 'provisional')"""
    msgs = []
    for i in range(max_frames):
        msgs.extend(s.feed(loud_frame()))
        if s.lang_status != "provisional":
            return msgs, round((i + 1) * 0.04, 2)
    return msgs, None


def test_another_language_is_confirmed_early_when_two_attempts_in_a_row_agree():
    s, eng, calls = _early(lambda secs: "bn")
    msgs, decided_at = _feed_until_decided(s)
    assert s.lang == "bn" and s.lang_status == "confirmed"
    assert calls == [1.0, 1.52], calls            # 1 s of voiced audio, then 0.5 s more (40 ms frames)
    assert decided_at == 1.52                     # was 2.5 s
    lid = [m for m in msgs if m["type"] == "lid" and m.get("confirmed")]
    assert lid[0]["lang"] == "bn" and lid[0]["switched"] is True
    assert [m["type"] for m in msgs].count("reset") == 1


def test_one_early_answer_is_not_enough_to_switch():
    """A second of audio is weak evidence: the early answer has to come twice in a row."""
    s, eng, calls = _early(lambda secs: "zh" if secs < 1.2 else "en")
    msgs, decided_at = _feed_until_decided(s)
    assert s.lang == "en" and s.lang_status == "confirmed" and s.asr is eng.sessions[0]
    assert "reset" not in [m["type"] for m in msgs]
    assert decided_at == 2.52


def test_the_provisional_language_is_not_confirmed_before_the_full_window():
    """whisper-tiny answers "en" for noise and for the first second of other languages
    (docs/latency.md): English, the provisional language, is only confirmed on the full
    window, as before."""
    s, eng, calls = _early(lambda secs: "en")
    msgs, decided_at = _feed_until_decided(s)
    assert calls == [1.0, 1.52, 2.04, 2.52], calls
    assert decided_at == 2.52 and s.lang == "en" and s.lang_status == "confirmed"


def test_speech_that_starts_late_is_still_detected_right():
    # the first seconds are noise ("en"), then Bengali speech
    s, eng, calls = _early(lambda secs: "en" if secs < 1.8 else "bn")
    msgs, decided_at = _feed_until_decided(s)
    assert s.lang == "bn" and s.lang_status == "confirmed"
    assert decided_at == 2.52                     # bn at 2.0 s and again at the full window
    assert not [m for m in msgs if m["type"] == "lid" and m.get("confirmed") and m["lang"] == "en"]


def test_undecided_attempts_end_in_fallback_after_the_full_window_and_one_more():
    s, eng, calls = _early(lambda secs: None)
    msgs, decided_at = _feed_until_decided(s)
    assert s.lang_status == "fallback" and s.lang == "en"
    assert calls == [1.0, 1.52, 2.04, 2.52, 4.0], calls
    assert decided_at == 4.0


def quiet_frame(ms=40):
    return (np.zeros(int(SAMPLE_RATE * ms / 1000))).astype("<i2").tobytes()


def test_the_second_early_attempt_does_not_wait_for_more_speech():
    """Seen on the live clip: one sentence, then two seconds without speech. The second
    early attempt is due half a second of audio after the first, voiced or not; counted in
    voiced audio it came 1.5 s later and the first Bengali subtitle with it."""
    s, eng, calls = _early(lambda secs: "bn", lid_min_rms=0.004)
    decided = None
    for i in range(100):
        s.feed(loud_frame() if i < 25 else quiet_frame())   # 1 s of speech, then quiet
        if s.lang_status != "provisional":
            decided = round((i + 1) * 0.04, 2)
            break
    assert calls == [1.0, 1.52] and decided == 1.52 and s.lang == "bn"


def test_early_attempts_stop_after_their_number_when_no_more_speech_comes():
    s, eng, calls = _early(lambda secs: "en", lid_min_rms=0.004)
    for i in range(250):                                     # 1 s of speech, then 9 s of quiet
        s.feed(loud_frame() if i < 25 else quiet_frame())
    assert calls == [1.0, 1.52, 2.04], calls                 # three early attempts, then it waits for the full window
    assert s.lang_status == "provisional"


def test_early_attempts_can_be_switched_off():
    s, eng, calls = _early(lambda secs: "bn", lid_first_window_s=2.5)
    msgs, decided_at = _feed_until_decided(s)
    assert calls == [2.52] and decided_at == 2.52


def test_a_settings_change_does_not_restart_detection_of_an_auto_session():
    """The extension re-sends its config (source_lang "auto" included) when the user changes
    a setting, e.g. the target language: a confirmed session must stay confirmed."""
    s, eng, calls = _early(lambda secs: "en")
    _feed_until_decided(s)
    assert s.lang_status == "confirmed"
    assert s.set_language("auto") == []
    assert s.lang_status == "confirmed" and s.lang_confirmed
    n = len(calls)
    for _ in range(100):
        s.feed(loud_frame())
    assert len(calls) == n                        # detection did not run again

    # from a manual choice back to Auto-detect: detection does start again
    s.set_language("zh")
    assert s.lang_status == "manual"
    out = s.set_language("auto")
    assert out and out[0]["status"] == "provisional" and s.lang_status == "provisional"


def test_lid_schedule_defaults_and_wiring(monkeypatch):
    from app import asr as asr_pkg
    from app.config import ASRConfig, settings

    cfg = ASRConfig()
    assert (cfg.lid_first_window_s, cfg.lid_retry_step_s, cfg.lid_window_s, cfg.lid_min_confidence) == (1.0, 0.5, 2.5, 0.6)
    monkeypatch.setattr(asr_pkg, "_engines", {"sherpa-zipformer": FakeEngine()})
    monkeypatch.setattr(settings.asr, "lid_first_window_s", 1.25)
    monkeypatch.setattr(settings.asr, "lid_retry_step_s", 0.75)
    s = asr_pkg.create_session(source_lang="auto", target_langs=["en"])
    assert (s.config.lid_first_window_s, s.config.lid_retry_step_s) == (1.25, 0.75)
