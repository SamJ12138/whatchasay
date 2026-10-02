"""Does the translator need punctuation and casing on ASR text? (Phase 3 Batch A)

For each sample WAV: run the real streaming Zipformer exactly as /ws/asr does (40 ms
frames, endpointing), take each final as it reaches the translator (TextNormalizer
applied), and translate it with OPUS-MT next to (a) a hand-written cased and
punctuated version of the same words and (b) for English, the punctuation/casing
model's output. Prints Markdown (the body of docs/asr-input-quality.md) and the
model's latency.

    venv\\Scripts\\python scripts\\asr_input_quality.py [--models-root DIR] [--zh-model large|bilingual]

Reads models only (ASR from <models-root>/asr, punctuation from <models-root>/punct,
OPUS-MT from settings.translation.ct2_dir); downloads nothing.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# Hand-written casing and punctuation of the recognizer's own words (nothing else
# changed). English: the punctuation of the source text (Hawthorne, The Scarlet Letter).
REFERENCE = {
    "AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE AND THERE THE SQUALID QUARTER OF THE BROTHELS":
        "After early nightfall the yellow lamps would light up, here and there, the squalid quarter of the brothels.",
    "GOD AS A DIRECT CONSEQUENCE OF THE SIN WHICH MAN THUS PUNISHED HAD GIVEN HER A LOVELY CHILD WHOSE PLACE WAS ON THAT SAME DISHONORED BOSOM TO CONNECT HER":
        "God, as a direct consequence of the sin which man thus punished, had given her a lovely child, whose place was on that same dishonored bosom, to connect her",
    "PARENT FOR EVER WITH THE RACE AND DESCENT OF MORTALS AND TO BE FINALLY A BLESSED SOUL IN HEAVEN":
        "parent for ever with the race and descent of mortals, and to be finally a blessed soul in heaven.",
    "对我做了介绍啊那么我想说的是呢大家如果对我的研究感兴趣呢": "对我做了介绍啊。那么我想说的是呢，大家如果对我的研究感兴趣呢，",
    "重点呢想谈三个问题": "重点呢，想谈三个问题。",
    "首先呢就是这一轮全球金融动荡的表现": "首先呢，就是这一轮全球金融动荡的表现。",
    "এদিকে মিঃ তুগলক অরবিন কেজরিওয়াল এবং তার দল আপনায় ঘণ্টায় টুইট করছেন ক্রেডিটের জন্য":
        "এদিকে, মিঃ তুগলক অরবিন কেজরিওয়াল এবং তার দল আপনায় ঘণ্টায় টুইট করছেন ক্রেডিটের জন্য।",
    "তোমার দেশ তোমার জন্য কি করতে পারে তা জিজ্ঞেস করো না বরং তুমি তোমার দেশের জন্য কি করতে পারো তা জিজ্ঞেস করুম":
        "তোমার দেশ তোমার জন্য কি করতে পারে তা জিজ্ঞেস করো না, বরং তুমি তোমার দেশের জন্য কি করতে পারো তা জিজ্ঞেস করুম।",
}
TARGETS = {"en": ["zh", "bn"], "zh": ["en", "bn"], "bn": ["en", "zh"]}


def finals_for(wav: Path, lang: str, engine) -> list:
    import numpy as np
    import soundfile as sf

    from app.asr.session import SessionConfig, StreamingASRSession

    a, sr = sf.read(str(wav), dtype="float32")
    if a.ndim > 1:
        a = a.mean(axis=1)
    a = np.concatenate([a, np.zeros(int(sr * 1.5), dtype="float32")])
    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
    s = StreamingASRSession(SessionConfig(source_lang=lang), {"sherpa-zipformer": engine})  # no restorer: raw
    out = []
    for i in range(0, len(pcm), 1280):  # 40 ms at 16 kHz
        out += [m["text"] for m in s.feed(pcm[i:i + 1280]) if m["type"] == "final"]
    out += [m["text"] for m in s.flush() if m["type"] == "final"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models-root", type=Path, default=BACKEND / "data" / "models")
    ap.add_argument("--zh-model", default="large", choices=("large", "bilingual"),
                    help="Mandarin model (large = the default before Phase 3)")
    args = ap.parse_args()
    os.chdir(BACKEND)
    from app.asr.punctuation import Punctuator
    from app.asr.sherpa_engine import DEFAULT_MODELS, MANDARIN_MODELS, SherpaZipformerEngine
    from app.config import settings
    from app.translation.base_translator import OpusCT2Engine
    from app.translation.text_normalizer import TextNormalizer

    asr_root = args.models_root / "asr"
    models = {**DEFAULT_MODELS, "zh": MANDARIN_MODELS[args.zh_model]}
    a = settings.asr
    engine = SherpaZipformerEngine(asr_root, models=models, num_threads=a.num_threads,
                                   rule1_min_trailing_silence=a.rule1_min_trailing_silence,
                                   rule2_min_trailing_silence=a.rule2_min_trailing_silence,
                                   rule3_min_utterance_length=a.rule3_min_utterance_length)
    mt = OpusCT2Engine(settings.translation.ct2_dir)
    punct = Punctuator(args.models_root / "punct")
    norm = TextNormalizer()

    print("| WAV | input | text given to OPUS-MT | translations |")
    print("|---|---|---|---|")
    for lang in ("en", "zh", "bn"):
        for wav_name in ("0.wav", "1.wav"):
            # Mandarin: the same two WAVs for either model (the large model's samples)
            d = MANDARIN_MODELS["large"].name if lang == "zh" else models[lang].name
            wav = asr_root / d / "test_wavs" / wav_name
            for raw in finals_for(wav, lang, engine):
                raw = norm.normalize(raw).text
                rows = [("raw ASR", raw), ("hand-restored", REFERENCE.get(raw, ""))]
                if punct.supports(lang):
                    rows.append(("model-restored", punct.restore(raw, lang, log=False)))
                for kind, text in rows:
                    if not text:
                        text, tr = "(no reference for this text)", ""
                    else:
                        tr = "<br>".join(f"{t}: {mt.translate_batch_sync([text], lang, t)[0]}" for t in TARGETS[lang])
                    print(f"| {lang}/{wav_name} | {kind} | {text} | {tr} |")

    ms = []
    for raw in [k for k in REFERENCE if k.isupper()] + ["WHERE DID YOU PUT THE KEYS", "I WILL BE BACK IN FIVE MINUTES PLEASE DO NOT TOUCH ANYTHING"]:
        for _ in range(20):
            t0 = time.perf_counter()
            punct.restore(raw, "en", log=False)
            ms.append((time.perf_counter() - t0) * 1000)
    ms.sort()
    print(f"\npunctuation model {punct.dir.name}: p50 {statistics.median(ms):.2f} ms, "
          f"p95 {ms[int(0.95 * (len(ms) - 1))]:.2f} ms over {len(ms)} calls (1 CPU thread)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
