"""OPUS-MT conversion leaves no Hugging Face checkpoint behind (follow-up 4).

The runtime reads only the converted folder (model.bin + source.spm / target.spm), so the
checkpoint is downloaded into a private temporary folder, with one weights file, converted
from that local path and deleted, whatever happens. The user's own Hugging Face cache is
never used. No network: the download and the converter are fakes."""

from pathlib import Path

import pytest

from app.translation import base_translator as bt


class Recorder:
    def __init__(self, fail=False):
        self.fail = fail
        self.downloads = []
        self.converted = []

    def snapshot_download(self, repo_id, local_dir=None, allow_patterns=None, **kw):
        assert local_dir, "must download into a private folder, not the shared cache"
        self.downloads.append({"repo": repo_id, "dir": Path(local_dir), "patterns": list(allow_patterns or []), **kw})
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / "pytorch_model.bin").write_bytes(b"x" * 10)
        return str(local_dir)

    def converter(self, model_name_or_path, **kw):
        rec = self

        class Conv:
            def convert(self, out, quantization=None, force=False):
                assert Path(model_name_or_path).is_dir(), "converter must read the local download"
                rec.converted.append({"src": Path(model_name_or_path), "out": Path(out), "q": quantization})
                if rec.fail:
                    raise RuntimeError("conversion failed")
                Path(out).mkdir(parents=True, exist_ok=True)
                (Path(out) / "model.bin").write_bytes(b"ct2")

        return Conv()


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(bt, "_snapshot_download", r.snapshot_download)
    monkeypatch.setattr(bt, "_files_in_repo", lambda repo: ["config.json", "pytorch_model.bin", "tf_model.h5",
                                                            "rust_model.ot", "source.spm", "target.spm", "vocab.json"])
    monkeypatch.setattr(bt, "_transformers_converter", r.converter)
    return r


def test_conversion_reads_a_private_download_and_deletes_it(rec, tmp_path):
    out = tmp_path / "ct2" / "Helsinki-NLP__opus-mt-en-zh"
    bt.OpusCT2Engine(tmp_path / "ct2")._convert("Helsinki-NLP/opus-mt-en-zh", out)
    assert (out / "model.bin").exists()
    d = rec.downloads[0]
    assert d["repo"] == "Helsinki-NLP/opus-mt-en-zh"
    assert rec.converted[0]["src"] == d["dir"] and rec.converted[0]["q"] == "int8"
    assert not d["dir"].exists(), "the downloaded checkpoint was left on disk"


def test_only_one_weights_file_is_downloaded(rec, tmp_path):
    bt.OpusCT2Engine(tmp_path / "ct2")._convert("Helsinki-NLP/opus-mt-en-zh", tmp_path / "out")
    pats = rec.downloads[0]["patterns"]
    weights = [p for p in pats if p.endswith((".bin", ".safetensors", ".h5", ".ot", ".msgpack"))]
    assert weights == ["pytorch_model.bin"], pats
    assert "*.spm" in pats and "*.json" in pats


def test_safetensors_preferred_when_the_repo_has_it(rec, tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "_files_in_repo", lambda repo: ["config.json", "model.safetensors", "pytorch_model.bin"])
    bt.OpusCT2Engine(tmp_path / "ct2")._convert("shhossain/opus-mt-en-to-bn", tmp_path / "out")
    weights = [p for p in rec.downloads[0]["patterns"] if p.endswith((".bin", ".safetensors"))]
    assert weights == ["model.safetensors"]


def test_a_failed_conversion_still_deletes_the_download(rec, tmp_path):
    rec.fail = True
    with pytest.raises(RuntimeError):
        bt.OpusCT2Engine(tmp_path / "ct2")._convert("Helsinki-NLP/opus-mt-en-zh", tmp_path / "out")
    assert not rec.downloads[0]["dir"].exists()
