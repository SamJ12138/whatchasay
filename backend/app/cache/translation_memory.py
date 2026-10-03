"""
SQLite-based translation memory for persistent caching.

Provides long-term storage of translations with:
- Exact match lookup
- Optional fuzzy matching
- User correction tracking
- Usage statistics
"""

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xxhash
import aiosqlite

from ..config import settings
from .. import obs

logger = logging.getLogger(__name__)

# Schema versions, kept in PRAGMA user_version:
#   1  baseline (fffcdda): translations, corrections, glossary
#   2  + translations.engine (Phase 2a; databases migrated by 2a have the column
#      but user_version 0, and are recognised by it)
#   3  the term glossary (T13): glossary_terms / glossary_heard / glossary_renderings
#      replace the storage-only `glossary` table, whose rows (one per target language)
#      become terms with renderings
SCHEMA_VERSION = 3


def _detect_version(con: sqlite3.Connection) -> int:
    """Schema version of an existing database; 0 = no TM tables yet (new file)."""
    stamped = con.execute("PRAGMA user_version").fetchone()[0]
    if stamped:
        return stamped
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "translations" not in tables:
        return 0
    if "glossary_terms" in tables:
        return 3
    columns = {r[1] for r in con.execute("PRAGMA table_info(translations)")}
    return 2 if "engine" in columns else 1


def _backup_db(db_path: Path, version: int) -> Path:
    """Copy the database to <name>.bak-<version> with SQLite's online backup (rows
    still in the -wal file included). An existing backup is never overwritten: the
    new one then gets a timestamp suffix."""
    target = db_path.with_name(f"{db_path.name}.bak-{version}")
    if target.exists():
        target = db_path.with_name(f"{db_path.name}.bak-{version}.{datetime.now():%Y%m%dT%H%M%S}")
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


