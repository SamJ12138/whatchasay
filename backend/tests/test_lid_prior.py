"""The language prior (docs/page-prior.md): what the page and the extension's channel memory
say about the spoken language before any audio. The session's first recognizer is the prior's
favourite when its probability reaches asr.lid_prior_threshold; language ID still runs:

- confirming the favoured language needs one attempt above the floor (English: above
  asr.lid_prior_floor_en, because whisper-tiny answers "en" with up to 0.94 on the first second
  of Mandarin and Bengali speech);
- switching away from it needs two early attempts in a row, as today, and English is accepted
  only from the full window on, as today (the same whisper-tiny habit);
- no prior, or one under the threshold: exactly today's behaviour.

Fake LID sequences: each attempt's answer is scripted by the seconds of audio it saw."""

import pytest

from app.asr.engine import SAMPLE_RATE
from app.asr.session import SessionConfig, StreamingASRSession, parse_prior
from tests.test_asr_session import FakeEngine, loud_frame


def _session(answers, prior=None, **kw):
    """answers(seconds) -> lang | (lang, confidence) | None. Returns (session, engine, calls)."""
    calls = []

    def identify(audio, allowed):
        calls.append(round(len(audio) / SAMPLE_RATE, 2))
        return answers(calls[-1])

    eng = FakeEngine()
    kw.setdefault("lid_min_rms", 0.001)
    kw.setdefault("source_lang", "auto")
    cfg = SessionConfig(allowed_langs=["en", "zh", "bn"], partial_interval_ms=0, prior=prior or {}, **kw)
    return StreamingASRSession(cfg, {"sherpa-zipformer": eng}, lid_identify=identify), eng, calls


def _run(s, max_frames=200):
    """-> (messages, seconds of audio fed when the status left 'provisional')"""
    msgs = list(s.startup_messages())
    for i in range(max_frames):
        msgs.extend(s.feed(loud_frame()))
        if s.lang_status != "provisional":
            return msgs, round((i + 1) * 0.04, 2)
    return msgs, None


def _types(msgs):
    return [m["type"] for m in msgs]


# ------------------------------------------------------------------ the first recognizer


def test_the_first_recognizer_is_the_prior_favourite():
    s, eng, _ = _session(lambda t: None, prior={"en": 0.1, "zh": 0.15, "bn": 0.75})
    assert s.lang == "bn" and eng.sessions[0].lang == "bn" and s.lang_status == "provisional"
    first = s.startup_messages()[0]
    assert first["type"] == "lid" and first["lang"] == "bn" and first["status"] == "provisional"
    assert first["source"] == "prior" and first["prior"] == pytest.approx(0.75)


def test_a_prior_under_the_threshold_is_not_used():
    s, eng, _ = _session(lambda t: None, prior={"en": 0.3, "zh": 0.15, "bn": 0.55})
    assert s.lang == "en" and s.startup_messages()[0]["source"] == "provisional"


def test_a_prior_for_a_language_without_a_recognizer_is_not_used():
    eng = FakeEngine(langs=("en", "zh"))
    cfg = SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], prior={"bn": 0.9, "en": 0.1})
    s = StreamingASRSession(cfg, {"sherpa-zipformer": eng}, lid_identify=lambda a, b: None)
    assert s.lang == "en"


def test_a_declared_language_ignores_the_prior():
    s, eng, calls = _session(lambda t: "bn", prior={"zh": 0.9}, source_lang="en")
    _run(s, 100)
    assert s.lang == "en" and s.lang_status == "manual" and calls == []


# ------------------------------------------------------------------ prior right


@pytest.mark.parametrize("lang", ["zh", "bn"])
def test_prior_right_is_confirmed_at_the_first_attempt(lang):
    s, eng, calls = _session(lambda t: lang, prior={lang: 0.8, "en": 0.2})
    msgs, decided = _run(s)
    assert calls == [1.0] and decided == 1.0                       # today: 1.52 (two attempts)
    assert s.lang == lang and s.lang_status == "confirmed" and s.asr is eng.sessions[0]
    assert "reset" not in _types(msgs)
    confirmed = [m for m in msgs if m["type"] == "lid" and m.get("confirmed")]
    assert confirmed[0]["lang"] == lang and not confirmed[0].get("switched")


def test_prior_right_english_is_confirmed_at_the_first_attempt_when_sure():
    s, eng, calls = _session(lambda t: ("en", 0.99), prior={"en": 0.8, "zh": 0.2})
    msgs, decided = _run(s)
    assert decided == 1.0 and s.lang == "en" and s.lang_status == "confirmed"   # today: 2.52


def test_an_english_answer_under_the_english_floor_does_not_confirm_early():
    """whisper-tiny said "en" at 0.84-0.94 on the first second of Mandarin and Bengali clips
    (docs/page-prior.md): an English prior is confirmed early only above lid_prior_floor_en."""
    s, eng, calls = _session(lambda t: ("en", 0.9), prior={"en": 0.8, "zh": 0.2})
    msgs, decided = _run(s)
    assert decided == 2.52 and s.lang == "en" and s.lang_status == "confirmed"  # the full window, as today


