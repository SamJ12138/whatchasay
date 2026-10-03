"""The glossary on the live clip (docs/live-test-youtube.md): the term রাজভোগ, heard as
রাজবুক, rendered rajbhog. Line 1 of the clip ("একটা রাজবুক দেখা তো" -> "A royal book.")
must read as rajbhog in 3 of 3 browser runs.

Slow: the real models, the real extension in Chromium (scripts/e2e_extension.py with
--glossary), the clip in ST_LID_CLIP_WAV (not in the repository)."""

import json
import os
from pathlib import Path

import pytest

from tests.conftest import has_zipformer
from tests.test_e2e_extension import PW_PYTHON, run_harness

CLIP = os.environ.get("ST_LID_CLIP_WAV")
TERM = {"source_lang": "bn", "canonical": "রাজভোগ", "heard_as": ["রাজবুক"], "renderings": {"en": "rajbhog"}}

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(900),
    pytest.mark.skipif(PW_PYTHON is None, reason="no Python with playwright (set ST_PLAYWRIGHT_PYTHON)"),
    pytest.mark.skipif(not has_zipformer("bn"), reason="Bengali Zipformer model not downloaded"),
    pytest.mark.skipif(not (CLIP and Path(CLIP).exists()), reason="ST_LID_CLIP_WAV not set (the clip is not in the repository)"),
]


def test_rajbhog_reads_correctly_on_line_one_in_three_of_three_runs():
    results = []
    for _ in range(3):
        summary, proc = run_harness("--path", "audio", "--video", CLIP, "--source", "auto", "--targets", "auto",
                                    "--lines", "3", "--hold", "6", "--show-source",
                                    "--glossary", json.dumps(TERM, ensure_ascii=False))
        assert summary["ok"], (summary, proc.stderr[-2000:])
        assert summary["glossary"]["saved"] == 1, summary["glossary"]
        lines = summary["lines"]
        assert lines, summary
        results.append({"run_id": summary["run_id"], "original": lines[0]["original"], "translation": lines[0]["translation"]})
    for r in results:
        assert "rajbhog" in (r["translation"] or "").lower(), results
        assert "book" not in (r["translation"] or "").lower(), results
        # the recognised line on screen carries the canonical spelling, not the misheard one
        assert "রাজভোগ" in (r["original"] or ""), results
    print("\nglossary live:", json.dumps(results, ensure_ascii=False))
