"""Browser harness: drives the real unpacked extension in Playwright Chromium,
end to end, with no human click. Two paths:

  --path audio     (default) live captions:
                   tab plays a WAV -> tabCapture (offscreen doc, AudioWorklet) -> /ws/asr
                   -> ASR + MT in the backend -> offscreen -> service worker -> content script
  --path captions  existing subtitles:
                   <video> with a WebVTT <track> -> subtitle detector -> service worker
                   socket /ws (cue) -> MT -> content script overlay
  --path sw-idle   the caption path across service-worker idle timeouts (raw CDP,
                   no Playwright: an attached DevTools session keeps the worker
                   alive): keepalive keeps it; idle stop -> restart -> next cue
                   still translated; forced stop -> same
  --path permissions  D2 on the real extension in a fresh profile (no backend):
                   no site access and no content scripts before opt-in, the
                   install-time permission warnings, the options page explains
                   caption mode, and switching it on requests the caption sites
  --path clear-memory  D3 in the options page: it says what is stored where; the
                   "Clear translation memory" button does nothing when its
                   confirmation is dismissed and empties the TM when accepted
  --path cloud-keys  D4 in the options page: keys an older version stored are
                   deleted with a notice saying where they go now; no key fields;
                   the cloud line names the providers the backend reports (run it
                   with SUBTITLE_CLOUD__ENABLED=true + a dummy key for the "on" case)
  --path security  E16/E17/W8 in the browser: no /ws connection before the tab is
                   enabled; the page's own scripts cannot open /ws or POST /translate;
                   after enabling, one connection from the extension; a cue posted
                   by a cross-origin iframe is ignored, a same-origin one accepted

How the user gesture is avoided (audio): Chromium's `--allowlisted-extension-id=<id>`
switch lets that extension call chrome.tabCapture.getMediaStreamId without the
action-click grant (tab_capture_api.cc). The id of an unpacked extension depends
on its path, so the browser is launched once to read it and then relaunched
with the switch. Do not add --use-fake-ui-for-media-stream: with it the
offscreen getUserMedia({chromeMediaSource:'tab'}) fails with "Requested device
not found". The audio is real tab audio (a <video> element playing the WAV,
autoplay allowed by --autoplay-policy), not a fake capture device. ASR_START is
sent from an extension page exactly like the popup does.

Site access (D2): the extension asks for none at install. A real user grants it
by clicking the action (activeTab, audio path) or by accepting caption mode's
optional site permissions; automation can do neither, so every path except
`permissions` loads a scratch copy of the extension whose manifest adds
`http://127.0.0.1/*` to host_permissions (the test page's origin), and the
backend started with --start-backend is told that copy's id. Caption paths also
switch caption mode on in the settings.

Success = the content script of the test tab logs an `ext_render` record for a
translation (extension/obs.js writes every record to console.debug with the
[ST-OBS] prefix). For the caption path, `--enable` first enables translation on
the tab the way the popup does (SET_TAB_ENABLED from an extension page); without
it the extension never connects (E17).

Needs: a Python with `playwright` (pip install playwright; the Chromium build
in %LOCALAPPDATA%/ms-playwright is used), and either a backend already running
on --port or --start-backend (uses backend/venv and a scratch translation
memory, never backend/data/translation_memory.db).

    python backend/scripts/e2e_extension.py --start-backend
    python backend/scripts/e2e_extension.py --start-backend --path captions
    python backend/scripts/e2e_extension.py --wav path/to.wav --targets zh,bn

Exit code 0 when a translated cue reached the content script, 1 otherwise.
Prints one JSON summary line at the end.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
EXTENSION = REPO / "extension"
DEFAULT_WAV = BACKEND / "data/models/asr/sherpa-onnx-streaming-zipformer-en-2023-06-26/test_wavs/0.wav"

AUDIO_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>audio harness</title></head>
<body><h1>audio harness</h1><video id="v" src="/clip.wav" autoplay loop controls></video>
<script>const v=document.getElementById('v');v.play().catch(e=>console.log('play failed',e));
// player shortcuts on the document, as video sites have them (YouTube: m mutes, space pauses, j rewinds)
document.addEventListener('keydown', (e) => {
  if (e.key === 'm') v.muted = !v.muted;
  else if (e.key === ' ') { if (v.paused) v.play(); else v.pause(); }
  else if (e.key === 'j') v.currentTime = Math.max(0, v.currentTime - 10);
});</script>
</body></html>"""

