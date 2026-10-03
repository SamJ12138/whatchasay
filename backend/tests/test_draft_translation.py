"""Incremental translation: while a line is still open, its stable prefix is translated
and shown as a draft; the final translation replaces it at the endpoint.

A word is stable when it has survived `asr.draft_stable_partials` consecutive partial
results (default 3); the stable prefix is re-translated at most once per
`asr.draft_debounce_ms` (default 1500 ms; both tuned on the live clip, docs/latency.md). Fake engines throughout: a scripted recognizer
and the fake MT engine, whose output names its input.
"""

import asyncio
from typing import List

import pytest

from app.asr.draft import DraftScheduler, LocalAgreement, stable_prefix
from app.asr.engine import AsrEvent, SAMPLE_RATE
from app.asr.session import SessionConfig, StreamingASRSession
from tests.fakes import pcm_frame


# ---------------------------------------------------------------- the stable prefix


def test_a_word_is_stable_once_it_survived_n_partials():
    assert stable_prefix(["one"], 2) == ""                                   # seen once
    assert stable_prefix(["one", "one two"], 2) == "one"
    assert stable_prefix(["one", "one two", "one two three"], 2) == "one two"
    assert stable_prefix(["one", "one two", "one two three"], 3) == "one"
    assert stable_prefix(["one two three"], 1) == "one two three"


def test_a_word_still_growing_is_not_stable():
    # the recognizer adds pieces to the last word: "raj" then "rajbhog"
    assert stable_prefix(["one raj", "one rajbhog"], 2) == "one"
    assert stable_prefix(["one raj", "one rajbhog", "one rajbhog is"], 2) == "one rajbhog"


def test_a_changed_word_ends_the_stable_prefix():
    assert stable_prefix(["we went to see", "we want to see it"], 2) == "we"


def test_chinese_characters_are_words_and_spaces_are_kept_as_written():
    assert stable_prefix(["昨天", "昨天是"], 2) == "昨天"
    assert stable_prefix(["昨天是 MON", "昨天是 MONDAY"], 2) == "昨天是"
    assert stable_prefix(["昨天是 MONDAY", "昨天是 MONDAY TO"], 2) == "昨天是 MONDAY"


def test_no_drafts_with_zero():
    assert stable_prefix(["one", "one two"], 0) == ""


# ---------------------------------------------------------------- local agreement on the draft translations


def _agreed(seq, k):
    """What the viewer sees after each draft translation of one line: None = no change."""
    a = LocalAgreement(k)
    return [a.push("line", t) for t in seq]


def test_the_display_is_the_longest_common_prefix_of_the_last_k_draft_translations():
    # the first draft has nothing to agree with and is shown as it is (waiting for a second
    # one would cost a whole debounce interval); then the prefix the last two agree on
    seq = ["If you don't", "If you don't order me.", "If you don't order me either.", "You don't have to order me either."]
    assert _agreed(seq, 2) == ["If you don't", None, "If you don't order me", None]   # None: the display does not change
    # k = 3: the last three must agree
    assert _agreed(seq, 3) == ["If you don't", None, None, None]
    # k = 1 (or less): no agreement, every draft as it is
    assert _agreed(seq, 1) == seq
    assert _agreed(seq, 0) == seq


def test_the_display_is_append_only_except_when_the_agreed_prefix_itself_shrinks():
    seq = ["from", "from the day", "from the day after tomorrow", "from the day after the next", "from the week after", "from the week after next"]
    out = _agreed(seq, 2)
    assert out == ["from", None, "from the day", "from the day after", "from the", "from the week after"]
    shown = [t for t in out if t is not None]
    for before, after in zip(shown, shown[1:]):
        agreed_shrank = len(after) < len(before)
        assert after.startswith(before) or (agreed_shrank and before.startswith(after)), (before, after)
    # where it shrank, the agreed prefix of the last two drafts had shrunk itself
    assert out[4] == "from the" and seq[3].startswith("from the") and seq[4].startswith("from the") and not seq[4].startswith("from the day")


def test_an_empty_agreement_holds_the_display_and_an_unchanged_one_is_not_resent():
    a = LocalAgreement(2)
    assert a.push("x", "Key") == "Key"
    assert a.push("x", "What does it look like?") is None          # nothing in common: what is shown stays
    assert a.push("x", "What does it look like now?") == "What does it look like"
    assert a.push("x", "What does it look like now, sir?") == "What does it look like now"
    assert a.push("x", "What does it look like now, madam?") is None   # agreed on the same prefix again
    assert a.shown("x") == "What does it look like now"
    assert a.push("x", "What does it look like now, madam?") == "What does it look like now, madam?"   # two alike: all of it
    a.close("x")
    assert a.shown("x") is None
    assert a.push("x", "Key") == "Key"                               # a closed line's history is gone


