"""End-to-end latency of live captions, as the README's table reports it.

Streams WAV files to /ws/asr at real-time pace in 40 ms frames (as the extension
does) and measures, per clip:
  partial lag   wall clock at a partial caption minus the audio time it covers
  final         wall clock at the final minus the audio time of the last change to
                the sentence (the pause the endpoint rule waits for is included)
  translation   wall clock at each target's translation minus the final's
Prints a Markdown table and the backend's run_id (its run log is
backend/logs/run_<run_id>.jsonl).

    venv\\Scripts\\python scripts\\latency_report.py en:clip.wav zh:a.wav bn:b.wav [--port 8765]

The backend must be running (run.py). Clips are resampled to 16 kHz mono; any language in
asr.languages. Targets: the other two of en/zh/bn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets

LANGS = ("en", "zh", "bn")


def p50(xs):
    return statistics.median(xs) if xs else float("nan")


async def run_clip(port: int, lang: str, wav: Path) -> dict:
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:  # linear resample, as scripts/e2e_ws_asr.py does
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype("float32")
        sr = 16000
    audio = np.concatenate([audio, np.zeros(int(sr * 1.5), dtype="float32")])
    targets = [t for t in LANGS if t != lang]
    url = f"ws://127.0.0.1:{port}/ws/asr?source_lang={lang}&target_langs={','.join(targets)}"
    out = {"lang": lang, "wav": wav.name, "seconds": round(len(audio) / sr, 1), "partial_lag": [], "final": [],
           "translation": {t: [] for t in targets}, "sentences": 0}
    last_change = {}  # utterance_id -> audio time of the last partial whose text changed
    final_wall = {}
    async with websockets.connect(url, max_size=None) as ws:
        start = time.time()

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                now = time.time() - start
                t = m.get("type")
                if t == "partial":
                    out["partial_lag"].append(now - m["t1"])
                    last_change[m["utterance_id"]] = m["t1"]
                elif t == "final":
                    out["sentences"] += 1
                    spoken_end = last_change.get(m["utterance_id"], m["t1"])
                    out["final"].append(now - spoken_end)
                    final_wall[m["utterance_id"]] = now
                elif t == "translation" and m.get("utterance_id") in final_wall:
                    for tgt in m.get("translations", {}):
                        if tgt in out["translation"]:
                            out["translation"][tgt].append(now - final_wall[m["utterance_id"]])

        task = asyncio.create_task(reader())
        frame = int(sr * 0.04)
        for i in range(0, len(audio), frame):
            await ws.send((np.clip(audio[i:i + frame], -1, 1) * 32767).astype("<i2").tobytes())
            due = start + (i + frame) / sr
            if due > time.time():
                await asyncio.sleep(due - time.time())
        await asyncio.sleep(3.0)  # the last translations
        task.cancel()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clips", nargs="+", help="lang:path.wav")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health/json", timeout=10) as r:
        health = json.loads(r.read())
    rows = []
    for spec in args.clips:
        lang, path = spec.split(":", 1)
        rows.append(asyncio.run(run_clip(args.port, lang, Path(path))))
    print(f"run_id {health.get('run_id')}; MT device {health.get('device')}; engines {list(health.get('mt_engines', {}))}\n")
    print("| clip | speech | sentences | partial lag p50 | final after last word p50 | translation after final p50 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        tr = ", ".join(f"{t} {p50(v) * 1000:.0f} ms" for t, v in r["translation"].items())
        print(f"| {r['wav']} | {r['lang']}, {r['seconds']} s | {r['sentences']} | {p50(r['partial_lag']):.2f} s "
              f"| {p50(r['final']):.2f} s | {tr} |")
    print("\n" + json.dumps(rows, default=lambda x: round(x, 3) if isinstance(x, float) else str(x))[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
