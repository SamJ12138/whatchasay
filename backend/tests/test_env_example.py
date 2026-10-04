"""backend/.env.example documents every backend setting with its real default and
holds no secret (Phase 3 Batch B). Uncommenting the whole file must change nothing."""

import json
import re
from pathlib import Path

from app.config import Settings

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
LINE = re.compile(r"^# (SUBTITLE_[A-Z0-9_]+)=(.*)$")
NOT_LITERAL = {"SUBTITLE_TRANSLATION__OPUS_MODELS": "{...}", "SUBTITLE_TRANSLATION__DEVICE": None}


def documented():
    out = {}
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m:
            assert m.group(1) not in out, f"{m.group(1)} documented twice"
            out[m.group(1)] = m.group(2)
    return out


def leaves(model, prefix="SUBTITLE_"):
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        if hasattr(type(value), "model_fields"):
            yield from leaves(value, f"{prefix}{name.upper()}__")
        else:
            yield f"{prefix}{name.upper()}", value


def clean_env(monkeypatch, tmp_path):
    import os

    for k in list(os.environ):
        if k.startswith("SUBTITLE_"):
            monkeypatch.delenv(k)
    return Settings(_env_file=tmp_path / "missing.env")


def test_every_setting_is_documented_and_nothing_else(monkeypatch, tmp_path):
    keys = {k for k, _ in leaves(clean_env(monkeypatch, tmp_path))}
    doc = set(documented())
    assert keys - doc == set(), f"undocumented: {sorted(keys - doc)}"
    assert doc - keys == set(), f"documented but not a setting: {sorted(doc - keys)}"


def test_documented_values_are_the_defaults(monkeypatch, tmp_path):
    """Uncomment every line (except the two that are not literal) and load it."""
    defaults = clean_env(monkeypatch, tmp_path)
    env = tmp_path / "all.env"
    env.write_text("\n".join(f"{k}={v}" for k, v in documented().items() if k not in NOT_LITERAL), encoding="utf-8")
    loaded = Settings(_env_file=env)
    want = dict(leaves(defaults))
    got = dict(leaves(loaded))
    diff = {k: (want[k], got[k]) for k in want if k not in NOT_LITERAL and want[k] != got[k]}
    assert not diff, diff


def test_no_secrets_and_cloud_off():
    doc = documented()
    for k, v in doc.items():
        if k.endswith(("_KEY", "_REGION")):
            assert v == "", f"{k} must be empty in the example"
    assert doc["SUBTITLE_CLOUD__ENABLED"] == "false"
    assert json.loads(doc["SUBTITLE_TM__PERSIST_AUDIO_SESSIONS"]) is False


def test_the_real_env_file_is_ignored_and_the_example_is_not():
    import subprocess

    repo = EXAMPLE.parents[1]
    ignored = subprocess.run(["git", "check-ignore", "-q", "backend/.env"], cwd=repo).returncode == 0
    example_ignored = subprocess.run(["git", "check-ignore", "-q", "backend/.env.example"], cwd=repo).returncode == 0
    assert ignored and not example_ignored


def test_every_setting_has_its_own_description_line():
    """The configuration table (docs/configuration.md) is generated from these comments: one per key."""
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if LINE.match(line):
            above = lines[i - 1]
            assert above.startswith("# ") and not LINE.match(above) and not above.startswith("# ----"), \
                f"{line.split('=')[0][2:]} has no description line of its own"
            assert "|" not in above, f"'|' would break the configuration table: {above}"


def test_the_configuration_doc_lists_every_setting_and_the_readme_points_at_it():
    doc = (EXAMPLE.parents[1] / "docs" / "configuration.md").read_text(encoding="utf-8")
    for key, value in documented().items():
        assert f"| `{key}` |" in doc, f"docs/configuration.md lacks {key}"
    readme = (EXAMPLE.parents[1] / "README.md").read_text(encoding="utf-8")
    assert "](docs/configuration.md)" in readme and "| `SUBTITLE_" not in readme