def test_a_wrong_first_answer_then_the_prior_language_confirms_it_at_once():
    # whisper-tiny's "en" on the first second of Mandarin, then "zh": one attempt is enough
    s, eng, calls = _session(lambda t: "en" if t < 1.2 else "zh", prior={"zh": 0.8, "en": 0.2})
    msgs, decided = _run(s)
    assert calls == [1.0, 1.52] and decided == 1.52 and s.lang == "zh" and s.lang_status == "confirmed"
    assert "reset" not in _types(msgs)


# ------------------------------------------------------------------ prior wrong


def test_prior_wrong_switches_as_today_on_two_agreeing_attempts():
    s, eng, calls = _session(lambda t: "bn", prior={"zh": 0.8, "en": 0.2})
    msgs, decided = _run(s)
    assert calls == [1.0, 1.52] and decided == 1.52
    assert s.lang == "bn" and s.lang_status == "confirmed" and _types(msgs).count("reset") == 1


def test_prior_wrong_one_early_answer_is_not_enough_to_switch():
    s, eng, calls = _session(lambda t: "bn" if t < 1.2 else None, prior={"zh": 0.8, "en": 0.2})
    msgs, decided = _run(s)
    assert s.lang_status == "fallback" and s.lang == "zh"
    assert "reset" not in _types(msgs)


def test_prior_wrong_english_speech_switches_on_the_full_window_as_today():
    """English is never accepted early as a switch: whisper-tiny's "en" on the first second of
    Mandarin would otherwise throw away a right Mandarin prior (zh sample, 6 s in: en 0.94, 0.90)."""
    s, eng, calls = _session(lambda t: ("en", 0.99), prior={"zh": 0.8, "en": 0.2})
    msgs, decided = _run(s)
    assert decided == 2.52 and s.lang == "en" and s.lang_status == "confirmed"
    assert _types(msgs).count("reset") == 1


def test_prior_right_survives_two_early_english_answers():
    s, eng, calls = _session(lambda t: "en" if t < 1.6 else "zh", prior={"zh": 0.8, "en": 0.2})
    msgs, decided = _run(s)
    assert s.lang == "zh" and s.lang_status == "confirmed" and decided == 2.04
    assert "reset" not in _types(msgs)


# ------------------------------------------------------------------ prior absent: today


SCRIPTS = {
    "bn": lambda t: "bn",
    "en": lambda t: "en",
    "zh then en": lambda t: "zh" if t < 1.2 else "en",
    "noise then bn": lambda t: "en" if t < 1.8 else "bn",
    "undecided": lambda t: None,
}


@pytest.mark.parametrize("name", list(SCRIPTS))
@pytest.mark.parametrize("prior", [None, {}, {"bn": 0.5, "en": 0.5}])
def test_without_a_usable_prior_the_session_behaves_as_today(name, prior):
    def run(p):
        s, eng, calls = _session(SCRIPTS[name], prior=p)
        msgs, decided = _run(s)
        strip = [{k: v for k, v in m.items() if k not in ("server_ts", "w_first", "w_last", "w_session", "lid_ms")}
                 for m in msgs]
        return strip, decided, calls, s.lang, s.lang_status

    assert run(prior) == run(None)


# ------------------------------------------------------------------ the prior on the wire


@pytest.mark.parametrize("raw,expected", [
    ("en:0.1,zh:0.8,bn:0.1", {"en": 0.1, "zh": 0.8, "bn": 0.1}),
    ("zh:0.8", {"zh": 0.8}),
    ("en:0.937,zh:0.032,bn:0.032", {"en": 0.937, "zh": 0.032, "bn": 0.032}),   # rounded by the extension: 1.001
    ("en:0.6,zh:0.6", {}),                      # not a distribution
    ("zh:0.8,xx:0.5", {"zh": 0.8}),             # unknown languages are dropped
    ("zh:1.7", {}),                             # not a probability
    ("zh:nan", {}),
    ("garbage", {}),
    ("", {}),
    (None, {}),
])
def test_parse_prior(raw, expected):
    assert parse_prior(raw, ["en", "zh", "bn"]) == expected


def test_prior_settings_and_wiring(monkeypatch):
    from app import asr as asr_pkg
    from app.config import ASRConfig, settings

    cfg = ASRConfig()
    assert (cfg.lid_prior_threshold, cfg.lid_prior_floor_en) == (0.6, 0.97)
    monkeypatch.setattr(asr_pkg, "_engines", {"sherpa-zipformer": FakeEngine()})
    monkeypatch.setattr(settings.asr, "lid_prior_threshold", 0.7)
    s = asr_pkg.create_session(source_lang="auto", target_langs=["en"], prior={"zh": 0.75})
    assert s.config.prior == {"zh": 0.75} and s.config.lid_prior_threshold == 0.7 and s.lang == "zh"


def test_ws_asr_reads_the_prior_from_its_query(app_env):
    """The extension sends the prior in the /ws/asr handshake (the session is created there);
    the provisional lid message names it."""
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="auto", target_langs="en",
                                                prior="en:0.1,zh:0.1,bn:0.8")) as ws:
            ready = ws.receive_json()
            assert ready["type"] == "ready" and ready["status"]["lang"] == "bn"
            lid = ws.receive_json()
            assert lid["type"] == "lid" and lid["lang"] == "bn" and lid["source"] == "prior"
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="auto", target_langs="en", prior="bn:2")) as ws:
            assert ws.receive_json()["status"]["lang"] == "en"      # not a probability: ignored
