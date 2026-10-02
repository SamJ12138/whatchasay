"""End-to-end test client for /ws/asr: streams a WAV at real-time pace exactly
like the extension (40 ms int16 frames) and prints every server message with
its latency relative to the audio clock.

Run (server must be up):
    venv/Scripts/python.exe scripts/e2e_ws_asr.py <wav> [source_lang] [target_langs]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets  # installed with uvicorn[standard]

ROOT = Path(__file__).resolve().parents[1]
wav = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/models/asr/sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09/test_wavs/0.wav"
source_lang = sys.argv[2] if len(sys.argv) > 2 else "auto"
targets = sys.argv[3] if len(sys.argv) > 3 else "en,zh"
url = f"ws://127.0.0.1:8765/ws/asr?source_lang={source_lang}&target_langs={targets}"


async def main():
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype("float32")
    # append 1.5 s of silence so the endpoint fires
    audio = np.concatenate([audio, np.zeros(int(16000 * 1.5), dtype="float32")])
    frame = int(16000 * 0.04)
    print(f"wav {wav.name}: {len(audio)/16000:.1f}s, url {url}")

    async with websockets.connect(url, max_size=None) as ws:
        start = None
        results = {"partials": 0}

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                wall = time.time() - start if start else 0
                t = m.get("type")
                if t == "partial":
                    results["partials"] += 1
                    print(f"  [partial] wall={wall:5.2f}s audio_t1={m['t1']:5.2f}s lag={wall-m['t1']:+.2f}s | {m['text'][:60]}")
                elif t == "final":
                    print(f"  [FINAL  ] wall={wall:5.2f}s audio_t1={m['t1']:5.2f}s lag={wall-m['t1']:+.2f}s | {m['text']}")
                elif t in ("translation", "revision"):
                    tr = {k: v["single_line"] for k, v in m["translations"].items()}
                    print(f"  [{t.upper():8s}] wall={wall:5.2f}s mt={m.get('mt_ms')}ms rev={m.get('revision')} | {tr}")
                else:
                    print(f"  [{t}] {json.dumps(m, ensure_ascii=False)[:160]}")

        task = asyncio.create_task(reader())
        start = time.time()
        for i in range(0, len(audio), frame):
            chunk = audio[i:i + frame]
            pcm = (np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes()
            await ws.send(pcm)
            target = start + (i + len(chunk)) / 16000
            now = time.time()
            if target > now:
                await asyncio.sleep(target - now)
        await ws.send(json.dumps({"type": "stop"}))
        await asyncio.sleep(2.5)
        task.cancel()
        print(f"done: {results['partials']} partials, total wall {time.time()-start:.2f}s")


asyncio.run(main())
