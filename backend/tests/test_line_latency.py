"""Per-line latency (docs/latency.md): what the viewer feels, per subtitle line.

    first_display_ms  audio time of the line's first word -> first text of the line on screen
    final_ms          audio time of the line's last word  -> final translation on screen

The backend's part: every partial / final (and the translation of a final) says at
which wall-clock time the audio of the line's first and last word reached it
(`w_first`, `w_last`, epoch seconds), so the content script, which knows when it drew
the text, can subtract. The summary is scripts/failure_report.py's."""

import importlib.util
from pathlib import Path
from typing import List

import pytest

from app.asr.engine import AsrEvent, SAMPLE_RATE
from app.asr.session import SessionConfig, StreamingASRSession
from tests.fakes import pcm_frame

FRAME_S = 0.04


class WordTimeSession:
    """Every 0.8 s of audio is one utterance: a partial once 0.4 s of it has been fed, a
    final at 0.8 s, with word times on the recognizer's own audio clock (seconds of audio
    since this recognizer started), however the audio is cut into feed() calls."""

    def __init__(self, lang):
        self.lang = lang
        self.samples = 0

    def feed(self, pcm16: bytes) -> List[AsrEvent]:
        half = int(0.4 * SAMPLE_RATE)
        before, self.samples = self.samples, self.samples + len(pcm16) // 2
        out = []
        for k in range(before // half + 1, self.samples // half + 1):  # every 0.4 s boundary crossed
            utt, start, clock = (k - 1) // 2, ((k - 1) // 2) * 0.8, k * 0.4
            if k % 2:
                out.append(AsrEvent("partial", "one two", self.lang, start, clock, utterance_id=utt,
                                    first_word_t=start + 0.2, last_word_t=start + 0.36))
            else:
                out.append(AsrEvent("final", "one two three", self.lang, start, clock, utterance_id=utt,
                                    first_word_t=start + 0.2, last_word_t=start + 0.6))
        return out

    def flush(self):
        return []

    def close(self):
        pass


class WordTimeEngine:
    name = "sherpa-zipformer"

    def __init__(self):
        self.sessions = []

    def supports(self, lang):
        return lang in ("en", "zh", "bn")

    def start_session(self, lang):
        self.sessions.append(WordTimeSession(lang))
        return self.sessions[-1]

    def status(self):
        return {}


class Clock:
    """Wall clock for the session: frame k (0-based) arrives at 1000 + 0.04 k."""

    def __init__(self):
        self.t = 1000.0 - FRAME_S

    def tick(self):
        self.t += FRAME_S

    def __call__(self):
        return self.t


def run(session, clock, frames):
    msgs = []
    for _ in range(frames):
        clock.tick()
        msgs.extend(session.feed(pcm_frame()))
    return msgs


def test_captions_say_when_the_audio_of_their_first_and_last_word_arrived():
    clock = Clock()
    s = StreamingASRSession(SessionConfig(source_lang="en", partial_interval_ms=0), {"sherpa-zipformer": WordTimeEngine()})
    s._now = clock
    msgs = run(s, clock, 40)
    partials = [m for m in msgs if m["type"] == "partial"]
    finals = [m for m in msgs if m["type"] == "final"]
    assert len(partials) == 2 and len(finals) == 2
    # utterance 0: first word at audio 0.20 s = frame 5 (arrived 1000.20), last word of the
    # partial at 0.36 s = frame 9 (1000.36), of the final at 0.60 s = frame 15 (1000.60)
    assert partials[0]["w_first"] == pytest.approx(1000.20, abs=1e-3)
    assert partials[0]["w_last"] == pytest.approx(1000.36, abs=1e-3)
    assert finals[0]["w_first"] == pytest.approx(1000.20, abs=1e-3)
    assert finals[0]["w_last"] == pytest.approx(1000.60, abs=1e-3)
    # utterance 1 starts at audio 0.80 s
    assert finals[1]["w_first"] == pytest.approx(1001.00, abs=1e-3)
    assert finals[1]["w_last"] == pytest.approx(1001.40, abs=1e-3)
    # the session's first audio frame, for "time to the first confirmed-language subtitle"
    assert all(m["w_session"] == pytest.approx(1000.0, abs=1e-3) for m in partials + finals)


def test_an_engine_without_word_times_falls_back_to_the_utterance_bounds():
    from tests.fakes import FakeASREngine

    clock = Clock()
    s = StreamingASRSession(SessionConfig(source_lang="en", partial_interval_ms=0), {"sherpa-zipformer": FakeASREngine(final_every=10)})
    s._now = clock
    msgs = run(s, clock, 10)
    final = [m for m in msgs if m["type"] == "final"][0]
    assert final["w_first"] == pytest.approx(1000.0, abs=1e-3)      # t0 = 0.0: the first frame
    assert final["w_last"] == pytest.approx(1000.36, abs=1e-3)      # t1 = 0.40 s: the end of frame 9


def test_after_a_language_switch_word_times_point_at_the_original_arrival_not_the_replay():
    """Auto-detect replays the buffered audio into the new recognizer, whose clock starts
    at zero at the first replayed sample. The words were spoken (and arrived) seconds
    before the replay: the latency has to count from then."""
    clock = Clock()
    eng = WordTimeEngine()
    s = StreamingASRSession(
        SessionConfig(source_lang="auto", allowed_langs=["en", "zh", "bn"], lid_window_s=0.4, lid_min_rms=0.001, partial_interval_ms=0),
        {"sherpa-zipformer": eng}, lid_identify=lambda audio, allowed: "bn")
    s._now = clock
    msgs = run(s, clock, 60)
    assert s.lang == "bn" and len(eng.sessions) == 2
    after = msgs[[m["type"] for m in msgs].index("reset"):]
    final = next(m for m in after if m["type"] == "final" and m["lang"] == "bn")
    # the new recognizer's clock zero is the session's first sample (all of it was buffered):
    # its first word at 0.20 s arrived at 1000.20, long before the switch at ~1000.36
    assert final["w_first"] == pytest.approx(1000.20, abs=1e-3)
    assert final["w_last"] == pytest.approx(1000.60, abs=1e-3)


def test_ws_asr_translation_repeats_the_word_times_of_its_final(app_env):
    app_env.asr.final_every = 10
    got = {"final": [], "translation": []}
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs="zh")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(10):
                ws.send_bytes(pcm_frame())
            while not got["translation"]:
                m = ws.receive_json()
                if m["type"] in got:
                    got[m["type"]].append(m)
    final, tr = got["final"][0], got["translation"][0]
    assert isinstance(final["w_first"], float) and final["w_first"] <= final["w_last"]
    assert (tr["w_first"], tr["w_last"]) == (final["w_first"], final["w_last"])


