"""T12: language detection with a session hint.

Rule (settings.lang_detect): a distinctive script decides first (CJK, kana,
Hangul, Bengali, Devanagari, ...). Latin-script text shorter than min_chars is
never guessed by langdetect: it takes the session hint. Longer text takes
langdetect's answer only when its probability is >= confidence_floor and the
language is in candidate_languages; otherwise the session hint. No hint ->
default_hint.
"""

import pytest

from app.config import settings
from app.models import SubtitleCue
from app.translation.language_detection import LanguageDetector, detect_language

# the three sentences of the e2e runs (HTTP /translate probes, one per first-class language)
E2E = [
    ("Where did you put the keys?", "en"),
    ("我五分钟后回来，什么都别碰。", "zh"),
    ("আমি পাঁচ মিনিটের মধ্যে ফিরে আসব।", "bn"),
]
SHORT_ENGLISH = ["I know.", "Let's go!", "Where are you?", "Thank you so much.", "Come here now."]


def test_defaults_are_configured():
    cfg = settings.lang_detect
    assert cfg.min_chars >= 20
    assert 0.5 < cfg.confidence_floor <= 1.0
    assert {"en", "zh", "bn"} <= set(cfg.candidate_languages)
    assert "tl" not in cfg.candidate_languages
    assert cfg.default_hint == "en"


def test_it_is_raining_again_is_english_in_an_english_session():
    assert detect_language("It is raining again.", "en")[0] == "en"
    assert LanguageDetector().detect("It is raining again.", hint="en")[0] == "en"


@pytest.mark.parametrize("text,lang", E2E)
def test_e2e_sentences(text, lang):
    assert detect_language(text, "en")[0] == lang


@pytest.mark.parametrize("text", SHORT_ENGLISH)
def test_short_english_sentences(text):
    assert detect_language(text, "en")[0] == "en"


def test_hint_decides_short_latin_text_only():
    # a short line follows the session language ...
    assert detect_language("Let's go!", "fr")[0] == "fr"
    # ... but a long, confidently French line is French even in an English session
    assert detect_language("Je ne sais pas où sont les clés de la voiture.", "en")[0] == "fr"
    # and a script beats the hint
    assert detect_language("我五分钟后回来", "en")[0] == "zh"


def test_language_outside_candidates_falls_back_to_hint():
    # langdetect says Tagalog (0.71) for this one; tl is not a candidate
    assert detect_language("It is raining again.", None)[0] == settings.lang_detect.default_hint


@pytest.mark.asyncio
async def test_pipeline_uses_the_cue_hint(app_env):
    c = SubtitleCue(text="It is raining again.", start_time=0, end_time=1, lang_hint="en")
    res = await app_env.pipeline.translate_cue(c, ["zh", "bn"])
    assert res.source_lang == "en"
    assert all(t.status == "ok" for t in res.translations.values())


def test_ws_session_hint_from_config(app_env):
    with app_env.client() as c, c.websocket_connect(app_env.ws_url("/ws")) as ws:
        ws.send_json({"type": "config", "correlation_id": "cfg", "payload": {"source_lang_hint": "en"}})
        assert ws.receive_json()["type"] == "result"
        ws.send_json({"type": "cue", "correlation_id": "c1",
                      "payload": {"cue": {"text": "It is raining again.", "start_time": 0, "end_time": 1},
                                  "target_languages": ["zh"]}})
        m = ws.receive_json()
    assert m["payload"]["source_lang"] == "en"
    assert m["payload"]["translations"]["zh"]["status"] == "ok"
