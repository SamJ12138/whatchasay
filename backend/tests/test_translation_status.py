"""Batch 2: no failed translation is stored or reported as success (T1-T8, T12, W6).

Every target in a /translate, /ws or /ws/asr reply carries status ok | fallback |
untranslated | error (error with error_type). Source text appears in a target
only as a placeholder with status untranslated. Failed/untranslated output never
reaches the TM or the memory cache.
"""

import asyncio
import sqlite3

import pytest

from app.models import SubtitleCue
from tests.fakes import FakeMTEngine, pcm_frame

PRIMARY_Q, FALLBACK_Q = 0.8, 0.6


def cue(text="Where did you put the keys?", src="en", **kw):
    return SubtitleCue(text=text, start_time=0, end_time=2, source_lang=src, **kw)


async def tm_row(env, text, src, tgt):
    return await env.tm.get(text, src, tgt)


# ---------------------------------------------------------------- T1 / T2 / T4


@pytest.mark.asyncio
async def test_no_engine_for_pair_is_untranslated_placeholder_not_stored(app_env):
    app_env.set_engines(fake=FakeMTEngine("fake", pairs={("en", "zh")}))
    res = await app_env.pipeline.translate_cue(cue(), ["zh", "bn"])
    zh, bn = res.translations["zh"], res.translations["bn"]
    assert zh.status == "ok" and zh.engine == "fake"
    assert bn.status == "untranslated"
    assert bn.single_line == "Where did you put the keys?"  # placeholder, allowed only here
    assert bn.error_type == "input_invalid"
    assert await tm_row(app_env, "Where did you put the keys?", "en", "bn") is None
    # not cached either: a second call asks the router again
    res2 = await app_env.pipeline.translate_cue(cue(), ["zh", "bn"])
    assert not res2.from_cache and res2.translations["bn"].status == "untranslated"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,error_type", [("fail", "unknown"), ("timeout", "timeout"), ("empty", "parse")])
async def test_engine_failure_without_fallback_is_error_never_source(app_env, mode, error_type):
    app_env.set_engines(fake=FakeMTEngine("fake", mode=mode))
    res = await app_env.pipeline.translate_cue(cue(), ["zh"])
    zh = res.translations["zh"]
    assert zh.status == "error"
    assert zh.error_type == error_type
    assert zh.error
    assert zh.single_line == "" and zh.lines == []
    assert await tm_row(app_env, "Where did you put the keys?", "en", "zh") is None
    res2 = await app_env.pipeline.translate_cue(cue(), ["zh"])
    assert not res2.from_cache


@pytest.mark.asyncio
async def test_engine_echoing_the_source_is_untranslated_and_not_stored(app_env):
    app_env.set_engines(fake=FakeMTEngine("fake", mode="source"))
    res = await app_env.pipeline.translate_cue(cue(), ["zh"])
    assert res.translations["zh"].status == "untranslated"
    assert await tm_row(app_env, "Where did you put the keys?", "en", "zh") is None