# ---------------------------------------------------------------- failure_report summary

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "failure_report.py"
_spec = importlib.util.spec_from_file_location("failure_report", SCRIPT)
fr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fr)


def _line(sid, first, final, first_tr=None, **ctx):
    return {"layer": "extension", "stage": "line_latency", "event": "success", "session_id": sid,
            "context": {"kind": "line", "first_display_ms": first, "final_ms": final, "first_translation_ms": first_tr, **ctx}}


def test_failure_report_summarises_line_latency_per_run_and_per_session():
    recs = [
        _line("s1", 400, 900, 1500), _line("s1", 600, 1100, 1700), _line("s1", 800, 1300, 2100),
        _line("s2", 1000, 2000, 2500),
        {"layer": "extension", "stage": "line_latency", "event": "success", "session_id": "s1",
         "context": {"kind": "first_confirmed", "since_session_ms": 5300, "lang": "bn"}},
        {"layer": "backend", "stage": "translate", "event": "success", "session_id": "s1", "duration_ms": 40, "context": {}},
    ]
    out = fr.line_latency(recs)
    assert out["lines"] == 4
    assert out["first_display_ms"] == {"n": 4, "p50": 600, "p95": 1000}
    assert out["final_ms"] == {"n": 4, "p50": 1100, "p95": 2000}
    assert out["first_translation_ms"] == {"n": 4, "p50": 1700, "p95": 2500}
    assert out["first_confirmed_ms"] == [5300]
    assert out["sessions"]["s1"]["lines"] == 3 and out["sessions"]["s1"]["first_display_ms"]["p50"] == 600
    assert out["sessions"]["s1"]["first_confirmed_ms"] == 5300
    assert out["sessions"]["s2"]["final_ms"]["p50"] == 2000


def test_failure_report_line_latency_is_empty_without_records():
    out = fr.line_latency([{"layer": "backend", "stage": "translate", "event": "success", "context": {}}])
    assert out["lines"] == 0 and out["first_display_ms"]["n"] == 0


# ---------------------------------------------------------------- the table script (docs/latency.md)

_tspec = importlib.util.spec_from_file_location("line_latency_table", SCRIPT.with_name("line_latency_table.py"))
lt = importlib.util.module_from_spec(_tspec)
_tspec.loader.exec_module(lt)


def test_table_row_and_run_log_stats(tmp_path, monkeypatch):
    import json

    log = tmp_path / "run_R1.jsonl"
    recs = [
        {"stage": "segment", "event": "success", "context": {"lang": "en", "audio_s": 1.5}},   # the provisional recognizer's
        {"stage": "segment", "event": "success", "context": {"lang": "bn", "audio_s": 2.08}},
        {"stage": "segment", "event": "success", "context": {"lang": "bn", "audio_s": 10.24}},
        {"stage": "translate", "event": "success", "duration_ms": 40.0, "context": {}},
        {"stage": "translate", "event": "success", "duration_ms": 120.0, "context": {}},
    ]
    log.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    monkeypatch.setenv("SUBTITLE_OBS_DIR", str(tmp_path))
    stats = lt.run_log_stats("R1", 20.0, "bn")
    assert stats == {"segments_s": [2.08, 10.24], "translate_calls": 2, "translate_calls_per_s": 0.1,
                     "translate_p50_ms": 40.0, "translate_p95_ms": 120.0}
    summary = {"ok": True, "run_id": "R1", "detected_lang": "bn", "run_log": stats,
               "line_latency": {"lines": 3, "lang": ["en", "bn", "bn"], "first_display_ms": [300, 2691, 741],
                                "final_ms": [900, 380, 690], "first_translation_ms": [None, 7900, 9810],
                                "first_confirmed_after_play_s": 5.54}}
    r = lt.row("clip", summary)
    assert r["lines"] == 2   # the provisional English recognizer's line is not a subtitle line
    assert (r["first_display_p50_ms"], r["first_display_max_ms"]) == (741, 2691)
    assert (r["first_translation_p50_ms"], r["final_p50_ms"], r["first_confirmed_s"]) == (7900, 380, 5.54)
    md = lt.markdown([r])
    assert "`R1`" in md and "| 0.74 / 2.69 |" in md and "| 7.90 / 9.81 |" in md and "2.08, 10.24" in md
