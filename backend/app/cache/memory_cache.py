"""
In-memory LRU cache for fast translation lookups.

Provides the fastest layer of caching for recently used translations.
"""

import time
import logging
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass, field
from collections import OrderedDict
import threading
import xxhash

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A single cache entry."""
    
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    access_count: int = 1
    
    @property
    def age(self) -> float:
        """Get entry age in seconds."""
        return time.time() - self.created_at
    
    @property
    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return self.age > settings.cache.memory_cache_ttl


class LRUCache:
    """
    Thread-safe LRU cache with TTL support.
    
    Features:
    - LRU eviction when capacity is reached
    - TTL-based expiration
    - Thread-safe operations
    - Statistics tracking
    """
    
    def __init__(self, max_size: int = 1000, ttl: int = 3600):
        """
        Initialize cache.
        
        Args:
            max_size: Maximum number of entries
            ttl: Time-to-live in seconds
        """
        self.max_size = max_size
        self.ttl = ttl
        
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        
        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._misses += 1
                return None
            
            # Check expiration
            if entry.is_expired:
                del self._cache[key]
                self._misses += 1
                return None
            
            # Update access
            entry.accessed_at = time.time()
            entry.access_count += 1
            
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            
            self._hits += 1
            return entry.value
    
    def set(self, key: str, value: Any) -> None:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        with self._lock:
            # Update existing entry
            if key in self._cache:
                entry = self._cache[key]
                entry.value = value
                entry.accessed_at = time.time()
                entry.access_count += 1
                self._cache.move_to_end(key)
                return
            
            # Evict if at capacity
            while len(self._cache) >= self.max_size:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                self._evictions += 1
            
            # Add new entry
            self._cache[key] = CacheEntry(key=key, value=value)
    
    def delete(self, key: str) -> bool:
        """
        Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if entry was deleted
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._cache.clear()
    
    def cleanup_expired(self) -> int:
        """
        Remove expired entries.
        
        Returns:
            Number of entries removed
        """
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired
            ]
            
            for key in expired_keys:
                del self._cache[key]
            
            return len(expired_keys)
    
    @property
    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        
        return {
            'size': self.size,
            'max_size': self.max_size,
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': hit_rate,
            'evictions': self._evictions,
        }


class TranslationCache:
    """
    Specialized cache for translation results.
    
    Uses composite keys based on source text and target languages.
    """
    
    def __init__(self):
        """Initialize translation cache."""
        self._cache = LRUCache(
            max_size=settings.cache.memory_cache_size,
            ttl=settings.cache.memory_cache_ttl
        )
    
    def _make_key(
        self,
        source_text: str,
        source_lang: str,
        target_langs: Tuple[str, ...]
    ) -> str:
        """
        Create cache key from translation parameters.
        
        Uses xxhash for fast hashing.
        """
        # Normalize text for caching
        normalized = source_text.lower().strip()
        
        # Create composite key
        key_parts = [normalized, source_lang] + sorted(target_langs)
        key_string = '|'.join(key_parts)
        
        return xxhash.xxh64(key_string.encode()).hexdigest()
    
    def get(
        self,
        source_text: str,
        source_lang: str,
        target_langs: Tuple[str, ...]
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached translation result.
        
        Args:
            source_text: Source text
            source_lang: Source language
            target_langs: Target languages
            
        Returns:
            Cached translation result or None
        """
        key = self._make_key(source_text, source_lang, target_langs)
        return self._cache.get(key)
    
    def set(
        self,
        source_text: str,
        source_lang: str,
        target_langs: Tuple[str, ...],
        result: Dict[str, Any]
    ) -> None:
        """
        Cache translation result.
        
        Args:
            source_text: Source text
            source_lang: Source language
            target_langs: Target languages
            result: Translation result to cache
        """
        key = self._make_key(source_text, source_lang, target_langs)
        self._cache.set(key, result)
    
    def invalidate(
        self,
        source_text: str,
        source_lang: str,
        target_langs: Tuple[str, ...]
    ) -> bool:
        """
        Invalidate a cached translation.
        
        Useful when user provides a correction.
        """
        key = self._make_key(source_text, source_lang, target_langs)
        return self._cache.delete(key)
    
    def clear(self) -> None:
        """Clear all cached translations."""
        self._cache.clear()
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return self._cache.stats


# Global cache instance
_translation_cache: Optional[TranslationCache] = None


def get_translation_cache() -> TranslationCache:
    """Get or create global translation cache."""
    global _translation_cache
    if _translation_cache is None:
        _translation_cache = TranslationCache()
    return _translation_cache