def test_agreement_counts_cjk_characters_as_words_and_is_per_line():
    a = LocalAgreement(2)
    assert a.push(0, "你把钥匙") == "你把钥匙"
    assert a.push(0, "你把钥匙放在") is None                           # agreed on what is shown already
    assert a.push(1, "我会") == "我会"                                # another line, its own history
    assert a.push(0, "你把钥匙放在哪里") == "你把钥匙放在"
    a.reset()
    assert a.shown(0) is None and a.shown(1) is None


# ---------------------------------------------------------------- the session marks the stable prefix


class ScriptedSession:
    """Plays a list of (kind, text) events, one per feed() call, as utterance 0, 1, ..."""

    def __init__(self, lang, script):
        self.lang = lang
        self.script = list(script)
        self.utt = 0
        self.samples = 0

    def feed(self, pcm16: bytes) -> List[AsrEvent]:
        self.samples += len(pcm16) // 2
        if not self.script:
            return []
        kind, text = self.script.pop(0)
        if kind is None:
            return []
        ev = AsrEvent(kind, text, self.lang, 0.0, self.samples / SAMPLE_RATE, utterance_id=self.utt)
        if kind == "final":
            self.utt += 1
        return [ev]

    def flush(self):
        return []

    def close(self):
        pass


class ScriptedEngine:
    name = "sherpa-zipformer"

    def __init__(self, script):
        self.script = script
        self.sessions = []

    def supports(self, lang):
        return lang in ("en", "zh", "bn")

    def start_session(self, lang):
        self.sessions.append(ScriptedSession(lang, self.script))
        return self.sessions[-1]

    def status(self):
        return {"engine": self.name, "languages": ["en", "zh", "bn"]}

    def warmup(self, langs):
        pass


LINE = [("partial", "where"), ("partial", "where did"), ("partial", "where did you"), ("partial", "where did you put"),
        ("partial", "where did you put the"), ("partial", "where did you put the keys"),
        ("final", "where did you put the keys")]


def test_partials_carry_the_stable_prefix_and_a_new_line_starts_from_nothing():
    script = LINE + [("partial", "i"), ("partial", "i will")]
    s = StreamingASRSession(SessionConfig(source_lang="en", partial_interval_ms=0, draft_stable_partials=2),
                            {"sherpa-zipformer": ScriptedEngine(script)})
    msgs = []
    for _ in range(len(script)):
        msgs.extend(s.feed(pcm_frame()))
    partials = [m for m in msgs if m["type"] == "partial"]
    assert [m.get("stable_text", "") for m in partials] == [
        "", "where", "where did", "where did you", "where did you put", "where did you put the", "", "i"]
    assert "stable_text" not in [m for m in msgs if m["type"] == "final"][0]


def test_the_stable_prefix_is_restored_like_the_caption_is():
    s = StreamingASRSession(SessionConfig(source_lang="en", partial_interval_ms=0, draft_stable_partials=2),
                            {"sherpa-zipformer": ScriptedEngine(LINE)},
                            restore_text=lambda text, lang, final: text.capitalize() + ("." if final else ""))
    msgs = []
    for _ in range(3):
        msgs.extend(s.feed(pcm_frame()))
    assert msgs[-1]["text"] == "Where did you" and msgs[-1]["stable_text"] == "Where did"


def test_no_stable_prefix_when_drafts_are_off():
    s = StreamingASRSession(SessionConfig(source_lang="en", partial_interval_ms=0, draft_stable_partials=0),
                            {"sherpa-zipformer": ScriptedEngine(LINE)})
    msgs = []
    for _ in range(4):
        msgs.extend(s.feed(pcm_frame()))
    assert all("stable_text" not in m for m in msgs)


# ---------------------------------------------------------------- the debounce


def test_the_scheduler_translates_the_newest_prefix_at_most_once_per_debounce():
    async def scenario():
        calls = []

        async def translate(key, text, msg):
            calls.append((asyncio.get_running_loop().time(), key, text))

        sched = DraftScheduler(translate, debounce_s=0.2)
        t0 = asyncio.get_running_loop().time()
        sched.offer(0, "one", {})
        await asyncio.sleep(0.02)
        sched.offer(0, "one two", {})           # inside the debounce: waits
        await asyncio.sleep(0.02)
        sched.offer(0, "one two three", {})     # replaces the waiting one
        await asyncio.sleep(0.4)
        sched.offer(0, "one two three", {})     # nothing new: no call
        await asyncio.sleep(0.05)
        return [(round(t - t0, 2), key, text) for t, key, text in calls]

    calls = asyncio.run(scenario())
    assert [(k, text) for _, k, text in calls] == [(0, "one"), (0, "one two three")], calls
    assert calls[0][0] < 0.05 and calls[1][0] >= 0.2 - 0.01, calls


