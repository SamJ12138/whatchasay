"""Pipeline behaviour with fake engines: fast path, per-request targets,
revision-2 refinement, cache interplay."""

import asyncio
from pathlib import Path
from typing import Dict, List

import pytest
import pytest_asyncio

from app.config import settings
from app.models import SubtitleCue
from app.translation import pipeline as pipeline_mod
from app.translation.base_translator import BaseTranslator
from app.cache.translation_memory import TranslationMemory


class FakeEngine:
    name = "fake"
    max_concurrency = 4

    def __init__(self):
        self.calls: List[tuple] = []

    def supports(self, s, t):
        return True

    def translate_batch_sync(self, texts, s, t):
        self.calls.append((tuple(texts), s, t))
        return [f"[{s}->{t}] {x}" for x in texts]

    def status(self):
        return {"engine": "fake"}


class FakeRefiner:
    async def refine(self, items, langs, prev_cues=None):
        return [{l: item["base_translations"][l] + " ✨" for l in langs if l in item["base_translations"]} for item in items]


@pytest_asyncio.fixture
async def pipe(tmp_path: Path, monkeypatch, clear_memory_cache):
    engine = FakeEngine()

    def fake_translator():
        bt = BaseTranslator.__new__(BaseTranslator)
        bt.device = "cpu"
        bt.engines = {"fake": engine}
        bt._engine_locks = {}
        return bt

    tm = TranslationMemory(db_path=tmp_path / "tm.db")
    await tm.initialize()

    async def fake_tm():
        return tm

    monkeypatch.setattr(pipeline_mod, "get_base_translator", fake_translator)
    monkeypatch.setattr(pipeline_mod, "get_translation_memory", fake_tm)
    monkeypatch.setattr(settings.mt, "engine_order", ["fake"])
    p = pipeline_mod.TranslationPipeline()
    await p.initialize()
    p._fake_engine = engine
    yield p
    await asyncio.sleep(0.05)  # let fire-and-forget TM writes finish
    await tm.close()


@pytest.mark.asyncio
async def test_fast_path_translates_all_targets(pipe):
    cue = SubtitleCue(text="Where are the keys?", start_time=0, end_time=2, source_lang="en")
    res = (await pipe.translate_batch([cue], ["zh", "bn"]))[0]
    assert res.translations["zh"].single_line == "[en->zh] Where are the keys?"
    assert res.translations["bn"].single_line == "[en->bn] Where are the keys?"
    assert res.revision == 1 and not res.post_edited


@pytest.mark.asyncio
async def test_source_equals_target_is_passthrough(pipe):
    cue = SubtitleCue(text="আমি ফিরে আসব", start_time=0, end_time=2, source_lang="bn")
    res = (await pipe.translate_batch([cue], ["bn", "en"]))[0]
    assert res.translations["bn"].single_line == "আমি ফিরে আসব"
    assert res.translations["en"].single_line.startswith("[bn->en]")


@pytest.mark.asyncio
async def test_memory_cache_hit_second_time(pipe):
    cue = SubtitleCue(text="Hello there", start_time=0, end_time=1, source_lang="en")
    await pipe.translate_batch([cue], ["zh"])
    n = len(pipe._fake_engine.calls)
    res = (await pipe.translate_batch([cue], ["zh"]))[0]
    assert res.from_cache and len(pipe._fake_engine.calls) == n


@pytest.mark.asyncio
async def test_passthrough_uses_request_targets(pipe):
    cue = SubtitleCue(text="[music]", start_time=0, end_time=1)
    res = (await pipe.translate_batch([cue], ["bn"]))[0]
    assert set(res.translations.keys()) == {"bn"}
    assert res.notes and (res.notes.get("is_sound_effect") or res.notes.get("is_music"))


@pytest.mark.asyncio
async def test_refine_batch_returns_revision_2(pipe, monkeypatch):
    from app.translation import refiner as refiner_mod

    monkeypatch.setattr(settings.refiner, "enabled", True)
    monkeypatch.setattr(settings.refiner, "skip_languages", ["bn"])
    monkeypatch.setattr(refiner_mod, "get_refiner", lambda: FakeRefiner())

    cue = SubtitleCue(text="Where are the keys?", start_time=0, end_time=2, source_lang="en")
    res = (await pipe.translate_batch([cue], ["zh", "bn"]))[0]
    changed = await pipe.refine_batch([res], ["zh", "bn"])
    assert len(changed) == 1
    r2 = changed[0]
    assert r2.revision == 2 and r2.post_edited
    assert r2.translations["zh"].single_line.endswith("✨")
    # Bengali is in skip_languages: untouched
    assert r2.translations["bn"].single_line == res.translations["bn"].single_line


@pytest.mark.asyncio
async def test_refiner_disabled_for_bengali_only(pipe, monkeypatch):
    monkeypatch.setattr(settings.refiner, "enabled", True)
    assert pipe.refiner_enabled(["zh", "bn"])
    assert not pipe.refiner_enabled(["bn"])
    monkeypatch.setattr(settings.refiner, "enabled", False)
    monkeypatch.setattr(settings.features, "use_post_editor", False)
    assert not pipe.refiner_enabled(["zh"])
