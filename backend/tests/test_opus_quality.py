"""D1: OPUS-MT (the default engine) on the six en/zh/bn directions must not
repeat itself: no 3-gram occurs twice in one output, and the output is at most
3x the source (words; characters for Chinese). Real CTranslate2 models: slow.
Before/after numbers: scripts/opus_quality.py."""

from pathlib import Path

import pytest

from tests.opus_samples import DIRECTIONS, SAMPLES, problems

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def opus():
    from app.config import get_opus_route, settings
    from app.translation.base_translator import CT2_AVAILABLE, OpusCT2Engine

    if not CT2_AVAILABLE:
        pytest.skip("ctranslate2 not installed")
    missing = {m for s, t in DIRECTIONS for _, _, m in (get_opus_route(s, t) or [])
               if not (Path(settings.translation.ct2_dir) / m.replace("/", "__") / "model.bin").exists()}
    if missing:
        pytest.skip(f"OPUS-MT models not converted: {sorted(missing)} (python scripts/download_models.py)")
    return OpusCT2Engine(settings.translation.ct2_dir)


@pytest.mark.parametrize("src,tgt", DIRECTIONS, ids=[f"{s}-{t}" for s, t in DIRECTIONS])
def test_opus_output_does_not_repeat(opus, src, tgt):
    outs = opus.translate_batch_sync(SAMPLES[src], src, tgt)
    bad = [(text, out, p) for text, out in zip(SAMPLES[src], outs) if (p := problems(text, src, out, tgt))]
    assert not bad, "\n".join(f"{t!r} -> {o!r}: {p}" for t, o, p in bad)


def test_english_to_chinese_is_simplified(opus):
    """opus-mt-en-zh is multi-target (zh variants); without its >>cmn_Hans<< token
    it mixes in traditional characters."""
    outs = opus.translate_batch_sync(SAMPLES["en"][:3], "en", "zh")
    traditional = set("們這來會後鐘麼還說話裡")
    assert not [o for o in outs if traditional & set(o)], outs
