"""D3: audio sessions leave no trace in the persistent translation memory by
default; caption cues and corrections persist; retention sweep; /tm/clear.
Scratch TM only (app_env / tmp_path)."""

import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest

from tests.conftest import ws_recv
from tests.fakes import pcm_frame


def tm_rows(app_env) -> int:
    """Rows in the scratch TM, read-only (never creates the file)."""
    path = app_env.tm.db_path
    if not path.exists():
        return 0
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
    except sqlite3.OperationalError:  # no tables yet
        return 0
    finally:
        con.close()


def run_audio(app_env, client, finals: int, targets: str = "zh"):
    """Stream frames over /ws/asr, one utterance at a time (each final's translation
    arrives before the next utterance starts, as in real speech)."""
    got = []
    with client.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs=targets)) as ws:
        assert ws.receive_json()["type"] == "ready"
        for n in range(1, finals + 1):
            for _ in range(app_env.asr.final_every):
                ws.send_bytes(pcm_frame())
            while len(got) < n:
                m = ws_recv(ws, timeout=5)
                assert m is not None, "no translation arrived"
                if m["type"] == "translation":
                    got.append(m)
    return got


def send_cue(client, app_env, text, cid="c1"):
    with client.websocket_connect(app_env.ws_url("/ws")) as ws:
        ws.send_json({"type": "cue", "correlation_id": cid,
                      "payload": {"cue": {"text": text, "start_time": 0, "end_time": 2, "source_lang": "en"},
                                  "target_languages": ["zh"]}})
        while True:
            m = ws_recv(ws, timeout=5)
            assert m is not None, "no reply"
            if m.get("correlation_id") == cid:
                return m


def test_defaults():
    from app.config import settings

    assert settings.tm.persist_audio_sessions is False
    assert settings.tm.persist_captions is True
    assert settings.tm.retention_days == 30


def test_audio_session_writes_nothing_to_the_tm(app_env):
    from app.cache.memory_cache import get_translation_cache

    app_env.asr.final_every = 5
    with app_env.client() as c:
        before = tm_rows(app_env)
        got = run_audio(app_env, c, finals=2)
        assert all(t["translations"]["zh"]["status"] == "ok" for t in got)
    assert tm_rows(app_env) == before == 0
    assert get_translation_cache().stats["size"] == 0, "audio text left in the process-wide cache"


def test_audio_session_cache_dies_with_the_session(app_env):
    app_env.asr.final_every = 5
    app_env.asr.numbered = False  # every final has the same text
    fake = app_env.engines["fake"]
    with app_env.client() as c:
        run_audio(app_env, c, finals=2)
        assert len(fake.calls) == 1, "second identical final was not served from the session cache"
        run_audio(app_env, c, finals=1)  # a new session starts with an empty cache
        assert len(fake.calls) == 2


def test_persist_audio_sessions_opt_in(app_env, monkeypatch):
    monkeypatch.setattr(app_env.settings.tm, "persist_audio_sessions", True)
    app_env.asr.final_every = 5
    with app_env.client() as c:
        run_audio(app_env, c, finals=1)
    assert tm_rows(app_env) == 1


def test_caption_cues_persist_by_default(app_env):
    with app_env.client() as c:
        r = send_cue(c, app_env, "Where did you put the keys?")
        assert r["payload"]["translations"]["zh"]["status"] == "ok"
    assert tm_rows(app_env) == 1


def test_caption_cues_not_persisted_when_off(app_env, monkeypatch):
    monkeypatch.setattr(app_env.settings.tm, "persist_captions", False)
    with app_env.client() as c:
        send_cue(c, app_env, "Where did you put the keys?")
    assert tm_rows(app_env) == 0


@pytest.mark.asyncio
async def test_retention_sweep_removes_old_machine_rows_only(scratch_tm, obs_records):
    tm = scratch_tm
    await tm.set("old machine", "en", "zh", "旧", ["旧"])
    await tm.set("recent machine", "en", "zh", "新", ["新"])
    await tm.store_correction("old corrected", "en", "zh", "x", "改", ["改"])
    old = (datetime.utcnow() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    await tm._connection.execute("UPDATE translations SET updated_at = ?, created_at = ? WHERE source_text IN (?, ?)",
                                 (old, old, "old machine", "old corrected"))
    await tm._connection.commit()
    removed = await tm.sweep(retention_days=30)
    assert removed == 1
    async with tm._connection.execute("SELECT source_text FROM translations ORDER BY source_text") as cur:
        left = [r[0] async for r in cur]
    assert left == ["old corrected", "recent machine"]
    sweep = [r for r in obs_records if r["stage"] == "tm_retention"]
    assert sweep and sweep[-1]["context"]["removed"] == 1 and sweep[-1]["context"]["retention_days"] == 30
    assert await tm.sweep(retention_days=0) == 0, "0 = keep everything"


def test_startup_runs_the_retention_sweep(app_env, obs_records):
    with app_env.client() as c:
        c.get("/health/json")
    assert [r for r in obs_records if r["stage"] == "tm_retention"], "no sweep at startup"


def test_tm_clear_endpoint_removes_everything_from_the_file(app_env):
    from app.cache.memory_cache import get_translation_cache

    secret = "Meet me at the old harbour at nine"
    with app_env.client() as c:
        send_cue(c, app_env, secret)
        assert tm_rows(app_env) == 1
        r = c.post("/tm/clear")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "cleared" and d["removed"]["translations"] == 1
        assert get_translation_cache().stats["size"] == 0
        assert c.get("/stats").json()["translation_memory"]["total_translations"] == 0
    assert tm_rows(app_env) == 0
    raw = b"".join(p.read_bytes() for p in app_env.tm.db_path.parent.glob(app_env.tm.db_path.name + "*"))
    assert secret.encode() not in raw, "cleared text still readable in the database files"


def test_tm_clear_refuses_a_web_page(app_env):
    with app_env.client() as c:
        r = c.post("/tm/clear", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_health_reports_what_is_stored(app_env):
    with app_env.client() as c:
        d = c.get("/health/json").json()
    assert d["privacy"]["tm"] == {"persist_audio_sessions": False, "persist_captions": True, "retention_days": 30}


@pytest.mark.asyncio
async def test_concurrent_first_use_initializes_once(tmp_path):
    """Found while writing these tests: the first TM lookups of a session can arrive
    together; two initialize() runs both added the engine column and one failed
    ("duplicate column name: engine"), failing that translation."""
    from app.cache.translation_memory import TranslationMemory

    tm = TranslationMemory(db_path=tmp_path / "tm.db")
    try:
        await asyncio.gather(*(tm.get("hello", "en", "zh") for _ in range(4)))
    finally:
        await tm.close()
