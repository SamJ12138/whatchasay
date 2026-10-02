"""English casing + punctuation before translation (Phase 3 Batch A;
docs/asr-input-quality.md): the English Zipformer's UPPERCASE, unpunctuated text
translates badly, so /ws/asr restores it first. Fast tests use a fake restorer and
never load or download the real model; the slow test runs the real one."""

import os
import statistics
import time
from pathlib import Path

import pytest

import app.asr as asr_pkg
from app.asr import punctuation
from app.asr.engine import AsrEvent
from app.asr.punctuation import Punctuator
from app.asr.session import SessionConfig, StreamingASRSession
from app.config import settings
from tests.conftest import MODELS_ROOT
from tests.fakes import pcm_frame


class OneFinal:
    """Engine session that emits one partial, then one final."""

    def __init__(self, lang, text):
        self.lang, self.text, self.n = lang, text, 0

    def feed(self, pcm16):
        self.n += 1
        kind = "final" if self.n == 2 else "partial"
        return [AsrEvent(kind, self.text, self.lang, 0.0, 0.04 * self.n, utterance_id=0)] if self.n <= 2 else []

    def flush(self):
        return []

    def close(self):
        pass


class Engine:
    name = "sherpa-zipformer"

    def __init__(self, text):
        self.text = text

    def supports(self, lang):
        return True

    def start_session(self, lang):
        return OneFinal(lang, self.text)


def fake_restore(text, lang, final):
    return text.lower().capitalize() + ("." if final else "") if lang == "en" else text


def run_session(text, lang, restore):
    s = StreamingASRSession(SessionConfig(source_lang=lang), {"sherpa-zipformer": Engine(text)}, restore_text=restore)
    out = s.feed(pcm_frame()) + s.feed(pcm_frame())
    return {m["type"]: m for m in out if m["type"] in ("partial", "final")}


def test_final_and_partial_text_is_restored_and_raw_text_kept():
    msgs = run_session("WHERE DID YOU PUT THE KEYS", "en", fake_restore)
    assert msgs["final"]["text"] == "Where did you put the keys."
    assert msgs["final"]["raw_text"] == "WHERE DID YOU PUT THE KEYS"
    assert msgs["partial"]["text"] == "Where did you put the keys"
    assert "raw_text" not in msgs["partial"]


def test_unchanged_text_has_no_raw_text():
    msgs = run_session("我五分钟后回来", "zh", fake_restore)
    assert msgs["final"]["text"] == "我五分钟后回来" and "raw_text" not in msgs["final"]


def test_a_failing_restorer_keeps_the_caption(obs_records):
    def boom(text, lang, final):
        raise RuntimeError("model crashed")

    msgs = run_session("HELLO THERE", "en", boom)
    assert msgs["final"]["text"] == "HELLO THERE"
    fails = [r for r in obs_records if r["stage"] == "punctuate" and r["event"] == "fail"]
    assert fails and fails[0]["context"]["degraded"] == "text kept as is"


def test_create_session_uses_the_package_restorer(monkeypatch):
    monkeypatch.setattr(asr_pkg, "_engines", {"sherpa-zipformer": Engine("HELLO")})
    monkeypatch.setattr(asr_pkg, "_lid_identify", lambda audio, allowed: None)
    monkeypatch.setattr(asr_pkg, "_restore_text", fake_restore)
    s = asr_pkg.create_session(source_lang="en", target_langs=["zh"])
    finals = [m for m in s.feed(pcm_frame()) + s.feed(pcm_frame()) if m["type"] == "final"]
    assert finals[0]["text"] == "Hello."


def test_punctuation_is_on_by_default_and_english_only():
    assert settings.asr.punctuation is True
    p = Punctuator(Path("nowhere"))
    assert p.supports("en") and not p.supports("zh") and not p.supports("bn")


def test_missing_model_passes_text_through_with_one_warning_and_no_download(monkeypatch, tmp_path, caplog, obs_records):
    def no_network(*a, **k):
        raise AssertionError("the backend must not download the punctuation model")

    monkeypatch.setattr(punctuation.urllib.request, "urlretrieve", no_network)
    p = Punctuator(tmp_path)
    with caplog.at_level("WARNING"):
        assert p.restore("WHERE DID YOU PUT THE KEYS", "en") == "WHERE DID YOU PUT THE KEYS"
        assert p.restore("HELLO", "en") == "HELLO"
    warnings = [r for r in caplog.records if "punctuation" in r.getMessage()]
    assert len(warnings) == 1 and "download_models.py" in warnings[0].getMessage()
    assert p.status()["available"] is False and p.status()["error"]
    assert [r for r in obs_records if r["stage"] == "startup" and r["event"] == "fail"
            and r["context"].get("component") == "punctuation"]


def test_punctuation_off_means_no_restorer(monkeypatch):
    monkeypatch.setattr(settings.asr, "punctuation", False)
    monkeypatch.setattr(asr_pkg, "_punctuator", None)
    assert asr_pkg.get_punctuator() is None
    assert asr_pkg._restore_text("HELLO", "en", True) == "HELLO"


def test_translator_receives_the_restored_text(app_env, monkeypatch):
    """/ws/asr: the final's restored text is what reaches the MT engine."""
    seen = []
    eng = app_env.engines["fake"]
    real = eng.translate_batch_sync

    def spy(texts, src, tgt):
        seen.extend(texts)
        return real(texts, src, tgt)

    monkeypatch.setattr(eng, "translate_batch_sync", spy)
    monkeypatch.setattr(asr_pkg, "_restore_text", fake_restore)
    app_env.asr.text = "WHERE DID YOU PUT THE KEYS"
    app_env.asr.numbered = False
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs="zh")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(10):
                ws.send_bytes(pcm_frame())
            while True:
                m = ws.receive_json()
                if m["type"] == "translation":
                    break
    assert seen == ["Where did you put the keys."], seen


PUNCT_DIR = MODELS_ROOT / "punct"


@pytest.mark.slow
@pytest.mark.skipif(not Punctuator(PUNCT_DIR).available(), reason="punctuation model not downloaded")
def test_real_model_restores_english_asr_text_under_50ms_p50():
    p = Punctuator(PUNCT_DIR)
    assert p.load(), p.status()
    cases = {
        "YET THESE THOUGHTS AFFECTED HESTER PRYNNE LESS WITH HOPE THAN APPREHENSION":
            "Yet these thoughts affected Hester Prynne less with hope than apprehension.",
        "I WILL BE BACK IN FIVE MINUTES PLEASE DO NOT TOUCH ANYTHING":
            "I will be back in five minutes. Please do not touch anything.",
    }
    ms = []
    for raw, want in cases.items():
        assert p.restore(raw, "en") == want
        for _ in range(20):
            t0 = time.perf_counter()
            p.restore(raw, "en", log=False)
            ms.append((time.perf_counter() - t0) * 1000)
    assert statistics.median(ms) < 50, ms
    assert p.restore("我五分钟后回来", "zh") == "我五分钟后回来"
