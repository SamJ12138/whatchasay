"""Batch 3: origin checks on HTTP and WebSockets, localhost bind (W8).

Allowed origins: the extension (chrome-extension://<id>; the id of the
unpacked extension next to this backend is derived from its path, more ids
from settings.server.extension_ids) and the backend's own loopback origin
(the /debug test page). Requests without an Origin header (curl, scripts,
tests) are not browser cross-site requests and are allowed.
"""

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest

from app.config import settings

EVIL = "https://evil.example"


def own_origin():
    return f"http://127.0.0.1:{settings.server.port}"


def ext_origin():
    from app.security import local_extension_id

    return f"chrome-extension://{local_extension_id()}"


# ---------------------------------------------------------------- extension id


def test_unpacked_extension_id_is_derived_like_chrome():
    from app.security import extension_id_for_path

    # Chrome derives an unpacked extension's id from its absolute path
    # (SHA-256 of the path bytes, first 128 bits, 0-f -> a-p). This pair was
    # observed in Chromium (Playwright) on the owner's machine.
    path = r"<repo>\extension"
    assert extension_id_for_path(path, windows=True) == "<extension-id>"
    # the drive letter is case-normalised by Chrome
    assert extension_id_for_path("c" + path[1:], windows=True) == "<extension-id>"
    eid = extension_id_for_path("/srv/subtitle-translator/extension", windows=False)
    assert len(eid) == 32 and set(eid) <= set("abcdefghijklmnop")


def test_allowed_origins_are_the_extension_and_own_loopback_only(monkeypatch):
    from app.security import allowed_origins, origin_allowed

    monkeypatch.setattr(settings.server, "extension_ids", ["abcdefghijklmnopabcdefghijklmnop"])
    allowed = allowed_origins()
    assert ext_origin() in allowed
    assert "chrome-extension://abcdefghijklmnopabcdefghijklmnop" in allowed
    assert own_origin() in allowed
    assert origin_allowed(None)  # no Origin header: not a browser cross-site request
    for bad in (EVIL, "null", "chrome-extension://someotherextensionidxxxxxxxxxxxx", "http://127.0.0.1:9999",
                "http://localhost.evil.example:8765", "*"):
        assert not origin_allowed(bad), bad


# ---------------------------------------------------------------- HTTP + CORS


def test_cors_preflight_from_evil_gets_no_allow_origin(app_env):
    with app_env.client() as c:
        r = c.options("/translate", headers={"Origin": EVIL, "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "content-type"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
    assert r.status_code in (400, 403)


def test_cors_preflight_from_extension_is_allowed_and_not_wildcard(app_env):
    with app_env.client() as c:
        r = c.options("/translate", headers={"Origin": ext_origin(), "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "content-type,x-session-id"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ext_origin()


def test_cross_site_requests_are_refused_and_never_reflected(app_env):
    with app_env.client() as c:
        for method, path, kw in (("get", "/health/json", {}), ("post", "/cache/clear", {}),
                                 ("post", "/config/speed_mode?mode=hybrid", {}),
                                 ("post", "/translate", {"json": {"text": "hi", "target_languages": ["zh"]}})):
            r = getattr(c, method)(path, headers={"Origin": EVIL}, **kw)
            assert r.status_code == 403, (path, r.status_code)
            assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
        assert settings.features.speed_mode == "fast"  # the cross-site POST did not run
        assert c.get("/health/json", headers={"Origin": ext_origin()}).headers["access-control-allow-origin"] == ext_origin()
        assert c.get("/health/json").status_code == 200  # no Origin (curl, scripts)
        assert c.get("/health/json", headers={"Origin": own_origin()}).status_code == 200  # /debug page


# ---------------------------------------------------------------- WebSockets


@pytest.mark.parametrize("path", ["/ws", "/ws/asr?source_lang=en&target_langs=zh"])
def test_websocket_from_foreign_origin_is_refused_in_process(app_env, path):
    from starlette.websockets import WebSocketDisconnect

    def exchange(ws):
        # one round trip that an accepted connection always answers
        if path.startswith("/ws/asr"):
            return ws.receive_json()["type"]  # "ready"
        ws.send_json({"type": "ping", "correlation_id": "p"})
        return ws.receive_json()["type"]  # "result"

    with app_env.client() as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(path, headers={"Origin": EVIL}) as ws:
                exchange(ws)
        with c.websocket_connect(path, headers={"Origin": ext_origin()}) as ws:
            assert exchange(ws) in ("ready", "result")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(app_env):
    import uvicorn
    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield port
    server.should_exit = True
    t.join(timeout=10)


@pytest.mark.parametrize("path", ["/ws", "/ws/asr"])
def test_websocket_handshake_from_foreign_origin_gets_403(live_server, path):
    import websockets

    async def attempt(origin):
        try:
            async with websockets.connect(f"ws://127.0.0.1:{live_server}{path}", origin=origin, open_timeout=5):
                return 101
        except websockets.exceptions.InvalidStatusCode as e:
            return e.status_code

    assert asyncio.run(attempt(EVIL)) == 403
    assert asyncio.run(attempt(ext_origin())) == 101


# ---------------------------------------------------------------- bind address


def test_backend_binds_loopback_only():
    from app.security import check_bind_host

    assert settings.server.host == "127.0.0.1"
    import run

    assert run.parse_args.__defaults__ is None  # parse_args reads settings.server.host
    for host in ("127.0.0.1", "localhost", "::1"):
        assert check_bind_host(host) == host
    for host in ("0.0.0.0", "::", "192.168.1.20", ""):
        with pytest.raises(ValueError):
            check_bind_host(host)


def test_run_py_refuses_a_public_bind(monkeypatch, capsys):
    import run

    monkeypatch.setattr("sys.argv", ["run.py", "--host", "0.0.0.0"])
    started = []
    monkeypatch.setattr(run, "run_server", lambda args: started.append(args))
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code != 0
    assert not started
