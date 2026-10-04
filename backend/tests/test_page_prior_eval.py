"""backend/scripts/page_prior_eval.py, its pure parts: which language a script, a
language code and each page signal imply, read from saved HTML (no network)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "page_prior_eval.py"
_spec = importlib.util.spec_from_file_location("page_prior_eval", SCRIPT)
pp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pp)


@pytest.mark.parametrize("text,any_rule,majority", [
    ("10,000 রাজভোগের Order | Ora Char Jon | Movie Scene | Prosenjit", "bn", "en"),
    ("File:台湾准备迎战强台风.webm - Wikimedia Commons", "zh", "en"),
    ("习近平抵达华盛顿对美国进行国事访问 | CCTV", "zh", "zh"),
    ("Why is Trump threatening a US diesel export ban? | BBC News", "en", "en"),
    ("Streettalk Ep.1 高 - Mandarin from the Streets", "en", "en"),  # one Han character is not enough
    ("", None, None),
    ("2026 | 12:30", None, None),
])
def test_script_language(text, any_rule, majority):
    assert pp.script_language(text, rule="any") == any_rule
    assert pp.script_language(text, rule="majority") == majority


@pytest.mark.parametrize("code,lang", [
    ("en", "en"), ("en-US", "en"), ("en_GB", "en"), ("zh-Hans", "zh"), ("zh_CN", "zh"), ("cmn", "zh"),
    ("bn", "bn"), ("bn-IN", "bn"), ("ben", "bn"), ("yue", None), ("hi", None), ("", None), (None, None),
])
def test_lang_from_code(code, lang):
    assert pp.lang_from_code(code) == lang


def _yt_page(player: dict, title="T - YouTube", html_lang="en", extra_head=""):
    return (f'<!doctype html><html lang="{html_lang}"><head><title>{title}</title>{extra_head}</head><body>'
            f"<script>var ytInitialPlayerResponse = {json.dumps(player, ensure_ascii=False)};var meta = 1;</script>"
            "</body></html>")


def test_youtube_signals_from_the_player_response():
    player = {
        "videoDetails": {"videoId": "x", "title": "10,000 রাজভোগের Order | Movie Scene", "author": "Bengali Movies",
                         "shortDescription": "Watch the Movie Scene from the Bengali Movie"},
        "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
            {"languageCode": "bn", "kind": "asr"}, {"languageCode": "en"}]}},
        "streamingData": {"adaptiveFormats": [
            {"mimeType": "audio/mp4", "audioTrack": {"id": "bn.4", "audioIsDefault": True}},
            {"mimeType": "audio/mp4", "audioTrack": {"id": "en.3", "audioIsDefault": False}}]},
    }
    sig = pp.extract_signals(_yt_page(player), "https://www.youtube.com/watch?v=x")
    assert sig["yt_caption_asr"]["lang"] == "bn"
    assert sig["yt_caption_tracks"]["value"] == ["en"] and sig["yt_caption_tracks"]["lang"] == "en"
    assert sig["yt_audio_track"]["lang"] == "bn"
    assert sig["yt_default_audio_language"]["value"] is None
    assert sig["title_script"]["lang"] == "bn"            # the player's title, not <title>
    assert sig["title_script_majority"]["lang"] == "en"
    assert sig["channel_script"]["lang"] == "en"
    assert sig["description_script"]["lang"] == "en"
    assert sig["page_script"]["lang"] == "bn"
    assert sig["html_lang"]["lang"] == "en"
    assert sig["og_locale"]["value"] is None


def test_page_script_takes_the_first_non_latin_text():
    player = {"videoDetails": {"title": "VOA Bangla Live Stream", "author": "VOA বাংলা", "shortDescription": ""}}
    head = '<meta property="og:description" content="Enjoy the videos and music you love, upload original content">'
    sig = pp.extract_signals(_yt_page(player, extra_head=head), "https://www.youtube.com/watch?v=x")
    assert sig["title_script"]["lang"] == "en"
    assert sig["page_script"]["lang"] == "bn"
    # an empty description is not replaced by YouTube's generic og:description
    assert sig["description_script"]["value"] is None and sig["description_script"]["lang"] is None
    latin = pp.extract_signals(_yt_page({"videoDetails": {"title": "Why is Trump threatening a ban", "author": "BBC News"}}),
                               "https://www.youtube.com/watch?v=y")
    assert latin["page_script"] == {"value": "latin", "lang": "en"}


def test_caption_tracks_in_several_languages_imply_nothing():
    player = {"videoDetails": {"title": "a"}, "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
        {"languageCode": "en"}, {"languageCode": "zh-Hans"}]}}}
    sig = pp.extract_signals(_yt_page(player), "https://www.youtube.com/watch?v=x")
    assert sorted(sig["yt_caption_tracks"]["value"]) == ["en", "zh-Hans"]
    assert sig["yt_caption_tracks"]["lang"] is None
    assert sig["yt_caption_asr"]["value"] is None


def test_generic_page_signals():
    html = ('<html lang="zh-CN"><head><meta property="og:locale" content="zh_CN">'
            '<meta property="og:title" content="台湾准备迎战强台风">'
            '<meta name="description" content="台湾正在准备迎战一场强台风"><title>新闻 - 美国之音</title></head></html>')
    sig = pp.extract_signals(html, "https://example.org/a")
    assert sig["html_lang"]["lang"] == "zh"
    assert sig["og_locale"]["lang"] == "zh"
    assert sig["title_script"]["lang"] == "zh"
    assert sig["description_script"]["lang"] == "zh"
    assert "yt_caption_asr" not in sig


def test_tally_counts_matches_per_signal():
    rows = [
        {"spoken": "bn", "signals": {"a": {"lang": "bn"}, "b": {"lang": "en"}}},
        {"spoken": "zh", "signals": {"a": {"lang": None}, "b": {"lang": "zh"}}},
        {"spoken": "en", "signals": {"a": {"lang": "en"}}},
    ]
    t = pp.tally(rows)
    assert t["a"] == {"pages": 3, "present": 2, "right": 2, "wrong": 0}
    assert t["b"] == {"pages": 2, "present": 2, "right": 1, "wrong": 1}  # not on the third page at all
