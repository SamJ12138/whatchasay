"""
StreamingASRSession: glue between raw audio frames from the extension and the
recognizer, with language auto-detection and partial/final caption emission.

One session per /ws/asr connection. It is engine-agnostic: the engine is
picked by name from the registry (sherpa-zipformer by default).

Language handling
-----------------
* explicit language  -> recognizer starts immediately.
* 'auto'             -> recognizer starts immediately with the most likely
                        language (first of allowed_langs) so captions appear
                        within ~0.4 s, while the first seconds of speech are
                        buffered for spoken-language-ID. If LID disagrees the
                        recognizer is swapped and the buffered audio replayed,
                        so nothing is lost. The UI gets a 'lid' message either
                        way and a 'reset' when the provisional captions must be
                        discarded.

                        When LID runs: first after lid_first_window_s (1.0 s)
                        of voiced audio, then every lid_retry_step_s (0.5 s)
                        of audio, voiced or not (speech is often followed by a
                        pause), as many times as steps fit before the full
                        window; then at the full window, lid_window_s (2.5 s)
                        of voiced audio, and once more at 1.6 x that. Before
                        the full window only a language other than the provisional one
                        is accepted, and only when two attempts in a row give
                        it: whisper-tiny answers "en" for noise and for the
                        first second of other languages, so the provisional
                        language is confirmed on the full window only. From the
                        full window on, any answer at or above the confidence
                        floor is accepted; no answer at the last attempt ->
                        fallback.

                        A language prior (config.prior, {lang: probability} from
                        the page and the extension's channel memory; docs/page-prior.md)
                        whose favourite reaches lid_prior_threshold makes that
                        language the first recognizer instead. Confirming it then
                        needs one attempt at or above the floor (English: at or
                        above lid_prior_floor_en, whisper-tiny's "en" on the first
                        second of Mandarin and Bengali reached 0.97); switching away
                        from it needs two early attempts in a row, as above, and
                        English only from the full window on (the same habit). No
                        prior, or one under the threshold: as above, unchanged.

                        Parallel recognizers: until the language is confirmed, every
                        frame also goes to the other languages' recognizers (at most
                        parallel_window_s of audio). Only the first recognizer's text
                        is sent. At confirmation the confirmed one's text is already
                        there: the same recognizer's open line is sent again,
                        confirmed (the overlay undims it); another recognizer takes
                        over with the finals it wrote so far and its open line, and
                        no audio is replayed or decoded twice; the others stop.
                        After the window only the first recognizer runs, and a later
                        switch replays the buffered audio as before.

Messages (JSON, sent by the WebSocket endpoint):
    {type:'partial', utterance_id, text, lang, lang_status, confirmed, t0, t1, w_first, w_last, w_session
                     [, stable_text]}   stable_text: the leading words of the open line that were the same
                     in the last `draft_stable_partials` partials (asr/draft.py), restored like `text`;
                     the endpoint translates it as a draft and does not send the field on
    {type:'final',   utterance_id, text, lang, lang_status, confirmed, t0, t1, w_first, w_last, w_session[, raw_text]}

Latency clock (docs/latency.md): w_first / w_last are the wall-clock times (epoch
seconds) at which the audio of the line's first and last recognised word reached this
session, w_session the arrival of the session's first frame. The content script
subtracts them from the moment it draws the text.

Text restoration: with a `restore_text(text, lang, final)` hook (asr/punctuation.py
for English) partial and final text is cased and punctuated before it is sent, so
the translator gets "Where did you put the keys" instead of "WHERE DID YOU PUT THE
KEYS". A final whose text changed also carries the recognizer's raw_text.
    {type:'lid',     lang, source:'auto'|'manual'|'provisional'|'fallback', status, confirmed}
    {type:'reset'}   discard provisional captions (language switched)

Language status (A1-A3), on every lid / partial / final:
    provisional  auto mode before LID has answered
    confirmed    LID identified the language (confirmed: true)
    manual       the language was declared by the user (confirmed: true)
    fallback     LID gave up (no answer inside the allowed languages, or no LID
                 model): the provisional language is kept, confirmed: false
    error        every LID attempt raised: provisional language kept,
                 confirmed: false, error_type / error on the lid message
confirmed is true only for confirmed and manual.
"""

