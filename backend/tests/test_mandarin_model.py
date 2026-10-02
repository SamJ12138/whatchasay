"""Mandarin ASR model (Phase 3 Batch A): the default has a declared permissive
license (bilingual zh-en 2023-02-20, Apache-2.0). The larger model that declares no
license is opt-in: config asr.zh_model=large, downloaded only by
`scripts/download_models.py --mandarin-large --accept-mandarin-large-terms`, never
by the backend. No network: downloads are refused by a stub."""

import pytest
from pydantic import ValidationError

from app.asr import sherpa_engine
from app.asr.sherpa_engine import DEFAULT_MODELS, MANDARIN_MODELS, ensure_model, models_for
from app.config import ASRConfig, settings

BILINGUAL = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
LARGE = "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("no download expected")

    monkeypatch.setattr(sherpa_engine.urllib.request, "urlretrieve", refuse)


def test_default_mandarin_model_has_a_declared_license():
    assert settings.asr.zh_model == "bilingual"
    assert DEFAULT_MODELS["zh"].name == BILINGUAL
    assert not DEFAULT_MODELS["zh"].gated
    assert MANDARIN_MODELS["large"].name == LARGE and MANDARIN_MODELS["large"].gated


def test_unknown_mandarin_model_is_refused():
    with pytest.raises(ValidationError):
        ASRConfig(zh_model="whatever")


def test_large_configured_but_absent_falls_back_with_one_warning(tmp_path, caplog, no_network):
    with caplog.at_level("WARNING"):
        models = models_for("large", tmp_path)
    assert models["zh"].name == BILINGUAL
    msgs = [r.getMessage() for r in caplog.records if "Mandarin" in r.getMessage()]
    assert len(msgs) == 1 and "--mandarin-large --accept-mandarin-large-terms" in msgs[0]


def test_large_used_when_present(tmp_path):
    d = tmp_path / LARGE
    d.mkdir()
    (d / "tokens.txt").write_text("x")
    assert models_for("large", tmp_path)["zh"].name == LARGE
    assert models_for("bilingual", tmp_path)["zh"].name == BILINGUAL


def test_backend_never_downloads_the_large_model(tmp_path, no_network):
    with pytest.raises(FileNotFoundError, match="--mandarin-large"):
        ensure_model(MANDARIN_MODELS["large"], tmp_path)


def test_unused_precisions_are_dropped_after_extraction(tmp_path, monkeypatch):
    """The bilingual archive ships fp32 and int8 copies (531 MB); only int8 is kept."""
    import io
    import tarfile

    spec = MANDARIN_MODELS["bilingual"]

    def fake_download(url, archive):
        assert url == spec.url
        with tarfile.open(archive, "w:bz2") as tf:
            for name in ("tokens.txt", spec.encoder, spec.decoder, spec.joiner, *spec.drop):
                data = b"x"
                info = tarfile.TarInfo(f"{spec.name}/{name}")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))

    monkeypatch.setattr(sherpa_engine.urllib.request, "urlretrieve", fake_download)
    d = ensure_model(spec, tmp_path)
    left = sorted(p.name for p in d.iterdir())
    assert left == sorted(["tokens.txt", spec.encoder, spec.decoder, spec.joiner])
    assert not list(tmp_path.glob("*.tar.bz2"))
