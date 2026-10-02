"""TM schema migrations back the database up first (<name>.bak-<old schema version>).

Every test builds its own database under tmp_path; nothing reads or writes
backend/data/.
"""

import sqlite3

import pytest

from app.cache import translation_memory as tm_mod
from app.cache.translation_memory import TranslationMemory

pytestmark = pytest.mark.asyncio

# The schema as it was before the `engine` column (baseline fffcdda): schema version 1.
V1_SCHEMA = """
CREATE TABLE translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT UNIQUE NOT NULL,
    source_text TEXT NOT NULL,
    source_lang TEXT NOT NULL,
    target_lang TEXT NOT NULL,
    translation TEXT NOT NULL,
    formatted_lines TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    use_count INTEGER DEFAULT 1,
    is_user_corrected BOOLEAN DEFAULT FALSE,
    quality_score REAL DEFAULT 1.0
);
CREATE TABLE corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text TEXT NOT NULL, source_lang TEXT NOT NULL, target_lang TEXT NOT NULL,
    original_translation TEXT NOT NULL, corrected_translation TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, applied BOOLEAN DEFAULT FALSE
);
"""


def make_v1_db(path, rows=3, wal_rows=0):
    """A v1 database; `wal_rows` more rows are left in the WAL (not checkpointed)."""
    con = sqlite3.connect(path)
    con.executescript(V1_SCHEMA)
    for i in range(rows):
        con.execute("INSERT INTO translations (content_hash, source_text, source_lang, target_lang, translation, formatted_lines)"
                    " VALUES (?, ?, 'en', 'zh', ?, '[]')", (f"h{i}", f"line {i}", f"译文 {i}"))
    con.commit()
    if wal_rows:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
        for i in range(rows, rows + wal_rows):
            con.execute("INSERT INTO translations (content_hash, source_text, source_lang, target_lang, translation, formatted_lines)"
                        " VALUES (?, ?, 'en', 'zh', ?, '[]')", (f"h{i}", f"line {i}", f"译文 {i}"))
        con.commit()
    return con  # caller closes (keeping it open keeps the WAL un-checkpointed)


def columns(path):
    con = sqlite3.connect(path)
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(translations)")}
    finally:
        con.close()


def count(path):
    assert path.exists(), f"{path.name} does not exist"
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
    finally:
        con.close()


def user_version(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


async def test_migration_from_v1_writes_a_backup_first(tmp_path, obs_records):
    db = tmp_path / "translation_memory.db"
    make_v1_db(db, rows=3).close()

    tm = TranslationMemory(db_path=db)
    await tm.initialize()
    await tm.close()

    backup = tmp_path / "translation_memory.db.bak-1"
    assert backup.exists(), sorted(p.name for p in tmp_path.iterdir())
    # the backup is the database as it was before the migration
    assert "engine" not in columns(backup)
    assert count(backup) == 3
    assert user_version(backup) == 0
    # and the database itself is migrated
    assert "engine" in columns(db)
    assert count(db) == 3
    assert user_version(db) == tm_mod.SCHEMA_VERSION
    lines = [r for r in obs_records if r["stage"] == "tm_migration"]
    assert lines, "the migration is not logged"
    done = [r for r in lines if r["event"] == "success"]
    assert done and done[-1]["context"]["from_version"] == 1 and done[-1]["context"]["to_version"] == tm_mod.SCHEMA_VERSION
    assert done[-1]["context"]["backup"].endswith("translation_memory.db.bak-1")


async def test_backup_includes_rows_still_in_the_wal(tmp_path):
    db = tmp_path / "tm.db"
    con = make_v1_db(db, rows=2, wal_rows=2)  # left open: 2 rows only in tm.db-wal
    try:
        tm = TranslationMemory(db_path=db)
        await tm.initialize()
        await tm.close()
    finally:
        con.close()
    assert count(tmp_path / "tm.db.bak-1") == 4


async def test_no_backup_when_nothing_is_migrated(tmp_path):
    db = tmp_path / "tm.db"
    tm = TranslationMemory(db_path=db)
    await tm.initialize()  # fresh database: created at the current schema
    await tm.close()
    tm = TranslationMemory(db_path=db)
    await tm.initialize()  # already current
    await tm.close()
    assert user_version(db) == tm_mod.SCHEMA_VERSION
    assert not list(tmp_path.glob("*.bak-*"))


async def test_engine_column_without_version_stamp_is_schema_2(tmp_path):
    """Databases migrated by Phase 2a (engine column added, no user_version) are
    schema 2: stamped without a backup at schema 2, backed up as .bak-2 later."""
    db = tmp_path / "tm.db"
    con = make_v1_db(db, rows=1)
    con.execute("ALTER TABLE translations ADD COLUMN engine TEXT")
    con.commit()
    con.close()
    tm = TranslationMemory(db_path=db)
    await tm.initialize()
    await tm.close()
    assert user_version(db) == tm_mod.SCHEMA_VERSION
    expected = [] if tm_mod.SCHEMA_VERSION == 2 else ["tm.db.bak-2"]
    assert sorted(p.name for p in tmp_path.glob("*.bak-*")) == expected


async def test_existing_backup_is_never_overwritten(tmp_path):
    db = tmp_path / "tm.db"
    make_v1_db(db, rows=2).close()
    (tmp_path / "tm.db.bak-1").write_bytes(b"older backup")
    tm = TranslationMemory(db_path=db)
    await tm.initialize()
    await tm.close()
    assert (tmp_path / "tm.db.bak-1").read_bytes() == b"older backup"
    extra = [p for p in tmp_path.glob("tm.db.bak-1*") if p.name != "tm.db.bak-1"]
    assert len(extra) == 1 and count(extra[0]) == 2


async def test_backup_failure_stops_the_migration(tmp_path, monkeypatch):
    db = tmp_path / "tm.db"
    make_v1_db(db, rows=1).close()
    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(tm_mod, "_backup_db", broken)
    tm = TranslationMemory(db_path=db)
    with pytest.raises(OSError):
        await tm.initialize()
    await tm.close()
    assert "engine" not in columns(db), "schema changed without a backup"
