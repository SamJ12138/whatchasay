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

Messages (JSON, sent by the WebSocket endpoint):
    {type:'partial', utterance_id, text, lang, lang_status, confirmed, t0, t1}
    {type:'final',   utterance_id, text, lang, lang_status, confirmed, t0, t1[, raw_text]}

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

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from .engine import AsrEvent, AsrSession, SAMPLE_RATE, StreamingASREngine, pcm16_to_float32
from .. import obs

logger = logging.getLogger(__name__)


@dataclass
class SessionConfig:
    source_lang: str = "auto"  # 'auto' | 'en' | 'zh' | 'bn' | ...
    target_langs: List[str] = field(default_factory=lambda: ["en"])
    engine: str = "auto"  # 'auto' | 'sherpa-zipformer'
    allowed_langs: List[str] = field(default_factory=lambda: ["en", "zh", "bn"])
    lid_window_s: float = 2.5     # seconds of voiced audio before running LID
    lid_min_rms: float = 0.004    # frames quieter than this do not count as voiced
    lid_max_buffer_s: float = 12.0
    partial_interval_ms: int = 120


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

        self._last_partial_sent = 0.0
        self._pending_partial: Optional[AsrEvent] = None
        self.started_at = time.time()
        self.samples_received = 0
        self.stats = {"partials": 0, "finals": 0, "lid_ms": 0.0, "lid_switches": 0}
        self._startup_msgs: List[dict] = []

        if config.source_lang and config.source_lang != "auto":
            self._start_recognizer(config.source_lang)
            self.lang_confirmed = True
            self.lang_status = "manual"
            self._lid_done = True
        else:
            provisional = self._provisional_language()
            self._start_recognizer(provisional)
            self._startup_msgs.append({"type": "lid", "lang": provisional, "source": "provisional", "confirmed": False,
                                       "status": "provisional"})

    # ------------------------------------------------------------------ setup

    def _provisional_language(self) -> str:
        for lang in self.config.allowed_langs:
            try:
                self._pick_engine(lang)
                return lang
            except RuntimeError:
                continue
        return self.config.allowed_langs[0]

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
        self._last_partial_sent = 0.0
        self._pending_partial = None
        logger.info("ASR session: lang=%s engine=%s", lang, self.engine.name)

    def startup_messages(self) -> List[dict]:
        msgs, self._startup_msgs = self._startup_msgs, []
        return msgs

    def set_language(self, lang: str) -> List[dict]:
        """Manual override from the UI. Restarts the recognizer."""
        out: List[dict] = []
        if lang == "auto":
            self.lang_confirmed = False
            self.lang_status = "provisional"
            self._lid_done = False
            self._lid_attempts = 0
            self._lid_errors = []
            self._reset_lid_buffer()
            out.append({"type": "lid", "lang": self.lang, "source": "provisional", "confirmed": False, "status": "provisional"})
            return out
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
        out: List[dict] = self.startup_messages()

        if not self._lid_done:
            out.extend(self._collect_for_lid(pcm16))

        if self.asr is not None:
            out.extend(self._emit(self.asr.feed(pcm16), capture_ts))
        return out

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

        if self._lid_voiced_samples < SAMPLE_RATE * self.config.lid_window_s:
            return []

        lang = None
        buffered_s = round(self._lid_buffer_samples / SAMPLE_RATE, 2)
        if self.lid_identify is not None:
            t0 = time.time()
            try:
                lang = self.lid_identify(np.concatenate(self._lid_buffer), self.config.allowed_langs)
            except Exception as e:
                logger.warning("LID failed: %s", e)
                self._lid_errors.append(e)
                obs.log_exc("lid", e, api="process", duration_ms=(time.time() - t0) * 1000, attempt=self._lid_attempts + 1,
                            buffered_s=buffered_s, degraded="treated as undecided")
            self.stats["lid_ms"] = round((time.time() - t0) * 1000)
        self._lid_attempts += 1

        if lang is None:
            if self._lid_attempts >= 2 or self.lid_identify is None:
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
            self._lid_voiced_samples = int(SAMPLE_RATE * self.config.lid_window_s * 0.4)
            return []

        self._lid_done = True
        self.lang_confirmed = True
        self.lang_status = "confirmed"
        out: List[dict] = []
        obs.log("lid", "success", duration_ms=self.stats["lid_ms"], lang=lang, provisional=self.lang,
                switched=lang != self.lang, attempt=self._lid_attempts, buffered_s=buffered_s)
        if lang != self.lang:
            # Wrong provisional guess: swap recognizer, tell UI to drop provisional captions,
            # and replay the buffered audio so the first words are not lost.
            logger.info("LID switched language %s -> %s (replaying %.1fs)", self.lang, lang, self._lid_buffer_samples / SAMPLE_RATE)
            self.stats["lid_switches"] += 1
            self._start_recognizer(lang)
            out.append({"type": "reset"})
            out.append({"type": "lid", "lang": lang, "source": "auto", "confirmed": True, "status": "confirmed",
                        "lid_ms": self.stats["lid_ms"], "switched": True})
            replay = np.concatenate(self._lid_buffer)
            pcm = (np.clip(replay, -1, 1) * 32767).astype("<i2").tobytes()
            out.extend(self._emit(self.asr.feed(pcm), None))
        else:
            out.append({"type": "lid", "lang": lang, "source": "auto", "confirmed": True, "status": "confirmed",
                        "lid_ms": self.stats["lid_ms"]})
        self._reset_lid_buffer()
        return out

    def _emit(self, events: List[AsrEvent], capture_ts: Optional[float]) -> List[dict]:
        out: List[dict] = []
        now = time.time()
        for ev in events:
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

    def _restore(self, ev: AsrEvent) -> str:
        if self.restore_text is None or not ev.text:
            return ev.text
        try:
            return self.restore_text(ev.text, ev.lang, ev.kind == "final") or ev.text
        except Exception as e:  # the hook should not raise; never lose a caption over it
            obs.log_exc("punctuate", e, api="process", lang=ev.lang, degraded="text kept as is")
            return ev.text

    def _event_msg(self, ev: AsrEvent, capture_ts: Optional[float] = None) -> dict:
        text = self._restore(ev)
        msg = {
            "type": ev.kind,
            "utterance_id": ev.utterance_id,
            "text": text,
            "lang": ev.lang,
            "lang_status": self.lang_status,
            "confirmed": self.lang_confirmed,
            "t0": round(ev.t0, 3),
            "t1": round(ev.t1, 3),
            "capture_ts": capture_ts,
            "server_ts": time.time(),
        }
        if ev.kind == "final" and text != ev.text:
            msg["raw_text"] = ev.text
        return msg

    # ------------------------------------------------------------------ close

    def flush(self) -> List[dict]:
        if self.asr is None:
            return []
        return [self._event_msg(ev) for ev in self.asr.flush()]

    def close(self) -> None:
        if self.asr is not None:
            self.asr.close()
            self.asr = None

    def status(self) -> dict:
        return {
            "lang": self.lang,
            "lang_confirmed": self.lang_confirmed,
            "lang_status": self.lang_status,
            "engine": self.engine.name if self.engine else None,
            "target_langs": self.config.target_langs,
            "seconds_received": round(self.samples_received / SAMPLE_RATE, 1),
            **self.stats,
        }
