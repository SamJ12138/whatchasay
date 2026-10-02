"""v1 leftovers and code docs/observations.md marked dead (Phase 3 Batch A): gone,
and they stay gone."""

from pathlib import Path

import pytest

from app.config import settings

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def test_speed_mode_endpoint_is_gone(app_env):
    """v1 speed modes ('hybrid' / 'accuracy' switched the synchronous post-editor);
    the extension never calls it. Refinement is the `refiner_enabled` config."""
    with app_env.client() as c:
        assert c.post("/config/speed_mode?mode=hybrid").status_code in (404, 405)
    assert settings.refiner.enabled is False


@pytest.mark.parametrize("section,field", [
    ("features", "speed_mode"), ("features", "post_edit_batch_size"),
    ("ollama", "fallback_model"), ("ollama", "max_retries"),
    ("asr", "whisper_model"), ("asr", "whisper_bn_model"),
    ("cloud", "asr_provider"), ("cloud", "gladia_api_key"), ("cloud", "elevenlabs_api_key"),
])
def test_unused_settings_are_gone(section, field):
    assert not hasattr(getattr(settings, section), field)


def test_no_references_to_asr_engines_that_do_not_exist():
    """A5: cloud_engine / whisper_engine were imported in a try block and never existed."""
    src = (BACKEND / "app" / "asr" / "__init__.py").read_text(encoding="utf-8")
    assert "cloud_engine" not in src and "whisper_engine" not in src
    assert not (BACKEND / "app" / "asr" / "cloud_engine.py").exists()


def test_v1_post_editor_files_are_gone():
    assert not (BACKEND / "ollama" / "Modelfile").exists()  # the v1 post-editor's model
    assert not (REPO / "docs" / "TRAINING.md").exists()  # fine-tuning that post-editor


def test_startup_no_longer_probes_v1_dependencies(app_env, obs_records):
    with app_env.client():
        pass
    probed = {(r.get("context") or {}).get("module") for r in obs_records if r["stage"] == "dependency"}
    assert "ollama" not in probed and "llama_cpp" not in probed
