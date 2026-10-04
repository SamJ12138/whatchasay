"""Parallel recognizers while the spoken language is not confirmed (docs/latency.md, "Language
prior"): every frame goes to all the allowed languages' recognizers for at most
asr.parallel_window_s of audio. The overlay shows the first recognizer's text (dimmed) as before;
at confirmation the confirmed recognizer's text is already there: the same recognizer's line is
re-sent confirmed (undimmed), another one takes over without replaying the buffered audio, and
the others stop. After the window only the first recognizer runs and a later switch replays the
buffer, as before."""

import pytest

from app.asr.engine import SAMPLE_RATE
from app.asr.session import SessionConfig, StreamingASRSession
from tests.test_asr_session import FakeEngine, loud_frame


def _session(answers, prior=None, **kw):
    calls = []

    def identify(audio, allowed):
        calls.append(round(len(audio) / SAMPLE_RATE, 2))
        return answers(calls[-1])

    eng = FakeEngine()
    kw.setdefault("lid_min_rms", 0.001)
    kw.setdefault("source_lang", "auto")
    cfg = SessionConfig(allowed_langs=["en", "zh", "bn"], partial_interval_ms=0, prior=prior or {}, **kw)
    return StreamingASRSession(cfg, {"sherpa-zipformer": eng}, lid_identify=identify), eng, calls


def _by_lang(eng):
    return {s.lang: s for s in eng.sessions}


def test_all_recognizers_hear_the_audio_until_confirmation():
    s, eng, _ = _session(lambda t: None, prior={"zh": 0.8, "en": 0.2})
    for _ in range(10):
        s.feed(loud_frame())
    rec = _by_lang(eng)
    assert sorted(rec) == ["bn", "en", "zh"]
    assert all(r.fed == 10 * 640 for r in rec.values())
    assert s.lang == "zh"


def test_only_the_first_recognizer_text_is_sent_before_confirmation():
    s, eng, _ = _session(lambda t: None, prior={"zh": 0.8, "en": 0.2})
    msgs = []
    for _ in range(20):
        msgs += s.feed(loud_frame())
    assert {m["lang"] for m in msgs if m["type"] in ("partial", "final")} == {"zh"}
    assert all(not m["confirmed"] for m in msgs if m["type"] in ("partial", "final"))


def test_a_wrong_prior_switches_without_replay_and_the_line_is_there_at_once():
    """The brief's case: prior wrong (zh), language ID confirms Bengali at about 1.2 s."""
    s, eng, calls = _session(lambda t: "bn", prior={"zh": 0.8, "en": 0.2}, lid_first_window_s=0.7)
    msgs, frames = [], 0
    while s.lang_status == "provisional" and frames < 100:
        out = s.feed(loud_frame())
        frames += 1
        msgs += out
    assert s.lang == "bn" and s.lang_status == "confirmed"
    assert frames * 0.04 == pytest.approx(1.24)                     # 0.72, then 1.24: two in a row
    # in the very feed that confirmed it: reset, lid, then the Bengali recognizer's text, confirmed
    types = [m["type"] for m in out]
    assert types[:2] == ["reset", "lid"] and out[1]["switched"] and out[1]["parallel"]
    shown = [m for m in out[2:] if m["type"] in ("partial", "final")]
    assert shown and all(m["lang"] == "bn" and m["confirmed"] for m in shown)
    # no audio decoded twice: the Bengali recognizer got each frame once, live
    rec = _by_lang(eng)
    assert rec["bn"].fed == frames * 640
    assert rec["zh"].closed and rec["en"].closed and not rec["bn"].closed
    assert len(eng.sessions) == 3                                  # no new recognizer for the switch


