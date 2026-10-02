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

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
EXTENSION = REPO / "extension"
DEFAULT_WAV = BACKEND / "data/models/asr/sherpa-onnx-streaming-zipformer-en-2023-06-26/test_wavs/0.wav"

AUDIO_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>audio harness</title></head>
<body><h1>audio harness</h1><video id="v" src="/clip.wav" autoplay loop controls></video>
<script>const v=document.getElementById('v');v.play().catch(e=>console.log('play failed',e));</script>
</body></html>"""

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


def test_extension_copy(extension: Path, scratch: Path) -> Path:
    """The extension with the test page's origin and tab capture granted (stand-ins
    for the user's activeTab click, caption-mode grant and first-use tabCapture
    prompt, which automation cannot give)."""
    dst = scratch / "extension-test"
    shutil.copytree(extension, dst, ignore=shutil.ignore_patterns("tests", "node_modules"))
    mf = dst / "manifest.json"
    m = json.loads(mf.read_text(encoding="utf-8"))
    m["host_permissions"] = sorted(set(m.get("host_permissions") or []) | set(TEST_ORIGINS))
    # tab audio capture is optional (asked for on the popup's Start click): grant it at install here
    m["permissions"] = sorted(set(m.get("permissions") or []) | {"tabCapture"})
    mf.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return dst.resolve()


def start_backend(port: int, scratch: Path, extension_ids=()) -> subprocess.Popen:
    py = BACKEND / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    env = dict(os.environ)
    if extension_ids:
        env["SUBTITLE_SERVER__EXTENSION_IDS"] = json.dumps(list(extension_ids))
    env["SUBTITLE_CACHE__TM_DATABASE_PATH"] = str(scratch / "tm.db")
    env["SUBTITLE_DATA_DIR"] = str(scratch / "data")
    env["SUBTITLE_SERVER__PORT"] = str(port)
    log = open(scratch / "backend.log", "w", encoding="utf-8")
    return subprocess.Popen([str(py), "run.py", "--port", str(port)], cwd=str(BACKEND), env=env,
                            stdout=log, stderr=subprocess.STDOUT)


def launch(pw, user_dir: Path, ext_id: str | None, headless: bool, extension: Path = EXTENSION):
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
    return pw.chromium.launch_persistent_context(str(user_dir), headless=False, args=args)


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


def tm_rows(port: int) -> int:
    """Rows in the backend's (scratch) translation memory."""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=5) as r:  # no Origin: allowed
        return json.loads(r.read())["translation_memory"]["total_translations"]


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
    opt.wait_for_function("document.getElementById('connectionStatus').textContent.includes('Connected')", timeout=15000)
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
                          f" targetLanguages: {json.dumps(targets)}, captionMode: true}}}}); return true; }})()")
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
    ap.add_argument("--path", choices=("audio", "captions", "security", "sw-idle", "permissions", "clear-memory"), default="audio")
    ap.add_argument("--idle", type=float, default=40.0, help="sw-idle: seconds of silence per phase (Chrome's idle timeout is 30 s)")
    ap.add_argument("--wav", type=Path, default=DEFAULT_WAV)
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
    args = ap.parse_args()

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
            args.extension = test_extension_copy(args.extension, scratch)
            summary["test_grant"] = list(TEST_ORIGINS)
        if args.start_backend:
            backend = start_backend(args.port, scratch, [extension_id_for_path(str(args.extension))])
        health = f"http://127.0.0.1:{args.port}/health/json"
        if not wait_http(health, 120 if args.start_backend else 5):
            summary["error"] = f"backend not reachable at {health}"
            return 1

        site = scratch / "site"
        site.mkdir()
        shutil.copy(args.wav, site / "clip.wav")
        (site / "subs.vtt").write_text(SUBS_VTT, encoding="utf-8")
        (site / "frame.html").write_text(FRAME_PAGE, encoding="utf-8")
        srv, http_port = serve_dir(site)
        srv2, http_port2 = serve_dir(site)  # a second origin for the cross-origin iframe
        page = {"audio": AUDIO_PAGE, "captions": CAPTION_PAGE, "sw-idle": IDLE_PAGE, "clear-memory": CAPTION_PAGE,
                "security": SECURITY_PAGE.replace("{frame_url}", f"http://127.0.0.1:{http_port2}/frame.html")}[args.path]
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

            # 2nd launch: allowlist it for tabCapture without a gesture
            ctx = launch(pw, scratch / "profile", ext_id, not args.headful, args.extension)
            sw = service_worker(ctx)
            targets = [t for t in args.targets.split(",") if t]
            caption_mode = args.path not in ("audio", "clear-memory")  # the audio path must work with caption mode off (D2)
            sw.evaluate("""async ({url, targets, captionMode}) => {
                const cur = (await chrome.storage.local.get('settings')).settings || {};
                await chrome.storage.local.set({settings: {...cur, serverUrl: url, targetLanguages: targets, captionMode}});
            }""", {"url": f"ws://127.0.0.1:{args.port}/ws", "targets": targets, "captionMode": caption_mode})
            if caption_mode:  # caption mode registers the content scripts for the granted origins
                end = time.time() + 10
                while time.time() < end and not sw.evaluate(
                        "chrome.scripting ? chrome.scripting.getRegisteredContentScripts().then(r => r.length) : 1"):
                    time.sleep(0.2)

            page = ctx.new_page()
            records = []

            def on_console(msg):
                text = msg.text
                if "[ST-OBS]" in text:
                    try:
                        records.append(json.loads(text.split("[ST-OBS]", 1)[1].strip()))
                    except Exception:
                        pass

            page.on("console", on_console)
            page.goto(page_url)
            page.wait_for_function("document.getElementById('v').currentTime > 0.1", timeout=15000)

            tab_id = sw.evaluate("async (u) => (await chrome.tabs.query({url: u}))[0].id", page_url)
            ctl = ctx.new_page()
            ctl.goto(f"chrome-extension://{ext_id}/popup/popup.html")
            if args.path == "clear-memory":
                ok = clear_memory_checks(ctx, sw, ext_id, args.port, summary)
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
                        break
                if not got:
                    page.wait_for_timeout(250)
            summary["seconds"] = round(time.time() - t0, 1)
            summary["renders"] = [
                {k: (r.get("context") or {}).get(k) for k in ("kind", "from", "targets", "statuses")}
                for r in records if r.get("stage") == "ext_render"
            ][:12]
            summary["ws"] = [{"event": r.get("event"), **{k: (r.get("context") or {}).get(k) for k in ("path", "action", "close_code")}}
                             for r in records if r.get("stage") == "ext_ws"][:6]
            summary["errors"] = [r.get("error_message") for r in records if r.get("event") == "fail"][:5]
            if args.path == "audio":
                page.wait_for_timeout(1500)  # let the other target's translation land too
                summary["tm_rows_after"] = tm_rows(args.port)
            if got:
                # D3: an audio session leaves no rows in the persistent TM
                summary["ok"] = args.path != "audio" or summary["tm_rows_after"] == summary["tm_rows_before"]
                c = got["context"]
                summary["first_translation"] = {"utterance_id": c.get("utterance_id"), "cue_id": c.get("cue_id"),
                                                "targets": c.get("targets"), "server_to_glass_ms": c.get("server_to_glass_ms")}
            if args.path == "audio":
                send_from_extension_page(ctl, {"type": "ASR_STOP"})
            ctx.close()
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
        print(json.dumps(summary, ensure_ascii=False))
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
