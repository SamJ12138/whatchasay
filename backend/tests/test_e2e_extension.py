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

from tests.conftest import MODELS_DIR, MODELS_ROOT, has_zipformer, zipformer_dir

ROOT = Path(__file__).resolve().parents[1]
_env_py = os.environ.get("ST_PLAYWRIGHT_PYTHON")
PW_PYTHON = (shutil.which(_env_py) or _env_py) if _env_py else (sys.executable if importlib.util.find_spec("playwright") else None)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(400),
    pytest.mark.skipif(PW_PYTHON is None, reason="no Python with playwright (set ST_PLAYWRIGHT_PYTHON)"),
    pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded"),
]


def run_harness(*args, env=None):
    port = os.environ.get("ST_HARNESS_PORT", "8799")
    # the backend reads the real models (MODELS_ROOT: backend/data/models or ST_MODELS_ROOT)
    models = {"SUBTITLE_ASR__MODELS_DIR": str(MODELS_DIR), "SUBTITLE_ASR__PUNCT_DIR": str(MODELS_ROOT / "punct")}
    proc = subprocess.run(
        [PW_PYTHON, str(ROOT / "scripts" / "e2e_extension.py"), "--start-backend", "--port", port, *args],
        capture_output=True, text=True, timeout=300, env={**os.environ, **models, **(env or {})},
    )
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    return summary, proc


def test_tab_audio_reaches_content_script_as_translated_cue():
    summary, proc = run_harness("--path", "audio")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert summary["extension_id_derived_ok"], summary  # app.security's derivation = Chrome's id
    assert summary["first_translation"]["targets"]
    # D3: the audio session left no trace in the persistent TM
    assert summary["tm_rows_after"] == summary["tm_rows_before"], summary


@pytest.mark.skipif(not has_zipformer("zh"), reason="licensed Mandarin model (bilingual zh-en) not downloaded")
def test_mandarin_tab_audio_with_the_licensed_model():
    """Phase 3 Batch A: Mandarin speech through the default Mandarin model, which has a
    declared license (Apache-2.0), to an English translation in the page."""
    wav = zipformer_dir("zh") / "test_wavs" / "0.wav"
    summary, proc = run_harness("--path", "audio", "--wav", str(wav), "--source", "zh", "--targets", "en")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert "en" in summary["first_translation"]["targets"]
    assert summary["asr_models"]["zh"] == "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20", summary
    assert summary["tm_rows_after"] == summary["tm_rows_before"], summary


def test_alt_e_correction_on_a_live_captions_only_tab_is_saved():
    """Follow-up 2: the tab has no caption socket (caption mode off); Alt+E, type, Enter
    must store the correction in the backend's TM (content script -> worker -> POST
    /corrections). The test page has player shortcuts on the document like a video site
    (m mutes, space pauses, j rewinds): typing the correction must not operate them."""
    summary, proc = run_harness("--path", "audio", "--correct", "--correct-text", "hm, just a moment")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert summary["correction_saved"], summary
    assert summary["correction"]["page_untouched"], summary["correction"]


@pytest.mark.parametrize("lang,target", [("en", "zh"), ("bn", "en")])
def test_first_confirmed_subtitle_is_the_first_undimmed_one(lang, target):
    """Auto-detect in the browser: what the provisional (English) recognizer hears is drawn
    dimmed above a "Detecting language…" label, and nothing is undimmed before the content
    script has the confirmed language. en: the provisional language was right, its text is
    undimmed in place. bn: detection switches, the English guesses are discarded and the
    first undimmed line is Bengali. The overlay is read through CDP every 250 ms."""
    if not has_zipformer(lang):
        pytest.skip(f"{lang} model not downloaded")
    if not (MODELS_DIR / "sherpa-onnx-whisper-tiny" / "tiny-encoder.int8.onnx").exists():
        pytest.skip("whisper-tiny LID model not downloaded")
    wav = zipformer_dir(lang) / "test_wavs" / "0.wav"
    # --video: the clip starts from its first word once capture runs (and does not loop)
    summary, proc = run_harness("--path", "audio", "--video", str(wav), "--source", "auto", "--targets", target,
                                "--lines", "1", "--hold", "4")
    assert summary.get("detected_lang") == lang, (summary, proc.stderr[-2000:])
    d = summary["dimming"]
    assert d["violations"] == [], d
    assert d["dimmed_samples"] >= 1, d       # the provisional phase was on screen, dimmed and labelled
    assert d["first_undimmed"], d
    assert d["first_undimmed"]["t"] >= d["confirmed_first_seen_s"], d
    if lang == "bn":
        assert d["dimmed_scripts"] == ["latin"], d
        assert d["first_undimmed"]["script"] == "bengali", d


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


def test_options_clear_translation_memory_button():
    """D3 in the options page: the storage sentence; Clear translation memory asks
    first, does nothing when dismissed and empties the TM when confirmed."""
    summary, proc = run_harness("--path", "clear-memory")
    assert summary["ok"], (summary, proc.stderr[-2000:])


@pytest.mark.parametrize("cloud", ["off", "on"])
def test_options_page_holds_no_keys_and_names_the_cloud_receivers(cloud):
    """D4: keys stored by an older version are deleted with a notice; no key fields;
    the cloud line matches what the backend reports (on = a dummy Google key; nothing
    is translated in this path, so no request leaves the machine)."""
    env = {"SUBTITLE_CLOUD__ENABLED": "true", "SUBTITLE_CLOUD__GOOGLE_API_KEY": "dummy-not-a-real-key"} if cloud == "on" else {}
    summary, proc = run_harness("--path", "cloud-keys", env=env)
    assert summary["ok"], (summary, proc.stderr[-2000:])
    assert bool(summary["receivers"]) == (cloud == "on")
