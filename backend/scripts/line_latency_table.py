"""Per-line latency table (docs/latency.md): what the viewer feels, measured in the browser.

Plays each clip N times through the browser harness (scripts/e2e_extension.py: the real
extension in Chromium, tab capture, a backend started per run, spoken language
Auto-detect, target English, or Chinese when the speech is English) and collects, per run:

  first display      audio of a line's first word -> first text of the line on screen
  final              audio of its last word -> final translation on screen
  first translation  audio of its first word -> first translated text on screen
  first confirmed    video start -> first subtitle in the confirmed language
  flicker            per line, the changes of its displayed translation that are not a pure append
                     (a draft or the final rewriting what was shown), and its draft count
  segments           length of each final (s), from the backend's run log
  translate          calls, calls per second of clip, p50 / p95 (ms), from the run log

    python scripts/line_latency_table.py --runs 3 --out runs.json en=clip-en.wav bn=clip-bn.wav

Needs a Python with Playwright (this interpreter, or ST_PLAYWRIGHT_PYTHON) and the models:
set ST_MODELS_ROOT (a folder with asr/ and punct/) or SUBTITLE_ASR__MODELS_DIR and
SUBTITLE_ASR__PUNCT_DIR. Translation runs on the CPU unless SUBTITLE_TRANSLATION__DEVICE
says otherwise. Prints a Markdown table; --out keeps every run's summary as JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
HARNESS = BACKEND / "scripts" / "e2e_extension.py"


def pct(xs, p):
    xs = sorted(x for x in xs if isinstance(x, (int, float)))
    if not xs:
        return None
    return xs[max(0, min(len(xs) - 1, math.ceil(p / 100 * len(xs)) - 1))]


def run_log_stats(run_id: str, clip_s: float, lang: str | None = None) -> dict:
    """Segment lengths (finals in the detected language) and translate calls of one
    backend run, from its run log."""
    path = Path(os.environ.get("SUBTITLE_OBS_DIR") or BACKEND / "logs") / f"run_{run_id}.jsonl"
    segments, translate = [], []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ctx = r.get("context") or {}
            if r.get("stage") == "segment" and r.get("event") == "success" and (lang is None or ctx.get("lang") == lang):
                segments.append(ctx.get("audio_s"))
            elif r.get("stage") == "translate" and r.get("event") == "success" and isinstance(r.get("duration_ms"), (int, float)):
                translate.append(r["duration_ms"])
    return {"segments_s": segments, "translate_calls": len(translate),
            "translate_calls_per_s": round(len(translate) / clip_s, 2) if clip_s else None,
            "translate_p50_ms": pct(translate, 50), "translate_p95_ms": pct(translate, 95)}


def row(name: str, s: dict) -> dict:
    """The numbers of one harness run that go into the table."""
    ll = s.get("line_latency") or {}
    # only lines in the detected language: what the provisional recognizer wrote before the
    # language was known is discarded at the switch and is not a subtitle line
    langs = ll.get("lang") or []
    keep = [i for i, lang in enumerate(langs) if lang == s.get("detected_lang")] if s.get("detected_lang") else list(range(len(langs)))

    def col(name):
        values = ll.get(name) or []
        return [values[i] for i in keep if i < len(values)]

    def mean(name):
        values = [v for v in col(name) if isinstance(v, (int, float))]
        return round(sum(values) / len(values), 2) if values else None

    return {
        "clip": name, "run_id": s.get("run_id"), "ok": bool(s.get("ok")), "detected": s.get("detected_lang"),
        "lines": len(keep),
        "first_display_p50_ms": pct(col("first_display_ms"), 50), "first_display_max_ms": pct(col("first_display_ms"), 100),
        "first_translation_p50_ms": pct(col("first_translation_ms"), 50),
        "first_translation_max_ms": pct(col("first_translation_ms"), 100),
        "final_p50_ms": pct(col("final_ms"), 50), "final_max_ms": pct(col("final_ms"), 100),
        "first_confirmed_s": ll.get("first_confirmed_after_play_s"),
        "non_append_revisions_per_line": mean("non_append_revisions"), "drafts_per_line": mean("drafts"),
        **{k: (s.get("run_log") or {}).get(k) for k in ("segments_s", "translate_calls", "translate_calls_per_s",
                                                           "translate_p50_ms", "translate_p95_ms")},
    }


def markdown(rows: list[dict]) -> str:
    def f(v, unit=""):
        return "-" if v is None else f"{v:g}{unit}" if isinstance(v, float) else f"{v}{unit}"

    def s(ms):
        return "-" if ms is None else f"{ms / 1000:.2f}"

    out = ["| Clip | run_id | Lines | First display p50 / max (s) | First translation p50 / max (s) | Final p50 / max (s) "
           "| First confirmed subtitle (s) | Segments (s) | Translate calls (per s) | Translate p50 / p95 (ms) | Drafts per line | Non-append revisions per line |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        segs = ", ".join(f"{x:g}" for x in (r.get("segments_s") or [])) or "-"
        out.append(f"| {r['clip']} | `{r['run_id']}` | {r['lines']} | {s(r['first_display_p50_ms'])} / {s(r['first_display_max_ms'])} "
                   f"| {s(r['first_translation_p50_ms'])} / {s(r['first_translation_max_ms'])} "
                   f"| {s(r['final_p50_ms'])} / {s(r['final_max_ms'])} | {f(r['first_confirmed_s'])} | {segs} "
                   f"| {f(r['translate_calls'])} ({f(r['translate_calls_per_s'])}) "
                   f"| {f(r['translate_p50_ms'])} / {f(r['translate_p95_ms'])} | {f(r.get('drafts_per_line'))} "
                   f"| {f(r['non_append_revisions_per_line'])} |")
    return "\n".join(out)


def clip_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", nargs="+", help="name=path.wav")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--out", type=Path, help="write every run's harness summary here (JSON)")
    ap.add_argument("--keep-text", action="store_true", help="keep the first five lines' text in --out (default: numbers only)")
    args = ap.parse_args()

    env_py = os.environ.get("ST_PLAYWRIGHT_PYTHON")
    python = (shutil.which(env_py) or env_py) if env_py else sys.executable
    env = dict(os.environ)
    root = os.environ.get("ST_MODELS_ROOT")
    if root:
        env.setdefault("SUBTITLE_ASR__MODELS_DIR", str(Path(root) / "asr"))
        env.setdefault("SUBTITLE_ASR__PUNCT_DIR", str(Path(root) / "punct"))
    if "SUBTITLE_ASR__MODELS_DIR" not in env:
        ap.error("set ST_MODELS_ROOT or SUBTITLE_ASR__MODELS_DIR (a backend on the default folder downloads missing models into it)")
    env.setdefault("SUBTITLE_TRANSLATION__DEVICE", "cpu")

    summaries, rows = [], []
    for spec in args.clips:
        name, path = spec.split("=", 1)
        wav = Path(path)
        seconds = clip_seconds(wav)
        for i in range(args.runs):
            proc = subprocess.run(
                [python, str(HARNESS), "--start-backend", "--port", str(args.port), "--path", "audio", "--video", str(wav),
                 "--wav", str(wav), "--source", "auto", "--targets", "auto", "--lines", "5",
                 "--hold", str(round(seconds + 4)), "--timeout", "60"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
            try:
                s = json.loads(proc.stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError):
                s = {"ok": False, "error": (proc.stderr or proc.stdout)[-500:]}
            s["clip"], s["clip_seconds"], s["run"] = name, round(seconds, 1), i + 1
            if s.get("run_id"):
                s["run_log"] = run_log_stats(s["run_id"], seconds, s.get("detected_lang"))
            if not args.keep_text:
                s.pop("lines", None)
            summaries.append(s)
            rows.append(row(name, s))
            print(f"{name} run {i + 1}: ok={s.get('ok')} run_id={s.get('run_id')} {json.dumps(s.get('line_latency'))}", file=sys.stderr, flush=True)
            if args.out:
                args.out.write_text(json.dumps({"rows": rows, "summaries": summaries}, indent=1), encoding="utf-8")
    print(markdown(rows))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
