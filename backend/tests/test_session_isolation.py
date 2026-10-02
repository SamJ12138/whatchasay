"""Batch 4 (W7): a tab's /ws config applies to that session only.

Two sessions set different target languages, refiner preference and line
limits; each gets its own; the server-wide defaults do not move.
"""

import pytest

from app.config import settings
from tests.conftest import ws_recv

LONG = "Where did you put the keys that I left on the kitchen table this morning before work?"


def snapshot():
    return {
        "targets": list(settings.translation.target_languages),
        "refiner": settings.refiner.enabled,
        "post_editor": settings.features.use_post_editor,
        "max_lines": settings.translation.max_lines,
        "max_chars": dict(settings.translation.max_chars_by_lang),
        "fast_mode": settings.features.fast_mode,
    }


def cue_msg(cid, text="Hello there", src="en"):
    return {"type": "cue", "correlation_id": cid,
            "payload": {"cue": {"text": text, "start_time": 0, "end_time": 1, "source_lang": src}}}


def config(ws, cid, **payload):
    ws.send_json({"type": "config", "correlation_id": cid, "payload": payload})
    m = ws_recv(ws)
    assert m["type"] == "result", m
    return m["payload"]


def test_two_sessions_keep_their_own_targets_and_the_default_is_unchanged(app_env):
    before = snapshot()
    with app_env.client() as c, \
            c.websocket_connect(app_env.ws_url("/ws", "tab-a")) as a, \
            c.websocket_connect(app_env.ws_url("/ws", "tab-b")) as b:
        config(a, "ca", target_languages=["zh"])
        config(b, "cb", target_languages=["bn"])
        a.send_json(cue_msg("a1"))
        b.send_json(cue_msg("b1"))
        ra, rb = ws_recv(a), ws_recv(b)
    assert set(ra["payload"]["translations"]) == {"zh"}
    assert set(rb["payload"]["translations"]) == {"bn"}
    assert snapshot() == before, "a tab's config changed the server-wide defaults"


def test_config_reply_reports_the_session_config_not_the_globals(app_env):
    with app_env.client() as c, c.websocket_connect(app_env.ws_url("/ws", "tab-a")) as a:
        p = config(a, "ca", target_languages=["bn"], max_lines=1, refiner_enabled=True)
    cur = p["current_config"]
    assert cur["target_languages"] == ["bn"]
    assert cur["max_lines"] == 1
    assert cur["refiner_enabled"] is True
    assert settings.translation.target_languages == ["en", "zh"]


def test_line_limits_are_per_session(app_env):
    before = snapshot()
    with app_env.client() as c, \
            c.websocket_connect(app_env.ws_url("/ws", "tab-a")) as a, \
            c.websocket_connect(app_env.ws_url("/ws", "tab-b")) as b:
        config(a, "ca", target_languages=["zh"], max_lines=1, max_chars_by_lang={"zh": 200})
        config(b, "cb", target_languages=["zh"])
        a.send_json(cue_msg("a1", LONG))
        ra = ws_recv(a)
        b.send_json(cue_msg("b1", LONG))
        rb = ws_recv(b)
    assert len(ra["payload"]["translations"]["zh"]["lines"]) == 1
    assert len(rb["payload"]["translations"]["zh"]["lines"]) == 2
    assert snapshot() == before


def test_refiner_preference_is_per_session(app_env, monkeypatch):
    from app.translation import refiner as refiner_mod

    class FakeRefiner:
        async def refine(self, items, langs, prev_cues=None):
            return [{l: it["base_translations"][l] + " (refined)" for l in langs if l in it["base_translations"]} for it in items]

    monkeypatch.setattr(refiner_mod, "get_refiner", lambda: FakeRefiner())
    before = snapshot()
    with app_env.client() as c, \
            c.websocket_connect(app_env.ws_url("/ws", "tab-a")) as a, \
            c.websocket_connect(app_env.ws_url("/ws", "tab-b")) as b:
        config(a, "ca", target_languages=["zh"], refiner_enabled=True)
        config(b, "cb", target_languages=["zh"], refiner_enabled=False)
        a.send_json(cue_msg("a1", "Refine this one"))
        assert ws_recv(a)["type"] == "result"
        rev = ws_recv(a)
        assert rev is not None, "session a asked for refinement and got no revision"
        assert rev["type"] == "revision" and rev["payload"]["translations"]["zh"]["single_line"].endswith("(refined)")
        b.send_json(cue_msg("b1", "Do not refine this one"))
        assert ws_recv(b)["type"] == "result"
        assert ws_recv(b, timeout=1.0) is None, "session b got a revision it did not ask for"
    assert snapshot() == before