class TranslationMemory:
    """
    Persistent translation memory using SQLite.
    
    Stores translations for reuse across sessions and provides
    the foundation for translation consistency.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize translation memory.
        
        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path or settings.cache.tm_database_path
        self._connection: Optional[aiosqlite.Connection] = None
        self._initialized = False
        self._init_lock: Optional[asyncio.Lock] = None
        self._migration: Optional[Dict[str, Any]] = None
    
    def _prepare_migration(self) -> None:
        """Before any schema change: find the database's version and, if it is
        older than SCHEMA_VERSION, back it up. A failed backup stops start-up of
        the TM (raises) so the schema is never changed without a copy."""
        self._migration = None
        if not self.db_path.exists():
            return
        con = sqlite3.connect(str(self.db_path))
        try:
            version = _detect_version(con)
        finally:
            con.close()
        if version == 0 or version >= SCHEMA_VERSION:
            return
        t0 = time.perf_counter()
        obs.log("tm_migration", "start", from_version=version, to_version=SCHEMA_VERSION, db=self.db_path.name)
        try:
            backup = _backup_db(self.db_path, version)
        except Exception as e:
            obs.log_exc("tm_migration", e, api="process", from_version=version, to_version=SCHEMA_VERSION,
                        db=self.db_path.name, degraded="migration not run: backup failed")
            raise
        logger.warning("Translation memory schema %d -> %d: backup written to %s", version, SCHEMA_VERSION, backup)
        self._migration = {"from_version": version, "backup": str(backup), "backup_ms": (time.perf_counter() - t0) * 1000}

    async def initialize(self) -> None:
        """Initialize the database and create tables. Concurrent first users wait for
        one initialization (two used to race: duplicate column / database locked)."""
        if self._initialized:
            return
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if not self._initialized:
                await self._initialize()

    async def _initialize(self) -> None:
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.get_running_loop().run_in_executor(None, self._prepare_migration)
        
        self._connection = await aiosqlite.connect(str(self.db_path))
        
        # Enable WAL mode for better concurrent access
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA synchronous=NORMAL")
        
        # Create tables
        await self._connection.executescript("""
            -- Main translation memory table
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_hash TEXT UNIQUE NOT NULL,
                source_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                translation TEXT NOT NULL,
                formatted_lines TEXT NOT NULL,  -- JSON array
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                use_count INTEGER DEFAULT 1,
                is_user_corrected BOOLEAN DEFAULT FALSE,
                quality_score REAL DEFAULT 1.0
            );
            
            -- Index for fast lookups
            CREATE INDEX IF NOT EXISTS idx_content_hash 
                ON translations(content_hash);
            CREATE INDEX IF NOT EXISTS idx_source_target 
                ON translations(source_lang, target_lang);
            CREATE INDEX IF NOT EXISTS idx_source_text 
                ON translations(source_text);
            
            -- User corrections log (for training data)
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                original_translation TEXT NOT NULL,
                corrected_translation TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                applied BOOLEAN DEFAULT FALSE
            );
            
            -- The term glossary (T13): a term has one canonical spelling in its source
            -- language, any number of "heard as" spellings and a rendering per target language
            CREATE TABLE IF NOT EXISTS glossary_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_lang TEXT NOT NULL,
                canonical TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_lang, canonical)
            );
            CREATE TABLE IF NOT EXISTS glossary_heard (
                term_id INTEGER NOT NULL REFERENCES glossary_terms(id) ON DELETE CASCADE,
                heard TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                UNIQUE(term_id, heard)
            );
            CREATE TABLE IF NOT EXISTS glossary_renderings (
                term_id INTEGER NOT NULL REFERENCES glossary_terms(id) ON DELETE CASCADE,
                target_lang TEXT NOT NULL,
                rendering TEXT NOT NULL,
                UNIQUE(term_id, target_lang)
            );
        """)

        # Additive migration: which engine produced a row (fallback rows are
        # stored with a lower quality and replaced by a later primary result).
        async with self._connection.execute("PRAGMA table_info(translations)") as cursor:
            columns = {row[1] async for row in cursor}
        if "engine" not in columns:
            await self._connection.execute("ALTER TABLE translations ADD COLUMN engine TEXT")
        await self._migrate_old_glossary()

        await self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await self._connection.commit()
        await self._publish_glossary()
        mig = self._migration
        if mig and mig.get("backup"):
            obs.log("tm_migration", "success", duration_ms=mig.get("backup_ms"), from_version=mig["from_version"],
                    to_version=SCHEMA_VERSION, backup=mig["backup"], db=self.db_path.name)
        self._initialized = True
        logger.info(f"Translation memory initialized: {self.db_path}")
    
    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            self._initialized = False
    
    def _compute_hash(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str
    ) -> str:
        """Compute content hash for lookup."""
        # Normalize text
        normalized = source_text.lower().strip()
        content = f"{normalized}|{source_lang}|{target_lang}"
        return xxhash.xxh64(content.encode()).hexdigest()
    
    async def get(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        touch: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Get translation from memory.
        
        Args:
            source_text: Source text
            source_lang: Source language code
            target_lang: Target language code
            touch: count the use (use_count, updated_at); False = read only,
                   for sessions that must leave no trace (D3)
            
        Returns:
            Translation entry or None
        """
        if not self._initialized:
            await self.initialize()
        
        content_hash = self._compute_hash(source_text, source_lang, target_lang)
        
        async with self._connection.execute("""
            SELECT translation, formatted_lines, is_user_corrected, quality_score, engine
            FROM translations
            WHERE content_hash = ?
        """, (content_hash,)) as cursor:
            row = await cursor.fetchone()
        
        if row and not touch:
            return {
                'translation': row[0],
                'lines': json.loads(row[1]),
                'is_user_corrected': bool(row[2]),
                'quality_score': row[3],
                'engine': row[4],
            }
        if row:
            # Update use count
            await self._connection.execute("""
                UPDATE translations 
                SET use_count = use_count + 1, updated_at = CURRENT_TIMESTAMP
                WHERE content_hash = ?
            """, (content_hash,))
            await self._connection.commit()
            
            return {
                'translation': row[0],
                'lines': json.loads(row[1]),
                'is_user_corrected': bool(row[2]),
                'quality_score': row[3],
                'engine': row[4],
            }
        
        return None
    
    async def set(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        translation: str,
        formatted_lines: List[str],
        is_user_corrected: bool = False,
        quality_score: float = 1.0,
        engine: Optional[str] = None,
    ) -> None:
        """
        Store translation in memory.

        An existing row is kept when it is a user correction (and this write is
        not), or when this write has a lower quality score (a fallback engine's
        output never replaces a primary or refined one). Otherwise this write
        replaces it.
        
        Args:
            source_text: Source text
            source_lang: Source language code
            target_lang: Target language code
            translation: Translated text
            formatted_lines: Formatted lines list
            is_user_corrected: Whether this is a user correction
            quality_score: Quality score (0-1)
        """
        if not self._initialized:
            await self.initialize()
        
        content_hash = self._compute_hash(source_text, source_lang, target_lang)
        lines_json = json.dumps(formatted_lines, ensure_ascii=False)
        
        keep_old = (
            "((translations.is_user_corrected AND NOT excluded.is_user_corrected) OR "
            "(NOT excluded.is_user_corrected AND excluded.quality_score < translations.quality_score))"
        )
        await self._connection.execute(f"""
            INSERT INTO translations
                (content_hash, source_text, source_lang, target_lang,
                 translation, formatted_lines, is_user_corrected, quality_score, engine)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                translation = CASE WHEN {keep_old} THEN translations.translation ELSE excluded.translation END,
                formatted_lines = CASE WHEN {keep_old} THEN translations.formatted_lines ELSE excluded.formatted_lines END,
                quality_score = CASE WHEN {keep_old} THEN translations.quality_score ELSE excluded.quality_score END,
                engine = CASE WHEN {keep_old} THEN translations.engine ELSE excluded.engine END,
                is_user_corrected = translations.is_user_corrected OR excluded.is_user_corrected,
                updated_at = CURRENT_TIMESTAMP,
                use_count = use_count + 1
        """, (content_hash, source_text, source_lang, target_lang,
              translation, lines_json, is_user_corrected, quality_score, engine))

        await self._connection.commit()

    async def store_correction(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        original_translation: str,
        corrected_translation: str,
        corrected_lines: List[str]
    ) -> None:
        """
        Store a user correction and update the translation.
        
        Also logs the correction for potential training data.
        """
        if not self._initialized:
            await self.initialize()
        
        # Log the correction
        await self._connection.execute("""
            INSERT INTO corrections 
                (source_text, source_lang, target_lang, 
                 original_translation, corrected_translation)
            VALUES (?, ?, ?, ?, ?)
        """, (source_text, source_lang, target_lang,
              original_translation, corrected_translation))
        
        # Update the translation
        await self.set(
            source_text=source_text,
            source_lang=source_lang,
            target_lang=target_lang,
            translation=corrected_translation,
            formatted_lines=corrected_lines,
            is_user_corrected=True,
            quality_score=1.0,  # User corrections are highest quality
            engine="user",
        )
    
    # ------------------------------------------------------------------ glossary (T13)

    async def _migrate_old_glossary(self) -> None:
        """Schema 1-2 kept a `glossary` table (source_term, target_term per target language,
        lower-cased). Each distinct (source_lang, source_term) becomes a term whose renderings
        are its rows; the old table goes."""
        async with self._connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='glossary'") as cur:
            if await cur.fetchone() is None:
                return
        async with self._connection.execute("SELECT source_lang, source_term, target_lang, target_term FROM glossary ORDER BY id") as cur:
            rows = [tuple(r) async for r in cur]
        for source_lang, source_term, target_lang, target_term in rows:
            if not (source_term or "").strip():
                continue
            await self._connection.execute("INSERT OR IGNORE INTO glossary_terms (source_lang, canonical) VALUES (?, ?)",
                                           (source_lang, source_term.strip()))
            async with self._connection.execute("SELECT id FROM glossary_terms WHERE source_lang = ? AND canonical = ?",
                                                (source_lang, source_term.strip())) as cur:
                term_id = (await cur.fetchone())[0]
            if (target_term or "").strip():
                await self._connection.execute("""
                    INSERT INTO glossary_renderings (term_id, target_lang, rendering) VALUES (?, ?, ?)
                    ON CONFLICT(term_id, target_lang) DO UPDATE SET rendering = excluded.rendering
                """, (term_id, target_lang, target_term.strip()))
        await self._connection.execute("DROP TABLE glossary")
        if rows:
            obs.log("tm_migration", "success", component="glossary", rows=len(rows), db=self.db_path.name)

    async def _publish_glossary(self) -> None:
        """Hand the terms to the in-process store the pipeline and live sessions read."""
        from ..translation.glossary import Term, store

        terms = await self._list_glossary()   # also called from _initialize, before the flag is set
        store.load(Term(source_lang=t["source_lang"], canonical=t["canonical"], heard_as=list(t["heard_as"]),
                        renderings=dict(t["renderings"]), id=t["id"]) for t in terms)

    async def _glossary_term(self, term_id: int) -> Optional[Dict[str, Any]]:
        async with self._connection.execute("SELECT id, source_lang, canonical FROM glossary_terms WHERE id = ?", (term_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        async with self._connection.execute("SELECT heard FROM glossary_heard WHERE term_id = ? ORDER BY position, rowid", (term_id,)) as cur:
            heard = [r[0] async for r in cur]
        async with self._connection.execute("SELECT target_lang, rendering FROM glossary_renderings WHERE term_id = ? ORDER BY target_lang",
                                            (term_id,)) as cur:
            renderings = {r[0]: r[1] async for r in cur}
        return {"id": row[0], "source_lang": row[1], "canonical": row[2], "heard_as": heard, "renderings": renderings}

    async def list_glossary(self, source_lang: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every term (of one source language), oldest first."""
        if not self._initialized:
            await self.initialize()
        return await self._list_glossary(source_lang)

    async def _list_glossary(self, source_lang: Optional[str] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT id FROM glossary_terms", ()
        if source_lang:
            sql, args = sql + " WHERE source_lang = ?", (source_lang,)
        async with self._connection.execute(sql + " ORDER BY id", args) as cur:
            ids = [r[0] async for r in cur]
        out = []
        for i in ids:
            t = await self._glossary_term(i)
            if t is not None:
                out.append(t)
        return out

    async def upsert_glossary_term(self, source_lang: str, canonical: str, heard_as: Optional[List[str]] = None,
                                   renderings: Optional[Dict[str, str]] = None, term_id: Optional[int] = None,
                                   replace: bool = False) -> Dict[str, Any]:
        """Create a term, or extend the one with this canonical spelling (heard-as spellings
        are added, renderings given replace the old one for that language). With term_id
        the term is addressed by id, and replace=True makes the lists exactly what is given
        (an edit from the Options table). Blank spellings raise ValueError."""
        if not self._initialized:
            await self.initialize()
        source_lang = (source_lang or "").strip()
        canonical = (canonical or "").strip()
        if not source_lang:
            raise ValueError("a term needs a source language")
        if not canonical:
            raise ValueError("a term needs its canonical spelling")
        heard = []
        for h in heard_as or []:
            h = (h or "").strip()
            if h and h != canonical and h not in heard:
                heard.append(h)
        rend = {k.strip(): (v or "").strip() for k, v in (renderings or {}).items() if k and k.strip()}
        if term_id is not None:
            existing = await self._glossary_term(term_id)
            if existing is None:
                raise KeyError(f"no glossary term {term_id}")
            await self._connection.execute("UPDATE glossary_terms SET source_lang = ?, canonical = ? WHERE id = ?",
                                           (source_lang, canonical, term_id))
        else:
            await self._connection.execute("INSERT OR IGNORE INTO glossary_terms (source_lang, canonical) VALUES (?, ?)",
                                           (source_lang, canonical))
            async with self._connection.execute("SELECT id FROM glossary_terms WHERE source_lang = ? AND canonical = ?",
                                                (source_lang, canonical)) as cur:
                term_id = (await cur.fetchone())[0]
        if replace:
            await self._connection.execute("DELETE FROM glossary_heard WHERE term_id = ?", (term_id,))
            await self._connection.execute("DELETE FROM glossary_renderings WHERE term_id = ?", (term_id,))
        async with self._connection.execute("SELECT COALESCE(MAX(position), -1) FROM glossary_heard WHERE term_id = ?", (term_id,)) as cur:
            pos = (await cur.fetchone())[0]
        for h in heard:
            pos += 1
            await self._connection.execute("INSERT OR IGNORE INTO glossary_heard (term_id, heard, position) VALUES (?, ?, ?)",
                                           (term_id, h, pos))
        # a heard-as spelling equal to the (new) canonical one is no longer a mishearing
        await self._connection.execute("DELETE FROM glossary_heard WHERE term_id = ? AND heard = ?", (term_id, canonical))
        for lang, text in rend.items():
            if text:
                await self._connection.execute("""
                    INSERT INTO glossary_renderings (term_id, target_lang, rendering) VALUES (?, ?, ?)
                    ON CONFLICT(term_id, target_lang) DO UPDATE SET rendering = excluded.rendering
                """, (term_id, lang, text))
            else:
                await self._connection.execute("DELETE FROM glossary_renderings WHERE term_id = ? AND target_lang = ?", (term_id, lang))
        await self._connection.commit()
        await self._publish_glossary()
        return await self._glossary_term(term_id)

    async def delete_glossary_term(self, term_id: int) -> bool:
        if not self._initialized:
            await self.initialize()
        cur = await self._connection.execute("DELETE FROM glossary_terms WHERE id = ?", (term_id,))
        removed = bool(cur.rowcount)
        # cascades need the pragma; delete the children by hand so it works without it
        await self._connection.execute("DELETE FROM glossary_heard WHERE term_id = ?", (term_id,))
        await self._connection.execute("DELETE FROM glossary_renderings WHERE term_id = ?", (term_id,))
        await self._connection.commit()
        await self._publish_glossary()
        return removed

    async def forget_machine_rows_containing(self, spellings: List[str]) -> int:
        """Machine translations (never user corrections) whose source text holds one of
        the spellings: translated before the term existed, so their rendering is stale."""
        if not self._initialized:
            await self.initialize()
        removed = 0
        for sp in {s for s in spellings if s and s.strip()}:
            cur = await self._connection.execute(
                "DELETE FROM translations WHERE NOT is_user_corrected AND instr(lower(source_text), lower(?)) > 0", (sp,))
            removed += cur.rowcount or 0
        await self._connection.commit()
        return removed

    async def export_corrections(self, output_path: Path) -> int:
        """
        Export corrections to JSONL file for training.
        
        Returns:
            Number of corrections exported
        """
        if not self._initialized:
            await self.initialize()
        
        count = 0
        
        with open(output_path, 'w', encoding='utf-8') as f:
            async with self._connection.execute("""
                SELECT source_text, source_lang, target_lang,
                       original_translation, corrected_translation, created_at
                FROM corrections
                WHERE applied = FALSE
                ORDER BY created_at
            """) as cursor:
                async for row in cursor:
                    entry = {
                        'source_text': row[0],
                        'source_lang': row[1],
                        'target_lang': row[2],
                        'original': row[3],
                        'corrected': row[4],
                        'timestamp': row[5],
                    }
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    count += 1
        
        return count
    
    async def sweep(self, retention_days: int) -> int:
        """Delete machine translations not used for `retention_days` (updated_at is
        bumped on every use); user corrections stay. 0 or less = keep everything."""
        if not self._initialized:
            await self.initialize()
        if retention_days <= 0:
            obs.log("tm_retention", "skip", retention_days=retention_days, error_message="retention disabled (0 = keep)")
            return 0
        t0 = time.perf_counter()
        cur = await self._connection.execute(
            "DELETE FROM translations WHERE NOT is_user_corrected AND updated_at < datetime('now', ?)",
            (f"-{int(retention_days)} days",))
        removed = cur.rowcount or 0
        await self._connection.commit()
        obs.log("tm_retention", "success", duration_ms=(time.perf_counter() - t0) * 1000,
                retention_days=retention_days, removed=removed)
        return removed

    async def clear(self) -> Dict[str, int]:
        """Delete everything (translations, corrections, glossary) and rebuild the file
        so the deleted text is not left in free pages or the WAL."""
        if not self._initialized:
            await self.initialize()
        removed = {}
        for table, key in (("translations", "translations"), ("corrections", "corrections"), ("glossary_terms", "glossary")):
            cur = await self._connection.execute(f"DELETE FROM {table}")
            removed[key] = cur.rowcount or 0
        for table in ("glossary_heard", "glossary_renderings"):
            await self._connection.execute(f"DELETE FROM {table}")
        await self._connection.commit()
        await self._connection.execute("VACUUM")
        await self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await self._publish_glossary()
        return removed

    async def get_stats(self) -> Dict[str, Any]:
        """Get translation memory statistics."""
        if not self._initialized:
            await self.initialize()
        
        stats = {}
        
        # Total entries
        async with self._connection.execute(
            "SELECT COUNT(*) FROM translations"
        ) as cursor:
            stats['total_translations'] = (await cursor.fetchone())[0]
        
        # User corrections
        async with self._connection.execute(
            "SELECT COUNT(*) FROM translations WHERE is_user_corrected = TRUE"
        ) as cursor:
            stats['user_corrections'] = (await cursor.fetchone())[0]
        
        # Pending corrections (for training)
        async with self._connection.execute(
            "SELECT COUNT(*) FROM corrections WHERE applied = FALSE"
        ) as cursor:
            stats['pending_corrections'] = (await cursor.fetchone())[0]
        
        # Glossary size (terms)
        async with self._connection.execute(
            "SELECT COUNT(*) FROM glossary_terms"
        ) as cursor:
            stats['glossary_size'] = (await cursor.fetchone())[0]
        
        # Most used translations
        async with self._connection.execute("""
            SELECT source_text, use_count
            FROM translations
            ORDER BY use_count DESC
            LIMIT 10
        """) as cursor:
            stats['most_used'] = [
                {'text': row[0][:50], 'count': row[1]}
                async for row in cursor
            ]
        
        return stats


# Global instance
_translation_memory: Optional[TranslationMemory] = None


async def get_translation_memory() -> TranslationMemory:
    """Get or create global translation memory instance."""
    global _translation_memory
    if _translation_memory is None:
        _translation_memory = TranslationMemory()
        await _translation_memory.initialize()
    return _translation_memory
