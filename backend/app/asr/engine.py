"""
Streaming ASR engine interface.

Every engine (today: the local sherpa-onnx Zipformer) implements the same tiny
protocol so the session layer and the WebSocket endpoint never care which one
is running.

Audio contract: 16 kHz, mono, signed 16-bit PCM little-endian, arbitrary
chunk sizes (the extension sends ~40 ms frames).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable
import time


SAMPLE_RATE = 16000


@dataclass
class AsrEvent:
    """One recognizer output.

    kind:
        'partial' - current (unstable) hypothesis for the utterance in progress
        'final'   - utterance finished (endpoint detected); text is stable
    t0/t1 are audio-clock seconds since the session started.
    """

    kind: str
    text: str
    lang: str
    t0: float
    t1: float
    confidence: float = 1.0
    utterance_id: int = 0
    # Wall-clock timestamp (time.time()) when the event was produced, so the
    # extension can measure end-to-end latency against its capture clock.
    produced_at: float = field(default_factory=time.time)


@runtime_checkable
class AsrSession(Protocol):
    """A single recognition stream (one per WebSocket connection)."""

    lang: str

    def feed(self, pcm16: bytes) -> List[AsrEvent]:
        """Consume audio, return zero or more events."""
        ...

    def flush(self) -> List[AsrEvent]:
        """Force-finalize whatever is buffered (e.g. on stop)."""
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class StreamingASREngine(Protocol):
    """Factory for sessions. Engines may cache heavy models internally."""

    name: str

    def supports(self, lang: str) -> bool:
        ...

    def start_session(self, lang: str) -> AsrSession:
        ...

    def status(self) -> dict:
        ...


def pcm16_to_float32(pcm16: bytes):
    """bytes (int16 LE) -> numpy float32 in [-1, 1]."""
    import numpy as np

    if len(pcm16) % 2:
        pcm16 = pcm16[:-1]
    return np.frombuffer(pcm16, dtype="<i2").astype("float32") / 32768.0
