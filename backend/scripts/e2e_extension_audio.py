"""Audio-path harness: drives the real extension's live-caption path end to end
without a human click.

    tab plays a WAV  ->  tabCapture (offscreen doc, AudioWorklet)  ->  /ws/asr
    ->  ASR + MT in the backend  ->  offscreen -> service worker -> content script

How the user gesture is avoided: Chromium's `--allowlisted-extension-id=<id>`
switch lets that extension call chrome.tabCapture.getMediaStreamId without the
action-click grant (tab_capture_api.cc). The id of an unpacked extension depends
on its path, so the browser is launched once to read it and then relaunched
with the switch. Do not add --use-fake-ui-for-media-stream: with it the
offscreen getUserMedia({chromeMediaSource:'tab'}) fails with "Requested device
not found". The audio is real tab audio (a <video> element playing the WAV,
autoplay allowed by --autoplay-policy), not a fake capture device. ASR_START is sent from an extension page exactly like the
popup does. Success = the content script of the captured tab logs an
`ext_render` record for a translation (extension/obs.js writes every record to
console.debug with the [ST-OBS] prefix).

Needs: a Python with `playwright` (pip install playwright; the Chromium build
in %LOCALAPPDATA%/ms-playwright is used), and either a backend already running
on --port or --start-backend (uses backend/venv and a scratch translation
memory, never backend/data/translation_memory.db).

    python backend/scripts/e2e_extension_audio.py --start-backend
    python backend/scripts/e2e_extension_audio.py --wav path/to.wav --targets zh,bn

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

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>audio harness</title></head>
<body><h1>audio harness</h1><video id="v" src="/clip.wav" autoplay loop controls></video>
<script>const v=document.getElementById('v');v.play().catch(e=>console.log('play failed',e));</script>
</body></html>"""


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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


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
    env["SUBTITLE_SERVER__PORT"] = str(port)
    log = open(scratch / "backend.log", "w", encoding="utf-8")
    return subprocess.Popen([str(py), "run.py", "--port", str(port)], cwd=str(BACKEND), env=env,
                            stdout=log, stderr=subprocess.STDOUT)


def launch(pw, user_dir: Path, ext_id: str | None, headless: bool):
    args = [
        f"--disable-extensions-except={EXTENSION}",
        f"--load-extension={EXTENSION}",
        "--autoplay-policy=no-user-gesture-required",
    ]
    if ext_id:
        args.append(f"--allowlisted-extension-id={ext_id}")
    if headless:
        args.append("--headless=new")
    return pw.chromium.launch_persistent_context(str(user_dir), headless=False, args=args)


def extension_id(ctx) -> str:
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
    return sw.url.split("/")[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", type=Path, default=DEFAULT_WAV)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--start-backend", action="store_true")
    ap.add_argument("--source", default="auto")
    ap.add_argument("--targets", default="en,zh")
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    scratch = Path(tempfile.mkdtemp(prefix="st-audio-harness-"))
    backend = None
    summary = {"ok": False, "wav": args.wav.name, "targets": args.targets}
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
        (site / "index.html").write_text(PAGE, encoding="utf-8")
        srv, http_port = serve_dir(site)
        page_url = f"http://127.0.0.1:{http_port}/index.html"

        with sync_playwright() as pw:
            # 1st launch: learn the unpacked extension's id
            ctx = launch(pw, scratch / "profile", None, not args.headful)
            ext_id = extension_id(ctx)
            ctx.close()
            summary["extension_id"] = ext_id

            # 2nd launch: allowlist it for tabCapture without a gesture
            ctx = launch(pw, scratch / "profile", ext_id, not args.headful)
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
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
            page.wait_for_function("document.getElementById('v').currentTime > 0.2", timeout=15000)

            tab_id = sw.evaluate("async (u) => (await chrome.tabs.query({url: u}))[0].id", page_url)
            ctl = ctx.new_page()
            ctl.goto(f"chrome-extension://{ext_id}/popup/popup.html")
            res = ctl.evaluate("""([tabId, src]) => new Promise(r =>
                chrome.runtime.sendMessage({type: 'ASR_START', tabId, sourceLang: src}, r))""", [tab_id, args.source])
            summary["asr_start"] = res
            if not (res and res.get("ok")):
                summary["error"] = f"ASR_START failed: {res}"
                return 1
            page.bring_to_front()

            t0 = time.time()
            got = None
            while time.time() - t0 < args.timeout:
                for r in records:
                    c = r.get("context") or {}
                    if r.get("stage") == "ext_render" and c.get("kind") in ("translation", "revision") and c.get("targets"):
                        got = r
                        break
                if got:
                    break
                page.wait_for_timeout(250)
            summary["seconds"] = round(time.time() - t0, 1)
            summary["renders"] = [
                {"kind": (r.get("context") or {}).get("kind"), "targets": (r.get("context") or {}).get("targets"),
                 "statuses": (r.get("context") or {}).get("statuses")}
                for r in records if r.get("stage") == "ext_render"
            ][:12]
            summary["errors"] = [r.get("error_message") for r in records if r.get("event") == "fail"][:5]
            if got:
                summary["ok"] = True
                summary["first_translation"] = {"utterance_id": got["context"].get("utterance_id"),
                                                "targets": got["context"].get("targets"),
                                                "server_to_glass_ms": got["context"].get("server_to_glass_ms")}
            ctl.evaluate("() => new Promise(r => chrome.runtime.sendMessage({type: 'ASR_STOP'}, r))")
            ctx.close()
            srv.shutdown()
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
