"""The term glossary (observations T13, T14).

A term has a canonical spelling in the source language, zero or more "heard as" spellings
(what the recognizer tends to write for it) and a rendering per target language. Two
jobs, both pure:

correct   On recognised text, before display and MT: a heard-as spelling becomes the
          canonical one. Exact spellings first (whole words; phrases for multi-word
          spellings; substrings for CJK), then a fuzzy pass within a small edit distance
          for the script (one edit for a spelling of up to 4 code points, two up to 8,
          three beyond; the first code point must agree, and a word under 4 code points
          is never matched fuzzily, so short common words are left alone).

protect / restore   Through MT the term travels as a placeholder the engine copies, and
          the rendering (or, without one for that target, the canonical spelling) is put
          back afterwards. OPUS-MT keeps no placeholder in every direction: measured on
          the real models (docs/observations.md T13), the best token per direction
          survives in 8 of 8 test sentences, but the same token is lost in another
          direction (e.g. "X1X" 8/8 bn->en, 0/8 en->bn; "[1]" 8/8 en->zh, 6/8 bn->en).
          So the placeholders are a ranked list per direction, the result is checked
          (each placeholder present exactly once) and a sentence whose placeholder was
          lost is re-translated with the next one, up to max_attempts (default 3: with the
          direction's top three every one of the 48 measured sentences was protected).
          After that the sentence is translated with its canonical spelling, unprotected,
          and the log says so.

GlossaryStore holds the terms the translation memory has, per source language, and is
reloaded on every change, so a running live session picks a new term up on its next line.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .. import obs

# Placeholder templates per (source, target); "{n}" is the term's index in the sentence.
# Order: measured survival on the real OPUS-MT models, 8 sentences per direction
# (scratch probe, 2026-10-02; the numbers are in docs/observations.md T13). "%s" has no
# index and is only used for a sentence with one term.
PLACEHOLDERS: Dict[Tuple[str, str], List[str]] = {
    ("bn", "en"): ["X{n}X", "{{n}}", "[{n}]"],       # 8/8, 8/8, 6/8
    ("en", "zh"): ["[{n}]", "KX{n}", "X{n}X"],         # 8/8, 8/8, 6/8
    ("zh", "en"): ["KX{n}", "N{n}N", "X{n}X"],         # 8/8, 8/8, 6/8
    ("en", "bn"): ["{X{n}}", "<X{n}>", "<{n}>"],     # 8/8, 8/8, 7/8
    ("bn", "zh"): ["KX{n}", "XY{n}Z", "[{n}]"],        # 8/8, 8/8, 7/8 (through English)
    ("zh", "bn"): ["%s", "[X{n}]", "X{n}X"],           # 7/8, 6/8; top three cover 8/8 (through English)
}
DEFAULT_PLACEHOLDERS = ["[X{n}]", "[{n}]", "XX{n}"]   # best three over all six directions: 48/48 with retry
DEFAULT_MAX_ATTEMPTS = 3


def placeholders(source_lang: str, target_lang: str) -> List[str]:
    return list(PLACEHOLDERS.get((source_lang, target_lang), DEFAULT_PLACEHOLDERS))


def _fill(template: str, n: int) -> str:
    return template if template == "%s" else template.replace("{n}", str(n))


@dataclass
class Term:
    source_lang: str
    canonical: str
    heard_as: List[str] = field(default_factory=list)
    renderings: Dict[str, str] = field(default_factory=dict)
    id: Optional[int] = None

    def rendering(self, target_lang: str) -> str:
        return self.renderings.get(target_lang) or self.canonical


# ---------------------------------------------------------------- text helpers

_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿ｦ-ﾟ]")


def is_cjk(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if _CJK.match(c)) * 2 >= len(letters)


def _is_separator(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def tokenize(text: str) -> List[Tuple[str, bool]]:
    """(piece, is_word) runs; separators (whitespace, punctuation) are kept as pieces so
    the text can be rebuilt. Combining marks (Bengali vowel signs) stay in their word."""
    out: List[Tuple[str, bool]] = []
    buf, word = "", None
    for ch in text:
        w = not _is_separator(ch)
        if word is None or w == word:
            buf += ch
            word = w
        else:
            out.append((buf, word))
            buf, word = ch, w
    if buf:
        out.append((buf, word))
    return out


def edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance on code points, stopping early past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            cur.append(v)
            best = min(best, v)
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def max_edits(spelling: str) -> int:
    n = len(spelling)
    return 1 if n <= 4 else 2 if n <= 8 else 3


def _fold(s: str) -> str:
    return s.casefold()


# Bengali: an independent vowel at the start of a word is what is left of a consonant +
# vowel sign when the recognizer drops the consonant ("আজ" for "রাজ"), so for the fuzzy
# comparison the independent vowels are folded to their sign form (one edit, not two).
_BN_INDEPENDENT_TO_SIGN = str.maketrans({
    "আ": "া", "ই": "ি", "ঈ": "ী", "উ": "ু", "ঊ": "ূ",
    "এ": "ে", "ঐ": "ৈ", "ও": "ো", "ঔ": "ৌ",
})


def _script_fold(s: str) -> str:
    return s.translate(_BN_INDEPENDENT_TO_SIGN)


# ---------------------------------------------------------------- the glossary of one language


class Glossary:
    """The terms of one source language, compiled for matching."""

    MIN_FUZZY_LEN = 4   # a word shorter than this is never matched fuzzily

    def __init__(self, terms: Iterable[Term]):
        self.terms: List[Term] = [t for t in terms if t.canonical and t.canonical.strip()]
        # (spelling tokens as words, term) for exact matching; the canonical spelling is a
        # spelling too (it protects itself and must never be "corrected")
        self._phrases: List[Tuple[Tuple[str, ...], Term, str]] = []
        self._cjk = any(is_cjk(t.canonical) for t in self.terms)
        for t in self.terms:
            for sp in [t.canonical, *t.heard_as]:
                words = tuple(_fold(w) for w, is_word in tokenize(sp) if is_word)
                if words:
                    self._phrases.append((words, t, sp))
        self._phrases.sort(key=lambda p: -len(p[0]))   # longest phrase first

    def __bool__(self) -> bool:
        return bool(self.terms)

    # -- correct ------------------------------------------------------------

    def correct(self, text: str) -> Tuple[str, List[Tuple[str, str, str]]]:
        """Replace heard-as spellings with the canonical one. Returns the text and the
        hits as (heard, canonical, "exact" | "fuzzy")."""
        if not self.terms or not text:
            return text, []
        if self._cjk:
            return self._correct_cjk(text)
        pieces = tokenize(text)
        words = [(i, _fold(p)) for i, (p, is_word) in enumerate(pieces) if is_word]
        hits: List[Tuple[str, str, str]] = []
        taken = set()
        out = list(pieces)
        # exact phrases, longest first
        for phrase, term, _ in self._phrases:
            L = len(phrase)
            for k in range(len(words) - L + 1):
                idx = [words[k + j][0] for j in range(L)]
                if any(i in taken for i in idx):
                    continue
                if tuple(words[k + j][1] for j in range(L)) != phrase:
                    continue
                taken.update(idx)
                heard = "".join(p for p, _ in pieces[idx[0]:idx[-1] + 1])
                if _fold(heard) != _fold(term.canonical):
                    out[idx[0]] = (term.canonical, True)
                    for i in idx[1:]:
                        out[i] = ("", True)
                    # swallow the separators between the phrase's words
                    for i in range(idx[0] + 1, idx[-1]):
                        if not pieces[i][1]:
                            out[i] = ("", False)
                    hits.append((heard, term.canonical, "exact"))
        # fuzzy: a word, or two adjacent words run together (the recognizer splits a word it
        # does not know: "আজ বুক" for রাজবুক), against the single-word spellings
        singles = [(phrase[0], term, sp) for phrase, term, sp in self._phrases if len(phrase) == 1]
        k = 0
        while k < len(words):
            i, w = words[k]
            if i in taken:
                k += 1
                continue
            candidates = []
            if len(w) >= self.MIN_FUZZY_LEN:
                candidates.append((w, [i]))
            if k + 1 < len(words) and words[k + 1][0] not in taken and words[k + 1][0] == i + 2 and not pieces[i + 1][1]                     and pieces[i + 1][0].isspace():
                joined = w + words[k + 1][1]
                if len(joined) >= self.MIN_FUZZY_LEN + 1:
                    candidates.append((joined, [i, i + 2]))
            best = None
            for cand, idx in candidates:
                for sp_fold, term, sp in singles:
                    d = self._fuzzy_distance(cand, sp_fold)
                    if d is not None and (best is None or d < best[0] or (d == best[0] and len(idx) > len(best[2]))):
                        best = (d, term, idx)
            if best is not None:
                _, term, idx = best
                heard = "".join(p for p, _ in pieces[idx[0]:idx[-1] + 1])
                if _fold(heard) != _fold(term.canonical):
                    taken.update(idx)
                    hits.append((heard, term.canonical, "fuzzy"))
                    out[idx[0]] = (term.canonical, True)
                    for j in range(idx[0] + 1, idx[-1] + 1):
                        out[j] = ("", pieces[j][1])
                    k += len(idx)
                    continue
            k += 1
        return "".join(p for p, _ in out), hits

    @staticmethod
    def _fuzzy_distance(word: str, spelling: str) -> Optional[int]:
        """Edit distance when `word` is a near spelling: within max_edits(spelling), and the
        first character agrees unless it is the one edit of a spelling of 5+ code points
        ("কাজবুক" for রাজবুক)."""
        lim = max_edits(spelling)
        w, sp = _script_fold(word), _script_fold(spelling)
        d = edit_distance(w, sp, lim)
        if d > lim:
            return None
        if w[0] != sp[0] and not (d <= 1 and len(sp) >= 5):
            return None
        return d

    def _correct_cjk(self, text: str) -> Tuple[str, List[Tuple[str, str, str]]]:
        hits: List[Tuple[str, str, str]] = []
        # exact spellings, longest first
        spellings = sorted(((sp, t) for t in self.terms for sp in [t.canonical, *t.heard_as] if sp), key=lambda x: -len(x[0]))
        protected: List[Tuple[int, int]] = []

        def free(a: int, b: int) -> bool:
            return all(b <= s or a >= e for s, e in protected)

        for sp, term in spellings:
            start = 0
            while True:
                k = text.find(sp, start)
                if k < 0:
                    break
                if free(k, k + len(sp)):
                    if sp != term.canonical:
                        text = text[:k] + term.canonical + text[k + len(sp):]
                        hits.append((sp, term.canonical, "exact"))
                        protected = [(s + len(term.canonical) - len(sp), e + len(term.canonical) - len(sp)) if s >= k else (s, e)
                                     for s, e in protected]
                    protected.append((k, k + len(term.canonical)))
                    start = k + len(term.canonical)
                else:
                    start = k + 1
        # fuzzy: windows of the spelling's length (+-1) within the edit limit
        for sp, term in spellings:
            L = len(sp)
            if L < self.MIN_FUZZY_LEN - 1:
                continue
            lim = max_edits(sp)
            k = 0
            while k < len(text):
                found = False
                for wl in (L, L - 1, L + 1):
                    if wl <= 0 or k + wl > len(text):
                        continue
                    win = text[k:k + wl]
                    if win[0] != sp[0] or not free(k, k + wl) or win == term.canonical:
                        continue
                    if edit_distance(win, sp, lim) <= lim:
                        text = text[:k] + term.canonical + text[k + wl:]
                        hits.append((win, term.canonical, "fuzzy"))
                        protected = [(s + len(term.canonical) - wl, e + len(term.canonical) - wl) if s >= k else (s, e)
                                     for s, e in protected]
                        protected.append((k, k + len(term.canonical)))
                        k += len(term.canonical)
                        found = True
                        break
                if not found:
                    k += 1
        return text, hits

    # -- protect / restore ------------------------------------------------------

    def protect(self, text: str, target_lang: str, template: str) -> Tuple[str, List[Tuple[str, str]]]:
        """Replace each term's canonical spelling with a placeholder; returns the text and
        the slots (placeholder, rendering) to restore. One placeholder per distinct term."""
        if not self.terms or not text:
            return text, []
        # spans of every term's canonical spelling, longest spelling first so a term inside
        # a longer one is not matched on its own; then numbered in reading order
        spans: List[Tuple[int, int, Term]] = []
        for term in sorted(self.terms, key=lambda t: -len(t.canonical)):
            for m in self._pattern(term.canonical).finditer(text):
                if all(m.end() <= s or m.start() >= e for s, e, _ in spans):
                    spans.append((m.start(), m.end(), term))
        if not spans:
            return text, []
        spans.sort()
        index: Dict[int, int] = {}
        slots: List[Tuple[str, str]] = []
        for _, _, term in spans:
            if id(term) not in index:
                if template == "%s" and slots:
                    return text, []   # one unnumbered placeholder cannot carry two terms
                index[id(term)] = len(slots) + 1
                slots.append((_fill(template, len(slots) + 1), term.rendering(target_lang)))
        out, last = [], 0
        for s, e, term in spans:
            out.append(text[last:s])
            out.append(slots[index[id(term)] - 1][0])
            last = e
        out.append(text[last:])
        return "".join(out), slots

    # a letter, digit or mark on either side means the spelling is part of a longer word
    # (\w leaves out combining marks such as Bengali vowel signs; the blocks add them)
    _WORD_CHARS = r"\wঀ-৿̀-ͯ"

    def _pattern(self, spelling: str) -> "re.Pattern[str]":
        esc = re.escape(spelling)
        if is_cjk(spelling):
            return re.compile(esc)
        return re.compile(rf"(?<![{self._WORD_CHARS}]){esc}(?![{self._WORD_CHARS}])", re.IGNORECASE)

    @staticmethod
    def restore(translated: str, slots: Sequence[Tuple[str, str]], target_lang: Optional[str] = None) -> Optional[str]:
        """Put the renderings back; None when a placeholder is missing or appears twice
        (the engine did not copy it), so the caller can retry or fall back. In English the
        article before the term follows the rendering, not the placeholder ("an X1X" ->
        "a rajbhog")."""
        out = translated
        for ph, rendering in slots:
            found = [m for m in re.finditer(re.escape(ph), out, re.IGNORECASE)]
            if len(found) != 1:
                return None
            m = found[0]
            before = out[:m.start()]
            if target_lang == "en" and rendering:
                art = re.search(r"(?i)\b(an?)(\s+)$", before)
                if art:
                    vowel = rendering[0].lower() in "aeiou"
                    want = ("an" if vowel else "a")
                    if art.group(1)[0].isupper():
                        want = want.capitalize()
                    before = before[:art.start(1)] + want + art.group(2)
            out = before + rendering + out[m.end():]
        return out


# ---------------------------------------------------------------- translate with retry


class ProtectRun:
    """One batch through MT with its terms protected, step by step so a sync and an async
    translator can drive it alike:

        run = ProtectRun(texts, src, tgt, glossary)
        while (b := run.next_batch()) is not None:
            run.accept(translate(b, src, tgt))      # a None result = that text failed; it is not retried
        run.outs, run.reports

    Texts without a term are translated once with everything else in the first batch. A
    text whose placeholder did not survive is re-translated with the next template of the
    direction, up to max_attempts; after that it is translated unprotected, as the user
    spelled it. reports[i] is {"terms": 0} for a text without a term, else terms, attempts,
    protected (bool) and the placeholder family that worked (None when none did)."""

    def __init__(self, texts: List[str], source_lang: str, target_lang: str, glossary: Optional[Glossary],
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self.texts = list(texts)
        self.src, self.tgt = source_lang, target_lang
        self.glossary = glossary
        self.templates = placeholders(source_lang, target_lang)
        self.attempts = max(1, min(max_attempts, len(self.templates)))
        self.outs: List[Optional[str]] = [None] * len(texts)
        self.reports: List[Dict[str, Any]] = [{"terms": 0} for _ in texts]
        self._pending = list(range(len(texts)))
        self._attempt = 0
        self.who: List[int] = []
        self._slots: Dict[int, List[Tuple[str, str]]] = {}
        self._plain = False
        self._done = False

    def next_batch(self) -> Optional[List[str]]:
        while not self._done:
            if self._attempt < self.attempts:
                tpl = self.templates[self._attempt]
                batch, who = [], []
                for i in self._pending:
                    protected, slots = self.glossary.protect(self.texts[i], self.tgt, tpl) if self.glossary else (self.texts[i], [])
                    if not slots:
                        if self._attempt == 0:        # no term: translated once, never retried
                            batch.append(self.texts[i])
                            who.append(i)
                            self._slots[i] = []
                        continue
                    self._slots[i] = slots
                    self.reports[i] = {"terms": len(slots), "attempts": self._attempt + 1, "protected": False, "placeholder": None}
                    batch.append(protected)
                    who.append(i)
                self._attempt += 1
                if batch:
                    self.who = who
                    return batch
                continue
            if self._pending and not self._plain:
                # every placeholder was lost: the sentence as the user spelled it, unprotected
                self._plain = True
                self.who = list(self._pending)
                self._slots = {i: [] for i in self._pending}
                return [self.texts[i] for i in self._pending]
            self._finish()
        return None

    def accept(self, results: Sequence[Optional[str]]) -> None:
        still: List[int] = []
        for i, out in zip(self.who, results):
            slots = self._slots.get(i) or []
            if out is None:
                continue                                # failed: the caller keeps its error
            if not slots:
                self.outs[i] = out
                continue
            restored = Glossary.restore(out, slots, self.tgt)
            if restored is None:
                still.append(i)
                continue
            self.outs[i] = restored
            self.reports[i]["protected"] = True
            self.reports[i]["placeholder"] = _fill(self.templates[self._attempt - 1], 1)   # the family that worked
        self._pending = still if not self._plain else []
        self.who = []
        if not self._pending:
            self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        for r in self.reports:
            if r.get("terms"):
                obs.log("glossary", "success" if r["protected"] else "skip", kind="protect", src=self.src, tgt=self.tgt,
                        terms=r["terms"], attempts=r["attempts"], protected=r["protected"], placeholder=r["placeholder"],
                        **({} if r["protected"] else {"error_type": "input_invalid",
                                                       "error_message": "no placeholder survived MT; translated unprotected",
                                                       "degraded": "term rendering lost in this line"}))


def translate_protected(translate: Callable[[List[str], str, str], List[str]], texts: List[str],
                        source_lang: str, target_lang: str, glossary: Optional[Glossary],
                        max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> Tuple[List[str], List[Dict[str, Any]]]:
    """ProtectRun driven by a plain batch call `translate(texts, src, tgt) -> texts`."""
    run = ProtectRun(texts, source_lang, target_lang, glossary, max_attempts)
    while (batch := run.next_batch()) is not None:
        run.accept(translate(batch, source_lang, target_lang))
    return [o if o is not None else "" for o in run.outs], run.reports


# ---------------------------------------------------------------- the store


class GlossaryStore:
    """The terms per source language, as the translation memory has them. `load(terms)`
    replaces everything; the TM calls it after every change."""

    def __init__(self) -> None:
        self._by_lang: Dict[str, Glossary] = {}
        self.version = 0

    def load(self, terms: Iterable[Term]) -> None:
        by: Dict[str, List[Term]] = {}
        for t in terms:
            by.setdefault(t.source_lang, []).append(t)
        self._by_lang = {lang: Glossary(ts) for lang, ts in by.items()}
        self.version += 1

    def for_lang(self, source_lang: Optional[str]) -> Optional[Glossary]:
        g = self._by_lang.get(source_lang or "")
        return g if g else None

    def correct(self, text: str, source_lang: Optional[str]) -> Tuple[str, List[Dict[str, str]]]:
        g = self.for_lang(source_lang)
        if g is None or not text:
            return text, []
        out, hits = g.correct(text)
        return out, [{"heard": h, "canonical": c, "how": how} for h, c, how in hits]

    def spellings(self) -> List[str]:
        """Every canonical and heard-as spelling (for forgetting stale machine rows)."""
        return [sp for g in self._by_lang.values() for t in g.terms for sp in [t.canonical, *t.heard_as]]


store = GlossaryStore()
