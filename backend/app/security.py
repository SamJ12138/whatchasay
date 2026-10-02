"""
Who may talk to the backend (W8).

The backend listens on loopback only (check_bind_host). Browsers still let any
web page send requests to 127.0.0.1 and open WebSockets to it, and they put the
page's origin in the Origin header. So every HTTP request and WebSocket
handshake that carries an Origin must come from:

    chrome-extension://<id>   the extension: the id of the unpacked extension
                              next to this backend (../extension, derived from
                              its path the way Chrome does) plus any id in
                              settings.server.extension_ids (store installs,
                              copies loaded from elsewhere)
    http://127.0.0.1:<port>   the backend's own pages (/debug test page)
    http://localhost:<port>

Anything else gets 403 (WebSockets: before the upgrade) and CORS never
reflects it. A request without an Origin header is not a browser cross-site
request (curl, scripts, tests) and is allowed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import sys
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Set

from .config import settings

EXTENSION_DIR = Path(__file__).resolve().parents[2] / "extension"
LOOPBACK_NAMES = {"localhost"}


def extension_id_for_path(path: str, windows: Optional[bool] = None) -> str:
    """Chrome's id for an unpacked extension loaded from `path`
    (crx_file::id_util::GenerateIdForPath): SHA-256 of the path's native bytes
    (UTF-16LE with an upper-case drive letter on Windows, UTF-8 elsewhere),
    first 16 bytes as hex, digits 0-f mapped to letters a-p."""
    windows = sys.platform == "win32" if windows is None else windows
    if windows:
        if len(path) > 1 and path[1] == ":":
            path = path[0].upper() + path[1:]
        data = path.encode("utf-16-le")
    else:
        data = path.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


@lru_cache(maxsize=1)
def local_extension_id() -> str:
    return extension_id_for_path(str(EXTENSION_DIR))


def allowed_origins() -> Set[str]:
    ids: Iterable[str] = list(settings.server.extension_ids or [])
    if settings.server.allow_local_extension:
        ids = [local_extension_id(), *ids]
    port = settings.server.port
    origins = {f"chrome-extension://{i.strip()}" for i in ids if i and i.strip()}
    origins |= {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}
    origins |= {o.rstrip("/") for o in (settings.server.extra_origins or [])}
    return origins


def origin_allowed(origin: Optional[str]) -> bool:
    if origin is None:
        return True
    return origin.rstrip("/") in allowed_origins()


def check_bind_host(host: str) -> str:
    """Return host if it is a loopback address, else raise ValueError."""
    h = (host or "").strip().strip("[]")
    if h.lower() in LOOPBACK_NAMES:
        return host
    try:
        if ipaddress.ip_address(h).is_loopback:
            return host
    except ValueError:
        pass
    raise ValueError(f"refusing to bind {host!r}: the backend serves the local extension only; use 127.0.0.1")


class OriginGuard:
    """ASGI middleware: refuse HTTP requests and WebSocket handshakes whose
    Origin header is not allowed. WebSockets are closed before accept, which
    uvicorn answers with HTTP 403 (no upgrade)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        origin = None
        for k, v in scope.get("headers") or []:
            if k == b"origin":
                origin = v.decode("latin-1")
                break
        if origin_allowed(origin):
            return await self.app(scope, receive, send)

        from . import obs

        path = scope.get("path", "")
        if scope["type"] == "websocket":
            obs.log("ws_connection", "fail", error_type="input_invalid", path=path, origin=origin[:100],
                    error_message="origin not allowed; handshake refused with 403")
            msg = await receive()  # websocket.connect
            if msg.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 1008})
            return
        obs.log("http", "fail", error_type="input_invalid", path=path, method=scope.get("method"), status=403,
                origin=origin[:100], error_message="origin not allowed")
        body = b'{"error":"origin not allowed"}'
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
