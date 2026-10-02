"""Bounded segments with the real Zipformer recognizers (slow): the English sample
`test_wavs/1.wav` (16.7 s, two long read sentences) and, when ST_LID_CLIP_WAV points at
it, the live test's film clip (docs/live-test-youtube.md, line 2).

What the pause and length rules change is how long a line stays open: the time from a
line's first word to its final, which is when its translation can start. What they do
not change is the first display of a line: partial results reached the viewer before
this change too, at the recognizer's decode-chunk lag (asserted here as unchanged).
"""

import os
import wave
from pathlib import Path

import pytest

from app.asr.engine import SAMPLE_RATE
from app.asr.session import SessionConfig, StreamingASRSession
from app.asr.sherpa_engine import SegmentRules, SherpaZipformerEngine
from tests.conftest import MODELS_DIR, has_zipformer, zipformer_dir

pytestmark = pytest.mark.slow

CLIP = os.environ.get("ST_LID_CLIP_WAV")
needs_clip = pytest.mark.skipif(not (CLIP and Path(CLIP).exists() and has_zipformer("bn")),
                                reason="ST_LID_CLIP_WAV not set (the clip is not in the repository)")
BEFORE = dict(rule3_min_utterance_length=10.0, rules=SegmentRules(split_gap_s=0, max_segment_s=0, max_segment_tokens=0))
FRAME = 1280  # bytes: 40 ms of 16 kHz int16


def _pcm(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SAMPLE_RATE, 1, 2), path
        return w.readframes(w.getnframes()) + b"\x00" * (SAMPLE_RATE * 2 * 2)   # + 2 s of silence


def _lines(lang: str, pcm: bytes, **engine_kw):
    """Every line as {first_word, last_word, shown, final}: audio-clock seconds of its first
    and last word, of the first event that showed its first word (a line cut off a longer
    one by the length rule was on screen before, inside that line's partial) and of its
    final (40 ms frames, as live audio)."""
    session = SherpaZipformerEngine(MODELS_DIR, **engine_kw).start_session(lang)
    lines, shown_up_to = {}, []
    for i in range(0, len(pcm), FRAME):
        clock = (i + FRAME) / 2 / SAMPLE_RATE
        for ev in session.feed(pcm[i:i + FRAME]):
            shown_up_to.append((ev.last_word_t, clock))
            ln = lines.setdefault(ev.utterance_id, {})
            ln.update(first_word=ev.first_word_t, last_word=ev.last_word_t, text=ev.text)
            if ev.kind == "final":
                ln["final"] = clock
    for ln in lines.values():
        ln["shown"] = min(clock for last, clock in shown_up_to if last >= ln["first_word"] - 1e-6)
    return [ln for _, ln in sorted(lines.items()) if "final" in ln]


def _span(ln):
    return ln["last_word"] - ln["first_word"]


@pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded")
def test_read_english_lines_are_bounded_and_close_sooner():
    pcm = _pcm(zipformer_dir("en") / "test_wavs" / "1.wav")
    before, after = _lines("en", pcm, **BEFORE), _lines("en", pcm)
    # before: the first sentence ran into the 10 s limit
    assert max(_span(ln) for ln in before) > 9.0, [round(_span(ln), 2) for ln in before]
    assert len(after) > len(before)
    assert max(_span(ln) for ln in after) <= 6.0 + 0.5, [round(_span(ln), 2) for ln in after]
    # first word -> final (when the translation can start): the longest wait drops
    wait = lambda lines: max(ln["final"] - ln["first_word"] for ln in lines)
    assert wait(before) > 9.5 and wait(after) < 7.5, (wait(before), wait(after))
    # first word -> first text: at the decode-chunk lag before and after (partials already streamed)
    for lines in (before, after):
        assert max(ln["shown"] - ln["first_word"] for ln in lines) < 1.0, lines
    # the same words, in order (the cut is between words, the stream is not reset)
    assert " ".join(ln["text"] for ln in after).split()[:20] == " ".join(ln["text"] for ln in before).split()[:20]


@needs_clip
def test_the_live_clips_run_on_is_cut_into_sentences():
    pcm = _pcm(Path(CLIP))
    before, after = _lines("bn", pcm, **BEFORE), _lines("bn", pcm)
    assert max(_span(ln) for ln in before) > 9.0, [round(_span(ln), 2) for ln in before]
    spans = [round(_span(ln), 2) for ln in after]
    assert len(after) >= 5 and max(spans) <= 6.0 + 0.5, spans
    # line 2 of the live test (the 9.6 s of dialogue from about 2.7 s) is at least three lines now
    assert sum(1 for ln in after if 2.0 <= ln["first_word"] < 12.5) >= 3, [(round(ln["first_word"], 2), s) for ln, s in zip(after, spans)]


@needs_clip
def test_the_first_sentence_stays_its_own_line_when_detection_replays_the_buffer():
    """Auto-detect: the first seconds are replayed into the Bengali recognizer once the
    language is known. As one block they ran the first sentence and the next ones into a
    10 s line; in frames the first sentence is its own final."""
    pcm = _pcm(Path(CLIP))
    engine = SherpaZipformerEngine(MODELS_DIR)
    session = StreamingASRSession(
        SessionConfig(source_lang="auto", target_langs=["en"], allowed_langs=["en", "zh", "bn"], lid_window_s=2.5),
        {"sherpa-zipformer": engine}, lid_identify=lambda audio, allowed: "bn")
    msgs = []
    for i in range(0, len(pcm), FRAME):
        msgs += session.feed(pcm[i:i + FRAME])
    finals = [m for m in msgs if m["type"] == "final" and m["lang"] == "bn"]
    assert finals, "no Bengali final"
    assert finals[0]["t1"] - finals[0]["t0"] < 3.5, (finals[0]["t0"], finals[0]["t1"])
    assert len(finals) >= 5