def test_a_closed_line_gets_no_more_drafts():
    async def scenario():
        calls = []

        async def translate(key, text, msg):
            calls.append((key, text))

        sched = DraftScheduler(translate, debounce_s=0.1)
        sched.offer(0, "one", {})
        await asyncio.sleep(0.01)
        sched.offer(0, "one two", {})           # waiting for the debounce
        sched.close(0)                          # the final arrived
        assert not sched.is_open(0) and sched.is_open(1)
        sched.offer(0, "one two three", {})
        sched.offer(1, "next", {})
        await asyncio.sleep(0.3)
        return calls

    assert asyncio.run(scenario()) == [(0, "one"), (1, "next")]


# ---------------------------------------------------------------- end to end over /ws/asr (fake engines)


def _run_line(app_env, monkeypatch, script, target="zh", frames=None):
    from app import asr as asr_pkg

    monkeypatch.setattr(app_env.settings.asr, "draft_debounce_ms", 0)
    if app_env.settings.asr.draft_stable_partials:
        monkeypatch.setattr(app_env.settings.asr, "draft_stable_partials", 2)
    monkeypatch.setattr(asr_pkg, "_engines", {"sherpa-zipformer": ScriptedEngine(script)})
    out = []
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs=target)) as ws:
            assert ws.receive_json()["type"] == "ready"
            import time

            for _ in range(frames or len(script)):
                ws.send_bytes(pcm_frame())
                time.sleep(0.03)                # let the draft of this partial go out, as live audio would
            while not any(m["type"] == "translation" for m in out):
                out.append(ws.receive_json())
            # whatever else is on its way
            from tests.conftest import ws_recv

            while True:
                m = ws_recv(ws, timeout=0.5)
                if m is None:
                    break
                out.append(m)
    return out


def test_a_draft_appears_before_the_endpoint_and_the_final_matches_the_full_segment_translation(app_env, monkeypatch):
    msgs = _run_line(app_env, monkeypatch, LINE)
    types = [m["type"] for m in msgs]
    drafts = [m for m in msgs if m["type"] == "draft"]
    assert drafts, types
    assert types.index("draft") < types.index("final"), types
    # drafts translate stable prefixes of the open line, growing
    sources = [d["source_text"] for d in drafts]
    assert all("where did you put the keys".startswith(s) and s for s in sources), sources
    assert sources == sorted(sources, key=len) and len(set(sources)) == len(sources), sources
    # what is sent is the part the last draft translations agree on (local agreement, k = 2):
    # a prefix of the newest translation, append-only here since the fake MT is prefix-stable
    shown = [d["translations"]["zh"]["single_line"] for d in drafts]
    for d, text in zip(drafts, shown):
        assert d["utterance_id"] == 0 and ("中[fake:en->zh] " + d["source_text"]).startswith(text) and text, d
        assert d["translations"]["zh"]["status"] == "ok"
        assert d["agreed_k"] == 2
    for before, after in zip(shown, shown[1:]):
        assert after.startswith(before), (before, after)
    # the final translation is the translation of the whole segment, whatever the drafts said
    final_tr = [m for m in msgs if m["type"] == "translation"][-1]
    assert final_tr["source_text"] == "where did you put the keys"
    assert final_tr["translations"]["zh"]["single_line"] == "中[fake:en->zh] where did you put the keys"
    # nothing of the line's draft arrives after its final caption
    assert "draft" not in types[types.index("final"):], types


def test_no_draft_when_the_target_is_the_spoken_language_or_drafts_are_off(app_env, monkeypatch):
    msgs = _run_line(app_env, monkeypatch, LINE, target="en")
    assert not [m for m in msgs if m["type"] == "draft"]
    monkeypatch.setattr(app_env.settings.asr, "draft_stable_partials", 0)
    msgs = _run_line(app_env, monkeypatch, LINE)
    assert not [m for m in msgs if m["type"] == "draft"]
    assert [m for m in msgs if m["type"] == "translation"][-1]["translations"]["zh"]["single_line"].endswith("keys")


def test_a_failing_draft_never_costs_the_final_translation(app_env, monkeypatch):
    """The draft is a convenience: an MT failure on a prefix is not shown and the line
    still gets its final translation (or the final's own error)."""
    fake = app_env.engines["fake"]
    real = fake.translate_batch_sync

    def flaky(texts, src, tgt):
        if texts[0] != "where did you put the keys":
            raise RuntimeError("draft failed on purpose")
        return real(texts, src, tgt)

    monkeypatch.setattr(fake, "translate_batch_sync", flaky)
    msgs = _run_line(app_env, monkeypatch, LINE)
    assert not [m for m in msgs if m["type"] == "draft"]
    assert not [m for m in msgs if m["type"] == "error"]
    assert [m for m in msgs if m["type"] == "translation"][-1]["translations"]["zh"]["status"] == "ok"


def test_draft_settings_defaults():
    from app.config import ASRConfig

    cfg = ASRConfig()
    assert (cfg.draft_stable_partials, cfg.draft_debounce_ms, cfg.draft_agree_k) == (3, 1500, 2)
