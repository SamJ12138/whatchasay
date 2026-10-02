"""Fakes shared by the fast suite: no model, no llama-server binary, no network.

FakeMTEngine        deterministic MT engine; mode = ok | fail | timeout | source | empty
make_translator     a BaseTranslator router over given engines (no real engine is built)
FakeASREngine       streaming ASR engine; sessions emit a partial per frame and a final
                    every `final_every` frames
FAKE_LLAMA_SERVER   path of tests/fake_llama_server.py (HTTP stub of llama-server)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.asr.engine import AsrEvent, SAMPLE_RATE

FAKE_LLAMA_SERVER = Path(__file__).with_name("fake_llama_server.py")

# Output marker per target language, in that language's script, so tests can
# check "the zh line is Chinese" without a model.
TARGET_MARK = {"zh": "中", "bn": "বা", "en": "EN", "ja": "日", "ko": "한"}


class FakeMTError(RuntimeError):
    pass


class FakeMTEngine:
    """MTEngine protocol. Records every call in `calls` as (texts, src, tgt)."""

    def __init__(self, name: str = "fake", mode: str = "ok", pairs: Optional[Iterable[Tuple[str, str]]] = None,
                 delay_s: float = 0.0, max_concurrency: int = 4):
        self.name = name
        self.mode = mode
        self.pairs = set(pairs) if pairs is not None else None
        self.delay_s = delay_s
        self.max_concurrency = max_concurrency
        self.calls: List[tuple] = []

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return self.pairs is None or (source_lang, target_lang) in self.pairs

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        self.calls.append((tuple(texts), source_lang, target_lang))
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.mode == "fail":
            raise FakeMTError(f"{self.name} failed on purpose")
        if self.mode == "timeout":
            import httpx

            raise httpx.ReadTimeout(f"{self.name} timed out on purpose")
        if self.mode == "source":
            return list(texts)
        if self.mode == "empty":
            return ["" for _ in texts]
        mark = TARGET_MARK.get(target_lang, target_lang)
        return [f"{mark}[{self.name}:{source_lang}->{target_lang}] {t}" for t in texts]

    def status(self) -> dict:
        return {"engine": self.name, "available": True, "mode": self.mode}


def make_translator(engines: Dict[str, object]):
    """A BaseTranslator whose engines are exactly `engines` (in that order)."""
    from app.translation.base_translator import BaseTranslator

    bt = BaseTranslator.__new__(BaseTranslator)
    bt.device = "cpu"
    bt.engines = dict(engines)
    bt._engine_locks = {}
    return bt


class FakeASRSession:
    def __init__(self, lang: str, final_every: int, text: str, numbered: bool = True):
        self.lang = lang
        self.final_every = final_every
        self.text = text
        self.numbered = numbered
        self.frames = 0
        self.samples = 0
        self.utt = 0
        self.closed = False

    def feed(self, pcm16: bytes) -> List[AsrEvent]:
        self.frames += 1
        self.samples += len(pcm16) // 2
        t1 = self.samples / SAMPLE_RATE
        if self.frames % self.final_every == 0:
            text = f"{self.text} {self.utt}" if self.numbered else self.text
            ev = AsrEvent("final", text, self.lang, 0.0, t1, utterance_id=self.utt)
            self.utt += 1
            return [ev]
        return [AsrEvent("partial", f"{self.text} {self.utt} ...", self.lang, 0.0, t1, utterance_id=self.utt)]

    def flush(self) -> List[AsrEvent]:
        return []

    def close(self) -> None:
        self.closed = True


class FakeASREngine:
    name = "sherpa-zipformer"

    def __init__(self, langs=("en", "zh", "bn"), final_every: int = 10, text: str = "hello world", numbered: bool = True):
        self.langs = set(langs)
        self.final_every = final_every
        self.text = text
        self.numbered = numbered  # False: every final has the same text
        self.sessions: List[FakeASRSession] = []

    def supports(self, lang: str) -> bool:
        return lang in self.langs

    def start_session(self, lang: str) -> FakeASRSession:
        s = FakeASRSession(lang, self.final_every, self.text, self.numbered)
        self.sessions.append(s)
        return s

    def status(self) -> dict:
        return {"engine": self.name, "languages": sorted(self.langs)}

    def warmup(self, langs) -> None:
        pass


def pcm_frame(ms: int = 40, amplitude: float = 0.3) -> bytes:
    import numpy as np

    n = int(SAMPLE_RATE * ms / 1000)
    return (np.sin(np.linspace(0, 200, n)) * amplitude * 32767).astype("<i2").tobytes()
