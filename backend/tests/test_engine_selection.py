"""D1: OPUS-MT on CPU is the default engine; HY-MT is optional (config), never
downloaded at runtime, and its absence is one line naming scripts/download_models.py."""

import logging

import pytest

from app.config import MTConfig, Settings


def test_default_engine_is_opus():
    cfg = MTConfig()
    assert cfg.engine == "opus"
    assert cfg.engine_order == ["opus"]


def test_hymt_is_chosen_by_config_with_opus_behind_it(monkeypatch):
    monkeypatch.setenv("SUBTITLE_MT__ENGINE", "hymt")
    s = Settings()
    assert s.mt.engine == "hymt"
    assert s.mt.engine_order == ["hymt", "opus"]


def test_unknown_engine_is_refused():
    with pytest.raises(ValueError):
        MTConfig(engine="nllb")


def _no_downloads(monkeypatch):
    """Any network fetch fails the test."""
    import urllib.request

    import huggingface_hub

    def boom(*a, **k):
        raise AssertionError(f"download attempted: {a[:2]}")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", boom)
    monkeypatch.setattr(urllib.request, "urlretrieve", boom)


def test_hymt_configured_but_absent_is_one_line_and_opus_serves(monkeypatch, tmp_path, caplog, obs_records):
    from app.config import settings
    from app.translation import hymt_translator
    from app.translation.base_translator import BaseTranslator

    _no_downloads(monkeypatch)
    monkeypatch.setattr(settings.mt, "engine", "hymt")
    monkeypatch.setattr(settings.mt, "engine_order", ["hymt", "opus"])
    monkeypatch.setattr(settings.mt, "hymt_gguf", tmp_path / "missing.gguf")
    monkeypatch.setattr(settings.translation, "device", "cuda")
    monkeypatch.setattr(hymt_translator, "LLAMA_SERVER", tmp_path / "llama-server.exe")
    with caplog.at_level(logging.WARNING):
        tr = BaseTranslator()  # must not raise
    assert "hymt" not in tr.engines
    assert "opus" in tr.engines
    msgs = [r.getMessage() for r in caplog.records if "HY-MT" in r.getMessage()]
    assert len(msgs) == 1, msgs
    assert "\n" not in msgs[0]
    assert "scripts/download_models.py --hymt --accept-hymt-license" in msgs[0]
    skip = [r for r in obs_records if r["stage"] == "startup" and (r["context"] or {}).get("engine") == "hymt"]
    assert skip and skip[-1]["event"] == "skip" and "download_models.py" in skip[-1]["error_message"]


def test_hymt_without_gpu_says_so(monkeypatch, tmp_path, caplog):
    from app.config import settings
    from app.translation.base_translator import BaseTranslator

    _no_downloads(monkeypatch)
    monkeypatch.setattr(settings.mt, "engine_order", ["hymt", "opus"])
    monkeypatch.setattr(settings.translation, "device", "cpu")
    with caplog.at_level(logging.WARNING):
        tr = BaseTranslator()
    assert "hymt" not in tr.engines
    msgs = [r.getMessage() for r in caplog.records if "HY-MT" in r.getMessage()]
    assert len(msgs) == 1 and "GPU" in msgs[0], msgs


def test_default_build_never_touches_hymt(monkeypatch, caplog):
    from app.config import settings
    from app.translation.base_translator import BaseTranslator

    _no_downloads(monkeypatch)
    monkeypatch.setattr(settings.mt, "engine_order", ["opus"])
    with caplog.at_level(logging.INFO):
        tr = BaseTranslator()
    assert list(tr.engines) == ["opus"]
    assert not [r for r in caplog.records if "HY-MT" in r.getMessage()]


def test_runtime_never_downloads_the_hymt_model_or_llama_server(monkeypatch, tmp_path):
    from app.translation import hymt_translator
    from app.translation.hymt_translator import HyMTEngine

    _no_downloads(monkeypatch)
    monkeypatch.setattr(hymt_translator, "LLAMA_SERVER", tmp_path / "llama-server.exe")
    eng = HyMTEngine(gguf_path=tmp_path / "missing.gguf")
    with pytest.raises(FileNotFoundError, match="download_models.py"):
        eng._ensure_model()
    with pytest.raises(FileNotFoundError, match="download_models.py"):
        hymt_translator.ensure_llama_server()
    eng.shutdown()
