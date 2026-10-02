"""Alt+E corrections on a tab that only runs live captions (no caption-path /ws socket).

The extension's service worker posts them to POST /corrections; the backend stores them
like a /ws correction and clears every in-memory cache, live sessions' own caches
included, so the very next identical sentence in the running session shows the fix."""

import pytest

from app.translation import pipeline as pipe_mod
from tests.fakes import pcm_frame

BODY = {"cue_id": "asr_3", "source_text": "hello world", "source_lang": "en",
        "original_translation": {"zh": "machine"}, "corrected_translation": {"zh": "你好，世界"}}


def test_corrections_endpoint_stores_the_correction(app_env):
    with app_env.client() as c:
        r = c.post("/corrections", json=BODY)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "saved"
        # the correction is now what /translate serves for that sentence
        d = c.post("/translate", json={"text": "hello world", "source_lang": "en", "target_languages": ["zh"]}).json()
    zh = d["translations"]["zh"]
    assert zh["single_line"] == "你好，世界", d


def test_corrections_endpoint_rejects_a_bad_body(app_env):
    with app_env.client() as c:
        assert c.post("/corrections", json={"cue_id": "x"}).status_code == 422


def test_a_correction_clears_live_session_caches(app_env):
    policy = pipe_mod.audio_session_policy()  # a running /ws/asr session's own cache
    policy.cache.set("hello world", "en", ("zh",), {"zh": "stale"})
    assert policy.cache.stats["size"] == 1
    with app_env.client() as c:
        assert c.post("/corrections", json=BODY).status_code == 200
    assert policy.cache.stats["size"] == 0


def _next_translation(ws):
    while True:
        m = ws.receive_json()
        if m["type"] == "translation":
            return m["translations"]["zh"]["single_line"]


def test_correction_applies_to_the_next_sentence_in_a_running_live_session(app_env):
    """Same sentence twice in one /ws/asr session, a correction posted in between."""
    app_env.asr.text = "hello world"
    app_env.asr.numbered = False
    app_env.asr.final_every = 3
    seen = []
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs="zh")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(3):
                ws.send_bytes(pcm_frame())
            seen.append(_next_translation(ws))
            r = c.post("/corrections", json={**BODY, "original_translation": {"zh": seen[0]}})
            assert r.status_code == 200, r.text
            for _ in range(3):
                ws.send_bytes(pcm_frame())
            seen.append(_next_translation(ws))
    assert seen[0] != "你好，世界"
    assert seen[1] == "你好，世界", seen