@pytest.mark.asyncio
async def test_router_exception_is_error_not_source(app_env, monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("router exploded")

    monkeypatch.setattr(app_env.translator, "translate_batch_detailed", broken)
    res = await app_env.pipeline.translate_cue(cue(), ["zh"])
    zh = res.translations["zh"]
    assert zh.status == "error" and zh.single_line == "" and zh.error_type
    assert await tm_row(app_env, "Where did you put the keys?", "en", "zh") is None


# ---------------------------------------------------------------- T5


@pytest.mark.asyncio
async def test_whole_batch_failure_returns_error_targets_not_source(app_env, monkeypatch):
    async def tm_down(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app_env.tm, "get", tm_down)
    res = await app_env.pipeline.translate_cue(cue(), ["zh", "bn"])
    for t in ("zh", "bn"):
        tl = res.translations[t]
        assert tl.status == "error"
        assert tl.error_type == "process"
        assert tl.single_line == "" and tl.lines == []
    assert res.notes and res.notes.get("error")


# ---------------------------------------------------------------- T3 fallback


@pytest.mark.asyncio
async def test_fallback_is_marked_stored_lower_and_replaced_by_primary(app_env):
    primary = FakeMTEngine("primary", mode="fail")
    backup = FakeMTEngine("backup")
    app_env.set_engines(primary=primary, backup=backup)
    text = "Where did you put the keys?"

    res = await app_env.pipeline.translate_cue(cue(text), ["zh"])
    zh = res.translations["zh"]
    assert zh.status == "fallback" and zh.engine == "backup"
    assert "backup" in zh.single_line
    row = await tm_row(app_env, text, "en", "zh")
    assert row and row["engine"] == "backup" and row["quality_score"] == pytest.approx(FALLBACK_Q)

    # primary is back: the fallback row must not be served as final; primary result replaces it
    primary.mode = "ok"
    res = await app_env.pipeline.translate_cue(cue(text), ["zh"])
    zh = res.translations["zh"]
    assert zh.status == "ok" and zh.engine == "primary"
    row = await tm_row(app_env, text, "en", "zh")
    assert row["engine"] == "primary" and row["quality_score"] == pytest.approx(PRIMARY_Q)

    # a later fallback write never downgrades the primary row
    await app_env.tm.set(text, "en", "zh", "worse", ["worse"], quality_score=FALLBACK_Q, engine="backup")
    row = await tm_row(app_env, text, "en", "zh")
    assert row["engine"] == "primary"


@pytest.mark.asyncio
async def test_fallback_row_is_best_available_when_primary_still_down(app_env):
    primary = FakeMTEngine("primary", mode="fail")
    backup = FakeMTEngine("backup")
    app_env.set_engines(primary=primary, backup=backup)
    text = "Where did you put the keys?"
    await app_env.pipeline.translate_cue(cue(text), ["zh"])
    app_env.pipeline._memory_cache.clear()
    backup.mode = "fail"  # now both are down
    res = await app_env.pipeline.translate_cue(cue(text), ["zh"])
    zh = res.translations["zh"]
    assert zh.status == "fallback" and zh.engine == "backup"
    assert "backup" in zh.single_line


@pytest.mark.asyncio
async def test_legacy_tm_row_equal_to_source_is_not_served(app_env):
    text = "It is raining again."
    await app_env.tm.set(text, "en", "zh", text, [text], quality_score=PRIMARY_Q)  # the T6 bad row
    res = await app_env.pipeline.translate_cue(cue(text), ["zh"])
    zh = res.translations["zh"]
    assert zh.status == "ok" and zh.single_line != text


# ---------------------------------------------------------------- T7


@pytest.mark.asyncio
async def test_tm_store_failure_is_awaited_and_logged(app_env, monkeypatch, obs_records):
    async def disk_full(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(app_env.tm, "set", disk_full)
    loop = asyncio.get_running_loop()
    unretrieved = []
    loop.set_exception_handler(lambda l, ctx: unretrieved.append(ctx))
    res = await app_env.pipeline.translate_cue(cue(), ["zh"])
    assert res.translations["zh"].status == "ok"
    fails = [r for r in obs_records if r["stage"] == "tm_store" and r["event"] == "fail"]
    assert fails, "TM store failure was not logged by the time the result came back"
    assert fails[0]["error_type"] == "process"
    import gc

    gc.collect()
    await asyncio.sleep(0)
    assert not unretrieved


# ---------------------------------------------------------------- passthrough / same language


@pytest.mark.asyncio
async def test_music_passthrough_is_untranslated_and_same_language_is_ok(app_env):
    res = await app_env.pipeline.translate_cue(SubtitleCue(text="♪♪", start_time=0, end_time=1), ["zh"])
    assert res.translations["zh"].status == "untranslated"
    res = await app_env.pipeline.translate_cue(cue(), ["en", "zh"])
    assert res.translations["en"].status == "ok" and res.translations["en"].engine == "identity"


# ---------------------------------------------------------------- wire formats (T8, W6)


def test_http_translate_returns_statuses_notes_and_degraded(app_env):
    app_env.set_engines(fake=FakeMTEngine("fake", pairs={("en", "zh")}))
    with app_env.client() as c:
        d = c.post("/translate", json={"text": "Where did you put the keys?", "source_lang": "en",
                                       "target_languages": ["zh", "bn"]}).json()
    assert d["translations"]["zh"]["status"] == "ok"
    assert d["translations"]["zh"]["engine"] == "fake"
    assert d["translations"]["bn"]["status"] == "untranslated"
    assert d["translations"]["bn"]["error_type"] == "input_invalid"
    assert d["degraded"] is True
    assert "notes" in d

    app_env.set_engines(fake=FakeMTEngine("fake", mode="fail"))
    with app_env.client() as c:
        d = c.post("/translate", json={"text": "Something new", "source_lang": "en", "target_languages": ["zh"]}).json()
    assert d["translations"]["zh"]["status"] == "error"
    assert d["translations"]["zh"]["single_line"] == ""
    assert d["degraded"] is True


def test_ws_result_payload_carries_per_target_status(app_env):
    app_env.set_engines(fake=FakeMTEngine("fake", pairs={("en", "zh")}))
    with app_env.client() as c, c.websocket_connect(app_env.ws_url("/ws")) as ws:
        ws.send_json({"type": "cue", "correlation_id": "c1",
                      "payload": {"cue": {"text": "Hello there", "start_time": 0, "end_time": 1, "source_lang": "en"},
                                  "target_languages": ["zh", "bn"]}})
        m = ws.receive_json()
    assert m["type"] == "result" and m["correlation_id"] == "c1"
    tr = m["payload"]["translations"]
    assert tr["zh"]["status"] == "ok"
    assert tr["bn"]["status"] == "untranslated"


def test_ws_asr_translation_messages_carry_status(app_env):
    app_env.set_engines(fake=FakeMTEngine("fake", mode="fail"))
    app_env.asr.final_every = 3
    with app_env.client() as c, c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs="zh")) as ws:
        assert ws.receive_json()["type"] == "ready"
        for _ in range(3):
            ws.send_bytes(pcm_frame())
        while True:
            m = ws.receive_json()
            if m["type"] == "translation":
                break
    assert m["translations"]["zh"]["status"] == "error"
    assert m["translations"]["zh"]["single_line"] == ""


@pytest.mark.parametrize("msg", [
    {"type": "correction", "payload": {"cue_id": "x", "source_text": "Hi", "source_lang": "en",
                                       "original_translation": {"zh": "a"}, "corrected_translation": {"zh": "b"}}},
    {"type": "config", "payload": {"max_lines": "not-a-number"}},
])
def test_ws_handler_errors_use_the_error_envelope(app_env, monkeypatch, msg):
    async def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app_env.tm, "store_correction", broken)
    with app_env.client() as c, c.websocket_connect(app_env.ws_url("/ws")) as ws:
        ws.send_json({**msg, "correlation_id": "k1"})
        m = ws.receive_json()
    assert m["type"] == "error", m
    assert m["correlation_id"] == "k1"
    assert m["payload"]["error"]
    assert m["payload"]["error_type"] in ("process", "input_invalid")
