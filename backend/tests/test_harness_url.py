"""The browser harness's --url mode (live test on a real page), its pure parts:
which origin the test copy is granted, when a page counts as blocked (consent wall,
bot check: the run stops, nothing is bypassed), which target the rule picks, and
how the overlay's lines are read and capped (never a full transcript)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_extension.py"
_spec = importlib.util.spec_from_file_location("e2e_extension", SCRIPT)
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def test_origin_pattern_is_the_page_origin():
    assert h.origin_pattern("https://www.youtube.com/watch?v=-tpVpbIxFmI") == "https://www.youtube.com/*"
    assert h.origin_pattern("http://127.0.0.1:8123/index.html") == "http://127.0.0.1/*"


def test_extension_copy_grants_the_extra_origin(tmp_path):
    ext = tmp_path / "ext"
    ext.mkdir()
    (ext / "manifest.json").write_text(json.dumps({"name": "x", "permissions": ["storage"]}), encoding="utf-8")
    dst = h.test_extension_copy(ext, tmp_path / "scratch", ["https://www.youtube.com/*"])
    m = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    assert "https://www.youtube.com/*" in m["host_permissions"]
    assert "http://127.0.0.1/*" in m["host_permissions"]
    assert "tabCapture" in m["permissions"]


@pytest.mark.parametrize("url,text,blocked", [
    ("https://consent.youtube.com/m?continue=x", "", True),
    ("https://consent.google.com/ml?continue=x", "", True),
    ("https://www.youtube.com/watch?v=x", "Sign in to confirm you're not a bot", True),
    ("https://www.google.com/sorry/index?continue=x", "unusual traffic from your computer network", True),
    ("https://www.youtube.com/watch?v=x", "Before you continue to YouTube", True),
    ("https://www.youtube.com/watch?v=x", "Ora Char Jon | Movie Scene  Subscribe  Share", False),
])
def test_block_reason(url, text, blocked):
    assert bool(h.block_reason(url, text)) is blocked


def test_auto_target_is_english_unless_the_speech_is_english():
    assert h.auto_target("bn") == "en"
    assert h.auto_target("zh") == "en"
    assert h.auto_target("en") == "zh"


def _el(cls, text=None, **data):
    attrs = ["class", cls] + [x for k, v in data.items() for x in (f"data-{k.replace('_', '-')}", v)]
    kids = [{"nodeType": 3, "nodeValue": text}] if text is not None else []
    return {"nodeType": 1, "nodeName": "DIV", "attributes": attrs, "children": kids}


def _overlay(*lines):
    stack = {"nodeType": 1, "nodeName": "DIV", "attributes": ["class", "subtitle-stack"], "children": list(lines)}
    shadow = {"nodeType": 11, "children": [{"nodeType": 1, "nodeName": "DIV", "attributes": ["class", "overlay-container"],
                                            "children": [stack]}]}
    return {"nodeType": 1, "nodeName": "DIV", "attributes": ["id", "subtitle-translator-host"], "shadowRoots": [shadow]}


def test_overlay_lines_reads_through_the_closed_shadow_root():
    node = _overlay(_el("subtitle-line primary", "I want ten thousand.", cue_id="asr_3", lang="en", type="primary"),
                    _el("subtitle-line original", "দশ হাজার চাই", cue_id="asr_3", type="original"),
                    _el("subtitle-line partial", "আর", cue_id="asr_4"))
    assert h.overlay_lines(node) == [
        {"type": "primary", "cue_id": "asr_3", "lang": "en", "text": "I want ten thousand."},
        {"type": "original", "cue_id": "asr_3", "lang": None, "text": "দশ হাজার চাই"},
        {"type": "partial", "cue_id": "asr_4", "lang": None, "text": "আর"},
    ]


def test_line_collector_keeps_at_most_n_translated_cues_and_first_seen_times():
    c = h.LineCollector(limit=2)
    c.add(1.0, [{"type": "partial", "cue_id": "asr_1", "lang": None, "text": "এক"}])
    c.add(2.0, [{"type": "original", "cue_id": "asr_1", "lang": None, "text": "এক দুই"}])
    c.add(2.5, [{"type": "primary", "cue_id": "asr_1", "lang": "en", "text": "One two"},
                {"type": "original", "cue_id": "asr_1", "lang": None, "text": "এক দুই"}])
    c.add(3.0, [{"type": "primary", "cue_id": "asr_1", "lang": "en", "text": "One, two."}])  # a revision replaces it
    for i, t in ((2, 4.0), (3, 5.0)):
        c.add(t, [{"type": "primary", "cue_id": f"asr_{i}", "lang": "en", "text": f"line {i}"},
                  {"type": "original", "cue_id": f"asr_{i}", "lang": None, "text": f"src {i}"}])
    assert c.first_caption_t == 1.0
    assert c.first_translation_t == 2.5
    assert c.lines() == [{"cue_id": "asr_1", "lang": "en", "original": "এক দুই", "translation": "One, two."},
                         {"cue_id": "asr_2", "lang": "en", "original": "src 2", "translation": "line 2"}]
    assert c.translated_cues == 3  # counted, not kept


def test_page_scripts_parse_as_javascript():
    """The --url page probes are built from Python strings; a stray brace once made every
    readiness check a SyntaxError that looked like 'no playable video'."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    for src in (h.PAGE_VIDEO_STATE_JS, h.PAGE_VIDEO_JS + ".play()"):
        r = subprocess.run([node, "-e", "new Function('return ' + process.argv[1])", src], capture_output=True, text=True)
        assert r.returncode == 0, (src, r.stderr[-500:])


def test_summary_line_is_ascii_whatever_the_console_code_page():
    """A GBK console (Chinese Windows) cannot print Bengali: the summary line escapes it."""
    line = h.summary_line({"page": {"title": "10,000 রাজভোগের Order"}, "lines": [{"translation": "我要一万"}]})
    assert line.isascii()
    assert json.loads(line)["page"]["title"] == "10,000 রাজভোগের Order"


def test_target_settings_use_the_settings_pages_keys():
    """The extension derives targetLanguages from primaryLang / secondaryLang on every read
    (background.js migrateSettings), so the harness sets those, like the Settings page."""
    assert h.target_settings(["en"]) == {"primaryLang": "en", "secondaryLang": "none", "targetLanguages": ["en"]}
    assert h.target_settings(["zh", "bn"]) == {"primaryLang": "zh", "secondaryLang": "bn", "targetLanguages": ["zh", "bn"]}
