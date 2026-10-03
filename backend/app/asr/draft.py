"""
Incremental translation of a line that is still open.

stable_prefix()   the part of the open line that has stopped changing: its leading
                  words that were the same in the last N partial results.
DraftScheduler    re-translates that prefix, the newest one only, at most once per
                  debounce interval, and never after the line's final arrived.
draft_long_enough() whether a stable prefix is worth a draft at all: at least a few
                  words (CJK: characters) or a good part of the line heard so far, so
                  a line's first draft is not one word.
LocalAgreement    what of a draft translation is shown: the longest common prefix of
                  the line's last K draft translations (local agreement), so a draft
                  that rewrites the previous one does not reach the screen until the
                  translations agree on how the sentence starts.

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


def draft_long_enough(stable: str, text: str, min_words: int = 3, min_cjk: int = 4, min_fraction: float = 0.4) -> bool:
    """A stable prefix is worth a draft once it has `min_words` words (`min_cjk` characters
    when it is CJK) or `min_fraction` of the words of the line heard so far (`text`, the
    newest partial), whichever comes first. The first drafts of a line used to be one word
    ("from", "Key"), on screen for a second before the next draft replaced them."""
    units = _units(stable or "")
    if not units:
        return False
    n, total = len(units), len(_units(text or ""))
    cjk = all(len(w) == 1 and _is_cjk(w) for w, _ in units)
    if n >= (min_cjk if cjk else min_words):
        return True
    return total > 0 and n >= min_fraction * total


def word_count(text: str) -> int:
    """Words as the agreement counts them: space-separated runs, every CJK character one."""
    return len(_units(text or ""))


_TRAILING_PUNCT = ".,;:!?…。，！？、"


def agreed_prefix(texts: List[str]) -> str:
    """The leading words (CJK characters) that all `texts` share, cut from the newest
    (the last) so its spacing is kept; '' when they share none. Punctuation glued to a
    word ("like?" against "like") does not break the agreement, and a cut prefix does
    not keep the punctuation the newest text put after its last agreed word."""
    if not texts:
        return ""
    units = [_units(t) for t in texts]
    newest = units[-1]
    core = lambda w: w.rstrip(_TRAILING_PUNCT) or w  # noqa: E731
    k = 0
    while k < min(len(u) for u in units) and all(core(u[k][0]) == core(newest[k][0]) for u in units):
        k += 1
    if not k:
        return ""
    if k == len(newest):
        return texts[-1].strip()
    return texts[-1][:newest[k - 1][1]].strip().rstrip(_TRAILING_PUNCT).strip()


class LocalAgreement:
    """Local agreement on a line's draft translations: push(key, text) returns what to
    show, the longest common prefix of the line's last `k` draft translations, or None
    when the display does not change (the same prefix as before, or nothing in common:
    what is shown stays until the translations agree again, or the final replaces it).
    The first draft of a line is shown as it is: waiting for a second one would cost a
    whole debounce interval. k < 2: no agreement, every draft as it is. The shown text is
    append-only except when the agreed prefix itself shrinks (the last drafts agree on
    less than the ones before)."""

    def __init__(self, k: int):
        self.k = max(1, int(k))
        self._history: Dict[Hashable, List[str]] = {}
        self._shown: Dict[Hashable, str] = {}

    def push(self, key: Hashable, text: str) -> Optional[str]:
        if self.k < 2:
            return text
        h = self._history.setdefault(key, [])
        h.append(text)
        del h[:-self.k]
        agreed = agreed_prefix(h)
        if not agreed or agreed == self._shown.get(key):
            return None
        self._shown[key] = agreed
        return agreed

    def shown(self, key: Hashable) -> Optional[str]:
        return self._shown.get(key)

    def close(self, key: Hashable) -> None:
        self._history.pop(key, None)
        self._shown.pop(key, None)
        if len(self._history) > 200:   # lines that never closed (a replaced recognizer's)
            for old in list(self._history)[:-200]:
                self.close(old)

    def reset(self) -> None:
        self._history.clear()
        self._shown.clear()


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
