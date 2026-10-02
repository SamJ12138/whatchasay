"""Tiny stand-in for llama.cpp's llama-server (stdlib only).

Speaks the two endpoints the backend uses:
    GET  /health                 {"status":"ok"} (503 {"status":"loading"} during --health-delay)
    POST /v1/chat/completions    {"choices":[{"message":{"content": "<fake> <last prompt line>"}}]}

Options make it misbehave on purpose:
    --exit-code N         exit status used by every exit below (default 3)
    --exit-on-start       exit immediately (after writing --stderr lines)
    --exit-after N        exit after answering N completions
    --health-delay S      report "loading" for S seconds first
    --never-healthy       /health never says ok
    --stderr-line TEXT    write TEXT to stderr at startup (repeatable)
    --reply-prefix TEXT   prefix of every completion (default "<fake>")
Every request is also logged to stderr ("fake-llama: POST /v1/chat/completions"),
so a captured stderr tail shows what the child saw before it died.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--exit-code", type=int, default=3)
    ap.add_argument("--exit-on-start", action="store_true")
    ap.add_argument("--exit-after", type=int, default=0)
    ap.add_argument("--health-delay", type=float, default=0.0)
    ap.add_argument("--never-healthy", action="store_true")
    ap.add_argument("--stderr-line", action="append", default=[])
    ap.add_argument("--reply-prefix", default="<fake>")
    args, _unknown = ap.parse_known_args()  # tolerate real llama-server flags

    for line in args.stderr_line:
        print(line, file=sys.stderr, flush=True)
    if args.exit_on_start:
        sys.stderr.flush()
        os._exit(args.exit_code)

    started = time.time()
    served = {"n": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            print("fake-llama: " + (fmt % a), file=sys.stderr, flush=True)

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                ready = not args.never_healthy and time.time() - started >= args.health_delay
                self._json(200 if ready else 503, {"status": "ok" if ready else "loading"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path != "/v1/chat/completions":
                self._json(404, {"error": "not found"})
                return
            prompt = req["messages"][-1]["content"]
            text = prompt.strip().split("\n")[-1]
            self._json(200, {"choices": [{"message": {"content": f"{args.reply_prefix} {text}"}}]})
            with lock:
                served["n"] += 1
                done = args.exit_after and served["n"] >= args.exit_after
            if done:
                print(f"fake-llama: exiting with {args.exit_code} after {served['n']} completion(s)", file=sys.stderr, flush=True)
                threading.Timer(0.05, lambda: os._exit(args.exit_code)).start()

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
