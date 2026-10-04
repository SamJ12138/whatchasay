"""How fast a clip's speaker talks and how they pause (docs/latency.md, the benchmark table).

The recognizer of the clip's language is run over the WAV offline (40 ms frames, as live
audio) and its token timestamps are kept. From them, per clip:

  words                 tokens that start a word (Mandarin: every character is one)
  speech span           first word to last word (s)
  words / min, span     words over the speech span
  words / min, speaking words over the span minus the pauses
  pauses                gaps between consecutive tokens of at least --pause s (0.3): count,
                        median, longest, their share of the span, and how many reach the
                        0.5 s at which a line is closed (SUBTITLE_ASR__SPLIT_GAP_S)

    python scripts/clip_speech_stats.py [--json out.json] en=clip-en.wav zh=clip-zh.wav

The language is given, not detected: the question is how the speaker talks, so the right
recognizer is used. Needs the models (SUBTITLE_ASR__MODELS_DIR, or ST_MODELS_ROOT with
asr/ under it). Prints a Markdown table.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import wave
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.asr.engine import SAMPLE_RATE  # noqa: E402
from app.asr.sherpa_engine import SherpaZipformerEngine, _word_start  # noqa: E402

FRAME = 1280  # bytes: 40 ms of 16 kHz int16


def pcm_of(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise SystemExit(f"{path}: needs 16 kHz mono 16-bit PCM")
        return w.readframes(w.getnframes())


def token_times(engine: SherpaZipformerEngine, lang: str, pcm: bytes) -> tuple[list[str], list[float]]:
    """Every token the recognizer emitted with its audio-clock time. The recognizer's segments
    are read after each frame; an endpoint resets its token list, so the count restarting at
    zero marks a new segment (times are absolute, so nothing is counted twice)."""
    session = engine.start_session(lang)
    tokens, times, seen = [], [], 0
    pcm = pcm + b"\x00" * (SAMPLE_RATE * 2 * 2)  # 2 s of silence: the last line ends
    for i in range(0, len(pcm), FRAME):
        session.feed(pcm[i:i + FRAME])
        tok, tim = session._tokens()
        if len(tok) < seen:
            seen = 0
        if len(tok) > seen:
            tokens.extend(tok[seen:])
            times.extend(tim[seen:])
            seen = len(tok)
    session.close()
    return tokens, times


def stats(tokens: list[str], times: list[float], pause_s: float) -> dict:
    if len(tokens) < 2:
        return {"words": len(tokens)}
    words = sum(1 for t in tokens if _word_start(t))
    span = times[-1] - times[0]
    gaps = [b - a for a, b in zip(times, times[1:])]
    pauses = [g for g in gaps if g >= pause_s]
    speaking = span - sum(pauses)
    return {"tokens": len(tokens), "words": words, "span_s": round(span, 1),
            "wpm_span": round(words / span * 60) if span else None,
            "wpm_speaking": round(words / speaking * 60) if speaking > 0 else None,
            "pauses": len(pauses), "pause_median_s": round(statistics.median(pauses), 2) if pauses else None,
            "pause_max_s": round(max(pauses), 2) if pauses else None,
            "pause_share": round(sum(pauses) / span, 2) if span else None,
            "pauses_0_5": sum(1 for g in pauses if g >= 0.5)}


def markdown(rows: list[dict]) -> str:
    def f(v):
        return "-" if v is None else f"{v:g}" if isinstance(v, float) else str(v)

    out = ["| Clip | Language | Clip (s) | Speech span (s) | Words (zh: characters) | Words / min over the span "
           "| Words / min while speaking | Pauses >= 0.3 s | Median pause (s) | Longest pause (s) | Pause share | Pauses >= 0.5 s |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["stats"]
        out.append(f"| {r['clip']} | {r['lang']} | {r['seconds']:g} | {f(s.get('span_s'))} | {f(s.get('words'))} | {f(s.get('wpm_span'))} "
                   f"| {f(s.get('wpm_speaking'))} | {f(s.get('pauses'))} | {f(s.get('pause_median_s'))} | {f(s.get('pause_max_s'))} "
                   f"| {f(s.get('pause_share'))} | {f(s.get('pauses_0_5'))} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", nargs="+", help="lang=path.wav, or lang:name=path.wav")
    ap.add_argument("--pause", type=float, default=0.3, help="a gap between tokens at least this long is a pause (s)")
    ap.add_argument("--json", type=Path, help="also write every clip's numbers here")
    args = ap.parse_args()
    models = os.environ.get("SUBTITLE_ASR__MODELS_DIR") or (
        os.environ.get("ST_MODELS_ROOT") and str(Path(os.environ["ST_MODELS_ROOT"]) / "asr"))
    if not models:
        ap.error("set SUBTITLE_ASR__MODELS_DIR or ST_MODELS_ROOT")
    engine = SherpaZipformerEngine(Path(models))
    rows = []
    for spec in args.clips:
        lang_name, path = spec.split("=", 1)
        lang, _, name = lang_name.partition(":")
        pcm = pcm_of(Path(path))
        tokens, times = token_times(engine, lang, pcm)
        rows.append({"clip": name or Path(path).stem, "lang": lang, "seconds": round(len(pcm) / 2 / SAMPLE_RATE, 1),
                     "stats": stats(tokens, times, args.pause)})
        print(f"{rows[-1]['clip']}: {rows[-1]['stats']}", file=sys.stderr, flush=True)
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(markdown(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