def test_the_backlog_carries_the_finals_heard_so_far_and_the_open_line():
    # FakeSession: a final every 2 s of audio; confirm at the full window (2.52 s)
    s, eng, _ = _session(lambda t: "bn" if t > 2.4 else None, prior={"zh": 0.8, "en": 0.2})
    out = []
    while s.lang_status == "provisional":
        out = s.feed(loud_frame())
    finals = [m for m in out if m["type"] == "final"]
    partials = [m for m in out if m["type"] == "partial"]
    # FakeSession writes a final on every frame from 2.00 to 2.16 s of audio: all five, in order
    assert [m["text"] for m in finals] == [f"bn final {i}" for i in range(1, 6)]
    assert len(partials) == 1 and partials[0]["text"].startswith("bn partial")   # only the newest


def test_prior_right_the_open_line_is_resent_confirmed():
    s, eng, _ = _session(lambda t: "zh", prior={"zh": 0.8, "en": 0.2})
    out = []
    while s.lang_status == "provisional":
        out = s.feed(loud_frame())
    lid = next(i for i, m in enumerate(out) if m["type"] == "lid")
    after = [m for m in out[lid + 1:] if m["type"] == "partial"]
    assert after and after[0]["lang"] == "zh" and after[0]["confirmed"]
    rec = _by_lang(eng)
    assert rec["en"].closed and rec["bn"].closed and not rec["zh"].closed
    assert "reset" not in [m["type"] for m in out]


def test_after_the_window_only_the_first_recognizer_runs_and_a_switch_replays():
    s, eng, _ = _session(lambda t: "bn" if t > 3.0 else None, prior={"zh": 0.8, "en": 0.2},
                         parallel_window_s=2.0)
    for _ in range(50):                                            # 2 s
        s.feed(loud_frame())
    rec = _by_lang(eng)
    assert rec["en"].closed and rec["bn"].closed and not rec["zh"].closed
    while s.lang_status == "provisional":
        out = s.feed(loud_frame())
    assert s.lang == "bn"
    assert len(eng.sessions) == 4                                  # a new Bengali recognizer, fed the buffer
    lid = next(m for m in out if m["type"] == "lid")
    assert lid["switched"] and not lid.get("parallel")


def test_the_window_can_be_switched_off():
    s, eng, _ = _session(lambda t: None, prior={"zh": 0.8}, parallel_window_s=0)
    s.feed(loud_frame())
    assert len(eng.sessions) == 1


def test_a_declared_language_runs_one_recognizer():
    s, eng, _ = _session(lambda t: None, source_lang="bn")
    s.feed(loud_frame())
    assert [x.lang for x in eng.sessions] == ["bn"]


def test_the_latency_clock_of_a_parallel_recognizer_starts_with_the_session():
    clock = iter(1000.0 + 0.04 * i for i in range(10_000))
    s, eng, _ = _session(lambda t: "bn", prior={"zh": 0.8, "en": 0.2}, lid_first_window_s=0.7)
    s._now = lambda: next(clock)
    out = []
    while s.lang_status == "provisional":
        out = s.feed(loud_frame())
    shown = [m for m in out if m["type"] in ("partial", "final")]
    # FakeSession reports t0 = 0: the first frame's arrival, not the moment of the switch
    assert shown[0]["w_first"] == pytest.approx(1000.0, abs=1e-3)


def test_parallel_settings_and_wiring(monkeypatch):
    from app import asr as asr_pkg
    from app.config import ASRConfig, settings

    assert ASRConfig().parallel_window_s == 5.0
    # one thread per recognizer: onnxruntime's pools spin between 40 ms frames, so two threads cost
    # 1.5 cores per stream at real time against 0.06 with one (docs/latency.md, "Language prior")
    assert ASRConfig().num_threads == 1
    monkeypatch.setattr(asr_pkg, "_engines", {"sherpa-zipformer": FakeEngine()})
    monkeypatch.setattr(settings.asr, "parallel_window_s", 3.0)
    s = asr_pkg.create_session(source_lang="auto", target_langs=["en"])
    assert s.config.parallel_window_s == 3.0