from __future__ import annotations

import bisect
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from .draft import draft_long_enough, stable_prefix
from .engine import AsrEvent, AsrSession, SAMPLE_RATE, StreamingASREngine, pcm16_to_float32
from .. import obs

logger = logging.getLogger(__name__)

# whisper-tiny answers "en" for noise and for the first second of Mandarin and Bengali speech
# (docs/latency.md, docs/page-prior.md): with a prior, an early answer may not switch to it
LATE_ONLY_LANGS = ("en",)


def parse_prior(raw: Optional[str], allowed: List[str]) -> Dict[str, float]:
    """The prior from the /ws/asr query, "en:0.1,zh:0.8,bn:0.1": languages outside `allowed`
    and values that are not probabilities are dropped; a total over 1 (beyond rounding) drops it all."""
    out: Dict[str, float] = {}
    for part in (raw or "").split(","):
        lang, sep, value = part.partition(":")
        try:
            p = float(value)
        except ValueError:
            continue
        if sep and lang.strip() in allowed and 0.0 <= p <= 1.0:  # nan fails both comparisons
            out[lang.strip()] = p
    return out if sum(out.values()) <= 1.01 else {}  # the extension rounds each to 3 decimals


@dataclass
class SessionConfig:
    source_lang: str = "auto"  # 'auto' | 'en' | 'zh' | 'bn' | ...
    target_langs: List[str] = field(default_factory=lambda: ["en"])
    engine: str = "auto"  # 'auto' | 'sherpa-zipformer'
    allowed_langs: List[str] = field(default_factory=lambda: ["en", "zh", "bn"])
    lid_window_s: float = 2.5     # the full window: seconds of voiced audio at which any LID answer is accepted
    lid_first_window_s: float = 1.0  # first (early) attempt; >= lid_window_s: no early attempts
    lid_retry_step_s: float = 0.5    # audio (voiced or not) between early attempts
    lid_min_rms: float = 0.004    # frames quieter than this do not count as voiced
    lid_max_buffer_s: float = 12.0
    partial_interval_ms: int = 120
    draft_stable_partials: int = 3  # a word is stable after this many partials in a row; 0 = no drafts
    draft_min_words: int = 3        # a draft only once the stable prefix has this many words...
    draft_min_cjk_chars: int = 4    # ...(CJK: characters)...
    draft_min_fraction: float = 0.4  # ...or this share of the line heard so far
    prior: Dict[str, float] = field(default_factory=dict)  # {lang: probability} before any audio (page, memory)
    lid_prior_threshold: float = 0.6  # the prior's favourite is the first recognizer at or above this
    lid_prior_floor_en: float = 0.97  # one attempt confirms a favoured English only at or above this
    parallel_window_s: float = 5.0    # all languages' recognizers hear the audio for at most this long (0 = off)


