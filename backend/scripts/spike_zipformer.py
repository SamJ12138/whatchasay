"""Spike: stream a WAV through the Zipformer engine at real-time pace and
print when partials/finals arrive relative to the audio clock.

Run:  venv/Scripts/python.exe scripts/spike_zipformer.py [lang] [wav]
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)

import numpy as np
import soundfile as sf

from app.asr.sherpa_engine import SherpaZipformerEngine, DEFAULT_MODELS

lang = sys.argv[1] if len(sys.argv) > 1 else "en"
models_root = ROOT / "data/models/asr"
if len(sys.argv) > 2:
    wav = Path(sys.argv[2])
else:
    mdir = models_root / DEFAULT_MODELS[lang].name
    wavs = sorted((mdir / "test_wavs").glob("*.wav")) if (mdir / "test_wavs").exists() else sorted(mdir.glob("*.wav"))
    wav = wavs[0]
print("wav:", wav)

audio, sr = sf.read(str(wav), dtype="float32")
if audio.ndim > 1:
    audio = audio.mean(axis=1)
if sr != 16000:
    import math
    n = int(len(audio) * 16000 / sr)
    audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype("float32")
    sr = 16000

eng = SherpaZipformerEngine(models_root, num_threads=2)
t0 = time.time()
sess = eng.start_session(lang)
print(f"load: {time.time()-t0:.2f}s, audio {len(audio)/sr:.1f}s")

frame = int(sr * 0.04)  # 40 ms like the extension
start = time.time()
realtime = "--fast" not in sys.argv
last_len = 0
for i in range(0, len(audio), frame):
    chunk = audio[i:i + frame]
    pcm = (np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes()
    audio_clock = (i + len(chunk)) / sr
    if realtime:
        target = start + audio_clock
        now = time.time()
        if target > now:
            time.sleep(target - now)
    t = time.time()
    events = sess.feed(pcm)
    proc_ms = (time.time() - t) * 1000
    for ev in events:
        wall = time.time() - start
        print(f"[{ev.kind:7s}] audio={audio_clock:5.2f}s wall={wall:5.2f}s lag={wall-audio_clock:+.2f}s feed={proc_ms:4.1f}ms | {ev.text}")
for ev in sess.flush():
    print(f"[flush  ] {ev.text}")
print(f"total wall {time.time()-start:.2f}s for {len(audio)/sr:.2f}s audio")
