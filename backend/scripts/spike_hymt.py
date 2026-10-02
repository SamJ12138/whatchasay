"""Spike: load HY-MT1.5-1.8B GGUF on the GPU and time short-line translations.

Run:  venv/Scripts/python.exe scripts/spike_hymt.py
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.translation import cuda_dlls  # noqa: E402  (adds torch/lib to DLL search path)

cuda_dlls.prepare()

from llama_cpp import Llama  # noqa: E402
from app.translation.hymt_translator import build_prompt  # noqa: E402

gguf = ROOT / "data/models/mt/HY-MT1.5-1.8B-Q8_0.gguf"
t0 = time.time()
llm = Llama(model_path=str(gguf), n_gpu_layers=-1, n_ctx=1024, n_batch=512, n_threads=4, flash_attn=True, verbose=False)
print(f"load: {time.time()-t0:.1f}s")

TESTS = [
    ("en", "zh", "I'll be back in five minutes, don't touch anything."),
    ("en", "bn", "I'll be back in five minutes, don't touch anything."),
    ("zh", "en", "我五分钟后回来，什么都别碰。"),
    ("zh", "bn", "我五分钟后回来，什么都别碰。"),
    ("bn", "en", "আমি পাঁচ মিনিটের মধ্যে ফিরে আসব, কিছু ছুঁয়ো না।"),
    ("bn", "zh", "আমি পাঁচ মিনিটের মধ্যে ফিরে আসব, কিছু ছুঁয়ো না।"),
    ("en", "zh", "Where did you put the keys?"),
    ("en", "bn", "Where did you put the keys?"),
]


def run(src, tgt, text):
    prompt = build_prompt(text, src, tgt)
    t = time.time()
    out = llm.create_chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=96, temperature=0.0, top_p=1.0, repeat_penalty=1.05,
    )
    ms = (time.time() - t) * 1000
    res = out["choices"][0]["message"]["content"].strip()
    toks = out["usage"]["completion_tokens"]
    return ms, toks, res


# warmup
run("en", "zh", "Hello")
for src, tgt, text in TESTS:
    ms, toks, res = run(src, tgt, text)
    print(f"{src}->{tgt} {ms:6.0f} ms {toks:3d} tok | {res}")
