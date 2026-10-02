"""scripts/download_models.py (D1): ASR models and OPUS-MT by default; HY-MT only
with --hymt, after the Tencent license summary, and only with --accept-hymt-license.
Downloads are replaced by recorders: no network, nothing written outside tmp_path."""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def dl(monkeypatch):
    spec = importlib.util.spec_from_file_location("download_models", REPO / "scripts" / "download_models.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []
    monkeypatch.setattr(mod, "fetch_asr", lambda spec, root: calls.append(("asr", spec.name, Path(root))))
    monkeypatch.setattr(mod, "fetch_lid", lambda root: calls.append(("lid", None, Path(root))))
    monkeypatch.setattr(mod, "fetch_punct", lambda root: calls.append(("punct", None, Path(root))))
    monkeypatch.setattr(mod, "fetch_opus", lambda hf, out: calls.append(("opus", hf, Path(out))))
    monkeypatch.setattr(mod, "fetch_hymt", lambda path: calls.append(("hymt", Path(path).name, Path(path))))
    monkeypatch.setattr(mod, "fetch_llama_server", lambda bin_dir: calls.append(("llama", None, Path(bin_dir))))
    mod.calls = calls
    return mod


def run(dl, tmp_path, *args):
    return dl.main(["--data-dir", str(tmp_path / "data"), "--bin-dir", str(tmp_path / "bin"), *args])


def test_default_downloads_asr_and_opus_only(dl, tmp_path, capsys):
    assert run(dl, tmp_path) == 0
    kinds = {c[0] for c in dl.calls}
    assert kinds == {"asr", "lid", "punct", "opus"}, dl.calls
    assert {c[1] for c in dl.calls if c[0] == "asr"} == {
        "sherpa-onnx-streaming-zipformer-en-2023-06-26",
        "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20",
        "sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09",
    }
    assert {c[1] for c in dl.calls if c[0] == "opus"} == {
        "Helsinki-NLP/opus-mt-en-zh", "Helsinki-NLP/opus-mt-zh-en",
        "Helsinki-NLP/opus-mt-bn-en", "shhossain/opus-mt-en-to-bn",
    }
    for kind, _, where in dl.calls:  # everything lands under --data-dir
        assert str(where).startswith(str(tmp_path / "data")), (kind, where)
    out = capsys.readouterr().out
    assert "Tencent" not in out
    assert "declares no license" not in out  # the default set has a declared license throughout


def test_hymt_needs_the_license_flag_and_shows_the_summary(dl, tmp_path, capsys):
    assert run(dl, tmp_path, "--hymt") == 2
    assert not [c for c in dl.calls if c[0] in ("hymt", "llama")], "downloaded without accepting the license"
    out = capsys.readouterr().out
    assert "TENCENT HY COMMUNITY LICENSE" in out.upper()
    for needle in ("European Union", "United Kingdom", "South Korea", "Acceptable Use Policy", "--accept-hymt-license",
                   "https://huggingface.co/tencent/HY-MT1.5-1.8B"):
        assert needle in out, needle


def test_hymt_with_accepted_license_downloads_model_and_server(dl, tmp_path, capsys):
    assert run(dl, tmp_path, "--hymt", "--accept-hymt-license") == 0
    hymt = [c for c in dl.calls if c[0] == "hymt"]
    assert hymt and hymt[0][2] == tmp_path / "data" / "models" / "mt" / "HY-MT1.5-1.8B-Q4_K_M.gguf"
    assert [c for c in dl.calls if c[0] == "llama"] == [("llama", None, tmp_path / "bin")]
    assert "South Korea" in capsys.readouterr().out  # the summary is shown on accept too


def test_accept_flag_alone_does_not_download_hymt(dl, tmp_path):
    assert run(dl, tmp_path, "--accept-hymt-license") == 0
    assert not [c for c in dl.calls if c[0] in ("hymt", "llama")]


def test_skip_flags(dl, tmp_path):
    assert run(dl, tmp_path, "--skip-asr", "--skip-opus") == 0
    assert dl.calls == []


LARGE_ZH = "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"


def test_mandarin_large_needs_the_flag_and_shows_the_notice(dl, tmp_path, capsys):
    assert run(dl, tmp_path, "--mandarin-large") == 2
    assert not dl.calls, "downloaded without accepting the terms"
    out = capsys.readouterr().out
    for needle in ("declares", "NO license", "WenetSpeech", "non-commercial", "--accept-mandarin-large-terms", "NOTICE.md"):
        assert needle in out, needle


def test_mandarin_large_with_accepted_terms_adds_it(dl, tmp_path, capsys):
    assert run(dl, tmp_path, "--mandarin-large", "--accept-mandarin-large-terms") == 0
    asr = [c[1] for c in dl.calls if c[0] == "asr"]
    assert LARGE_ZH in asr and "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20" in asr
    assert "SUBTITLE_ASR__ZH_MODEL=large" in capsys.readouterr().out


def test_accept_flag_alone_does_not_download_the_large_mandarin_model(dl, tmp_path):
    assert run(dl, tmp_path, "--accept-mandarin-large-terms") == 0
    assert LARGE_ZH not in [c[1] for c in dl.calls if c[0] == "asr"]


def test_punctuation_model_lands_under_data_dir(dl, tmp_path):
    assert run(dl, tmp_path, "--skip-opus") == 0
    assert ("punct", None, tmp_path / "data" / "models" / "punct") in dl.calls
