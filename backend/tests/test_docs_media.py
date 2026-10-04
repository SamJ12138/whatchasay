"""The README's pictures exist and the demo GIF stays within its limits (Phase 3 Batch F):
at most 8 MB and 12 s, and every relative link in the README points at a tracked file."""

import re
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = (REPO / "README.md").read_text(encoding="utf-8")
GIF = REPO / "docs" / "demo.gif"


def gif_seconds(data: bytes) -> float:
    """Sum of the frame delays, walking the GIF's blocks (delays are in centiseconds)."""

    def skip_sub_blocks(i: int) -> int:
        while data[i]:
            i += data[i] + 1
        return i + 1

    flags = data[10]
    i = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0)  # header + screen descriptor + global table
    total = 0
    while i < len(data):
        block = data[i]
        if block == 0x3B:  # trailer
            break
        if block == 0x21:  # extension
            if data[i + 1] == 0xF9:  # graphic control: size 4, packed, delay (u16), transparent index
                total += struct.unpack("<H", data[i + 4:i + 6])[0]
            i = skip_sub_blocks(i + 2)
        elif block == 0x2C:  # image descriptor (+ local colour table) + LZW code size + data
            lflags = data[i + 9]
            i += 10 + (3 * 2 ** ((lflags & 7) + 1) if lflags & 0x80 else 0)
            i = skip_sub_blocks(i + 1)
        else:
            raise ValueError(f"unexpected GIF block 0x{block:02x} at {i}")
    return total / 100


def test_readme_shows_the_demo_gif_and_the_screenshot():
    assert "](docs/demo.gif)" in README
    assert "](docs/screenshot.png)" in README
    assert "DEMO-GIF" not in README and "Demo GIF coming" not in README


def test_demo_gif_size_and_length():
    data = GIF.read_bytes()
    assert data[:6] in (b"GIF87a", b"GIF89a")
    assert len(data) <= 8 * 1024 * 1024, len(data)
    assert 0 < gif_seconds(data) <= 12.0, gif_seconds(data)


OPENING = (
    "I built this for couples who do not share a first language, because my girlfriend watches videos I cannot "
    "follow and I watch ones she cannot, and most of them either have no subtitles or have subtitles that are wrong "
    "in ways that matter. whatchasay listens to whatever is playing in a Chrome tab and lays live translated "
    "subtitles over it, so the two of you can sit on the same couch, watch the same thing, and laugh at the same "
    "moment instead of one person explaining the joke to the other afterwards. It runs entirely on your own "
    "machine, and nothing you watch leaves your laptop unless you decide it should."
)


def paragraphs(text: str) -> list[str]:
    """Prose paragraphs of the README (badges, headings and images skipped), whitespace collapsed."""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text)]
    return [p for p in paras if p and not p.startswith(("#", "[![", "!["))]


def test_readme_opens_with_the_owner_paragraph_verbatim():
    paras = paragraphs(README)
    assert paras[0] == OPENING, paras[0]
    # its last sentence is not said twice in the paragraph after it
    assert "stay on your machine" not in paras[1], paras[1]


YT_GIF = REPO / "docs" / "demo-youtube.gif"
FASTEST_GIF = REPO / "docs" / "demo-fastest.gif"


def test_fastest_gif_sits_right_under_the_opening_with_its_caption():
    """The headline GIF is the fastest clip of the public-clip benchmark (docs/latency.md): its
    caption names the clip and its license, says it ran on CPU, that it is the fastest of the
    clips in the table, and links the table."""
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", README) if b.strip()]
    i = blocks.index(OPENING)
    assert re.fullmatch(r"!\[[^\]]+\]\(docs/demo-fastest\.gif\)", blocks[i + 1]), blocks[i + 1]
    caption = blocks[i + 2]
    for part in ("CPU", "fastest of the six", "docs/latency.md", "commons.wikimedia.org", "run `2026"):
        assert part in caption, (part, caption)
    assert "public domain" in caption or "CC BY" in caption, caption


def test_fastest_gif_size_and_length():
    data = FASTEST_GIF.read_bytes()
    assert data[:6] in (b"GIF87a", b"GIF89a")
    assert len(data) <= 5 * 1024 * 1024, len(data)
    assert 5.0 <= gif_seconds(data) <= 10.0, gif_seconds(data)


def test_youtube_gif_moved_to_using_it_with_the_rajbhog_case():
    """The Bengali film-scene GIF sits in "Using it", right after the glossary paragraph whose
    rajbhog example comes from that scene, with its credit."""
    using = README[README.index("## Using it"):README.index("## Optional extras")]
    glossary = using.index("**The glossary.**")
    remembered = using.index("**What is remembered.**")
    gif = using.index("](docs/demo-youtube.gif)")
    assert glossary < gif < remembered
    assert README.count("docs/demo-youtube.gif") == 1
    caption = " ".join(using[gif:remembered].split())
    for part in ("rajbhog", "Ora Char Jon", "Bengali Movies with English Subtitle", "https://www.youtube.com/watch?v=-tpVpbIxFmI",
                 "run on CPU"):
        assert part in caption, (part, caption)


def test_nasa_gif_moved_to_how_it_works():
    how = README.index("## How it works")
    after = README.index("\n## ", how + 1)
    assert README.index("](docs/demo.gif)") in range(how, after)


def test_youtube_gif_size_and_length():
    data = YT_GIF.read_bytes()
    assert data[:6] in (b"GIF87a", b"GIF89a")
    assert len(data) <= 5 * 1024 * 1024, len(data)
    assert 5.0 <= gif_seconds(data) <= 10.0, gif_seconds(data)


