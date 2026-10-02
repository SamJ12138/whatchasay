"""Both extension paths end to end in a real Chromium (scripts/e2e_extension.py).

Slow: starts the backend with real models and Chromium with the unpacked
extension. Needs a Python with playwright: set ST_PLAYWRIGHT_PYTHON to it (a
name on PATH or a path), or run pytest from an interpreter that has playwright.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import has_zipformer

ROOT = Path(__file__).resolve().parents[1]
_env_py = os.environ.get("ST_PLAYWRIGHT_PYTHON")
PW_PYTHON = (shutil.which(_env_py) or _env_py) if _env_py else (sys.executable if importlib.util.find_spec("playwright") else None)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(400),
    pytest.mark.skipif(PW_PYTHON is None, reason="no Python with playwright (set ST_PLAYWRIGHT_PYTHON)"),
    pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded"),
]


def run_harness(*args):
    port = os.environ.get("ST_HARNESS_PORT", "8799")
    proc = subprocess.run(
        [PW_PYTHON, str(ROOT / "scripts" / "e2e_extension.py"), "--start-backend", "--port", port, *args],
        capture_output=True, text=True, timeout=300,
    )
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    return summary, proc


def test_tab_audio_reaches_content_script_as_translated_cue():
    summary, proc = run_harness("--path", "audio")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert summary["first_translation"]["targets"]


def test_page_subtitles_are_translated_over_ws():
    summary, proc = run_harness("--path", "captions", "--enable")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert "zh" in summary["first_translation"]["targets"]


def test_origins_and_per_tab_enable_in_the_browser():
    summary, proc = run_harness("--path", "security")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert all(summary["checks"].values()), summary["checks"]


def test_caption_session_survives_service_worker_idle_timeout():
    """Real idle timeout: the harness drives Chromium over raw CDP with nothing
    attached to the service worker (Playwright's attachment keeps it alive)."""
    summary, proc = run_harness("--path", "sw-idle")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert summary["checks"]["B_worker_stopped_by_idle_timeout"], summary


def test_fresh_install_asks_for_no_site_access_and_caption_mode_is_opt_in():
    """D2 on the real extension (not the harness's test copy): no origins, no install
    warnings, no content scripts; caption mode requests the caption sites; Start asks
    for tab audio capture."""
    summary, proc = run_harness("--path", "permissions")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert summary["granted"]["origins"] == [] and summary["install_warnings"] == []
