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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xxhash
import aiosqlite

from ..config import settings

logger = logging.getLogger(__name__)


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
    
    async def initialize(self) -> None:
        """Initialize the database and create tables."""
        if self._initialized:
            return
        
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
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
            
            -- Glossary for consistent terms
            CREATE TABLE IF NOT EXISTS glossary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_term TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_term TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                context TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_term, source_lang, target_lang)
            );
            
            CREATE INDEX IF NOT EXISTS idx_glossary_source 
                ON glossary(source_term, source_lang);
        """)
        
        await self._connection.commit()
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
        target_lang: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get translation from memory.
        
        Args:
            source_text: Source text
            source_lang: Source language code
            target_lang: Target language code
            
        Returns:
            Translation entry or None
        """
        if not self._initialized:
            await self.initialize()
        
        content_hash = self._compute_hash(source_text, source_lang, target_lang)
        
        async with self._connection.execute("""
            SELECT translation, formatted_lines, is_user_corrected, quality_score
            FROM translations
            WHERE content_hash = ?
        """, (content_hash,)) as cursor:
            row = await cursor.fetchone()
        
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
        quality_score: float = 1.0
    ) -> None:
        """
        Store translation in memory.
        
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
        
        await self._connection.execute("""
            INSERT INTO translations 
                (content_hash, source_text, source_lang, target_lang, 
                 translation, formatted_lines, is_user_corrected, quality_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                translation = CASE WHEN translations.is_user_corrected AND NOT excluded.is_user_corrected
                                   THEN translations.translation ELSE excluded.translation END,
                formatted_lines = CASE WHEN translations.is_user_corrected AND NOT excluded.is_user_corrected
                                   THEN translations.formatted_lines ELSE excluded.formatted_lines END,
                is_user_corrected = translations.is_user_corrected OR excluded.is_user_corrected,
                quality_score = CASE WHEN translations.is_user_corrected AND NOT excluded.is_user_corrected
                                   THEN translations.quality_score ELSE excluded.quality_score END,
                updated_at = CURRENT_TIMESTAMP,
                use_count = use_count + 1
        """, (content_hash, source_text, source_lang, target_lang,
              translation, lines_json, is_user_corrected, quality_score))
        
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
            quality_score=1.0  # User corrections are highest quality
        )
    
    async def get_glossary_term(
        self,
        source_term: str,
        source_lang: str,
        target_lang: str
    ) -> Optional[str]:
        """
        Get glossary translation for a term.
        
        Useful for maintaining consistency on names and technical terms.
        """
        if not self._initialized:
            await self.initialize()
        
        async with self._connection.execute("""
            SELECT target_term
            FROM glossary
            WHERE source_term = ? AND source_lang = ? AND target_lang = ?
        """, (source_term.lower(), source_lang, target_lang)) as cursor:
            row = await cursor.fetchone()
        
        return row[0] if row else None
    
    async def add_glossary_term(
        self,
        source_term: str,
        source_lang: str,
        target_term: str,
        target_lang: str,
        context: Optional[str] = None
    ) -> None:
        """Add or update a glossary term."""
        if not self._initialized:
            await self.initialize()
        
        await self._connection.execute("""
            INSERT INTO glossary (source_term, source_lang, target_term, target_lang, context)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_term, source_lang, target_lang) DO UPDATE SET
                target_term = excluded.target_term,
                context = excluded.context
        """, (source_term.lower(), source_lang, target_term, target_lang, context))
        
        await self._connection.commit()
    
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
        
        # Glossary size
        async with self._connection.execute(
            "SELECT COUNT(*) FROM glossary"
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