def test_live_youtube_test_record_has_its_numbers_and_at_most_five_lines():
    doc = (REPO / "docs" / "live-test-youtube.md").read_text(encoding="utf-8")
    for field in ("run_id", "Detected language", "Time to first subtitle", "p50", "p95", "youtube.com/watch?v=-tpVpbIxFmI"):
        assert field in doc, field
    assert re.search(r"run_\d{8}T\d{6}-[0-9a-f]{6}", doc)
    # the translated lines are a numbered table; never more than five (no transcript)
    rows = re.findall(r"^\| [1-9] \|", doc, re.M)
    assert 1 <= len(rows) <= 5, rows


def test_relative_links_in_the_readme_exist():
    links = re.findall(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)", README)
    missing = [l for l in links if not (REPO / l.split("#")[0]).exists()]
    assert links and not missing, missing


def test_using_it_explains_the_glossary_with_the_rajbhog_case_and_the_limitation_line_is_gone():
    """"Using it": corrections point at the glossary for a word that keeps coming out wrong;
    the glossary paragraph says what a term is, both ways to add one, that a running session
    uses it on its next line, and gives the live test's rajbhog case as the example. The old
    "a term-level glossary is planned" limitation line is gone; the full case (every pass of
    the recognizer, what was typed, the reruns) stays in docs/live-test-youtube.md."""
    using = " ".join(README[README.index("## Using it"):README.index("## Optional extras")].split())
    corrections = using[using.index("**Corrections.**"):using.index("**The glossary.**")]
    assert "teach the glossary" in corrections
    assert "glossary is planned" not in README and "There is no glossary" not in README
    glossary = using[using.index("**The glossary.**"):using.index("**What is remembered.**")]
    for part in ("correct spelling", "heard as", "Settings → Glossary", "Alt+E", "next line", "রাজভোগ", "রাজবুক",
                 "rajbhog", "A royal book.", "3 of 3"):
        assert part in glossary, (part, glossary)
    assert "in live captions and in caption mode alike" not in README
    doc = (REPO / "docs" / "live-test-youtube.md").read_text(encoding="utf-8")
    for real in ("রাজভোগ", "রাজবুক", "A Royal Book Shower", "Show me a rajbhog.", "was not fixed"):
        assert real in doc, real



def test_how_it_works_describes_the_streaming_design_with_the_latency_table():
    """Latency Batch 4: one paragraph on how a line reaches the screen while it is still being
    spoken, and the per-line latency table, before and after, with the details in docs/latency.md."""
    how = README[README.index("## How it works"):README.index("## Configuration")]
    flat = " ".join(how.split())
    for part in ("partial", "stable", "draft", "in progress", "docs/latency.md", "First translated text",
                 "First confirmed-language subtitle", "non-append"):
        assert part in flat, part
    live = [line for line in how.splitlines() if line.startswith("| Live clip")]
    assert len(live) == 1 and live[0].count("->") >= 2, live     # before -> after, per column
    assert "20261002T110447-172f05" not in README                # the pre-streaming table is gone
    assert "sentence end after 0.6 s of silence" not in README


def test_latency_doc_has_the_baseline_the_before_after_tables_and_the_flicker_numbers():
    doc = (REPO / "docs" / "latency.md").read_text(encoding="utf-8")
    for part in ("first_display_ms", "final_ms", "## Baseline", "## Before and after", "Non-append revisions per line",
                 "Translate calls", "Where quality dropped", "--prime-audio"):
        assert part in doc, part
    after = doc[doc.index("## Before and after"):]
    assert len(set(re.findall(r"`(\d{8}T\d{6}-[0-9a-f]{6})`", after))) >= 24      # 4 clips x 3 runs x before / after


def test_latency_doc_benchmark_has_six_public_clips_three_runs_each_with_speech_rate():
    """The public-clip benchmark: two licensed clips per spoken language, three browser runs
    each with their run_ids, the clips' speech rate and pauses next to the latencies, every
    clip credited in NOTICE.md, and the fastest clip named (the headline GIF)."""
    doc = (REPO / "docs" / "latency.md").read_text(encoding="utf-8")
    bench = doc[doc.index("## Benchmark across public clips"):]
    assert len(set(re.findall(r"`(\d{8}T\d{6}-[0-9a-f]{6})`", bench))) >= 18      # 6 clips x 3 runs
    for part in ("Words (zh: characters) / min", "Median pause", "Non-append revisions per line", "NOTICE.md", "demo-fastest.gif",
                 "Detected", "First translation p50"):
        assert part in bench, part
    notice = (REPO / "NOTICE.md").read_text(encoding="utf-8")
    for clip in ("Zero Gravity Coffee Cup", "Helix", "挪威", "台风", "তরিকুল", "Sanjoy"):
        assert clip in notice, clip


def test_a9_is_closed_as_a_model_limit_with_the_chunk_number():
    """A9 remainder: the Bengali recognizer ends utterances on its 0.64 s decode-chunk
    boundary and sherpa-onnx offers no smaller-chunk export of that model (one Bengali
    streaming model in its catalogue, one ONNX export, no checkpoint). The README's
    limitations and observations A9 say so, with the number, and A9 is closed."""
    limits = README[README.index("## Status and limitations"):README.index("## License")]
    bengali = [b for b in limits.split("\n- ") if "0.64" in b]
    assert len(bengali) == 1, bengali
    for part in ("0.64", "0.32", "decode", "no smaller-chunk export"):
        assert part in bengali[0], (part, bengali[0])
    obs = (REPO / "docs" / "observations.md").read_text(encoding="utf-8")
    a9 = next(line for line in obs.splitlines() if line.startswith("| A9 |"))
    assert "closed" in a9 and "model limit" in a9 and "0.64" in a9 and "alphacep/vosk-model-small-streaming-bn" in a9, a9
    assert "Fast dialogue with pauses under half a second still runs two sentences into one line" not in README
