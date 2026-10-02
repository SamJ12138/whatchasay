"""Startup must survive a failed MT warmup (baseline behaviour, audit of f9758f0)."""

import pytest

from app import main


@pytest.mark.asyncio
async def test_lifespan_survives_pipeline_init_failure(monkeypatch):
    async def boom():
        raise RuntimeError("pipeline init failed")

    monkeypatch.setattr(main, "warmup_pipeline", boom)
    monkeypatch.setattr(main, "get_pipeline", boom)
    monkeypatch.setattr(main.asr_pkg, "warmup_asr", lambda: None)

    class _TM:
        async def close(self):
            pass

    async def fake_tm():
        return _TM()

    monkeypatch.setattr(main, "get_translation_memory", fake_tm)
    async with main.lifespan(main.app):
        pass  # reaching here means the server would have started
