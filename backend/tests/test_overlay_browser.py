"""The overlay's geometry in a real Chromium (docs/overlay/README.md): where the subtitle
block sits against the video, and that it holds still while a line streams.

Slow: the browser harness with the real extension and a backend with real models
(scripts/e2e_extension.py --geometry). The harness pages play the English sample WAV
as a video: `--layout page` (a 640x360 player in a page column) and `--layout fill`
(the video fills the viewport, as in fullscreen). The YouTube page needs the network
and a headful browser: set ST_YOUTUBE_URL to the live test's URL to run those.
"""

import os

import pytest

from tests.conftest import has_zipformer, zipformer_dir
from tests.test_e2e_extension import PW_PYTHON, run_harness

pytestmark = [
    pytest.mark.slow,
    pytest.mark.timeout(400),
    pytest.mark.skipif(PW_PYTHON is None, reason="no Python with playwright (set ST_PLAYWRIGHT_PYTHON)"),
    pytest.mark.skipif(not has_zipformer("en"), reason="English Zipformer model not downloaded"),
]

YOUTUBE_URL = os.environ.get("ST_YOUTUBE_URL")


def geometry_run(*args):
    wav = zipformer_dir("en") / "test_wavs" / "0.wav"
    summary, proc = run_harness("--path", "audio", "--video", str(wav), "--source", "en", "--targets", "zh",
                                "--prime-audio", "--size", "960x540", "--hold", "8", "--geometry", *args)
    assert summary["ok"], (summary, proc.stderr[-2000:])
    ov = summary["overlay"]
    assert ov["with_lines"] >= 10, ov   # the overlay was read while lines were on screen
    return ov, summary


def test_on_a_normal_page_the_block_sits_below_the_video_and_never_over_it():
    ov, _ = geometry_run("--layout", "page")
    assert ov["block_intersects_video"] == 0, ov
    assert ov["block_outside_video"] == ov["with_lines"], ov
    assert ov["max_coverage"] == 0.0, ov


def test_when_the_video_fills_the_viewport_the_block_overlays_its_bottom_15_percent():
    ov, _ = geometry_run("--layout", "fill")
    assert ov["block_intersects_video"] == ov["with_lines"], ov
    assert ov["block_in_bottom_15"] == ov["with_lines"], ov
    assert ov["lines_in_bottom_15"] == ov["lines_over_video"], ov


def test_the_block_stays_clear_of_the_control_bar_when_it_is_showing():
    ov, summary = geometry_run("--layout", "fill", "--hover-controls")
    assert summary.get("controls_rect"), summary   # the harness page's bar was showing
    assert ov["controls_overlap"] == 0, ov
    assert ov["lines_over_video"] >= 10, ov


@pytest.mark.skipif(not YOUTUBE_URL, reason="set ST_YOUTUBE_URL (network, headful browser)")
@pytest.mark.parametrize("fullscreen", [False, True], ids=["normal", "fullscreen"])
def test_on_the_youtube_page(fullscreen):
    args = ["--path", "audio", "--url", YOUTUBE_URL, "--source", "auto", "--targets", "auto", "--prime-audio",
            "--size", "960x540", "--hold", "20", "--timeout", "60", "--headful", "--geometry"]
    if fullscreen:
        args.append("--fullscreen")
    summary, proc = run_harness(*args)
    if summary.get("blocked"):
        pytest.skip(f"page blocked: {summary['blocked']}")
    assert summary["ok"], (summary, proc.stderr[-2000:])
    ov = summary["overlay"]
    assert ov["with_lines"] >= 10, ov
    if fullscreen:
        assert summary.get("fullscreen") is True, summary
        assert ov["block_intersects_video"] == ov["with_lines"], ov
        assert ov["block_in_bottom_15"] == ov["with_lines"], ov
    else:
        assert ov["block_intersects_video"] == 0, ov
        assert ov["max_coverage"] == 0.0, ov
