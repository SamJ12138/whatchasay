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
    for src in (h.PAGE_VIDEO_STATE_JS, h.PAGE_VIDEO_JS + ".play()", h.PRIME_AUDIO_JS):
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



def test_line_latency_summary_from_the_content_scripts_records():
    """--lines: the summary keeps the per-line latencies the content script logged
    (stage line_latency, docs/latency.md) and when the first confirmed-language subtitle
    was drawn, counted from the moment the harness started the video."""
    def rec(**ctx):
        return {"stage": "line_latency", "event": "success", "context": ctx}

    records = [
        rec(kind="line", cue_id="asr_0", lang="bn", first_display_ms=480, final_ms=760, first_translation_ms=3100, draft_shown=True,
            drafts=4, translation_revisions=3, non_append_revisions=1),
        {"stage": "ext_render", "event": "success", "context": {"kind": "final"}},
        rec(kind="first_confirmed", lang="bn", since_session_ms=5450, at_ms=1700000005300),
        rec(kind="line", cue_id="asr_1", lang="bn", first_display_ms=900, final_ms=1200, first_translation_ms=None, draft_shown=False),
    ]
    out = h.line_latency_summary(records, play_t=1700000000.0)
    assert out == {
        "lines": 2, "lang": ["bn", "bn"], "first_display_ms": [480, 900], "final_ms": [760, 1200], "first_translation_ms": [3100, None],
        "drafts_shown": 1, "drafts": [4, None], "translation_revisions": [3, None], "non_append_revisions": [1, None],
        "first_confirmed_after_play_s": 5.3, "first_confirmed_since_session_ms": 5450,
    }
    assert h.line_latency_summary([], play_t=None)["lines"] == 0


# ---- overlay geometry (docs/overlay/README.md) ----

def _box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def test_union_area_counts_overlap_once():
    assert h.union_area([_box(0, 0, 10, 10), _box(5, 5, 10, 10)]) == 175
    assert h.union_area([_box(0, 0, 10, 10), _box(0, 0, 10, 10)]) == 100
    assert h.union_area([]) == 0


def test_coverage_is_the_part_of_the_video_under_the_caption_lines():
    video = _box(0, 0, 100, 100)
    lines = [{"type": "primary", "box": _box(10, 80, 80, 10)},   # 8% of the video
             {"type": "original", "box": _box(10, 95, 80, 10)},  # half of it below the video: 4%
             {"type": "notice", "box": _box(0, 0, 100, 100)}]    # notices are not counted
    assert h.coverage_of(lines, video) == 0.12
    assert h.coverage_of(lines, None) == 0.0


def _g(lines, video=_box(0, 0, 100, 100), block=None):
    return {"video": video, "viewport": {"w": 100, "h": 100}, "fullscreen": False, "lines": lines, "block": block,
            "extras": [], "coverage": h.coverage_of(lines, video)}


def _line(kind, text, box, in_progress=False, draft=False, cue_id="asr_0"):
    return {"type": kind, "text": text, "box": box, "cue_id": cue_id, "in_progress": in_progress, "draft": draft}


def test_geometry_log_finds_the_three_moments_once_each():
    g = h.GeometryLog(None, None, 0)
    assert g.moment_of(_g([]), 0.1) is None            # too early for the idle shot
    assert g.moment_of(_g([]), 0.4) == "idle"
    g.record_moment("idle", _g([]), 0.4, None)
    short = _line("partial", "where did", _box(0, 80, 40, 10), in_progress=True)
    assert g.moment_of(_g([short]), 1.0) is None       # a partial that short is not mid-sentence yet
    partial = _line("partial", "where did you put the keys", _box(0, 80, 80, 10), in_progress=True)
    assert g.moment_of(_g([partial]), 1.5) == "partial"
    g.record_moment("partial", _g([partial]), 1.5, None)
    final = [_line("primary", "你把钥匙放在哪里了?", _box(0, 70, 80, 10)), _line("original", "Where did you put the keys?", _box(0, 85, 80, 10))]
    assert g.moment_of(_g(final), 3.0) == "final"
    g.record_moment("final", _g(final), 3.0, None)
    assert g.moment_of(_g(final), 4.0) is None
    assert g.moments["final"]["coverage"] == 0.16
    assert [ln["chars"] for ln in g.moments["final"]["lines"]] == [10, 27]  # lengths only, never the text


def test_geometry_log_places_the_block_against_the_video():
    g = h.GeometryLog(None, None, 0)
    video = _box(0, 0, 100, 100)
    below = _line("primary", "below the video", _box(10, 104, 80, 10))
    g.add(_g([below], video, block=_box(10, 102, 80, 20)), 1.0)
    inside = _line("primary", "in the bottom 20%", _box(10, 82, 80, 8))
    g.add(_g([inside], video, block=_box(10, 80, 80, 12)), 2.0)
    high = _line("primary", "too high", _box(10, 50, 80, 10))
    g.add(_g([high], video, block=_box(10, 50, 80, 10)), 3.0)
    # the text inside the band, the reserved box (taller than the text) reaching above it
    text_in = _line("primary", "two rows\nof text", _box(10, 81, 80, 15))
    g.add(_g([text_in], video, block=_box(10, 70, 80, 26)), 4.0)
    s = g.summary()
    assert s["band_pct"] == 20
    assert (s["block_outside_video"], s["block_intersects_video"], s["block_in_band"]) == (1, 3, 1)
    assert (s["lines_over_video"], s["lines_in_band"]) == (3, 2)
    assert s["max_coverage"] == 0.12


