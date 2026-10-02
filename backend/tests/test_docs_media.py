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
