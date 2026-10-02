"""Download the models the backend uses (run with the backend's Python).

    backend\\venv\\Scripts\\python scripts\\download_models.py              # ASR + OPUS-MT (default engine)
    backend\\venv\\Scripts\\python scripts\\download_models.py --hymt       # shows the HY-MT license summary
    backend\\venv\\Scripts\\python scripts\\download_models.py --hymt --accept-hymt-license

Default: the streaming speech models (sherpa-onnx Zipformer for English, Mandarin and
Bengali, whisper-tiny for spoken-language ID) and the OPUS-MT models for every
en/zh/bn direction (converted once to CTranslate2 int8; needs transformers + torch).
--hymt adds Tencent HY-MT1.5-1.8B (GGUF) and llama.cpp's llama-server, the optional GPU
engine, only after its license summary is shown and --accept-hymt-license is given.
Files already present are skipped. Licenses: NOTICE.md.
"""

from __future__ import annotations

import argparse
import itertools
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

HYMT_FILE = "HY-MT1.5-1.8B-Q4_K_M.gguf"
HYMT_LICENSE_URL = "https://huggingface.co/tencent/HY-MT1.5-1.8B/blob/main/License.txt"

HYMT_LICENSE_SUMMARY = f"""\
Tencent HY-MT1.5-1.8B is licensed under the TENCENT HY COMMUNITY LICENSE AGREEMENT.
It is not an open-source license. Summary (the full text decides; read it first):
  {HYMT_LICENSE_URL}
  - Territory: the agreement does not apply in the European Union, the United Kingdom
    or South Korea, and the model, its output and results must not be used there.
  - Acceptable Use Policy (Exhibit A) binds you; if you pass the model or its output
    on, you must pass its use restrictions on to your recipients (section 5(a)).
  - The model and its output must not be used to improve any other AI model (5(b)).
  - Redistribution needs a copy of the agreement and its notice text (section 3);
    services with over 100 million monthly active users need a license from Tencent.
This script downloads the model for your own use on this machine; the project does
not bundle or redistribute it. It is optional: OPUS-MT (CPU) is the default engine.
"""


# ---------------------------------------------------------------- fetchers
# Module-level so tests can replace them (no network in the fast suite).

def fetch_asr(spec, root: Path) -> Path:
    from app.asr.sherpa_engine import ensure_model

    return ensure_model(spec, Path(root))


def fetch_lid(root: Path) -> Path:
    from app.asr.lid import SpokenLanguageId

    return SpokenLanguageId(Path(root))._ensure_model()


def fetch_opus(hf_name: str, out_dir: Path) -> Path:
    from app.translation.base_translator import OpusCT2Engine

    out_dir = Path(out_dir)
    if not (out_dir / "model.bin").exists():
        OpusCT2Engine(out_dir.parent)._convert(hf_name, out_dir)
    return out_dir


def fetch_hymt(path: Path) -> Path:
    from app.translation.hymt_translator import download_hymt_model

    return download_hymt_model(Path(path))


def fetch_llama_server(bin_dir: Path) -> Path:
    from app.translation.hymt_translator import download_llama_server

    return download_llama_server(Path(bin_dir))


# ---------------------------------------------------------------- plan

def opus_models(langs) -> list:
    """OPUS-MT models needed for every direction between `langs` (pivots included)."""
    from app.config import get_opus_route

    names = []
    for src, tgt in itertools.permutations(langs, 2):
        for _, _, model in get_opus_route(src, tgt) or []:
            if model not in names:
                names.append(model)
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", type=Path, default=BACKEND / "data", help="models go to <data-dir>/models (default backend/data)")
    ap.add_argument("--bin-dir", type=Path, default=BACKEND / "bin" / "llama", help="where llama-server goes (--hymt)")
    ap.add_argument("--langs", default="en,zh,bn", help="OPUS-MT directions between these languages")
    ap.add_argument("--skip-asr", action="store_true")
    ap.add_argument("--skip-opus", action="store_true")
    ap.add_argument("--hymt", action="store_true", help="also the optional HY-MT GPU engine (license gate)")
    ap.add_argument("--accept-hymt-license", action="store_true", help="accept the Tencent HY Community License")
    args = ap.parse_args(argv)

    models = Path(args.data_dir) / "models"
    if args.hymt:
        print(HYMT_LICENSE_SUMMARY)
        if not args.accept_hymt_license:
            print("Not downloading anything. To accept the license and download HY-MT, run again with:\n"
                  "  --hymt --accept-hymt-license")
            return 2
        print("License accepted with --accept-hymt-license.\n")

    if not args.skip_asr:
        from app.asr.sherpa_engine import DEFAULT_MODELS

        for lang, spec in DEFAULT_MODELS.items():
            print(f"ASR {lang}: {spec.name}")
            fetch_asr(spec, models / "asr")
        print("Language ID: sherpa-onnx-whisper-tiny")
        fetch_lid(models / "asr")
        print("Note: the Mandarin model declares no license upstream; see NOTICE.md.")

    if not args.skip_opus:
        for name in opus_models([l for l in args.langs.split(",") if l]):
            print(f"OPUS-MT: {name}")
            fetch_opus(name, models / "ct2" / name.replace("/", "__"))

    if args.hymt:
        print(f"HY-MT: {HYMT_FILE}")
        fetch_hymt(models / "mt" / HYMT_FILE)
        print(f"llama-server: {args.bin_dir}")
        fetch_llama_server(args.bin_dir)
        print("Enable it with SUBTITLE_MT__ENGINE=hymt (needs an NVIDIA GPU).")
    print("Done.")
    return 0


if __name__ == "__main__":
    os.chdir(BACKEND)  # the backend's relative data paths resolve as they do for run.py
    sys.exit(main())
