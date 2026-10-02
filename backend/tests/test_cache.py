import asyncio
from pathlib import Path

import pytest

from app.cache.memory_cache import LRUCache, TranslationCache
from app.cache.translation_memory import TranslationMemory


def test_lru_cache_evicts_oldest():
    c = LRUCache(max_size=2, ttl=3600)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") is None
    assert c.get("c") == 3


def test_translation_cache_key_is_order_independent():
    c = TranslationCache()
    c.clear()
    c.set("hello", "en", ("zh", "bn"), {"zh": {"single_line": "你好"}})
    assert c.get("hello", "en", ("bn", "zh")) is not None
    assert c.get("hello", "en", ("zh",)) is None
    c.clear()


@pytest.mark.asyncio
async def test_translation_memory_roundtrip_and_correction_guard(tmp_path: Path):
    tm = TranslationMemory(db_path=tmp_path / "tm.db")
    await tm.initialize()
    try:
        await tm.set("Where are the keys?", "en", "zh", "钥匙在哪里？", ["钥匙在哪里？"], quality_score=0.8)
        got = await tm.get("Where are the keys?", "en", "zh")
        assert got and got["translation"] == "钥匙在哪里？"
        assert not got["is_user_corrected"]

        # user correction wins ...
        await tm.store_correction("Where are the keys?", "en", "zh", "钥匙在哪里？", "钥匙放哪儿了？", ["钥匙放哪儿了？"])
        got = await tm.get("Where are the keys?", "en", "zh")
        assert got["translation"] == "钥匙放哪儿了？" and got["is_user_corrected"]

        # ... and a later machine write (fast MT or refiner) must NOT clobber it
        await tm.set("Where are the keys?", "en", "zh", "钥匙在哪？", ["钥匙在哪？"], quality_score=0.9)
        got = await tm.get("Where are the keys?", "en", "zh")
        assert got["translation"] == "钥匙放哪儿了？"
        assert got["is_user_corrected"]
    finally:
        await tm.close()