def test_display_times_count_the_finals_that_wrap_and_the_ones_still_truncated():
    g = h.GeometryLog(None, None, 0)
    video = _box(0, 0, 100, 100)
    g.add(_g([_line("primary", "one row", _box(10, 90, 80, 8), cue_id="asr_0")], video), 1.0)
    g.add(_g([_line("primary", "two rows\nof text", _box(10, 82, 80, 16), cue_id="asr_1")], video), 3.0)
    g.add(_g([_line("primary", "…newest words of a\nlonger translation", _box(10, 82, 80, 16), cue_id="asr_2")], video), 5.0)
    g.add(_g([], video), 9.0)
    d = g.summary()["display_times"]
    assert d["lines"] == 3
    assert [(p["cue_id"], p["rows"], p["truncated"]) for p in d["per_line"]] == [("asr_0", 1, False), ("asr_1", 2, False), ("asr_2", 2, True)]
    assert (d["wrapped"], d["truncated"]) == (2, 1)


def test_geometry_log_records_the_block_at_each_partial_text_change():
    g = h.GeometryLog(None, None, partial_updates=3)
    block = _box(10, 80, 80, 20)
    for i, text in enumerate(["a", "a", "a b", "a b c", "a b c d"]):
        g.add(_g([_line("partial", text, _box(10, 85, 20 + 10 * i, 10), in_progress=True)], block=block), i * 0.1)
    s = g.summary()["partial_updates"]
    assert s["count"] == 3  # "a" repeated is not an update; the cap holds
    assert s["heights"] == [20] and s["height_changes"] == 0 and s["position_changes"] == 0
    g2 = h.GeometryLog(None, None, partial_updates=5)
    g2.add(_g([_line("partial", "x", None, in_progress=True)], block=_box(10, 80, 80, 20)), 0)
    g2.add(_g([_line("partial", "x y", None, in_progress=True)], block=_box(10, 70, 80, 30)), 1)
    s2 = g2.summary()["partial_updates"]
    assert s2["height_changes"] == 1 and s2["position_changes"] == 1


def test_glossary_flag_posts_each_term_to_the_running_backend(app_env):
    """--glossary: the terms are POSTed to /glossary before the clip plays (the user had
    taught them earlier); the summary says how many were saved."""
    import threading
    import uvicorn
    from app.main import app

    port = 8798
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        assert h.wait_http(f"http://127.0.0.1:{port}/health/json", 30)
        out = h.post_glossary(port, [{"source_lang": "bn", "canonical": "রাজভোগ", "heard_as": ["রাজবুক"], "renderings": {"en": "rajbhog"}},
                                     {"source_lang": "bn", "canonical": " ", "heard_as": [], "renderings": {}}])
        assert out["saved"] == 1 and out["failed"] == 1 and len(out["ids"]) == 1, out
        assert json.loads(__import__("urllib.request").request.urlopen(f"http://127.0.0.1:{port}/glossary").read())["terms"][0]["canonical"] == "রাজভোগ"
    finally:
        server.should_exit = True
        t.join(timeout=10)


def test_prior_summary_reads_the_prior_and_whether_it_was_right():
    recs = [
        {"stage": "ext_prior", "event": "success", "context": {"prior": {"en": 0.075, "zh": 0.075, "bn": 0.85},
                                                               "votes": ["pageScript:bn"], "remembered": False}},
        {"stage": "lid_prior", "event": "success", "context": {"favoured": "bn", "provisional": "bn"}},
        {"stage": "lid", "event": "skip", "context": {"attempt": 1}},
        {"stage": "lid", "event": "success", "context": {"lang": "bn", "attempt": 2, "early": True, "switched": False,
                                                         "prior": "bn", "prior_right": True, "buffered_s": 1.52}},
    ]
    assert h.prior_summary(recs) == {"prior": {"en": 0.075, "zh": 0.075, "bn": 0.85}, "votes": ["pageScript:bn"],
                                     "remembered": False, "favoured": "bn", "confirmed": "bn", "attempt": 2,
                                     "switched": False, "right": True}
    assert h.prior_summary([]) == {"prior": None, "votes": [], "remembered": None, "favoured": None,
                                   "confirmed": None, "attempt": None, "switched": None, "right": None}


def test_run_log_records_reads_the_backend_run_log(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "run_R1.jsonl").write_text('{"stage": "lid_prior", "event": "success"}\nnot json\n', encoding="utf-8")
    monkeypatch.delenv("SUBTITLE_OBS_DIR", raising=False)
    assert h.run_log_records("R1", tmp_path) == [{"stage": "lid_prior", "event": "success"}]
    assert h.run_log_records("R2", tmp_path) == [] and h.run_log_records(None, tmp_path) == []

