"""Sample sentences and output checks for OPUS-MT on the six en/zh/bn directions
(Phase 2b, D1). Used by tests/test_opus_quality.py (slow, real models) and
scripts/opus_quality.py (before/after numbers).

The sentences are the ones already in the repo: the /translate probes
(test_app_inprocess), the caption harness's WebVTT lines, the English test-wav
transcripts that ship with the Zipformer model (ASR style: upper case, no
punctuation), and the zh/bn lines of the line-breaker and cache tests.
"""

from __future__ import annotations

import unicodedata
from typing import Dict, List, Tuple

SAMPLES: Dict[str, List[str]] = {
    "en": [
        "Where did you put the keys?",
        "I will be back in five minutes.",
        "Please do not touch anything.",
        "AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE AND THERE THE SQUALID QUARTER OF THE BROTHELS",
        "YET THESE THOUGHTS AFFECTED HESTER PRYNNE LESS WITH HOPE THAN APPREHENSION",
    ],
    "zh": [
        "我五分钟后回来，什么都别碰。",
        "钥匙放哪儿了？",
        "我五分钟后回来，什么都别碰，我们等一下再说这件事情好吗",
    ],
    "bn": [
        "আমি পাঁচ মিনিটের মধ্যে ফিরে আসব।",
        "আমি পাঁচ মিনিটের মধ্যে ফিরে আসব। কিছু ছুঁয়ো না।",
        "আমি ফিরে আসব",
    ],
}

DIRECTIONS: List[Tuple[str, str]] = [("en", "zh"), ("en", "bn"), ("zh", "en"), ("zh", "bn"), ("bn", "en"), ("bn", "zh")]

# scripts written without spaces between words: length and n-grams count characters
_CHAR_LANGS = {"zh", "ja"}


def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch).startswith(("P", "S"))


def units(text: str, lang: str) -> List[str]:
    """Length units: characters for Chinese/Japanese, whitespace-separated words
    otherwise (not a \\w regex: Bengali vowel signs are combining marks and would
    split words); punctuation and spaces never count."""
    if lang in _CHAR_LANGS:
        return [ch for ch in text if not ch.isspace() and not _is_punct(ch)]
    words = ("".join(ch for ch in w if not _is_punct(ch)).lower() for w in text.split())
    return [w for w in words if w]


def repeated_ngrams(text: str, lang: str, n: int = 3) -> List[Tuple[str, ...]]:
    """n-grams (of units) that occur more than once in one output."""
    u = units(text, lang)
    seen, rep = set(), []
    for i in range(len(u) - n + 1):
        g = tuple(u[i:i + n])
        if g in seen and g not in rep:
            rep.append(g)
        seen.add(g)
    return rep


def length_ratio(source: str, src: str, output: str, tgt: str) -> float:
    return len(units(output, tgt)) / max(1, len(units(source, src)))


def problems(source: str, src: str, output: str, tgt: str, max_ratio: float = 3.0) -> List[str]:
    """Empty when the output passes: no repeated 3-gram, length within max_ratio x the source."""
    out = []
    rep = repeated_ngrams(output, tgt)
    if rep:
        out.append(f"repeated 3-gram(s) {rep[:3]}")
    r = length_ratio(source, src, output, tgt)
    if r > max_ratio:
        out.append(f"length ratio {r:.1f} > {max_ratio}")
    if not output.strip():
        out.append("empty output")
    return out
