"""OPUS-MT (CTranslate2, CPU) on the six en/zh/bn directions: outputs, the D1
checks (no repeated 3-gram, length within 3x the source) and per-call latency.

    venv\\Scripts\\python scripts\\opus_quality.py                 # current settings
    venv\\Scripts\\python scripts\\opus_quality.py --no-repeat 2 --rep 1.2 --ratio 0   # override decoding

Reads the converted models in settings.translation.ct2_dir (never converts or
downloads: a missing model is reported). Latency: each sentence is translated
once to warm up, then --reps times; p50 over all timed calls of a direction
and over everything. Prints one JSON document.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beam", type=int)
    ap.add_argument("--rep", type=float, help="repetition_penalty")
    ap.add_argument("--no-repeat", type=int, help="no_repeat_ngram_size (0 = off)")
    ap.add_argument("--ratio", type=float, help="max output tokens per source token (0 = fixed cap)")
    ap.add_argument("--reps", type=int, default=5)
    args = ap.parse_args()

    import os

    os.chdir(BACKEND)
    from app.config import get_opus_route, settings
    from app.translation.base_translator import OpusCT2Engine
    from tests.opus_samples import DIRECTIONS, SAMPLES, problems

    t = settings.translation
    if args.beam is not None:
        t.opus_beam_size = args.beam
    if args.rep is not None:
        t.opus_repetition_penalty = args.rep
    if args.no_repeat is not None:
        t.opus_no_repeat_ngram_size = args.no_repeat
    if args.ratio is not None:
        t.opus_max_length_ratio = args.ratio
    decoding = {k: getattr(t, k) for k in ("opus_beam_size", "opus_repetition_penalty", "opus_no_repeat_ngram_size",
                                           "opus_max_length_ratio", "opus_max_tokens")}
    engine = OpusCT2Engine(t.ct2_dir)
    missing = sorted({m for s, g in DIRECTIONS for _, _, m in (get_opus_route(s, g) or [])
                      if not (Path(t.ct2_dir) / m.replace("/", "__") / "model.bin").exists()})
    if missing:
        print(json.dumps({"error": "OPUS-MT models not converted", "missing": missing}))
        return 2

    report = {"decoding": decoding, "directions": {}, "failures": []}
    all_ms = []
    for src, tgt in DIRECTIONS:
        rows, ms = [], []
        for text in SAMPLES[src]:
            out = engine.translate_batch_sync([text], src, tgt)[0]  # warm-up (and the output)
            for _ in range(args.reps):
                t0 = time.perf_counter()
                engine.translate_batch_sync([text], src, tgt)
                ms.append((time.perf_counter() - t0) * 1000)
            bad = problems(text, src, out, tgt)
            rows.append({"source": text, "output": out, "problems": bad})
            if bad:
                report["failures"].append({"dir": f"{src}->{tgt}", "source": text, "output": out, "problems": bad})
        all_ms += ms
        report["directions"][f"{src}->{tgt}"] = {"p50_ms": round(statistics.median(ms), 1), "rows": rows}
    report["p50_ms_all"] = round(statistics.median(all_ms), 1)
    report["p95_ms_all"] = round(sorted(all_ms)[int(0.95 * (len(all_ms) - 1))], 1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not report["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
