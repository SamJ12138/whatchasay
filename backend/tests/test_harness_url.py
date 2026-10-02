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
        {"type": "primary", "cue_id": "asr_3", "lang": "en", "text": "I want ten thousand.", "dimmed": False},
        {"type": "original", "cue_id": "asr_3", "lang": None, "text": "দশ হাজার চাই", "dimmed": False},
        {"type": "partial", "cue_id": "asr_4", "lang": None, "text": "আর", "dimmed": False},
    ]
    assert h.overlay_label(node) is None


def test_overlay_lines_tell_dimmed_provisional_text_and_the_label():
    node = _overlay(_el("subtitle-line original provisional", "A cut the rush book.", cue_id="asr_0", type="original"),
                    _el("subtitle-line partial provisional", "they call", cue_id="asr_1"),
                    _el("subtitle-line notice lang-pending", "Detecting language…"))
    assert [(ln["type"], ln["dimmed"]) for ln in h.overlay_lines(node)] == [("original", True), ("partial", True)]
    assert h.overlay_label(node) == "Detecting language…"


def test_script_of_names_the_writing_system_not_the_text():
    assert h.script_of("একটা রাজবুক দেখা তো") == "bengali"
    assert h.script_of("A cut the rush book.") == "latin"
    assert h.script_of("你把钥匙放在哪里了?") == "han"
    assert h.script_of("... 12") is None


def _ln(kind, text, dimmed, cue="asr_0"):
    return {"type": kind, "cue_id": cue, "lang": None, "text": text, "dimmed": dimmed}


def test_dimming_log_passes_when_the_first_undimmed_line_comes_after_confirmation():
    d = h.DimmingLog()
    d.add(0.5, [], "Detecting language…", confirmed=False)
    d.add(1.0, [_ln("partial", "a cut the", True)], "Detecting language…", confirmed=False)
    d.add(1.5, [_ln("original", "A cut the rush book.", True)], "Detecting language…", confirmed=False)
    d.add(5.5, [_ln("partial", "একটা", False)], None, confirmed=True)
    d.add(6.0, [_ln("primary", "A royal book.", False), _ln("original", "একটা রাজবুক দেখা তো", False)], None, confirmed=True)
    s = d.summary()
    assert s["samples"] == 5 and s["dimmed_samples"] == 2 and s["dimmed_scripts"] == ["latin"]
    assert s["first_undimmed"] == {"t": 5.5, "type": "partial", "cue_id": "asr_0", "script": "bengali"}
    assert s["confirmed_first_seen_s"] == 5.5
    assert s["violations"] == []
    assert "একটা" not in json.dumps(s, ensure_ascii=False)  # scripts and times, never the text


@pytest.mark.parametrize("sample,violation", [
    (([_ln("partial", "a cut the", False)], "Detecting language…", False), "undimmed_before_confirmed"),
    (([_ln("partial", "a cut the", True)], None, False), "dimmed_without_label"),
    (([_ln("partial", "a cut the", True)], None, True), "dimmed_after_confirmed"),
    (([_ln("partial", "একটা", False)], "Detecting language…", True), "label_after_confirmed"),
])
def test_dimming_log_names_each_violation(sample, violation):
    d = h.DimmingLog()
    lines, label, confirmed = sample
    d.add(1.0, lines, label, confirmed=confirmed)
    assert [v["kind"] for v in d.summary()["violations"]] == [violation]


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


def test_correction_target_is_the_translated_cue_on_screen():
    """What Alt+E will edit: the cue whose translation is on screen, with its original."""
    lines = [{"type": "primary", "cue_id": "asr_0", "lang": "en", "text": "A royal book.", "dimmed": False},
             {"type": "original", "cue_id": "asr_0", "lang": None, "text": "একটা রাজবুক দেখা তো", "dimmed": False},
             {"type": "partial", "cue_id": "asr_1", "lang": None, "text": "এর থেকে", "dimmed": False}]
    assert h.correction_target(lines) == {"cue_id": "asr_0", "lang": "en", "original": "একটা রাজবুক দেখা তো",
                                          "translation": "A royal book."}
    assert h.correction_target(lines[2:]) is None                      # nothing translated on screen
    assert h.correction_target([dict(ln, dimmed=True) for ln in lines]) is None  # provisional lines are not offered


def test_kept_translation_memory_is_never_the_users_own(tmp_path):
    """--tm keeps a correction between two harness runs; backend/data is refused."""
    assert h.harness_tm_path(tmp_path / "live.db") == (tmp_path / "live.db").resolve()
    for bad in (h.BACKEND / "data" / "translation_memory.db", h.BACKEND / "data" / "x" / "tm.db", h.BACKEND / "data"):
        with pytest.raises(ValueError):
            h.harness_tm_path(bad)


@pytest.mark.parametrize("before,after,elapsed,ok", [
    ({"muted": False, "paused": False, "t": 12.0, "loop": False}, {"muted": False, "paused": False, "t": 14.1, "loop": False}, 2.0, True),
    ({"muted": False, "paused": False, "t": 12.0, "loop": False}, {"muted": True, "paused": False, "t": 14.0, "loop": False}, 2.0, False),   # m
    ({"muted": False, "paused": False, "t": 12.0, "loop": False}, {"muted": False, "paused": True, "t": 12.4, "loop": False}, 2.0, False),   # space
    ({"muted": False, "paused": False, "t": 12.0, "loop": False}, {"muted": False, "paused": False, "t": 4.0, "loop": False}, 2.0, False),   # j
    ({"muted": False, "paused": False, "t": 6.1, "loop": True}, {"muted": False, "paused": False, "t": 1.5, "loop": True}, 2.0, True),       # wrapped
    (None, {"muted": False, "paused": False, "t": 1.0, "loop": False}, 2.0, False),
])
def test_page_untouched_by_typing_a_correction(before, after, elapsed, ok):
    assert h.page_untouched(before, after, elapsed) is ok

