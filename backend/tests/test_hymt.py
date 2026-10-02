"""HY-MT prompt construction (always) and a GPU translation smoke test
(only when llama-server + model are present)."""

import time
from pathlib import Path

import pytest

from app.translation.hymt_translator import build_prompt, LLAMA_SERVER
from app.config import settings


def test_prompt_uses_chinese_template_when_zh_involved():
    p = build_prompt("Hello", "en", "zh")
    assert p.startswith("将以下文本翻译为中文")
    p = build_prompt("你好", "zh", "bn")
    assert "孟加拉语" in p


def test_prompt_uses_english_template_otherwise():
    p = build_prompt("Hello", "en", "bn")
    assert p.startswith("Translate the following segment into Bengali")


@pytest.mark.slow
@pytest.mark.skipif(
    settings.translation.device != "cuda" or not LLAMA_SERVER.exists() or not Path(settings.mt.hymt_gguf).exists(),
    reason="needs CUDA, llama-server binary and the HY-MT GGUF",
)
def test_hymt_engine_translates_under_budget():
    from app.translation.hymt_translator import HyMTEngine

    eng = HyMTEngine()
    try:
        eng.warmup()
        assert eng.status()["available"], eng.status()
        t = time.perf_counter()
        out = eng.translate_batch_sync(["Where did you put the keys?"], "en", "zh")
        ms = (time.perf_counter() - t) * 1000
        assert any("一" <= ch <= "鿿" for ch in out[0])
        assert ms < 600, f"en->zh took {ms:.0f} ms"
        out = eng.translate_batch_sync(["Where did you put the keys?"], "en", "bn")
        assert any("ঀ" <= ch <= "৿" for ch in out[0])
    finally:
        eng.shutdown()
