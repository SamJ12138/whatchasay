"""Bounded segments (observations A9): where SherpaSession ends a line.

The recognizer's own endpoint (trailing silence, `asr.rule2_min_trailing_silence`) only
fires on decode-chunk boundaries (0.64 s for the Bengali model), so film dialogue ran
several sentences into one final, up to the 10 s cut inside a word. SherpaSession now
also closes a line
  * at a pause the recognizer already reports: two consecutive tokens at least
    `asr.split_gap_s` apart, at a word boundary, when the line so far is at least
    `asr.split_min_piece_s` long (a hesitation does not become a one-word line) and the
    pause is at least `asr.split_gap_ratio` times the line's median token gap (slow,
    deliberate speech is not chopped);
  * when the open line exceeds `asr.max_segment_s` or `asr.max_segment_tokens`: at its
    widest pause, never inside a word.
A scripted stand-in for sherpa-onnx's OnlineRecognizer drives the real SherpaSession.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from app.asr.engine import SAMPLE_RATE
from app.asr.sherpa_engine import SegmentRules, SherpaSession

FRAME = int(SAMPLE_RATE * 0.04)
OFF = SegmentRules(split_gap_s=0, max_segment_s=0, max_segment_tokens=0)


class FakeRecognizer:
    """Tokens appear once the audio up to their time has been decoded; decoding advances in
    whole chunks. Endpoint rules as sherpa-onnx applies them, looked at after decoding:
    rule 2 = `rule2` s of decoded audio after the last token, rule 3 = the segment is
    `rule3` s long. Timestamps are relative to the segment's start_time, as sherpa's are;
    a word's first token starts with a space, as the Zipformer models' tokens do."""

    def __init__(self, timeline, chunk_s=0.32, rule2=0.6, rule3=30.0):
        self.timeline = list(timeline)          # [(token, absolute audio time)]
        self.chunk = chunk_s
        self.rule2, self.rule3 = rule2, rule3
        self.fed = 0
        self.decoded = 0.0
        self.seg_start = 0.0
        self.base = 0                           # tokens consumed by earlier segments

    def create_stream(self):
        return SimpleNamespace(accept_waveform=self._accept)

    def _accept(self, sr, samples):
        self.fed += len(samples)

    def is_ready(self, stream):
        return self.fed / SAMPLE_RATE - self.decoded >= self.chunk - 1e-9

    def decode_stream(self, stream):
        self.decoded += self.chunk

    def _visible(self):
        return [(tok, t) for tok, t in self.timeline[self.base:] if t <= self.decoded + 1e-9]

    def get_result(self, stream):
        return "".join(tok for tok, _ in self._visible()).strip()

    def get_result_all(self, stream):
        vis = self._visible()
        return SimpleNamespace(tokens=[tok for tok, _ in vis], timestamps=[t - self.seg_start for _, t in vis],
                               start_time=self.seg_start, text=self.get_result(stream))

    def is_endpoint(self, stream):
        vis = self._visible()
        if self.decoded - self.seg_start >= self.rule3:
            return True
        return bool(vis) and self.decoded - vis[-1][1] >= self.rule2

    def reset(self, stream):
        self.base += len(self._visible())
        self.seg_start = self.decoded


def spoken(*parts, start=0.5, step=0.2):
    """Words `step` s apart; a number is a pause of that many seconds before the next
    word; '+xyz' continues the previous word. -> [(token, time)]"""
    out, t = [], start
    for p in parts:
        if isinstance(p, (int, float)):
            t += p - step
            continue
        for w in p.split():
            out.append((w[1:], round(t, 3)) if w.startswith("+") else (" " + w, round(t, 3)))
            t += step
    return out


def run(timeline, seconds, rules=None, **rec_kw):
    rec = FakeRecognizer(timeline, **rec_kw)
    s = SherpaSession(SimpleNamespace(name="fake"), "bn", rec, rules=rules or SegmentRules())
    events = []
    silence = np.zeros(FRAME, dtype="<i2").tobytes()
    for _ in range(int(seconds / 0.04)):
        events.extend(s.feed(silence))
    return events, s


def finals(events):
    return [(e.utterance_id, e.text) for e in events if e.kind == "final"]


A = "a1 a2 a3 a4 a5 a6 a7 a8 a9 a10 a11 a12"      # 12 words, 2.2 s
B = "b1 b2 b3 b4 b5 b6"


def test_a_sentence_without_a_pause_is_one_final_at_the_endpoint():
    events, _ = run(spoken(A), 5.0)
    assert finals(events) == [(0, A)]
    assert [e.text for e in events if e.kind == "partial"][-1] == A


def test_a_pause_between_sentences_ends_the_line_without_waiting_for_the_endpoint():
    """0.55 s between the sentences: shorter than the 0.6 s endpoint rule, and on 0.64 s
    decode chunks the endpoint does not see it, so both sentences were one final."""
    tl = spoken(A, 0.55, B)
    assert finals(run(tl, 6.0, rules=OFF, chunk_s=0.64)[0]) == [(0, A + " " + B)]   # before
    events, _ = run(tl, 6.0, chunk_s=0.64)
    assert finals(events) == [(0, A), (1, B)]
    first, second = [e for e in events if e.kind == "final"]
    assert (first.first_word_t, first.last_word_t) == (pytest.approx(0.5), pytest.approx(2.7))
    assert second.first_word_t == pytest.approx(3.25)
    # the split final comes with the decode that first shows the word after the pause; what
    # follows it is the next line's partial
    i = events.index(first)
    assert events[i + 1].kind == "partial" and events[i + 1].utterance_id == 1 and events[i + 1].text.startswith("b1")
    assert all(e.utterance_id == 0 for e in events[:i])


def test_a_hesitation_near_the_start_does_not_become_a_short_line():
    tl = spoken("well", 0.7, A)                      # 0.7 s after the first word
    assert finals(run(tl, 6.0)[0]) == [(0, "well " + A)]
    tl = spoken("a1 a2 a3 a4", 0.6, B, "b7 b8")      # 0.6 s of line before the pause: under split_min_piece_s
    assert len(finals(run(tl, 6.0, chunk_s=0.64)[0])) == 1


def test_slow_deliberate_speech_is_not_chopped_at_its_ordinary_gaps():
    """A word every 0.45 s, then a 0.6 s gap: a pause for fast speech, an ordinary gap here."""
    tl = spoken("s1 s2 s3 s4 s5 s6 s7", 0.6, "s8 s9", step=0.45)
    assert len(finals(run(tl, 8.0, chunk_s=0.64)[0])) == 1
    fast = spoken(A, 0.6, "b1 b2")                   # the same 0.6 s after 0.2 s gaps is a pause
    assert len(finals(run(fast, 8.0, chunk_s=0.64)[0])) == 2


def test_a_pause_inside_a_word_is_not_a_boundary():
    tl = spoken(A, "raj", 0.6, "+bhog", B)
    assert finals(run(tl, 7.0, chunk_s=0.64)[0]) == [(0, A + " rajbhog " + B)]


def test_cjk_tokens_are_words_of_their_own():
    line1 = [(ch, round(0.5 + 0.25 * i, 2)) for i, ch in enumerate("你好吗我很好谢谢你")]   # 9 characters, 2.0 s
    line2 = [(ch, round(3.2 + 0.25 * i, 2)) for i, ch in enumerate("明天见")]
    events, _ = run(line1 + line2, 6.0, chunk_s=0.64)
    assert finals(events) == [(0, "你好吗我很好谢谢你"), (1, "明天见")]


def test_a_line_longer_than_the_time_limit_is_cut_at_its_widest_pause_never_inside_a_word():
    """9 s of speech, a token every 0.3 s, one slightly longer gap (0.36 s) before word 11:
    no pause the pause rule accepts. Past max_segment_s the line is cut at that gap."""
    tl, t = [], 0.5
    for i in range(30):
        if i == 10:
            t += 0.06
        tl.append((f" w{i}", round(t, 2)))
        tl.append(("x", round(t + 0.1, 2)))          # every word has a second token
        t += 0.3
    assert len(finals(run(tl, 12.0, rules=OFF)[0])) == 1
    events, _ = run(tl, 12.0, rules=SegmentRules(max_segment_s=6.0))
    f = [e for e in events if e.kind == "final"]
    assert len(f) >= 2
    assert f[0].text.split() == [f"w{i}x" for i in range(10)], f[0].text
    for e in f:
        assert e.last_word_t - e.first_word_t <= 6.0 + 0.4, (e.text, e.first_word_t, e.last_word_t)
    assert " ".join(e.text for e in f).split() == [f"w{i}x" for i in range(30)]   # nothing lost, doubled or cut inside a word


def test_a_line_with_more_tokens_than_the_limit_is_cut():
    tl = [(f" w{i}", round(0.5 + 0.2 * i, 2)) for i in range(30)]
    events, _ = run(tl, 9.0, rules=SegmentRules(max_segment_s=0, max_segment_tokens=12))
    f = [e for e in events if e.kind == "final"]
    assert len(f) >= 3 and all(len(e.text.split()) <= 12 for e in f), finals(events)
    assert " ".join(e.text for e in f).split() == [f"w{i}" for i in range(30)]


def test_utterance_ids_only_grow_and_nothing_follows_a_final_with_its_id():
    tl = spoken(A, 0.55, B, 1.5, "c1 c2")            # a split, then a real endpoint, then more
    events, _ = run(tl, 9.0, chunk_s=0.64)
    assert finals(events) == [(0, A), (1, B), (2, "c1 c2")]
    done = set()
    for e in events:
        assert e.utterance_id not in done, f"event for utterance {e.utterance_id} after its final"
        if e.kind == "final":
            done.add(e.utterance_id)


def test_flush_closes_the_open_line_after_a_split():
    tl = spoken(A, 0.55, B)
    rec = FakeRecognizer(tl, chunk_s=0.64)
    s = SherpaSession(SimpleNamespace(name="fake"), "bn", rec, rules=SegmentRules())
    events = []
    for _ in range(int(3.9 / 0.04)):                 # stop while the second sentence is still open
        events.extend(s.feed(np.zeros(FRAME, dtype="<i2").tobytes()))
    events.extend(s.flush())
    texts = [e.text for e in events if e.kind == "final"]
    assert texts == [A, "b1 b2 b3"]


def test_endpoint_settings_come_from_config(monkeypatch):
    from app import asr as asr_pkg
    from app.config import settings

    if not asr_pkg.SHERPA_AVAILABLE:
        pytest.skip("sherpa-onnx not installed")
    monkeypatch.setattr(asr_pkg, "_engines", None)
    for name, value in (("split_gap_s", 0.33), ("split_gap_ratio", 3.0), ("split_min_piece_s", 1.5),
                        ("max_segment_s", 5.5), ("max_segment_tokens", 33)):
        monkeypatch.setattr(settings.asr, name, value)
    eng = asr_pkg.get_engines()["sherpa-zipformer"]
    assert eng.rules == SegmentRules(split_gap_s=0.33, split_gap_ratio=3.0, split_min_piece_s=1.5, max_segment_s=5.5,
                                     max_segment_tokens=33)
    assert eng.rule2 == settings.asr.rule2_min_trailing_silence


def test_default_endpoint_settings():
    from app.config import ASRConfig

    cfg = ASRConfig()
    assert (cfg.rule2_min_trailing_silence, cfg.split_gap_s, cfg.split_gap_ratio, cfg.split_min_piece_s) == (0.6, 0.5, 2.5, 2.0)
    assert (cfg.max_segment_s, cfg.max_segment_tokens) == (6.0, 48)
    # the recognizer's own hard reset (cuts inside a word) is only a backstop now
    assert cfg.rule3_min_utterance_length == 30.0


def test_the_length_cut_prefers_a_pause_that_leaves_a_real_line_before_it():
    """Seen on the live clip: the widest gap of a too-long line was a hesitation after its
    second word, so the cut made a two-word line (6 s late) and left the rest as long as
    before. The cut goes to the widest gap that has at least split_min_piece_s before it."""
    tl, t = [], 0.5
    for i in range(30):
        if i == 2:
            t += 0.15      # a hesitation: the widest gap of all, 0.45 s, after two words
        if i == 12:
            t += 0.06
        tl.append((f" w{i}", round(t, 2)))
        t += 0.3
    events, _ = run(tl, 12.0, rules=SegmentRules(max_segment_s=6.0))
    f = [e for e in events if e.kind == "final"]
    assert f[0].text.split() == [f"w{i}" for i in range(12)], f[0].text
    assert " ".join(e.text for e in f).split() == [f"w{i}" for i in range(30)]