# --video: a real video, played (with sound) only once live captions are running, so the
# recording and the screenshot show the clip from its first word. Both layouts have a
# control bar over the bottom of the video that appears on hover, as video sites do
# (#controls; the overlay must stay clear of it, docs/overlay/README.md).
CONTROLS_CSS = """.player{position:relative;line-height:0}
#controls{position:absolute;left:0;right:0;bottom:0;height:48px;background:rgba(20,20,20,.85);opacity:0;transition:opacity .1s}
.player:hover #controls{opacity:1}"""
# --layout fill (default): the video fills the viewport, as in fullscreen.
DEMO_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Demo video</title>
<style>html,body{margin:0;height:100%;background:#111}body{display:flex;align-items:center;justify-content:center}
.player{width:100%;height:100%;display:flex;align-items:center;justify-content:center}
video{width:100%;height:100%;object-fit:contain;background:#000}""" + CONTROLS_CSS + """</style></head>
<body><div class="player"><video id="v" src="/clip{ext}" playsinline></video><div id="controls"></div></div></body></html>"""
# --layout page: a normal page, the video in a column with text around it (most sites).
PAGE_LAYOUT = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Video page</title>
<style>html,body{margin:0;background:#f4f4f4;color:#222;font:15px/1.5 sans-serif}
header{background:#222;color:#fff;padding:12px 24px;font-size:18px}
main{width:640px;margin:16px auto}.player{width:640px}video{width:640px;height:360px;background:#000;display:block}
h1{font-size:20px;margin:12px 0 4px}p{margin:8px 0}""" + CONTROLS_CSS + """</style></head>
<body><header>Video page</header><main>
<div class="player"><video id="v" src="/clip{ext}" playsinline></video><div id="controls"></div></div>
<h1>A video on a normal page</h1>
<p>The player does not fill the window: the page has a header above it and text below it, like a video on most
sites. The subtitle block belongs in this space, not over the picture.</p>
<p>More text so the page is taller than the window and can scroll.</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p>
</main></body></html>"""

CAPTION_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>caption harness</title></head>
<body><h1>caption harness</h1>
<video id="v" src="/clip.wav" autoplay muted loop controls width="640" height="360">
  <track kind="subtitles" srclang="en" label="English" src="/subs.vtt" default>
</video>
<script>const v=document.getElementById('v');v.play().catch(e=>console.log('play failed',e));</script>
</body></html>"""

SECURITY_PAGE = CAPTION_PAGE.replace("</body>", '<iframe id="xo" src="{frame_url}" width="200" height="60"></iframe></body>')
# sw-idle: the subtitles end after a few seconds and the clip does not loop, so the
# tab really goes quiet; later cues are posted by the page itself (same origin)
IDLE_PAGE = CAPTION_PAGE.replace(" autoplay muted loop ", " autoplay muted ")
FRAME_PAGE = """<!doctype html><html><body>cross-origin frame</body></html>"""

SUBS_VTT = """WEBVTT

00:00:00.200 --> 00:00:02.500
Where did you put the keys?

00:00:02.600 --> 00:00:05.000
I will be back in five minutes.

00:00:05.100 --> 00:00:09.000
Please do not touch anything.
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def wait_http(url: str, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def serve_dir(directory: Path) -> tuple[socketserver.TCPServer, int]:
    handler = lambda *a, **k: _QuietHandler(*a, directory=str(directory), **k)  # noqa: E731

    class Quiet(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass

    srv = Quiet(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def extension_id_for_path(path: str) -> str:
    """Chrome's id for an unpacked extension (same as backend app/security.py, copied
    so the harness does not import the backend's settings)."""
    if os.name == "nt":
        if len(path) > 1 and path[1] == ":":
            path = path[0].upper() + path[1:]
        data = path.encode("utf-16-le")
    else:
        data = path.encode("utf-8")
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(data).hexdigest()[:32])


TEST_ORIGINS = ("http://127.0.0.1/*",)


def origin_pattern(url: str) -> str:
    """The host-permission pattern for a page's origin (any port)."""
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}/*"


# --url: pages that stand between the browser and the video. The run stops on them;
# nothing is clicked, dismissed or worked around.
BLOCK_TEXT = (("confirm you're not a bot", "bot check"), ("unusual traffic from your computer", "bot check"),
              ("before you continue to youtube", "consent wall"), ("before you continue to google", "consent wall"))


def block_reason(url: str, text: str) -> str | None:
    p = urlsplit(url)
    host = p.hostname or ""
    if host.startswith("consent."):
        return f"consent wall ({host})"
    if p.path.startswith("/sorry/"):
        return f"bot check ({host}{p.path})"
    low = (text or "").lower().replace("’", "'")
    for marker, reason in BLOCK_TEXT:
        if marker in low:
            return f'{reason} ("{marker}")'
    return None


def target_settings(targets: list[str]) -> dict:
    """Target languages as the Settings page stores them: background.js derives
    targetLanguages from primaryLang / secondaryLang on every read, so setting
    targetLanguages alone is overwritten (with the defaults en + zh)."""
    return {"primaryLang": targets[0], "secondaryLang": targets[1] if len(targets) > 1 else "none",
            "targetLanguages": list(targets[:2])}


def auto_target(lang: str) -> str:
    """--targets auto: English, unless the speech is English; then Chinese."""
    return "zh" if lang == "en" else "en"


LINE_TYPES = ("primary", "secondary", "original", "partial")


def overlay_lines(node: dict, want_label: bool = False):
    """The overlay's subtitle lines from a CDP DOM.describeNode(pierce) tree: the overlay
    lives in a closed shadow root, which page scripts cannot read but DevTools can.
    dimmed = drawn as provisional text (the spoken language is not confirmed yet)."""
    out: list[dict] = []
    label: list[str] = []

    def text_of(n):
        return (n.get("nodeValue") or "") if n.get("nodeType") == 3 else "".join(text_of(c) for c in n.get("children") or [])

    def walk(n):
        if n.get("nodeType") == 1:
            a = n.get("attributes") or []
            attrs = dict(zip(a[::2], a[1::2]))
            classes = attrs.get("class", "").split()
            kind = next((c for c in classes if c in LINE_TYPES), None)
            if "subtitle-line" in classes and kind:
                out.append({"type": kind, "cue_id": attrs.get("data-cue-id"), "lang": attrs.get("data-lang"), "text": text_of(n),
                            "dimmed": "provisional" in classes})
                return
            if "lang-pending" in classes:
                label.append(text_of(n))
                return
        for c in (n.get("shadowRoots") or []) + (n.get("children") or []):
            walk(c)

    walk(node)
    return (label[0] if label else None) if want_label else out


def overlay_label(node: dict) -> str | None:
    """The overlay's language label ("Detecting language…", or that detection gave up),
    shown while the spoken language is not confirmed; None when it is not up."""
    return overlay_lines(node, want_label=True)


SCRIPTS = (("bengali", 0x0980, 0x09FF), ("han", 0x4E00, 0x9FFF), ("latin", 0x41, 0x5A), ("latin", 0x61, 0x7A))


def script_of(text: str) -> str | None:
    """The writing system most of a line's letters are in (the summary keeps this, not the text)."""
    counts: dict = {}
    for ch in text or "":
        for name, lo, hi in SCRIPTS:
            if lo <= ord(ch) <= hi:
                counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else None


class DimmingLog:
    """Overlay samples against the language status the content script had rendered:
    until spoken-language ID confirms, every caption line must be dimmed and the label
    up; from then on nothing is dimmed and the label is gone. `confirmed` is True /
    False, or None when the confirmation arrived while the overlay was being read
    (that sample proves nothing either way)."""

    def __init__(self):
        self.samples = 0
        self.dimmed_samples = 0
        self.dimmed_scripts: set = set()
        self.first_dimmed_t = None
        self.first_undimmed = None
        self.confirmed_first_seen = None
        self.violations: list = []

    def add(self, t: float, lines: list[dict], label: str | None, confirmed: bool | None) -> None:
        self.samples += 1
        if confirmed is not False and self.confirmed_first_seen is None:
            self.confirmed_first_seen = t
        dimmed = [ln for ln in lines if ln.get("dimmed")]
        undimmed = [ln for ln in lines if not ln.get("dimmed")]
        if dimmed:
            self.dimmed_samples += 1
            self.dimmed_scripts.update(filter(None, (script_of(ln["text"]) for ln in dimmed)))
            if self.first_dimmed_t is None:
                self.first_dimmed_t = t
        if undimmed and self.first_undimmed is None:
            ln = undimmed[0]
            self.first_undimmed = {"t": t, "type": ln["type"], "cue_id": ln["cue_id"], "script": script_of(ln["text"])}
        found = []
        if confirmed is False:
            if undimmed:
                found.append("undimmed_before_confirmed")
            if dimmed and not label:
                found.append("dimmed_without_label")
        elif confirmed is True:
            if dimmed:
                found.append("dimmed_after_confirmed")
            if label:
                found.append("label_after_confirmed")
        self.violations.extend({"kind": k, "t": t} for k in found)

    def summary(self) -> dict:
        return {"samples": self.samples, "dimmed_samples": self.dimmed_samples, "dimmed_scripts": sorted(self.dimmed_scripts),
                "first_dimmed_s": self.first_dimmed_t, "first_undimmed": self.first_undimmed,
                "confirmed_first_seen_s": self.confirmed_first_seen, "violations": self.violations[:10]}


class LineCollector:
    """First-seen times of a caption and of a translation, and the first `limit`
    translated cues (latest text of each, with its original). Later cues are counted,
    not kept: the live test records a few lines, never the transcript."""

    def __init__(self, limit: int):
        self.limit = limit
        self.first_caption_t = None
        self.first_translation_t = None
        self._kept: dict = {}  # cue_id -> {"cue_id", "lang", "original", "translation"}
        self._originals: dict = {}
        self._translated: set = set()

    @property
    def translated_cues(self) -> int:
        return len(self._translated)

    def add(self, t: float, lines: list[dict]) -> None:
        if lines and self.first_caption_t is None:
            self.first_caption_t = t
        for ln in lines:
            cue = ln["cue_id"]
            if ln["type"] in ("primary", "secondary") and ln["text"]:
                if self.first_translation_t is None:
                    self.first_translation_t = t
                self._translated.add(cue)
                if cue in self._kept or len(self._kept) < self.limit:
                    entry = self._kept.setdefault(cue, {"cue_id": cue, "lang": None, "original": None, "translation": None})
                    entry.update(lang=ln["lang"], translation=ln["text"])
            elif ln["type"] == "original" and (cue in self._kept or len(self._kept) < self.limit):
                self._originals[cue] = ln["text"]
        for cue, entry in self._kept.items():
            if cue in self._originals:
                entry["original"] = self._originals[cue]

    def lines(self) -> list[dict]:
        return list(self._kept.values())


def line_latency_summary(records: list[dict], play_t: float | None) -> dict:
    """Per-line latencies as the content script logged them (stage line_latency,
    docs/latency.md), in line order, and the first confirmed-language subtitle: seconds
    after the harness started the video, and ms after the session's first audio frame."""
    ctxs = [r.get("context") or {} for r in records if r.get("stage") == "line_latency" and r.get("event") == "success"]
    lines = [c for c in ctxs if c.get("kind") == "line"]
    first = next((c for c in ctxs if c.get("kind") == "first_confirmed"), None)
    after_play = round(first["at_ms"] / 1000 - play_t, 2) if first and play_t and first.get("at_ms") else None
    return {"lines": len(lines), "lang": [c.get("lang") for c in lines],
            "first_display_ms": [c.get("first_display_ms") for c in lines],
            "final_ms": [c.get("final_ms") for c in lines],
            "first_translation_ms": [c.get("first_translation_ms") for c in lines],
            "drafts_shown": sum(1 for c in lines if c.get("draft_shown")),
            "drafts": [c.get("drafts") for c in lines],
            "translation_revisions": [c.get("translation_revisions") for c in lines],
            "non_append_revisions": [c.get("non_append_revisions") for c in lines],
            "first_confirmed_after_play_s": after_play,
            "first_confirmed_since_session_ms": first.get("since_session_ms") if first else None}


def correction_target(lines: list[dict]) -> dict | None:
    """The cue Alt+E would edit, as drawn: the translated line on screen (not dimmed,
    provisional lines are not offered) with its original."""
    tr = next((ln for ln in lines if ln["type"] in ("primary", "secondary") and ln["text"] and not ln.get("dimmed")), None)
    if tr is None:
        return None
    orig = next((ln["text"] for ln in lines if ln["type"] == "original" and ln["cue_id"] == tr["cue_id"]), None)
    return {"cue_id": tr["cue_id"], "lang": tr["lang"], "original": orig, "translation": tr["text"]}


# --prime-audio: a near-silent tone on the page, so the tab's audio output is already running when
# the clip starts, as it is when a viewer turns captions on over a playing video. Without it Chrome
# starts the output with the clip and the first few hundred milliseconds never reach the capture: a
# clip that begins with speech loses its first sentence (docs/latency.md, Batch 3).
PRIME_AUDIO_JS = """async () => {
  const ctx = new AudioContext();
  const osc = ctx.createOscillator(), gain = ctx.createGain();
  gain.gain.value = 0.00002;
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  window.__stPrime = ctx;
  await new Promise(r => setTimeout(r, 600));
}"""

VIDEO_STATE_JS = "(v => v ? {muted: v.muted, paused: v.paused, t: v.currentTime, loop: v.loop} : null)"


def page_untouched(before: dict | None, after: dict | None, elapsed_s: float) -> bool:
    """Typing a correction must not operate the page (on YouTube m mutes, j and l seek 10 s,
    space pauses): same muted / paused state, and the video's clock moved with the wall
    clock (not checked for a looping clip, whose clock wraps)."""
    if not before or not after:
        return False
    if before["muted"] != after["muted"] or before["paused"] != after["paused"]:
        return False
    if before.get("loop") or before["paused"]:
        return True
    return abs((after["t"] - before["t"]) - elapsed_s) < 3.0


def harness_tm_path(path: Path) -> Path:
    """--tm: a translation memory kept between runs (a correction made in one run, used
    in the next). Never the user's own: anything under backend/data is refused."""
    resolved = Path(path).resolve()
    data = (BACKEND / "data").resolve()
    if resolved == data or data in resolved.parents:
        raise ValueError(f"--tm must not be inside {data} (the real translation memory lives there)")
    return resolved


PAGE_VIDEO_JS = "(document.querySelector('video.html5-main-video') || document.querySelector('video'))"
PAGE_VIDEO_STATE_JS = ("(() => { const v = " + PAGE_VIDEO_JS + "; return v ? {ready: v.readyState,"
                       " ad: !!document.querySelector('.ad-showing')} : null; })()")


def wait_for_page_video(page, summary: dict, timeout_s: float = 180.0) -> bool:
    """--url: wait until the page's video can play and no ad is showing (YouTube marks its
    player .ad-showing). A consent wall or bot check ends the run: summary["blocked"]."""
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            reason = block_reason(page.url, page.evaluate("document.body ? document.body.innerText.slice(0, 20000) : ''"))
            if reason:
                summary["blocked"] = reason
                summary["error"] = f"blocked: {reason}; not bypassed"
                return False
            st = page.evaluate(PAGE_VIDEO_STATE_JS)
        except Exception as e:  # navigation in progress (a redirect, a consent page)
            summary["last_page_error"] = str(e).splitlines()[0][:200]
            st = None
        if st and st["ad"]:
            summary["ad_before_start"] = True
        elif st and st["ready"] >= 3:
            return True
        page.wait_for_timeout(500)
    summary["error"] = f"no playable video on {page.url} after {timeout_s:.0f} s"
    return False


def summary_line(summary: dict) -> str:
    """The JSON summary, ASCII only: page titles and subtitle lines can be in any script,
    and a Windows console's code page (GBK, cp1252) cannot print all of them."""
    return json.dumps(summary, ensure_ascii=True)


def read_overlay(cdp) -> tuple[list[dict], str | None]:
    """(caption lines, language label) as drawn right now."""
    root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
    nid = cdp.send("DOM.querySelector", {"nodeId": root, "selector": "#subtitle-translator-host"}).get("nodeId")
    if not nid:
        return [], None
    node = cdp.send("DOM.describeNode", {"nodeId": nid, "depth": -1, "pierce": True})["node"]
    return overlay_lines(node), overlay_label(node)


# ---------------------------------------------------------------------------
# Overlay geometry (docs/overlay/README.md): where the subtitle block and its lines are
# on the page, against the video's rectangle, read through the closed shadow root with
# CDP box models. Coverage = the part of the video's area under the lines' boxes.
# ---------------------------------------------------------------------------

def rect_of_quad(quad: list) -> dict:
    xs, ys = quad[0::2], quad[1::2]
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def rect_intersection(a: dict, b: dict) -> dict | None:
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0} if x1 > x0 and y1 > y0 else None


def union_area(rects: list[dict]) -> float:
    """Area of the union of rectangles (coordinate compression; boxes may overlap)."""
    rects = [r for r in rects if r and r["w"] > 0 and r["h"] > 0]
    if not rects:
        return 0.0
    xs = sorted({v for r in rects for v in (r["x"], r["x"] + r["w"])})
    ys = sorted({v for r in rects for v in (r["y"], r["y"] + r["h"])})
    area = 0.0
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx, cy = (xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2
            if any(r["x"] <= cx <= r["x"] + r["w"] and r["y"] <= cy <= r["y"] + r["h"] for r in rects):
                area += (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j])
    return area


def bounding_rect(rects: list[dict]) -> dict | None:
    rects = [r for r in rects if r]
    if not rects:
        return None
    x0, y0 = min(r["x"] for r in rects), min(r["y"] for r in rects)
    x1, y1 = max(r["x"] + r["w"] for r in rects), max(r["y"] + r["h"] for r in rects)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def coverage_of(lines: list[dict], video: dict | None) -> float:
    """Fraction of the video's area under the caption lines' boxes (notices excluded)."""
    if not video or video["w"] <= 0 or video["h"] <= 0:
        return 0.0
    boxes = [rect_intersection(ln["box"], video) for ln in lines if ln.get("box") and ln["type"] in LINE_TYPES]
    return round(union_area(boxes) / (video["w"] * video["h"]), 4)


VIDEO_RECT_JS = """(v) => { if (!v) return null; const r = v.getBoundingClientRect();
  return {video: {x: r.left, y: r.top, w: r.width, h: r.height}, viewport: {w: innerWidth, h: innerHeight},
          fullscreen: !!document.fullscreenElement}; }"""


def read_overlay_geometry(cdp, page, video_expr: str) -> dict:
    """One sample: the video's rect (viewport coordinates), every subtitle line with its
    border box, the subtitle block's box (the fixed block that holds the lines, when the
    overlay has one), the extras (notices, the language label) and the coverage."""
    out = {"video": None, "viewport": None, "fullscreen": False, "lines": [], "block": None, "extras": [], "coverage": 0.0}
    v = page.evaluate(f"(() => {{ const f = {VIDEO_RECT_JS}; return f({video_expr}); }})()")
    if v:
        out.update(v)
    root = cdp.send("DOM.getDocument", {"depth": 0})["root"]["nodeId"]
    nid = cdp.send("DOM.querySelector", {"nodeId": root, "selector": "#subtitle-translator-host"}).get("nodeId")
    if not nid:
        return out
    node = cdp.send("DOM.describeNode", {"nodeId": nid, "depth": -1, "pierce": True})["node"]

    def text_of(n):
        return (n.get("nodeValue") or "") if n.get("nodeType") == 3 else "".join(text_of(c) for c in n.get("children") or [])

    def box_of(n):
        try:
            return rect_of_quad(cdp.send("DOM.getBoxModel", {"backendNodeId": n["backendNodeId"]})["model"]["border"])
        except Exception:
            return None

    def walk(n):
        if n.get("nodeType") == 1:
            a = n.get("attributes") or []
            attrs = dict(zip(a[::2], a[1::2]))
            classes = attrs.get("class", "").split()
            if "subtitle-block" in classes:
                out["block"] = box_of(n)
            if "subtitle-line" in classes:
                kind = next((c for c in classes if c in LINE_TYPES), None)
                entry = {"type": kind or "notice", "text": text_of(n), "box": box_of(n), "cue_id": attrs.get("data-cue-id"),
                         "in_progress": "in-progress" in classes, "draft": "draft" in classes}
                (out["lines"] if kind else out["extras"]).append(entry)
                return
        for c in (n.get("shadowRoots") or []) + (n.get("children") or []):
            walk(c)

    walk(node)
    out["coverage"] = coverage_of(out["lines"], out["video"])
    return out


class GeometryLog:
    """Samples of the overlay's geometry while captions run, and what the brief asks of
    them: the three moments (idle, a partial mid-sentence, a final translation) with a
    screenshot each, the block against the video (page vs fullscreen placement), and the
    block's box across consecutive partial updates (it must not move or grow)."""

    MOMENTS = ("idle", "partial", "final")

    def __init__(self, shots_dir: Path | None, shot_name: str | None, partial_updates: int):
        self.shots_dir, self.shot_name = shots_dir, shot_name
        self.partial_updates = partial_updates
        self.samples: list[dict] = []
        self.moments: dict = {}
        self.updates: list[dict] = []       # block box at each partial-text change
        self._last_partial_text = None
        self.max_coverage = 0.0
        self.with_lines = 0
        self.block_intersects_video = 0
        self.block_outside_video = 0
        self.block_in_bottom_15 = 0
        self.lines_in_bottom_15 = 0
        self.lines_over_video = 0
        self.controls_overlap = 0
        self.most_lines = None   # the sample with the most caption lines: types, cues, lengths (for diagnosis)

    @staticmethod
    def caption_lines(g: dict) -> list[dict]:
        return [ln for ln in g["lines"] if ln["type"] in LINE_TYPES]

    def moment_of(self, g: dict, t: float) -> str | None:
        lines = self.caption_lines(g)
        if "idle" not in self.moments and t >= 0.3:
            return "idle"
        if "partial" not in self.moments:
            # mid-sentence: 20 characters, or 8 in a CJK script (a draft translation into Chinese)
            prog = [ln for ln in lines if ln["in_progress"] and len(ln["text"]) >= (8 if script_of(ln["text"]) == "han" else 20)]
            if prog and not any(ln["type"] in ("primary", "secondary") and not ln["draft"] for ln in lines):
                return "partial"
        if "final" not in self.moments:
            if any(ln["type"] in ("primary", "secondary") and not ln["draft"] and ln["text"] for ln in lines):
                return "final"
        return None

    def add(self, g: dict, t: float, controls: dict | None = None) -> None:
        g = dict(g, t=t)
        if len(self.samples) < 2000:
            self.samples.append({k: g[k] for k in ("t", "coverage", "block")} | {"lines": len(self.caption_lines(g))})
        lines = self.caption_lines(g)
        self.max_coverage = max(self.max_coverage, g["coverage"])
        if lines and (self.most_lines is None or len(lines) > self.most_lines["n"]):
            self.most_lines = {"n": len(lines), "t": t, "lines": [{"type": ln["type"], "cue_id": ln["cue_id"], "chars": len(ln["text"]),
                                                                  "in_progress": ln["in_progress"], "draft": ln["draft"]} for ln in lines]}
        if lines and g["video"]:
            self.with_lines += 1
            v = g["video"]
            boxes = [ln["box"] for ln in lines if ln["box"]]
            block = g["block"] or bounding_rect(boxes)
            if block:
                if rect_intersection(block, v):
                    self.block_intersects_video += 1
                else:
                    self.block_outside_video += 1
                if block["y"] >= v["y"] + 0.85 * v["h"] - 0.5 and block["y"] + block["h"] <= v["y"] + v["h"] + 0.5:
                    self.block_in_bottom_15 += 1
            if any(rect_intersection(b, v) for b in boxes):
                self.lines_over_video += 1
                if all(b["y"] >= v["y"] + 0.85 * v["h"] - 0.5 for b in boxes if rect_intersection(b, v)):
                    self.lines_in_bottom_15 += 1
            if controls and any(rect_intersection(b, controls) for b in boxes):
                self.controls_overlap += 1
        # consecutive partial updates: the in-progress line's text changed
        prog = next((ln for ln in lines if ln["in_progress"]), None)
        if prog and prog["text"] != self._last_partial_text:
            self._last_partial_text = prog["text"]
            if len(self.updates) < max(self.partial_updates, 0):
                self.updates.append({"t": t, "block": g["block"] or bounding_rect([ln["box"] for ln in lines if ln["box"]]),
                                     "chars": len(prog["text"])})

    def shot_path(self, moment: str) -> Path | None:
        if not self.shots_dir or not self.shot_name:
            return None
        return self.shots_dir / f"{self.shot_name}-{moment}.png"

    def record_moment(self, moment: str, g: dict, t: float, shot: Path | None) -> None:
        self.moments[moment] = {"t": t, "coverage": g["coverage"], "video": g["video"], "block": g["block"],
                                "lines": [{"type": ln["type"], "chars": len(ln["text"]), "box": ln["box"],
                                           "in_progress": ln["in_progress"], "draft": ln["draft"]} for ln in self.caption_lines(g)],
                                "extras": [{"chars": len(e["text"]), "box": e["box"]} for e in g["extras"]],
                                "shot": str(shot) if shot else None}

    def summary(self) -> dict:
        heights = sorted({round(u["block"]["h"], 1) for u in self.updates if u["block"]})
        moves = 0
        prev = None
        for u in self.updates:
            b = u["block"]
            if b and prev and (abs(b["x"] - prev["x"]) > 0.5 or abs(b["y"] - prev["y"]) > 0.5):
                moves += 1
            prev = b or prev
        last = self.samples[-1] if self.samples else {}
        return {"samples": len(self.samples), "with_lines": self.with_lines, "max_coverage": self.max_coverage,
                "moments": self.moments,
                "block_intersects_video": self.block_intersects_video, "block_outside_video": self.block_outside_video,
                "block_in_bottom_15": self.block_in_bottom_15, "lines_over_video": self.lines_over_video,
                "lines_in_bottom_15": self.lines_in_bottom_15, "controls_overlap": self.controls_overlap,
                "partial_updates": {"count": len(self.updates), "heights": heights, "height_changes": max(0, len(heights) - 1),
                                    "position_changes": moves, "first": self.updates[0] if self.updates else None,
                                    "last": self.updates[-1] if self.updates else None},
                "last_block": last.get("block"), "most_lines": self.most_lines}


def test_extension_copy(extension: Path, scratch: Path, extra_origins=()) -> Path:
    """The extension with the test page's origin and tab capture granted (stand-ins
    for the user's activeTab click, caption-mode grant and first-use tabCapture
    prompt, which automation cannot give)."""
    dst = scratch / "extension-test"
    shutil.copytree(extension, dst, ignore=shutil.ignore_patterns("tests", "node_modules"))
    mf = dst / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    m["host_permissions"] = sorted(set(m.get("host_permissions") or []) | set(TEST_ORIGINS) | set(extra_origins))
    # tab audio capture is optional (asked for on the popup's Start click): grant it at install here
    m["permissions"] = sorted(set(m.get("permissions") or []) | {"tabCapture"})
    mf.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return dst.resolve()


def start_backend(port: int, scratch: Path, extension_ids=(), tm: Path | None = None,
                  backend_dir: Path | None = None) -> subprocess.Popen:
    """backend_dir: run the backend code of another checkout (a before / after comparison)
    with this checkout's venv; its relative model paths then need absolute settings
    (SUBTITLE_ASR__MODELS_DIR, SUBTITLE_ASR__PUNCT_DIR, SUBTITLE_TRANSLATION__CT2_DIR)."""
    py = BACKEND / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    env = dict(os.environ)
    if extension_ids:
        env["SUBTITLE_SERVER__EXTENSION_IDS"] = json.dumps(list(extension_ids))
    env["SUBTITLE_CACHE__TM_DATABASE_PATH"] = str(tm or scratch / "tm.db")
    env["SUBTITLE_DATA_DIR"] = str(scratch / "data")
    env["SUBTITLE_SERVER__PORT"] = str(port)
    log = open(scratch / "backend.log", "w", encoding="utf-8")
    return subprocess.Popen([str(py), "run.py", "--port", str(port)], cwd=str(backend_dir or BACKEND), env=env,
                            stdout=log, stderr=subprocess.STDOUT)


def launch(pw, user_dir: Path, ext_id: str | None, headless: bool, extension: Path = EXTENSION,
           record_dir: Path | None = None, size: tuple | None = None):
    args = [
        f"--disable-extensions-except={extension}",
        f"--load-extension={extension}",
        "--autoplay-policy=no-user-gesture-required",
        "--lang=en-US",  # English permission warnings in the summary
    ]
    if ext_id:
        args.append(f"--allowlisted-extension-id={ext_id}")
    if headless:
        args.append("--headless=new")
    kw = {}
    if size:
        kw["viewport"] = {"width": size[0], "height": size[1]}
    if record_dir:  # Playwright's own recorder: the tab as rendered, overlay included
        kw["record_video_dir"] = str(record_dir)
        kw["record_video_size"] = {"width": size[0], "height": size[1]} if size else {"width": 1280, "height": 720}
    return pw.chromium.launch_persistent_context(str(user_dir), headless=False, args=args, **kw)


def service_worker(ctx):
    return ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)


def send_from_extension_page(ctl, message: dict):
    return ctl.evaluate("(m) => new Promise(r => chrome.runtime.sendMessage(m, r))", message)


def permissions_checks(ctx, ext_id: str, summary: dict) -> bool:
    """D2 on the real extension in a fresh profile."""
    checks = summary.setdefault("checks", {})
    opt = next((p for p in ctx.pages if p.url.startswith(f"chrome-extension://{ext_id}/options/")), None) or ctx.new_page()
    if not opt.url.startswith(f"chrome-extension://{ext_id}/options/"):
        opt.goto(f"chrome-extension://{ext_id}/options/options.html")
    opt.wait_for_load_state()
    granted = opt.evaluate("chrome.permissions.getAll()")
    manifest = opt.evaluate("chrome.runtime.getManifest()")
    summary["granted"] = granted
    summary["install_warnings"] = opt.evaluate(
        "(m) => new Promise(r => chrome.management.getPermissionWarningsByManifest(JSON.stringify(m), r))", manifest)
    registered = opt.evaluate("chrome.scripting ? chrome.scripting.getRegisteredContentScripts() : Promise.resolve([])")
    checks["fresh_profile_no_site_access"] = not granted.get("origins")
    checks["no_install_warnings"] = summary["install_warnings"] == []
    checks["no_content_scripts_before_opt_in"] = not manifest.get("content_scripts") and not registered
    explanation = opt.evaluate("(document.getElementById('captionModeExplanation') || {}).textContent || ''")
    summary["explanation"] = explanation.strip()
    checks["options_explain_caption_mode"] = "reads the subtitle text" in explanation
    toggle = opt.query_selector("#captionMode")
    checks["caption_mode_off_by_default"] = bool(toggle) and not toggle.is_checked()
    if not toggle:
        return False
    expected = opt.evaluate("(globalThis.STCaptionMode || {}).CAPTION_ORIGINS || []")
    opt.evaluate("""() => {
        window.__permRequests = [];
        const real = chrome.permissions.request.bind(chrome.permissions);
        chrome.permissions.request = (p, cb) => { window.__permRequests.push(JSON.parse(JSON.stringify(p))); return real(p, cb); };
    }""")
    opt.click("#captionMode + .toggle-slider")  # a real click: permissions.request needs the user gesture
    opt.wait_for_timeout(2500)
    requests = opt.evaluate("window.__permRequests")
    settings = opt.evaluate("chrome.storage.local.get('settings').then(r => r.settings || {})")
    summary["after_enable_click"] = {"requests": requests, "caption_mode_saved": settings.get("captionMode"),
                                     "granted": opt.evaluate("chrome.permissions.getAll()")}
    checks["enabling_requests_the_caption_sites"] = bool(requests) and sorted(requests[0].get("origins") or []) == sorted(expected) \
        and len(expected) > 0
    # headless Chromium cannot show the permission prompt, so nothing is granted: caption mode must stay off
    checks["not_on_until_granted"] = settings.get("captionMode") is not True

    # live captions: tab audio capture is asked for on the first Start click, not at install
    pop = ctx.new_page()
    pop.goto(f"chrome-extension://{ext_id}/popup/popup.html")
    pop.wait_for_load_state()
    pop.wait_for_timeout(500)
    pop.evaluate("""() => {
        window.__permRequests = [];
        const real = chrome.permissions.request.bind(chrome.permissions);
        chrome.permissions.request = (p, cb) => { window.__permRequests.push(JSON.parse(JSON.stringify(p))); return real(p, cb); };
    }""")
    pop.click("#btn-live")
    pop.wait_for_timeout(2500)
    capture_requests = pop.evaluate("window.__permRequests")
    summary["after_start_click"] = {"requests": capture_requests,
                                    "status": pop.evaluate("document.getElementById('live-status').textContent")}
    checks["start_requests_tab_capture"] = capture_requests[:1] == [{"permissions": ["tabCapture"]}]
    return all(checks.values())


def wait_until(page, expr: str, timeout_s: float) -> None:
    """Poll a JS expression with evaluate (CDP). Not wait_for_function: it builds the
    predicate with new Function, which an extension page's CSP blocks."""
    end = time.time() + timeout_s
    while time.time() < end:
        if page.evaluate(expr):
            return
        page.wait_for_timeout(200)
    raise TimeoutError(f"not true after {timeout_s}s: {expr}")


def tm_rows(port: int) -> int:
    """Rows in the backend's (scratch) translation memory."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=5) as r:  # no Origin: allowed
        return json.loads(r.read())["translation_memory"]["total_translations"]


def tm_corrections(port: int) -> int:
    """User corrections in the backend's (scratch) translation memory."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=5) as r:
        return json.loads(r.read())["translation_memory"]["user_corrections"]


def asr_models(port: int) -> dict:
    """{lang: model name} of the backend's Zipformer engine, and the punctuation model."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/json", timeout=5) as r:
        asr = json.loads(r.read())["asr"]
    eng = (asr.get("engines") or {}).get("sherpa-zipformer") or {}
    return {**eng.get("models", {}), "punctuation": (asr.get("punctuation") or {}).get("model")}


def clear_memory_checks(ctx, sw, ext_id: str, port: int, summary: dict) -> bool:
    """D3: the options page's storage sentence and its Clear translation memory button."""
    checks = summary.setdefault("checks", {})
    body = json.dumps({"text": "Meet me at the old harbour at nine", "source_lang": "en", "target_languages": ["zh"]}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/translate", data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=60).read()  # a caption-style translation: persisted
    seeded = tm_rows(port)
    checks["seeded"] = seeded >= 1
    sw.evaluate("""async (port) => {
        const cur = (await chrome.storage.local.get('settings')).settings || {};
        await chrome.storage.local.set({settings: {...cur, serverHost: '127.0.0.1', serverPort: port}});
    }""", port)
    opt = ctx.new_page()
    opt.goto(f"chrome-extension://{ext_id}/options/options.html")
    wait_until(opt, "document.getElementById('connectionStatus').textContent.includes('Connected')", 15)
    note = opt.evaluate("document.getElementById('storageNote').textContent")
    summary["storage_note"] = note
    checks["options_say_what_is_stored_where"] = "translation memory" in note and "live-caption" in note and "30 days" in note
    dialogs = []
    opt.once("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    opt.click("#clearMemory")
    opt.wait_for_timeout(1500)
    checks["dismissed_confirmation_keeps_the_tm"] = len(dialogs) == 1 and tm_rows(port) == seeded
    opt.once("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    opt.click("#clearMemory")
    end = time.time() + 10
    while time.time() < end and tm_rows(port):
        opt.wait_for_timeout(250)
    checks["confirmed_clear_empties_the_tm"] = len(dialogs) == 2 and tm_rows(port) == 0
    summary["dialog"] = dialogs[:1]
    summary["status_bar"] = opt.evaluate("document.getElementById('statusBar').textContent")
    return all(checks.values())


LEGACY_KEYS = {"google_api_key": "legacy-secret-4242", "groq_api_key": "legacy-secret-4243",
               "azure_translator_key": "", "refiner_provider": "groq"}


def cloud_keys_checks(ctx, sw, ext_id: str, port: int, summary: dict) -> bool:
    """D4: stored keys are migrated away; the options page never shows a key field
    and says which provider (if any) receives subtitle text."""
    checks = summary.setdefault("checks", {})
    sw.evaluate("""async ({port, keys}) => {
        const cur = (await chrome.storage.local.get('settings')).settings || {};
        await chrome.storage.local.set({settings: {...cur, serverHost: '127.0.0.1', serverPort: port, schemaVersion: 3, cloudKeys: keys}});
    }""", {"port": port, "keys": LEGACY_KEYS})
    opt = ctx.new_page()
    opt.goto(f"chrome-extension://{ext_id}/options/options.html")
    wait_until(opt, "document.getElementById('connectionStatus').textContent.includes('Connected')", 15)
    stored = opt.evaluate("chrome.storage.local.get('settings').then(r => r.settings || {})")
    checks["keys_deleted_from_storage"] = "cloudKeys" not in stored and "legacy-secret" not in json.dumps(stored)
    notice = opt.evaluate("(() => { const n = document.getElementById('keyNotice'); return {hidden: n.hidden, text: n.textContent}; })()")
    summary["notice"] = " ".join(notice["text"].split())
    checks["notice_says_where_keys_go"] = not notice["hidden"] and "2 cloud API key(s)" in notice["text"] and "backend/.env" in notice["text"]
    checks["no_key_fields_in_options"] = opt.evaluate("document.querySelectorAll('input[type=password]').length") == 0 \
        and "legacy-secret" not in opt.content()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/json", timeout=5) as r:
        receivers = json.loads(r.read())["privacy"]["cloud"]["receivers"]
    line = opt.evaluate("document.getElementById('cloudStatus').textContent")
    summary["cloud_status"], summary["receivers"] = line, receivers
    checks["options_show_who_receives_text"] = all(x["provider"] in line and x["host"] in line for x in receivers) \
        if receivers else "no subtitle text leaves this computer" in line
    opt.click("#dismissCloudNotice")
    opt.wait_for_timeout(500)
    stored = opt.evaluate("chrome.storage.local.get('settings').then(r => r.settings || {})")
    checks["notice_dismissed"] = opt.evaluate("document.getElementById('keyNotice').hidden") and "keysRemovedNotice" not in stored
    return all(checks.values())


def backend_connections(port: int) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=5) as r:  # no Origin: allowed
        return json.loads(r.read())["connections"]


def security_checks(page, ctl, tab_id, records, port, summary) -> bool:
    """E16 / E17 / W8 in a real browser. Fills summary["checks"]; True if all pass."""
    checks = summary.setdefault("checks", {})

    def ws_successes():
        return [r for r in records if r.get("stage") == "ext_ws" and r.get("event") == "success"
                and (r.get("context") or {}).get("path") == "/ws"]

    def detected(cue_id):
        return any(r.get("stage") == "ext_caption_detect" and (r.get("context") or {}).get("cue_id") == cue_id
                   for r in records)

    page.wait_for_timeout(4000)  # page loaded, video playing, subtitles cueing
    checks["E17_no_connection_before_enable"] = not ws_successes() and backend_connections(port) == 0
    summary["before_enable_connections"] = backend_connections(port)
    checks["E17_no_detection_before_enable"] = not any(r.get("stage") == "ext_caption_detect" for r in records)

    page_ws = page.evaluate("""(port) => new Promise(r => {
        const ws = new WebSocket('ws://127.0.0.1:' + port + '/ws');
        ws.onopen = () => { ws.close(); r('open'); };
        ws.onerror = () => r('refused');
        setTimeout(() => r('timeout'), 5000);
    })""", port)
    page_post = page.evaluate("""(port) => fetch('http://127.0.0.1:' + port + '/translate', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: 'hi', target_languages: ['zh']})})
        .then(r => 'status ' + r.status, e => 'blocked')""", port)
    checks["W8_page_cannot_open_ws"] = page_ws == "refused"
    checks["W8_page_cannot_post_translate"] = page_post == "blocked"
    summary["page_attempts"] = {"ws": page_ws, "post": page_post}

    res = send_from_extension_page(ctl, {"type": "SET_TAB_ENABLED", "tabId": tab_id, "enabled": True})
    page.bring_to_front()
    # the socket lives in the service worker (its ext_ws records do not reach the page console),
    # so "connected" is read from the backend's own count
    deadline = time.time() + 20
    while time.time() < deadline and backend_connections(port) != 1:
        page.wait_for_timeout(250)
    summary["after_enable"] = {"reply": res, "connections": backend_connections(port),
                               "tab_records": [(r.get("context") or {}).get("action") for r in records if r.get("stage") == "ext_tab"]}
    checks["E17_connects_after_enable"] = bool(res and res.get("ok")) and backend_connections(port) == 1
    page.wait_for_timeout(1000)  # config message + first cues

    forged = {"__subtrans": "cue", "cue": {"cueId": "forged-cross-origin", "text": "FORGED CUE", "startTime": 0, "endTime": 1}}
    same = {"__subtrans": "cue", "cue": {"cueId": "same-origin-cue", "text": "Same origin cue", "startTime": 0, "endTime": 1}}
    frame = next(f for f in page.frames if "frame.html" in f.url)
    frame.evaluate("(m) => parent.postMessage(m, '*')", forged)
    page.evaluate("(m) => window.postMessage(m, '*')", same)
    page.wait_for_timeout(2000)
    checks["E16_cross_origin_cue_ignored"] = not detected("forged-cross-origin")
    checks["E16_same_origin_cue_accepted"] = detected("same-origin-cue")

    send_from_extension_page(ctl, {"type": "SET_TAB_ENABLED", "tabId": tab_id, "enabled": False})
    page.wait_for_timeout(1500)
    checks["E17_disable_closes_connection"] = backend_connections(port) == 0
    summary["errors"] = [r.get("error_message") for r in records if r.get("event") == "fail"][:5]
    return all(checks.values())


class CDP:
    """Minimal Chrome DevTools Protocol client (flattened sessions) on websockets'
    sync client. The sw-idle path cannot use Playwright: Playwright attaches
    DevTools to every service worker, and Chrome never stops an attached worker
    for idleness (measured: same worker instance after 45 s of silence)."""

    def __init__(self, url: str):
        from websockets.sync.client import connect

        self.ws = connect(url, max_size=None, open_timeout=15)
        self.next_id = 0
        self.lock = threading.Lock()
        self.cond = threading.Condition()
        self.results: dict = {}
        self.listeners: list = []
        self.closed = False
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for raw in self.ws:
                m = json.loads(raw)
                with self.cond:
                    if "id" in m:
                        self.results[m["id"]] = m
                    self.cond.notify_all()
                if "id" not in m:
                    for fn in list(self.listeners):
                        try:
                            fn(m)
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            with self.cond:
                self.closed = True
                self.cond.notify_all()

    def send(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 30):
        with self.lock:
            self.next_id += 1
            mid = self.next_id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        end = time.time() + timeout
        with self.cond:
            while mid not in self.results:
                left = end - time.time()
                if left <= 0 or self.closed:
                    raise TimeoutError(f"CDP {method} timed out")
                self.cond.wait(left)
            res = self.results.pop(mid)
        if "error" in res:
            raise RuntimeError(f"CDP {method}: {res['error']}")
        return res.get("result", {})

    def evaluate(self, session: str, expr: str, context_id: int | None = None, timeout: float = 30):
        p = {"expression": expr, "awaitPromise": True, "returnByValue": True}
        if context_id is not None:
            p["contextId"] = context_id
        r = self.send("Runtime.evaluate", p, session, timeout)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:500])
        return (r.get("result") or {}).get("value")

    def attach(self, target_id: str) -> str:
        return self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})["sessionId"]

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def chromium_executable() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        return pw.chromium.executable_path


def launch_raw(user_dir: Path, extension: Path, headless: bool):
    """Chromium with the unpacked extension and a DevTools port, without Playwright
    (nothing attaches to the service worker unless the caller does)."""
    user_dir.mkdir(parents=True, exist_ok=True)
    args = [chromium_executable(), f"--user-data-dir={user_dir}", "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check",
            f"--disable-extensions-except={extension}", f"--load-extension={extension}",
            "--autoplay-policy=no-user-gesture-required"]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = user_dir / "DevToolsActivePort"
    end = time.time() + 30
    while time.time() < end:
        try:
            lines = port_file.read_text().split()
            if len(lines) >= 2:
                return proc, f"ws://127.0.0.1:{lines[0]}{lines[1]}"
        except OSError:
            pass
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("Chromium did not write DevToolsActivePort")


def sw_idle_run(args, page_url: str, scratch: Path, summary: dict) -> bool:
    """A caption session that goes quiet for longer than the service worker's idle
    timeout (30 s) still delivers the next cue. Three phases on one tab:
      A  idle `--idle` s with the content script's keepalive running: the worker
         must not be stopped, and the next cue is translated;
      B  keepalive cleared (in the content script's own world): Chrome's real idle
         timeout stops the worker; the next cue must still be translated (port
         reopened -> worker restarted -> socket reconnected);
      C  worker stopped through DevTools (ServiceWorker.stopAllWorkers), the way
         any other termination looks; the next cue must still be translated."""
    checks = summary.setdefault("checks", {})
    proc, browser_ws = launch_raw(scratch / "profile-raw", args.extension, not args.headful)
    cdp = CDP(browser_ws)
    try:
        sw_events: list = []  # (t, 'created'|'destroyed', targetId)
        sw_ids: set = set()
        records: list = []
        contexts: dict = {}
        state = {"ext_id": None, "page_session": None}

        def on_event(m):
            meth, p = m.get("method"), m.get("params") or {}
            ti = p.get("targetInfo") or {}
            if meth == "Target.targetCreated" and ti.get("type") == "service_worker" \
                    and ti.get("url", "").startswith("chrome-extension://") and ti["url"].endswith("/background.js"):
                state["ext_id"] = state["ext_id"] or ti["url"].split("/")[2]
                sw_ids.add(ti["targetId"])
                sw_events.append((time.time(), "created", ti["targetId"]))
            elif meth == "Target.targetDestroyed" and p.get("targetId") in sw_ids:
                sw_events.append((time.time(), "destroyed", p["targetId"]))
            elif meth == "Runtime.consoleAPICalled" and m.get("sessionId") == state["page_session"]:
                text = " ".join(str(a.get("value", "")) for a in p.get("args") or [])
                if "[ST-OBS]" in text:
                    try:
                        records.append(json.loads(text.split("[ST-OBS]", 1)[1].strip()))
                    except Exception:
                        pass
            elif meth == "Runtime.executionContextCreated" and m.get("sessionId") == state["page_session"]:
                c = p.get("context") or {}
                contexts[c.get("id")] = c

        cdp.listeners.append(on_event)
        cdp.send("Target.setDiscoverTargets", {"discover": True})
        end = time.time() + 20
        while not state["ext_id"] and time.time() < end:
            time.sleep(0.2)
        ext_id = state["ext_id"]
        summary["extension_id"] = ext_id
        if not ext_id:
            summary["error"] = "extension service worker not found"
            return False
        for t in cdp.send("Target.getTargets")["targetInfos"]:
            if t["type"] == "page" and t["url"].startswith(f"chrome-extension://{ext_id}/options/"):
                cdp.send("Target.closeTarget", {"targetId": t["targetId"]})  # opened by onInstalled

        def ext_page():
            tid = cdp.send("Target.createTarget", {"url": f"chrome-extension://{ext_id}/popup/popup.html"})["targetId"]
            s = cdp.attach(tid)
            end = time.time() + 10
            while time.time() < end and cdp.evaluate(s, "typeof chrome !== 'undefined' && !!chrome.runtime && document.readyState") != "complete":
                time.sleep(0.2)
            return tid, s

        ctl_id, ctl = ext_page()
        targets = [t for t in args.targets.split(",") if t]
        cdp.evaluate(ctl, "(async () => { const cur = (await chrome.storage.local.get('settings')).settings || {};"
                          f" await chrome.storage.local.set({{settings: {{...cur, serverUrl: {json.dumps(f'ws://127.0.0.1:{args.port}/ws')},"
                          f" ...{json.dumps(target_settings(targets))}, captionMode: true}}}}); return true; }})()")
        end = time.time() + 10
        while time.time() < end and not cdp.evaluate(
                ctl, "chrome.scripting ? chrome.scripting.getRegisteredContentScripts().then(r => r.length) : 1"):
            time.sleep(0.2)

        page_id = cdp.send("Target.createTarget", {"url": page_url})["targetId"]
        state["page_session"] = page = cdp.attach(page_id)
        cdp.send("Runtime.enable", session=page)
        end = time.time() + 15
        while time.time() < end and cdp.evaluate(page, "document.readyState") != "complete":
            time.sleep(0.2)
        frame_id = cdp.send("Page.getFrameTree", session=page)["frameTree"]["frame"]["id"]
        tab_id = cdp.evaluate(ctl, f"chrome.tabs.query({{url: {json.dumps(page_url)}}}).then(t => t[0] && t[0].id)")
        enable = cdp.evaluate(ctl, "new Promise(r => chrome.runtime.sendMessage("
                                   f"{{type: 'SET_TAB_ENABLED', tabId: {int(tab_id)}, enabled: true}}, r))")
        summary["enable"] = enable

        def rendered(cue_id=None):
            return [r for r in records if r.get("stage") == "ext_render"
                    and (r.get("context") or {}).get("from") == "backend"
                    and (cue_id is None or (r.get("context") or {}).get("cue_id") == cue_id)]

        def wait_for(pred, timeout):
            end = time.time() + timeout
            while time.time() < end:
                if pred():
                    return True
                time.sleep(0.25)
            return bool(pred())

        checks["first_cue_translated"] = wait_for(lambda: rendered(), 30)
        cdp.send("Target.closeTarget", {"targetId": ctl_id})  # no extension page stays open

        def post_cue(cue_id, text):
            cue = {"cueId": cue_id, "text": text, "startTime": 0, "endTime": 2}
            cdp.evaluate(page, f"window.postMessage({json.dumps({'__subtrans': 'cue', 'cue': cue})}, '*'); true")

        def destroyed_since(t0):
            return [e for e in sw_events if e[1] == "destroyed" and e[0] >= t0]

        def content_world():
            """The content script's isolated world in the tab's top frame."""
            worlds = [cid for cid, c in contexts.items()
                      if (c.get("auxData") or {}).get("type") == "isolated" and (c.get("auxData") or {}).get("frameId") == frame_id]
            return worlds[-1] if worlds else None

        timeline = summary.setdefault("timeline", {})
        # A: quiet tab, keepalive on
        t_a = time.time()
        time.sleep(args.idle)
        timeline["A_idle_s"] = round(time.time() - t_a, 1)
        timeline["A_sw_stops"] = len(destroyed_since(t_a))
        checks["A_keepalive_kept_worker"] = not destroyed_since(t_a)
        post_cue("after-idle-a", "The meeting starts at nine tomorrow morning.")
        checks["A_cue_after_idle_translated"] = wait_for(lambda: rendered("after-idle-a"), 20)

        # B: keepalive off -> Chrome's own idle timeout stops the worker
        world = content_world()
        timeline["B_content_worlds"] = len([c for c in contexts.values() if (c.get("auxData") or {}).get("type") == "isolated"
                                            and (c.get("auxData") or {}).get("frameId") == frame_id])
        timeline["B_port_before"] = cdp.evaluate(page, "(() => { const w = globalThis.subtitleWS; return w ? {port: !!w.port,"
                                                       " timer: w.keepaliveTimer != null, wanted: w.wanted} : null; })()",
                                                 context_id=world) if world else None
        cleared = cdp.evaluate(page, "(() => { const w = globalThis.subtitleWS; if (!w || !w.port) return false;"
                                     " clearInterval(w.keepaliveTimer); w.keepaliveTimer = null; return true; })()",
                               context_id=world) if world else False
        timeline["B_keepalive_cleared"] = bool(cleared)
        t_b = time.time()
        stopped = wait_for(lambda: destroyed_since(t_b), args.idle + 45)
        timeline["B_sw_stopped_after_s"] = round(destroyed_since(t_b)[0][0] - t_b, 1) if stopped else None
        checks["B_worker_stopped_by_idle_timeout"] = bool(cleared) and stopped
        time.sleep(3)  # quiet a little longer after the stop, then the next cue
        post_cue("after-idle-b", "Please call me when you get home.")
        checks["B_cue_after_idle_stop_translated"] = wait_for(lambda: rendered("after-idle-b"), 20)

        # C: worker stopped from outside
        t_c = time.time()
        cdp.send("ServiceWorker.enable", session=page)
        cdp.send("ServiceWorker.stopAllWorkers", session=page)
        timeline["C_sw_stopped"] = wait_for(lambda: destroyed_since(t_c), 10)
        time.sleep(2)
        post_cue("after-stop-c", "We need two more chairs in the kitchen.")
        checks["C_cue_after_forced_stop_translated"] = wait_for(lambda: rendered("after-stop-c"), 20)

        summary["sw_events"] = [(round(t - t_a, 1), kind) for t, kind, _ in sw_events if t >= t_a]
        summary["renders"] = [(r.get("context") or {}).get("cue_id") for r in rendered()][-8:]
        summary["errors"] = [r.get("error_message") for r in records if r.get("event") == "fail"][:6]
        return all(checks.values())
    finally:
        try:
            cdp.send("Browser.close", timeout=5)
        except Exception:
            pass
        cdp.close()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=("audio", "captions", "security", "sw-idle", "permissions", "clear-memory", "cloud-keys"), default="audio")
    ap.add_argument("--idle", type=float, default=40.0, help="sw-idle: seconds of silence per phase (Chrome's idle timeout is 30 s)")
    ap.add_argument("--wav", type=Path, default=DEFAULT_WAV)
    ap.add_argument("--video", type=Path, help="audio path: play this video file (with sound) instead of the WAV")
    ap.add_argument("--record", type=Path, help="audio path: record the tab with Playwright into this folder")
    ap.add_argument("--size", help="viewport and recording size, e.g. 1280x720")
    ap.add_argument("--screenshot", type=Path, help="audio path: save a screenshot after the first translation")
    ap.add_argument("--correct", action="store_true", help="audio path: after the first translation, correct it with Alt+E")
    ap.add_argument("--correct-text", default="corrected by the harness", help="--correct: the translation typed in its place")
    ap.add_argument("--tm", type=Path, help="--start-backend: keep the translation memory in this file between runs "
                                            "(default: a scratch file, deleted); not under backend/data")
    ap.add_argument("--font-size", type=int, help="overlay font size in px (the Options page setting; default 20)")
    ap.add_argument("--hold", type=float, default=0.0, help="audio path: keep captioning this many seconds after the first translation")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--start-backend", action="store_true")
    ap.add_argument("--source", default="auto")
    ap.add_argument("--targets", default="en,zh")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--extension", type=Path, default=EXTENSION, help="extension folder to load (default: ../extension)")
    ap.add_argument("--enable", action="store_true", help="captions: enable translation on the tab like the popup does")
    ap.add_argument("--no-test-grant", action="store_true",
                    help="load the extension as is (no 127.0.0.1 host permission; the paths that need site access fail)")
    ap.add_argument("--url", help="audio path: live-caption the first <video> of this real page (the test copy is granted "
                                  "its origin, standing in for the toolbar click); exit 2 on a consent wall or bot check")
    ap.add_argument("--start-at", type=float, default=0.0, help="--url: seek the video here (s) before capture starts")
    ap.add_argument("--backend-dir", type=Path,
                    help="--start-backend: run the backend code in this folder (another checkout's backend/) with this "
                         "checkout's venv, for before / after comparisons; pair it with --extension")
    ap.add_argument("--prime-audio", action="store_true",
                    help="audio path with --video / --url: start a near-silent tone on the page before the clip plays, "
                         "so the tab's audio output is already running (as over a video that is already playing)")
    ap.add_argument("--lines", type=int, default=0,
                    help="audio path: read the overlay (CDP) and keep the first N translated lines with their originals")
    ap.add_argument("--layout", choices=("fill", "page"), default="fill",
                    help="--video: fill = the video fills the viewport (as in fullscreen); page = a normal page, "
                         "the video in a column with text around it")
    ap.add_argument("--fullscreen", action="store_true",
                    help="--url: put the page's player into fullscreen (YouTube's f key) before the clip plays")
    ap.add_argument("--geometry", action="store_true",
                    help="audio path: sample the overlay's geometry against the video rect every tick "
                         "(summary.overlay: coverage, block placement)")
    ap.add_argument("--shots", type=Path, help="--geometry: screenshot the three moments (idle, partial, final) into this folder")
    ap.add_argument("--shot-name", help="--shots: file name prefix (<name>-idle.png, ...)")
    ap.add_argument("--partial-updates", type=int, default=0,
                    help="--geometry: poll fast and record the block's box at the first N partial-text changes")
    ap.add_argument("--show-source", action="store_true",
                    help="audio path: the source line (spoken words) on under the translation (settings.showOriginal; off by default)")
    ap.add_argument("--hover-controls", action="store_true",
                    help="audio path: keep the mouse over the player so the site's control bar is showing")
    args = ap.parse_args()
    if args.shots and not args.geometry:
        args.geometry = True
    if args.partial_updates and not args.geometry:
        args.geometry = True
    if args.url and args.path != "audio":
        ap.error("--url needs --path audio")
    if args.tm:
        try:
            args.tm = harness_tm_path(args.tm)
        except ValueError as e:
            ap.error(str(e))

    from playwright.sync_api import sync_playwright

    scratch = Path(tempfile.mkdtemp(prefix="st-ext-harness-"))
    backend = None
    summary = {"ok": False, "path": args.path, "wav": args.wav.name, "targets": args.targets}
    try:
        if args.path == "permissions":
            with sync_playwright() as pw:
                ctx = launch(pw, scratch / "profile", None, not args.headful, args.extension)
                ext_id = service_worker(ctx).url.split("/")[2]
                summary["extension_id"] = ext_id
                summary["ok"] = permissions_checks(ctx, ext_id, summary)
                ctx.close()
            return 0 if summary["ok"] else 1
        if not args.no_test_grant:
            extra = [origin_pattern(args.url)] if args.url else []
            args.extension = test_extension_copy(args.extension, scratch, extra)
            summary["test_grant"] = list(TEST_ORIGINS) + extra
        if args.start_backend:
            backend = start_backend(args.port, scratch, [extension_id_for_path(str(args.extension))], tm=args.tm,
                                    backend_dir=args.backend_dir)
        health = f"http://127.0.0.1:{args.port}/health/json"
        if not wait_http(health, 120 if args.start_backend else 5):
            summary["error"] = f"backend not reachable at {health}"
            return 1
        with urllib.request.urlopen(health, timeout=5) as r:
            summary["run_id"] = json.loads(r.read()).get("run_id")

        site = scratch / "site"
        site.mkdir()
        if not args.url:
            shutil.copy(args.wav, site / "clip.wav")
        if args.video:
            shutil.copy(args.video, site / ("clip" + args.video.suffix))
        (site / "subs.vtt").write_text(SUBS_VTT, encoding="utf-8")
        (site / "frame.html").write_text(FRAME_PAGE, encoding="utf-8")
        srv, http_port = serve_dir(site)
        srv2, http_port2 = serve_dir(site)  # a second origin for the cross-origin iframe
        page = {"audio": AUDIO_PAGE, "captions": CAPTION_PAGE, "sw-idle": IDLE_PAGE, "clear-memory": CAPTION_PAGE, "cloud-keys": CAPTION_PAGE,
                "security": SECURITY_PAGE.replace("{frame_url}", f"http://127.0.0.1:{http_port2}/frame.html")}[args.path]
        if args.video and args.path == "audio":
            page = (PAGE_LAYOUT if args.layout == "page" else DEMO_PAGE).replace("{ext}", args.video.suffix)
        (site / "index.html").write_text(page, encoding="utf-8")
        page_url = f"http://127.0.0.1:{http_port}/index.html"

        if args.path == "sw-idle":
            ok = sw_idle_run(args, page_url, scratch, summary)
            srv.shutdown()
            srv2.shutdown()
            summary["ok"] = ok
            return 0 if ok else 1

        with sync_playwright() as pw:
            # 1st launch: learn the unpacked extension's id
            ctx = launch(pw, scratch / "profile", None, not args.headful, args.extension)
            ext_id = service_worker(ctx).url.split("/")[2]
            ctx.close()
            summary["extension_id"] = ext_id
            # the backend allow-lists the id derived from the path; it must be Chrome's
            summary["extension_id_derived_ok"] = ext_id == extension_id_for_path(str(args.extension))

            # 2nd launch: allowlist it for tabCapture without a gesture
            size = tuple(int(x) for x in args.size.split("x")) if args.size else None
            ctx = launch(pw, scratch / "profile", ext_id, not args.headful, args.extension,
                         record_dir=args.record, size=size)
            sw = service_worker(ctx)
            auto = args.targets == "auto"  # English first; switched to Chinese if the speech turns out to be English
            targets = ["en"] if auto else [t for t in args.targets.split(",") if t]
            caption_mode = args.path not in ("audio", "clear-memory", "cloud-keys")  # the audio path must work with caption mode off (D2)
            sw.evaluate("""async ({url, langs, captionMode, fontSize, showSource}) => {
                const cur = (await chrome.storage.local.get('settings')).settings || {};
                const extra = fontSize ? {fontSize} : {};
                await chrome.storage.local.set({settings: {...cur, serverUrl: url, ...langs, captionMode, ...extra, showOriginal: !!showSource}});
            }""", {"url": f"ws://127.0.0.1:{args.port}/ws", "langs": target_settings(targets), "captionMode": caption_mode,
                   "fontSize": args.font_size, "showSource": args.show_source})
            if caption_mode:  # caption mode registers the content scripts for the granted origins
                end = time.time() + 10
                while time.time() < end and not sw.evaluate(
                        "chrome.scripting ? chrome.scripting.getRegisteredContentScripts().then(r => r.length) : 1"):
                    time.sleep(0.2)

            page = ctx.new_page()
            page_opened = time.time()  # the recording of this page starts about now
            records = []

            def on_console(msg):
                text = msg.text
                if "[ST-OBS]" in text:
                    try:
                        records.append(json.loads(text.split("[ST-OBS]", 1)[1].strip()))
                    except Exception:
                        pass

            page.on("console", on_console)
            if args.url:
                page.goto(args.url)
                if not wait_for_page_video(page, summary):
                    return 2 if summary.get("blocked") else 1
                page.evaluate(f"(t) => {{ const v = {PAGE_VIDEO_JS}; v.pause(); v.currentTime = t; }}", args.start_at)
                summary["page"] = {"title": page.title(), "url": page.url}
                tab_id = sw.evaluate("async (u) => (await chrome.tabs.query({})).find(t => (t.url || '').startsWith(u)).id",
                                     args.url.split("#")[0])
            else:
                page.goto(page_url)
                if args.video and args.path == "audio":  # played after ASR_START
                    page.wait_for_function("document.getElementById('v').readyState >= 3", timeout=15000)
                else:
                    page.wait_for_function("document.getElementById('v').currentTime > 0.1", timeout=15000)
                tab_id = sw.evaluate("async (u) => (await chrome.tabs.query({url: u}))[0].id", page_url)
            cdp = ctx.new_cdp_session(page) if (args.lines or args.geometry) else None
            collector = LineCollector(args.lines) if args.lines else None
            dimming = DimmingLog() if args.lines else None
            geometry = GeometryLog(args.shots, args.shot_name, args.partial_updates) if args.geometry else None
            video_expr = PAGE_VIDEO_JS if args.url else "document.getElementById('v')"
            controls_expr = "null" if args.url else "document.getElementById('controls')"
            if args.shots:
                args.shots.mkdir(parents=True, exist_ok=True)
            play_t = None
            lid_seen: list = []

            def controls_rect():
                """The site's control bar when it is showing (the harness pages' #controls; on
                YouTube .ytp-chrome-bottom unless the player has hidden it), else None."""
                try:
                    return page.evaluate("""([sel, yt]) => {
                        let el = sel ? document.getElementById(sel) : null;
                        if (yt) { const p = document.querySelector('.html5-video-player');
                                  if (p && !p.classList.contains('ytp-autohide')) el = p.querySelector('.ytp-chrome-bottom'); }
                        if (!el) return null;
                        const cs = getComputedStyle(el); if (cs.opacity === '0' || cs.visibility === 'hidden') return null;
                        const r = el.getBoundingClientRect(); return r.height ? {x: r.left, y: r.top, w: r.width, h: r.height} : null; }""",
                                         [None if args.url else "controls", bool(args.url)])
                except Exception:
                    return None

            def sample_geometry():
                """One geometry sample (and the moment's screenshot, when it is one)."""
                if geometry is None or play_t is None:
                    return
                try:
                    t = round(time.time() - play_t, 2)
                    g = read_overlay_geometry(cdp, page, video_expr)
                    moment = geometry.moment_of(g, t)
                    if moment:
                        shot = geometry.shot_path(moment)
                        if shot:
                            page.screenshot(path=str(shot))
                            g = read_overlay_geometry(cdp, page, video_expr)  # what the screenshot shows
                        geometry.record_moment(moment, g, t, shot)
                    geometry.add(g, t, controls_rect() if args.hover_controls else None)
                except Exception as e:
                    summary["geometry_errors"] = summary.get("geometry_errors", 0) + 1
                    summary["geometry_last_error"] = str(e).splitlines()[0][:200]

            def lid_certain() -> bool:
                """The content script has rendered a confirmed (or user-chosen) language."""
                return any(r.get("stage") == "ext_render" and (r.get("context") or {}).get("kind") == "lid"
                           and (r.get("context") or {}).get("lid_status") in ("confirmed", "manual") for r in records)

            def tick():
                """While captions run: read the overlay, and apply --targets auto once the language is known."""
                if collector is not None and play_t is not None:
                    try:
                        before = lid_certain()
                        lines, label = read_overlay(cdp)
                        after = lid_certain()  # console records reach us before a later DOM answer does
                        t = round(time.time() - play_t, 2)
                        collector.add(t, lines)
                        dimming.add(t, lines, label, before if before == after else None)
                    except Exception:
                        summary["overlay_read_errors"] = summary.get("overlay_read_errors", 0) + 1
                    if args.url and page.evaluate("!!document.querySelector('.ad-showing')"):
                        summary["ad_during_run"] = True
                sample_geometry()
                lids = [(r.get("context") or {}) for r in records
                        if r.get("stage") == "ext_render" and (r.get("context") or {}).get("kind") == "lid"]
                for c in lids[len(lid_seen):]:
                    lid_seen.append({"lang": c.get("lang"), "status": c.get("lid_status"),
                                     "t": round(time.time() - play_t, 2) if play_t else None})
                    if auto and c.get("lid_status") in ("confirmed", "manual") and auto_target(c["lang"]) != targets[0]:
                        targets[:] = [auto_target(c["lang"])]
                        summary["target_switched"] = {"to": targets[0], "after_lid": c.get("lang")}
                        send_from_extension_page(ctl, {"type": "UPDATE_SETTINGS", "settings": target_settings(targets)})
            ctl = ctx.new_page()
            ctl.goto(f"chrome-extension://{ext_id}/popup/popup.html")
            if args.path in ("clear-memory", "cloud-keys"):
                checks_for = clear_memory_checks if args.path == "clear-memory" else cloud_keys_checks
                ok = checks_for(ctx, sw, ext_id, args.port, summary)
                ctx.close()
                srv.shutdown()
                srv2.shutdown()
                summary["ok"] = ok
                return 0 if ok else 1
            if args.path == "audio":
                summary["tm_rows_before"] = tm_rows(args.port)
                res = send_from_extension_page(ctl, {"type": "ASR_START", "tabId": tab_id, "sourceLang": args.source})
                summary["asr_start"] = res
                if not (res and res.get("ok")):
                    summary["error"] = f"ASR_START failed: {res}"
                    return 1
                if args.video or args.url:  # capture is running: start the clip now
                    if args.prime_audio:
                        page.evaluate(PRIME_AUDIO_JS)
                        summary["prime_audio"] = True
                    if args.fullscreen and args.url:
                        # YouTube's own shortcut; a trusted key press carries the user activation
                        # the Fullscreen API needs
                        page.bring_to_front()
                        page.evaluate(f"{PAGE_VIDEO_JS}.focus()")
                        page.keyboard.press("f")
                        page.wait_for_timeout(800)
                        summary["fullscreen"] = page.evaluate("!!document.fullscreenElement")
                    if args.hover_controls:
                        v = page.evaluate(f"(() => {{ const f = {VIDEO_RECT_JS}; return f({video_expr}); }})()")
                        if v and v["video"]:
                            page.mouse.move(v["video"]["x"] + v["video"]["w"] / 2, v["video"]["y"] + v["video"]["h"] * 0.6)
                    page.evaluate(f"{PAGE_VIDEO_JS}.play()" if args.url else "document.getElementById('v').play()")
                    play_t = time.time()
                    summary["play_started_s"] = round(play_t - page_opened, 2)
                else:  # the WAV page autoplays (and loops): the geometry clock starts with capture
                    play_t = time.time()
            elif args.path == "security":
                ok = security_checks(page, ctl, tab_id, records, args.port, summary)
                ctx.close()
                srv.shutdown()
                srv2.shutdown()
                summary["ok"] = ok
                return 0 if ok else 1
            elif args.enable:
                summary["enable"] = send_from_extension_page(ctl, {"type": "SET_TAB_ENABLED", "tabId": tab_id, "enabled": True})
            page.bring_to_front()

            kinds = ("translation", "revision") if args.path == "audio" else ("backend",)
            t0 = time.time()
            got = None
            while time.time() - t0 < args.timeout and not got:
                for r in records:
                    c = r.get("context") or {}
                    if r.get("stage") == "ext_render" and (c.get("kind") in kinds or c.get("from") in kinds) and c.get("targets"):
                        got = r
                        got_at = time.time()
                        break
                if not got:
                    tick()
                    page.wait_for_timeout(250)
            summary["seconds"] = round(time.time() - t0, 1)
            summary["renders"] = [
                {k: (r.get("context") or {}).get(k) for k in ("kind", "from", "targets", "statuses")}
                for r in records if r.get("stage") == "ext_render"
            ][:12]
            summary["ws"] = [{"event": r.get("event"), **{k: (r.get("context") or {}).get(k) for k in ("path", "action", "close_code")}}
                             for r in records if r.get("stage") == "ext_ws"][:6]
            summary["errors"] = [r.get("error_message") for r in records if r.get("event") == "fail"][:5]
            if got and args.screenshot:
                page.wait_for_timeout(1200)  # the source line and the translation both on screen
                page.screenshot(path=str(args.screenshot))
                summary["screenshot"] = str(args.screenshot)
            if got and args.hold:
                end = time.time() + args.hold
                while time.time() < end:
                    tick()
                    if geometry is not None and args.partial_updates and len(geometry.updates) < args.partial_updates:
                        # between ticks: fast samples so consecutive partial updates are each seen
                        for _ in range(5):
                            page.wait_for_timeout(40)
                            sample_geometry()
                    else:
                        page.wait_for_timeout(250)
            if args.path == "audio":
                tick()
                summary["lid"] = lid_seen
                if geometry is not None:
                    summary["overlay"] = geometry.summary()
                    summary["layout"] = "url" if args.url else args.layout
                    if args.hover_controls:
                        summary["controls_rect"] = controls_rect()
                summary["detected_lang"] = next((x["lang"] for x in reversed(lid_seen) if x["status"] in ("confirmed", "manual")), None)
                summary["targets_final"] = list(targets)
            if collector is not None:
                summary["first_caption_after_play_s"] = collector.first_caption_t
                summary["first_translation_after_play_s"] = collector.first_translation_t
                summary["lines"] = collector.lines()
                summary["translated_cues"] = collector.translated_cues
                summary["dimming"] = dimming.summary()
            if args.path == "audio":
                page.wait_for_timeout(1500)  # let the other target's translation land too
                summary["tm_rows_after"] = tm_rows(args.port)
                summary["line_latency"] = line_latency_summary(records, play_t)
                summary["asr_models"] = asr_models(args.port)
                summary["finals"] = [(r.get("context") or {}).get("text_len") for r in records
                                     if r.get("stage") == "ext_render" and (r.get("context") or {}).get("kind") == "final"][:5]
                if got and args.correct:
                    summary["corrections_before"] = tm_corrections(args.port)
                    # the cue Alt+E will edit, and the page's video before any key is typed
                    target = correction_target(read_overlay(cdp or ctx.new_cdp_session(page))[0])
                    video_js = VIDEO_STATE_JS + "(" + (PAGE_VIDEO_JS if args.url else "document.getElementById('v')") + ")"
                    video_before, typed_t0 = page.evaluate(video_js), time.time()
                    # Alt+E on a live-captions-only tab (no caption socket): edit the translation on screen
                    # and press Enter; the correction must reach the backend's TM (through the worker)
                    # the clip keeps playing: new captions arrive while the line is being edited
                    page.keyboard.press("Alt+e")
                    page.wait_for_timeout(300)
                    page.keyboard.press("Control+a")
                    page.keyboard.type(args.correct_text)
                    page.keyboard.press("Enter")
                    video_after, typed_s = page.evaluate(video_js), time.time() - typed_t0
                    end = time.time() + 8
                    while time.time() < end and tm_corrections(args.port) == summary["corrections_before"]:
                        page.wait_for_timeout(250)
                    summary["corrections_after"] = tm_corrections(args.port)
                    summary["correction_saved"] = summary["corrections_after"] == summary["corrections_before"] + 1
                    summary["correction"] = {"target": target, "typed": args.correct_text,
                                             "video_before": video_before, "video_after": video_after,
                                             "page_untouched": page_untouched(video_before, video_after, typed_s)}
            if got:
                # D3: an audio session leaves no rows in the persistent TM
                summary["ok"] = args.path != "audio" or summary["tm_rows_after"] == summary["tm_rows_before"]
                c = got["context"]
                summary["first_translation"] = {"utterance_id": c.get("utterance_id"), "cue_id": c.get("cue_id"),
                                                "targets": c.get("targets"), "server_to_glass_ms": c.get("server_to_glass_ms")}
            if args.path == "audio":
                send_from_extension_page(ctl, {"type": "ASR_STOP"})
            summary["first_translation_s"] = round(got_at - page_opened, 2) if got else None
            ctx.close()
            if args.record:
                summary["video"] = page.video.path() if page.video else None
            srv.shutdown()
            srv2.shutdown()
        return 0 if summary["ok"] else 1
    finally:
        if backend is not None:
            if os.name == "nt":
                # kill the tree: llama-server is the backend's child and terminate() skips atexit
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(backend.pid)], capture_output=True)
            else:
                backend.terminate()
            try:
                backend.wait(timeout=10)
            except Exception:
                backend.kill()
        print(summary_line(summary))
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
