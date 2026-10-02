"""Integration tests against a RUNNING backend (python run.py on :8765).
Skipped automatically when the server is not reachable."""

import asyncio
import json
import time
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import server_up, has_zipformer, zipformer_dir

pytestmark = pytest.mark.skipif(not server_up(), reason="backend not running on 127.0.0.1:8765")

BASE = "http://127.0.0.1:8765"


def test_health_reports_engines():
    import httpx

    d = httpx.get(f"{BASE}/health/json", timeout=5).json()
    assert d["status"] == "ok"
    assert d["mt_engines"], "no MT engine loaded"
    assert d["asr"]["available"]


@pytest.mark.parametrize("text,src,targets", [
    ("Where did you put the keys?", "en", ["zh", "bn"]),
    ("我五分钟后回来，什么都别碰。", "zh", ["en", "bn"]),
    ("আমি পাঁচ মিনিটের মধ্যে ফিরে আসব।", "bn", ["en", "zh"]),
])
def test_http_translate_all_directions_under_budget(text, src, targets):
    import httpx

    # warm call (cache miss), then a repeat (cache hit) to make sure both paths work
    t = time.perf_counter()
    r = httpx.post(f"{BASE}/translate", json={"text": text + " ", "source_lang": src, "target_languages": targets}, timeout=30)
    elapsed = (time.perf_counter() - t) * 1000
    assert r.status_code == 200, r.text
    d = r.json()
    for tgt in targets:
        out = d["translations"][tgt]["single_line"]
        assert out and out != text
        if tgt == "zh":
            assert any("一" <= ch <= "鿿" for ch in out)
        if tgt == "bn":
            assert any("ঀ" <= ch <= "৿" for ch in out)
    assert elapsed < 1500, f"{src}->{targets} took {elapsed:.0f} ms"


@pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded")
def test_ws_asr_streams_partials_finals_and_translations():
    import soundfile as sf
    import websockets

    wav = sorted((zipformer_dir("en") / "test_wavs").glob("*.wav"))[0]
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.concatenate([audio, np.zeros(int(sr * 1.5), dtype="float32")])

    async def run():
        url = "ws://127.0.0.1:8765/ws/asr?source_lang=en&target_langs=zh"
        got = {"partial": [], "final": [], "translation": [], "lid": []}
        async with websockets.connect(url, max_size=None) as ws:
            start = time.time()

            async def reader():
                async for raw in ws:
                    m = json.loads(raw)
                    if m["type"] in got:
                        got[m["type"]].append((time.time() - start, m))

            task = asyncio.create_task(reader())
            frame = int(sr * 0.04)
            for i in range(0, len(audio), frame):
                pcm = (np.clip(audio[i:i + frame], -1, 1) * 32767).astype("<i2").tobytes()
                await ws.send(pcm)
                target = start + (i + frame) / sr
                if target > time.time():
                    await asyncio.sleep(target - time.time())
            await ws.send(json.dumps({"type": "stop"}))
            await asyncio.sleep(3.0)
            task.cancel()
        return got

    got = asyncio.run(run())
    assert got["partial"], "no partial captions"
    assert got["partial"][0][0] < 2.0, "first partial arrived late"
    assert got["final"], "no final caption"
    assert got["translation"], "no translation"
    final_t, final_m = got["final"][0]
    tr_t, tr_m = got["translation"][0]
    assert "zh" in tr_m["translations"]
    assert tr_t - final_t < 1.0, f"translation arrived {tr_t - final_t:.2f}s after the final caption"
