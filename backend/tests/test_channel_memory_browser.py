"""The channel memory of the language prior in a real Chromium on YouTube (docs/page-prior.md;
docs/latency.md, "Channel memory on a second visit"): one channel opened twice in one browser
profile (scripts/e2e_extension.py --profile keeps the profile and the extension's storage
between runs).

The video is the cold case of docs/latency.md: a Bengali vlog whose title is romanised and whose
YouTube caption language is English, so its page alone says English (0.94). The first visit is
that row as measured (empty memory); after language ID confirmed Bengali there, the second visit
must start in the remembered Bengali and confirm it without a switch, as fast as on the pages
whose prior is right. Both visits play the same stretch (from 0:30), so the memory is the only
difference. Not another video of the channel for the first visit: on 9 of 10 stretches of its
other videos whisper-tiny confirmed English or Mandarin (docs/latency.md), so the memory would
learn the wrong language. What the first visit teaches is whisper-tiny's answer, which varies on
this stretch too, so the second visit is also tested on its own, with the memory a right first
visit leaves.

Slow, network, headful browser: set ST_YOUTUBE_CHANNEL_TESTS=1 to run.
"""

import json
import os

import pytest

from app.asr.session import SessionConfig
from tests.conftest import has_zipformer
from tests.test_e2e_extension import PW_PYTHON, run_harness

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(700),
    pytest.mark.skipif(PW_PYTHON is None, reason="no Python with playwright (set ST_PLAYWRIGHT_PYTHON)"),
    pytest.mark.skipif(not (has_zipformer("en") and has_zipformer("bn")), reason="English and Bengali models needed"),
    pytest.mark.skipif(not os.environ.get("ST_YOUTUBE_CHANNEL_TESTS"),
                       reason="set ST_YOUTUBE_CHANNEL_TESTS=1 (network, headful browser)"),
]

KEY = "yt:UCjvVSB0qKSumhKf2EryI5VQ"  # the channel's memory key (Bidisha Das, Bengali vlogs)
COLD, START = "https://www.youtube.com/watch?v=zTC-FeZtMSE", 30
THRESHOLD = SessionConfig().lid_prior_threshold
# the slowest run of the right-prior rows of docs/latency.md (VOA Helix: whisper-tiny's first
# answers there were under the floor too); those decided at the first attempt took 1.28-1.91 s
RIGHT_PRIOR_MAX_S = 2.74


def visit(profile, *extra):
    summary, proc = run_harness("--path", "audio", "--url", COLD, "--start-at", str(START), "--source", "auto",
                                "--targets", "auto", "--hold", "6", "--timeout", "60", "--headful", "--size", "960x540",
                                "--profile", str(profile), *extra)
    if summary.get("blocked"):
        pytest.skip(f"page blocked: {summary['blocked']}")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    return summary


def needs_bengali_confirmed(summary):
    """The first visit teaches the memory what language ID confirmed; on this stretch that was
    Bengali in 5 of 6 runs (docs/latency.md, cold case). Another answer is language ID's miss,
    not the memory's."""
    confirmed = summary["language_prior"]["confirmed"]
    if confirmed != "bn":
        pytest.skip(f"language ID confirmed {confirmed!r} on the first visit (whisper-tiny's miss)")


def test_the_remembered_language_wins_on_a_second_visit(tmp_path):
    profile = tmp_path / "profile"
    first = visit(profile)
    p1 = first["language_prior"]
    assert not p1["remembered"] and p1["favoured"] == "en", p1  # the page alone: English
    needs_bengali_confirmed(first)
    assert first["language_memory"][KEY]["lang"] == "bn", first["language_memory"]

    second = visit(profile)
    p2 = second["language_prior"]
    assert p2["remembered"] is True, p2
    assert {"captionAsr:en", "pageScript:en", "memory:bn"} <= set(p2["votes"]), p2
    assert p2["favoured"] == "bn" and p2["prior"]["bn"] >= THRESHOLD, p2
    # the memory's language holds: confirmed without a switch (at the first attempt unless
    # whisper-tiny's first answer is under the floor, as on the right-prior pages)
    assert (p2["confirmed"], p2["switched"]) == ("bn", False), p2
    t = second["line_latency"]["first_confirmed_after_play_s"]
    assert t is not None and t <= RIGHT_PRIOR_MAX_S, second["line_latency"]
    assert second["language_memory"][KEY]["hits"] == 2, second["language_memory"]


def test_a_second_visit_after_one_right_confirmation(tmp_path):
    """The second visit alone, with what a first visit that confirmed Bengali leaves (written
    with --memory: on this stretch whisper-tiny's own first-visit answer varies, Bengali 5 of 6
    runs on 2026-10-04, Mandarin in 3 of 6 on 2026-10-05)."""
    seed = {KEY: {"lang": "bn", "misses": 0, "hits": 1}}
    s = visit(tmp_path / "profile", "--memory", json.dumps(seed))
    p = s["language_prior"]
    assert {"captionAsr:en", "pageScript:en", "memory:bn"} <= set(p["votes"]), p
    assert p["favoured"] == "bn" and p["prior"]["bn"] >= THRESHOLD, p
    assert (p["confirmed"], p["switched"]) == ("bn", False), p
    t = s["line_latency"]["first_confirmed_after_play_s"]
    assert t is not None and t <= RIGHT_PRIOR_MAX_S, s["line_latency"]


def test_a_memory_wrong_once_drops_under_the_threshold(tmp_path):
    # an earlier visit left English for the channel; the speech is Bengali
    seed = {KEY: {"lang": "en", "misses": 0, "hits": 3}}
    s = visit(tmp_path / "profile", "--memory", json.dumps(seed))
    p = s["language_prior"]
    assert p["remembered"] is True and "memory:en" in p["votes"], p
    needs_bengali_confirmed(s)
    m = s["language_memory"][KEY]
    # language ID's answer replaces the remembered language and the entry keeps the miss
    assert (m["lang"], m["misses"]) == ("bn", 1), m
    # once wrong, the memory on its own no longer reaches the threshold
    assert max(m["alone"].values()) < THRESHOLD, m
