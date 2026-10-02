"""
Incremental translation of a line that is still open.

stable_prefix()   the part of the open line that has stopped changing: its leading
                  words that were the same in the last N partial results.
DraftScheduler    re-translates that prefix, the newest one only, at most once per
                  debounce interval, and never after the line's final arrived.

The draft is shown above the growing source text and replaced by the final
translation at the endpoint (main.py: /ws/asr `draft` messages).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Hashable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿" or "぀" <= ch <= "ヿ"


def _units(text: str) -> List[Tuple[str, int]]:
    """Words of a caption with the index where each ends: space-separated runs, and
    every CJK character on its own (Chinese is written without spaces)."""
    out: List[Tuple[str, int]] = []
    start = None
    for i, ch in enumerate(text):
        if ch.isspace() or _is_cjk(ch):
            if start is not None:
                out.append((text[start:i], i))
                start = None
            if not ch.isspace():
                out.append((ch, i + 1))
        elif start is None:
            start = i
    if start is not None:
        out.append((text[start:], len(text)))
    return out


def stable_prefix(history: List[str], n: int) -> str:
    """The leading words of the newest partial that are unchanged in the last `n`
    partials (`history`, oldest first). Fewer than n partials so far, or n < 1: ''.
    A word still being written (the recognizer appends pieces to it) differs from
    partial to partial and so is never part of the prefix."""
    if n < 1 or len(history) < n:
        return ""
    recent = [_units(t) for t in history[-n:]]
    newest = recent[-1]
    k = 0
    while k < min(len(u) for u in recent) and all(u[k][0] == newest[k][0] for u in recent):
        k += 1
    if n == 1:
        k = len(newest)
    return history[-1][:newest[k - 1][1]].strip() if k else ""


class DraftScheduler:
    """Hands the newest stable prefix of an open line to `translate(key, text, msg)`,
    one call at a time and at most one start per `debounce_s`. `key` names the line
    (its utterance id); close(key) at the line's final stops its drafts, including one
    that is waiting; a draft already being translated checks is_open(key) before it
    is sent. Exceptions of `translate` are logged and swallowed: a draft is optional."""

    def __init__(self, translate: Callable[[Hashable, str, Dict[str, Any]], Awaitable[None]], debounce_s: float):
        self._translate = translate
        self._debounce = max(0.0, debounce_s)
        self._pending: Optional[Tuple[Hashable, str, Dict[str, Any]]] = None
        self._last_text: Dict[Hashable, str] = {}
        self._closed: List[Hashable] = []
        self._last_start = float("-inf")
        self._task: Optional[asyncio.Task] = None
        self.started = 0  # translations started (for the log)

    def is_open(self, key: Hashable) -> bool:
        return key not in self._closed

    def offer(self, key: Hashable, text: str, msg: Dict[str, Any]) -> None:
        if not text or not self.is_open(key) or self._last_text.get(key) == text:
            return
        self._pending = (key, text, msg)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def close(self, key: Hashable) -> None:
        self._closed.append(key)
        del self._closed[:-200]
        self._last_text.pop(key, None)
        if self._pending is not None and self._pending[0] == key:
            self._pending = None

    def reset(self) -> None:
        """The recognizer was replaced: its lines are gone and the ids start again."""
        self._pending = None
        self._last_text.clear()
        self._closed.clear()

    def cancel(self) -> None:
        self._pending = None
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while self._pending is not None:
            wait = self._last_start + self._debounce - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            item, self._pending = self._pending, None
            if item is None:
                return
            key, text, msg = item
            if not self.is_open(key) or self._last_text.get(key) == text:
                continue
            self._last_text[key] = text
            self._last_start = loop.time()
            self.started += 1
            try:
                await self._translate(key, text, msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # a draft is optional; the final translation does not depend on it
                logger.debug("draft translation failed: %s", e)