class StreamingASRSession:
    def __init__(
        self,
        config: SessionConfig,
        engines: Dict[str, StreamingASREngine],
        lid_identify: Optional[Callable[[np.ndarray, List[str]], Optional[str]]] = None,
        restore_text: Optional[Callable[[str, str, bool], str]] = None,
    ):
        self.config = config
        self.engines = engines
        self.lid_identify = lid_identify
        self.restore_text = restore_text

        self.lang: Optional[str] = None
        self.lang_confirmed: bool = False
        self.lang_status: str = "provisional"  # provisional | confirmed | manual | fallback | error
        self.engine: Optional[StreamingASREngine] = None
        self.asr: Optional[AsrSession] = None

        # audio kept for LID / replay while the language is provisional
        self._lid_buffer: List[np.ndarray] = []
        self._lid_buffer_samples = 0
        self._lid_voiced_samples = 0
        self._lid_attempts = 0
        self._lid_errors: List[BaseException] = []
        self._lid_done = False
        self._lid_early_left = self._early_attempts()  # early attempts not made yet
        self._lid_next_early: Optional[int] = None    # samples_received at which the next one runs
        self._lid_full_attempts = 0                   # attempts on the full window (at most 2)
        self._lid_early_answer: Optional[str] = None  # what the previous early attempt said
        self._prior_lang: Optional[str] = None        # the prior's favourite, when it is the first recognizer
        # the other languages' recognizers while the language is not confirmed: lang -> (engine, session),
        # what each wrote (finals, and the partials of its open line), and where their audio clock starts
        self._parallel: Dict[str, tuple] = {}
        self._parallel_finals: Dict[str, List[AsrEvent]] = {}
        self._parallel_open: Dict[str, List[AsrEvent]] = {}
        self._parallel_origin = 0
        self._open_partial: Optional[AsrEvent] = None  # the first recognizer's newest partial of its open line

        self._last_partial_sent = 0.0
        self._pending_partial: Optional[AsrEvent] = None
        # raw text of the open line's recent partials, for its stable prefix
        self._partial_history: List[str] = []
        self._history_utterance: Optional[int] = None
        self.started_at = time.time()
        self.samples_received = 0
        # latency clock: when each frame arrived (end sample -> wall time), and where on the
        # session's sample count the current recognizer's audio clock starts
        self._now: Callable[[], float] = time.time
        self._arrival_end: List[int] = []
        self._arrival_wall: List[float] = []
        self._asr_origin = 0
        self._session_wall: Optional[float] = None
        self.stats = {"partials": 0, "finals": 0, "lid_ms": 0.0, "lid_switches": 0}
        self._startup_msgs: List[dict] = []

        if config.source_lang and config.source_lang != "auto":
            self._start_recognizer(config.source_lang)
            self.lang_confirmed = True
            self.lang_status = "manual"
            self._lid_done = True
        else:
            self._prior_lang = self._prior_favourite()
            provisional = self._prior_lang or self._provisional_language()
            self._start_recognizer(provisional)
            msg = {"type": "lid", "lang": provisional, "source": "prior" if self._prior_lang else "provisional",
                   "confirmed": False, "status": "provisional"}
            if self._prior_lang:
                msg["prior"] = round(config.prior[self._prior_lang], 3)
            self._startup_msgs.append(msg)
            self._start_parallel()
            if config.prior:
                obs.log("lid_prior", "success" if self._prior_lang else "skip", prior=config.prior,
                        favoured=self._prior_lang, provisional=provisional, threshold=config.lid_prior_threshold,
                        error_message=None if self._prior_lang else "no language reaches the threshold")

    # ------------------------------------------------------------------ setup

    def _provisional_language(self) -> str:
        for lang in self.config.allowed_langs:
            try:
                self._pick_engine(lang)
                return lang
            except RuntimeError:
                continue
        return self.config.allowed_langs[0]

    def _start_parallel(self) -> None:
        """The other allowed languages' recognizers, fed from now on (only with language ID)."""
        self._stop_parallel(None)
        if self.config.parallel_window_s <= 0 or self.lid_identify is None:
            return
        for lang in self.config.allowed_langs:
            if lang == self.lang or lang in self._parallel:
                continue
            try:
                eng = self._pick_engine(lang)
            except RuntimeError:
                continue
            self._parallel[lang] = (eng, eng.start_session(lang))
            self._parallel_finals[lang], self._parallel_open[lang] = [], []
        self._parallel_origin = self.samples_received

    def _stop_parallel(self, reason: Optional[str], keep: Optional[str] = None) -> None:
        """Close the parallel recognizers (but `keep`)."""
        if not self._parallel:
            return
        stopped = [l for l in self._parallel if l != keep]
        for lang in stopped:
            self._parallel.pop(lang)[1].close()
            self._parallel_finals.pop(lang, None)
            self._parallel_open.pop(lang, None)
        if reason:
            obs.log("asr_parallel", "success", reason=reason, stopped=stopped, kept=keep,
                    audio_s=round((self.samples_received - self._parallel_origin) / SAMPLE_RATE, 2))

    def _feed_parallel(self, pcm16: bytes) -> None:
        for lang, (_, sess) in self._parallel.items():
            finals, open_line = self._parallel_finals[lang], self._parallel_open[lang]
            for ev in sess.feed(pcm16):
                if ev.kind == "final":
                    finals.append(ev)
                    open_line.clear()
                else:
                    if open_line and open_line[-1].utterance_id != ev.utterance_id:
                        open_line.clear()
                    open_line.append(ev)
                    del open_line[:-8]
        window = int(SAMPLE_RATE * self.config.parallel_window_s)
        if self.samples_received - self._parallel_origin >= window:
            self._stop_parallel("window")

    def _take_parallel(self, lang: str) -> List[AsrEvent]:
        """Make `lang`'s parallel recognizer the session's; stop the others. Returns what it wrote."""
        eng, sess = self._parallel[lang]
        backlog = self._parallel_finals[lang] + self._parallel_open[lang]
        self._stop_parallel("confirmed", keep=lang)
        self._parallel.clear()
        self._parallel_finals.clear()
        self._parallel_open.clear()
        if self.asr is not None:
            self.asr.close()
        self.lang, self.engine, self.asr = lang, eng, sess
        self._asr_origin = self._parallel_origin
        self._last_partial_sent = 0.0
        self._pending_partial = None
        self._open_partial = None
        self._partial_history = []
        self._history_utterance = None
        logger.info("ASR session: lang=%s engine=%s (parallel recognizer)", lang, eng.name)
        return backlog

    def _emit_backlog(self, events: List[AsrEvent]) -> List[dict]:
        """A taken-over recognizer's text: every final, and the newest partial of its open line."""
        out: List[dict] = []
        last_partial = None
        for ev in events:
            self._note_for_stability(ev)
            if ev.kind == "final":
                self.stats["finals"] += 1
                out.append(self._event_msg(ev))
                last_partial = None
            else:
                last_partial = ev
        if last_partial is not None:
            self.stats["partials"] += 1
            self._last_partial_sent = time.time()
            self._open_partial = last_partial
            out.append(self._event_msg(last_partial))
        return out

    def _prior_favourite(self) -> Optional[str]:
        """The prior's likeliest language if it reaches the threshold and can be recognised."""
        prior = {l: p for l, p in (self.config.prior or {}).items() if l in self.config.allowed_langs}
        if not prior:
            return None
        lang = max(prior, key=prior.get)
        if prior[lang] < self.config.lid_prior_threshold:
            return None
        try:
            self._pick_engine(lang)
        except RuntimeError:
            return None
        return lang

    def _pick_engine(self, lang: str) -> StreamingASREngine:
        wanted = self.config.engine
        if wanted != "auto" and wanted in self.engines and self.engines[wanted].supports(lang):
            return self.engines[wanted]
        for eng in self.engines.values():
            if eng.supports(lang):
                return eng
        raise RuntimeError(f"No ASR engine supports language {lang!r}")

    def _start_recognizer(self, lang: str) -> None:
        if self.asr is not None:
            self.asr.close()
        self.lang = lang
        self.engine = self._pick_engine(lang)
        self.asr = self.engine.start_session(lang)
        self._asr_origin = self.samples_received
        self._last_partial_sent = 0.0
        self._pending_partial = None
        self._open_partial = None
        self._partial_history = []
        self._history_utterance = None
        logger.info("ASR session: lang=%s engine=%s", lang, self.engine.name)

    def startup_messages(self) -> List[dict]:
        msgs, self._startup_msgs = self._startup_msgs, []
        return msgs

    def _early_attempts(self) -> int:
        """How many early attempts fit before the full window: lid_first_window_s, then one per
        lid_retry_step_s (1.0, 1.5, 2.0 for a 2.5 s window)."""
        c = self.config
        n, t = 0, c.lid_first_window_s
        while c.lid_retry_step_s > 0 and t < c.lid_window_s - 1e-9 and n < 20:
            n, t = n + 1, t + c.lid_retry_step_s
        return n

    def set_language(self, lang: str) -> List[dict]:
        """The spoken language from the UI: a language restarts the recognizer with it;
        "auto" starts detection, unless this session is already detecting or has detected
        (the extension re-sends its whole config when any setting changes)."""
        out: List[dict] = []
        if lang == "auto":
            if self.config.source_lang == "auto" and self.lang_status in ("provisional", "confirmed"):
                return out
            self.config.source_lang = "auto"
            self.lang_confirmed = False
            self.lang_status = "provisional"
            self._lid_done = False
            self._lid_attempts = 0
            self._lid_errors = []
            self._lid_early_left = self._early_attempts()
            self._lid_next_early = None
            self._lid_full_attempts = 0
            self._lid_early_answer = None
            self._reset_lid_buffer()
            self._start_parallel()
            out.append({"type": "lid", "lang": self.lang, "source": "provisional", "confirmed": False, "status": "provisional"})
            return out
        self.config.source_lang = lang
        self._prior_lang = None
        self._stop_parallel("manual")
        if self.asr is not None and lang == self.lang:
            self.lang_confirmed = True
            self.lang_status = "manual"
            self._lid_done = True
            return [{"type": "lid", "lang": lang, "source": "manual", "confirmed": True, "status": "manual"}]
        if self.asr is not None:
            for ev in self.asr.flush():
                out.append(self._event_msg(ev))
        self._start_recognizer(lang)
        self.lang_confirmed = True
        self.lang_status = "manual"
        self._lid_done = True
        self._reset_lid_buffer()
        out.append({"type": "lid", "lang": lang, "source": "manual", "confirmed": True, "status": "manual"})
        return out

    def _reset_lid_buffer(self) -> None:
        self._lid_buffer.clear()
        self._lid_buffer_samples = 0
        self._lid_voiced_samples = 0

    # ------------------------------------------------------------------- feed

    def feed(self, pcm16: bytes, capture_ts: Optional[float] = None) -> List[dict]:
        """Consume one audio frame; return JSON-able messages to send."""
        self.samples_received += len(pcm16) // 2
        self._note_arrival()
        out: List[dict] = self.startup_messages()

        self._frame_replayed = False
        if self._parallel:  # before language ID: a recognizer taken over at this frame has heard it
            self._feed_parallel(pcm16)
        if not self._lid_done:
            out.extend(self._collect_for_lid(pcm16))

        if self.asr is not None and not self._frame_replayed:
            out.extend(self._emit(self.asr.feed(pcm16), capture_ts))
        return out

    def _note_arrival(self) -> None:
        now = self._now()
        if self._session_wall is None:
            self._session_wall = now
        self._arrival_end.append(self.samples_received)
        self._arrival_wall.append(now)
        if len(self._arrival_end) > 9000:  # keep the last few minutes (40 ms frames)
            del self._arrival_end[:4500], self._arrival_wall[:4500]

    def _wall_at(self, recognizer_t: Optional[float]) -> Optional[float]:
        """Wall-clock time at which the audio at `recognizer_t` (seconds on the current
        recognizer's clock) reached the session: the arrival of the frame holding it."""
        if recognizer_t is None or not self._arrival_end:
            return None
        sample = self._asr_origin + int(round(recognizer_t * SAMPLE_RATE))
        i = min(bisect.bisect_right(self._arrival_end, sample), len(self._arrival_end) - 1)
        return round(self._arrival_wall[i], 3)

    def _collect_for_lid(self, pcm16: bytes) -> List[dict]:
        samples = pcm16_to_float32(pcm16)
        self._lid_buffer.append(samples)
        self._lid_buffer_samples += len(samples)
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        if rms >= self.config.lid_min_rms:
            self._lid_voiced_samples += len(samples)

        max_samples = int(SAMPLE_RATE * self.config.lid_max_buffer_s)
        while self._lid_buffer_samples > max_samples and len(self._lid_buffer) > 1:
            dropped = self._lid_buffer.pop(0)
            self._lid_buffer_samples -= len(dropped)

        voiced_s = self._lid_voiced_samples / SAMPLE_RATE
        full_at = self.config.lid_window_s * (1.6 if self._lid_full_attempts else 1.0)
        if voiced_s >= full_at - 1e-6:
            early = False
            self._lid_full_attempts += 1
        elif self._lid_full_attempts == 0 and self._lid_early_left > 0 and (
                voiced_s >= self.config.lid_first_window_s - 1e-6 if self._lid_next_early is None
                else self.samples_received >= self._lid_next_early):
            early = True
            self._lid_early_left -= 1
            self._lid_next_early = self.samples_received + int(SAMPLE_RATE * self.config.lid_retry_step_s)
        else:
            return []
        final_attempt = self._lid_full_attempts >= 2

        lang, confidence = None, None
        buffered_s = round(self._lid_buffer_samples / SAMPLE_RATE, 2)
        if self.lid_identify is not None:
            t0 = time.time()
            try:
                # an answer, or (answer, confidence of the likeliest allowed language)
                lang = self.lid_identify(np.concatenate(self._lid_buffer), self.config.allowed_langs)
                if isinstance(lang, tuple):
                    lang, confidence = lang
            except Exception as e:
                logger.warning("LID failed: %s", e)
                self._lid_errors.append(e)
                obs.log_exc("lid", e, api="process", duration_ms=(time.time() - t0) * 1000, attempt=self._lid_attempts + 1,
                            buffered_s=buffered_s, degraded="treated as undecided")
            self.stats["lid_ms"] = round((time.time() - t0) * 1000)
        self._lid_attempts += 1

        if early and self.lid_identify is not None:
            # A short window is weak evidence. Accepted early: a language other than the provisional
            # one that two attempts in a row agree on. The provisional language waits for the full
            # window, unless the prior favoured it: then one answer above the floor confirms it.
            agreed = lang is not None and lang != self.lang and lang == self._lid_early_answer
            if self._prior_lang is not None and lang in LATE_ONLY_LANGS and lang != self._prior_lang:
                agreed = False
            favoured = (lang is not None and lang == self._prior_lang and lang == self.lang
                        and (lang != "en" or confidence is None or confidence >= self.config.lid_prior_floor_en))
            self._lid_early_answer = lang
            if not (agreed or favoured):
                obs.log("lid", "skip", duration_ms=self.stats["lid_ms"], attempt=self._lid_attempts, buffered_s=buffered_s,
                        early=True, answer=lang, confidence=confidence, prior=self._prior_lang,
                        error_message="early attempt: not decided yet")
                return []

        if lang is None:
            if final_attempt or self.lid_identify is None:
                # give up: keep the provisional language, and say so (never "confirmed")
                self._lid_done = True
                self._reset_lid_buffer()
                errored = self.lid_identify is not None and len(self._lid_errors) >= self._lid_attempts
                self.lang_status = "error" if errored else "fallback"
                logger.info("LID undecided; keeping provisional language %s (%s)", self.lang, self.lang_status)
                obs.log("lid", "skip", duration_ms=self.stats["lid_ms"], error_type="input_invalid",
                        error_message="LID undecided (no result inside allowed languages)", attempt=self._lid_attempts,
                        buffered_s=buffered_s,
                        degraded=f"provisional language {self.lang!r} kept, reported as {self.lang_status} (not confirmed)")
                msg = {"type": "lid", "lang": self.lang, "source": "fallback", "confirmed": False,
                       "status": self.lang_status, "lid_ms": self.stats["lid_ms"]}
                if errored:
                    last = self._lid_errors[-1]
                    et = obs.classify(last, "process")
                    msg.update(error_type="process" if et == "unknown" else et, error=str(last) or type(last).__name__)
                elif self.lid_identify is None:
                    msg["error"] = "no spoken-language-ID model"
                return [msg]
            # collect a bit more speech and try once more
            obs.log("lid", "skip", duration_ms=self.stats["lid_ms"], error_type="input_invalid",
                    error_message="LID undecided; collecting more speech for one more attempt",
                    attempt=self._lid_attempts, buffered_s=buffered_s)
            return []

        self._lid_done = True
        self.lang_confirmed = True
        self.lang_status = "confirmed"
        out: List[dict] = []
        obs.log("lid", "success", duration_ms=self.stats["lid_ms"], lang=lang, provisional=self.lang,
                switched=lang != self.lang, attempt=self._lid_attempts, buffered_s=buffered_s, early=early,
                confidence=confidence, prior=self._prior_lang,
                prior_right=None if self._prior_lang is None else lang == self._prior_lang)
        if lang != self.lang and lang in self._parallel:
            # Wrong provisional guess, but the right recognizer has been listening all along:
            # it takes over with what it wrote, nothing is replayed
            logger.info("LID switched language %s -> %s (parallel recognizer, no replay)", self.lang, lang)
            self.stats["lid_switches"] += 1
            backlog = self._take_parallel(lang)
            out.append({"type": "reset"})
            out.append({"type": "lid", "lang": lang, "source": "auto", "confirmed": True, "status": "confirmed",
                        "lid_ms": self.stats["lid_ms"], "switched": True, "parallel": True})
            out.extend(self._emit_backlog(backlog))
            self._frame_replayed = True  # it was fed this frame before language ID ran
        elif lang != self.lang:
            # Wrong provisional guess: swap recognizer, tell UI to drop provisional captions,
            # and replay the buffered audio so the first words are not lost.
            logger.info("LID switched language %s -> %s (replaying %.1fs)", self.lang, lang, self._lid_buffer_samples / SAMPLE_RATE)
            self.stats["lid_switches"] += 1
            self._start_recognizer(lang)
            # the new recognizer's clock starts at the first replayed sample
            self._asr_origin = self.samples_received - self._lid_buffer_samples
            out.append({"type": "reset"})
            out.append({"type": "lid", "lang": lang, "source": "auto", "confirmed": True, "status": "confirmed",
                        "lid_ms": self.stats["lid_ms"], "switched": True})
            replay = np.concatenate(self._lid_buffer)
            pcm = (np.clip(replay, -1, 1) * 32767).astype("<i2").tobytes()
            # in 40 ms frames, as live audio arrives: the recognizer looks at its endpoint once
            # per feed(), so one block could not end a sentence inside the buffer (A9)
            step = int(SAMPLE_RATE * 0.04) * 2
            events: List[AsrEvent] = []
            for i in range(0, len(pcm), step):
                events.extend(self.asr.feed(pcm[i:i + step]))
            out.extend(self._emit(events, None))
            self._frame_replayed = True  # the buffer ends with the frame that is being fed
        else:
            self._stop_parallel("confirmed", keep=None)
            out.append({"type": "lid", "lang": lang, "source": "auto", "confirmed": True, "status": "confirmed",
                        "lid_ms": self.stats["lid_ms"]})
            # the open line is on screen, dimmed: send it again, confirmed
            if self._open_partial is not None:
                out.append(self._event_msg(self._open_partial))
        self._reset_lid_buffer()
        return out

    def _emit(self, events: List[AsrEvent], capture_ts: Optional[float]) -> List[dict]:
        out: List[dict] = []
        now = time.time()
        for ev in events:
            self._note_for_stability(ev)
            self._open_partial = ev if ev.kind == "partial" else None
            if ev.kind == "partial":
                if (now - self._last_partial_sent) * 1000 < self.config.partial_interval_ms:
                    self._pending_partial = ev
                    continue
                self._last_partial_sent = now
                self._pending_partial = None
                self.stats["partials"] += 1
            else:
                self._pending_partial = None
                self.stats["finals"] += 1
            out.append(self._event_msg(ev, capture_ts))
        if self._pending_partial is not None and (now - self._last_partial_sent) * 1000 >= self.config.partial_interval_ms:
            ev = self._pending_partial
            self._pending_partial = None
            self._last_partial_sent = now
            self.stats["partials"] += 1
            out.append(self._event_msg(ev, capture_ts))
        return out

    def _note_for_stability(self, ev: AsrEvent) -> None:
        """Every partial the recognizer produced (sent or throttled) counts towards a word's
        stability; a final or a new line starts the history again."""
        if ev.kind != "partial" or ev.utterance_id != self._history_utterance:
            self._partial_history = []
            self._history_utterance = ev.utterance_id if ev.kind == "partial" else None
        if ev.kind == "partial" and (not self._partial_history or self._partial_history[-1] != ev.text):
            self._partial_history.append(ev.text)
            del self._partial_history[:-max(1, self.config.draft_stable_partials)]

    def _restore(self, text: str, lang: str, final: bool) -> str:
        if self.restore_text is None or not text:
            return text
        try:
            return self.restore_text(text, lang, final) or text
        except Exception as e:  # the hook should not raise; never lose a caption over it
            obs.log_exc("punctuate", e, api="process", lang=lang, degraded="text kept as is")
            return text

    def _event_msg(self, ev: AsrEvent, capture_ts: Optional[float] = None) -> dict:
        text = self._restore(ev.text, ev.lang, ev.kind == "final")
        msg = {
            "type": ev.kind,
            "utterance_id": ev.utterance_id,
            "text": text,
            "lang": ev.lang,
            "lang_status": self.lang_status,
            "confirmed": self.lang_confirmed,
            "t0": round(ev.t0, 3),
            "t1": round(ev.t1, 3),
            "w_first": self._wall_at(ev.first_word_t if ev.first_word_t is not None else ev.t0),
            "w_last": self._wall_at(ev.last_word_t if ev.last_word_t is not None else ev.t1),
            "w_session": round(self._session_wall, 3) if self._session_wall is not None else None,
            "capture_ts": capture_ts,
            "server_ts": time.time(),
        }
        if ev.kind == "final" and text != ev.text:
            msg["raw_text"] = ev.text
        if ev.kind == "partial" and self.config.draft_stable_partials > 0 and ev.utterance_id == self._history_utterance:
            stable = stable_prefix(self._partial_history, self.config.draft_stable_partials)
            if stable and draft_long_enough(stable, ev.text, self.config.draft_min_words, self.config.draft_min_cjk_chars,
                                            self.config.draft_min_fraction):
                msg["stable_text"] = self._restore(stable, ev.lang, False)
        return msg

    # ------------------------------------------------------------------ close

    def flush(self) -> List[dict]:
        if self.asr is None:
            return []
        return [self._event_msg(ev) for ev in self.asr.flush()]

    def close(self) -> None:
        self._stop_parallel(None)
        if self.asr is not None:
            self.asr.close()
            self.asr = None

    def status(self) -> dict:
        return {
            "lang": self.lang,
            "lang_confirmed": self.lang_confirmed,
            "lang_status": self.lang_status,
            "prior": self._prior_lang,
            "parallel": sorted(self._parallel),
            "engine": self.engine.name if self.engine else None,
            "target_langs": self.config.target_langs,
            "seconds_received": round(self.samples_received / SAMPLE_RATE, 1),
            **self.stats,
        }
