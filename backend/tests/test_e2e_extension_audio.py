"""Audio path end to end through the real extension (scripts/e2e_extension_audio.py).

Slow: starts the backend with real models and a Chromium with the unpacked
extension. Needs a Python with playwright: set ST_PLAYWRIGHT_PYTHON to it, or
run pytest from an interpreter that has playwright installed.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import shutil

import pytest

from tests.conftest import has_zipformer

ROOT = Path(__file__).resolve().parents[1]
PW_PYTHON = shutil.which(os.environ.get("ST_PLAYWRIGHT_PYTHON", "")) or os.environ.get("ST_PLAYWRIGHT_PYTHON") or (sys.executable if importlib.util.find_spec("playwright") else None)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(PW_PYTHON is None, reason="no Python with playwright (set ST_PLAYWRIGHT_PYTHON)"),
    pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded"),
]


def test_tab_audio_reaches_content_script_as_translated_cue():
    port = os.environ.get("ST_HARNESS_PORT", "8799")
    proc = subprocess.run(
        [PW_PYTHON, str(ROOT / "scripts" / "e2e_extension_audio.py"), "--start-backend", "--port", port],
        capture_output=True, text=True, timeout=300,
    )
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert summary["first_translation"]["targets"]
