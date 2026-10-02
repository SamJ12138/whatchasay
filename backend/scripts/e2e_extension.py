"""Browser harness: drives the real unpacked extension in Playwright Chromium,
end to end, with no human click. Two paths:

  --path audio     (default) live captions:
                   tab plays a WAV -> tabCapture (offscreen doc, AudioWorklet) -> /ws/asr
                   -> ASR + MT in the backend -> offscreen -> service worker -> content script
  --path captions  existing subtitles:
                   <video> with a WebVTT <track> -> subtitle detector -> service worker
                   socket /ws (cue) -> MT -> content script overlay
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


def start_backend(port: int, scratch: Path) -> subprocess.Popen:
    py = BACKEND / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
    env = dict(os.environ)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", choices=("audio", "captions", "security"), default="audio")
    ap.add_argument("--wav", type=Path, default=DEFAULT_WAV)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--start-backend", action="store_true")
    ap.add_argument("--source", default="auto")
    ap.add_argument("--targets", default="en,zh")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--headful", action="store_true")
    ap.add_argument("--extension", type=Path, default=EXTENSION, help="extension folder to load (default: ../extension)")
    ap.add_argument("--enable", action="store_true", help="captions: enable translation on the tab like the popup does")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    scratch = Path(tempfile.mkdtemp(prefix="st-ext-harness-"))
    backend = None
    summary = {"ok": False, "path": args.path, "wav": args.wav.name, "targets": args.targets}
    try:
        if args.start_backend:
            backend = start_backend(args.port, scratch)
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
        page = {"audio": AUDIO_PAGE, "captions": CAPTION_PAGE,
                "security": SECURITY_PAGE.replace("{frame_url}", f"http://127.0.0.1:{http_port2}/frame.html")}[args.path]
        (site / "index.html").write_text(page, encoding="utf-8")
        page_url = f"http://127.0.0.1:{http_port}/index.html"

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
            sw.evaluate("""async ({url, targets}) => {
                const cur = (await chrome.storage.local.get('settings')).settings || {};
                await chrome.storage.local.set({settings: {...cur, serverUrl: url, targetLanguages: targets}});
            }""", {"url": f"ws://127.0.0.1:{args.port}/ws", "targets": targets})

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
            if args.path == "audio":
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
            if got:
                summary["ok"] = True
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
