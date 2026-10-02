"""
Caching module for translation results.

Provides:
- In-memory LRU cache for fast lookups
- SQLite-based translation memory for persistence
"""

from .memory_cache import LRUCache, TranslationCache, get_translation_cache
from .translation_memory import TranslationMemory, get_translation_memory

__all__ = [
    'LRUCache',
    'TranslationCache',
    'get_translation_cache',
    'TranslationMemory',
    'get_translation_memory',
]
