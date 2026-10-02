"""Nothing personal in tracked files (Phase 3 Batch B): no local usernames, home
paths, personal email domains, phone numbers or e-mail addresses (except no-reply
ones), and no subtitle text from the owner's real sessions. Runs on `git ls-files`.

The last check needs the local translation memory (backend/data/, never tracked):
it reads a copy in tmp_path and is skipped where there is none (CI)."""

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TM = REPO / "backend" / "data" / "translation_memory.db"

PATTERNS = {
    # the personal strings are split so this file passes its own check
    "local username": re.compile("is" + "amb", re.I),
    "e-mail local part": re.compile("jia" + r"ti\d", re.I),
    "school domain": re.compile("gettys" + "burg", re.I),
    "gmail": re.compile("g" + r"mail\.com", re.I),
    "Windows home path": re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}(?!<)", re.I),
    "MSYS home path": re.compile(r"/[a-z]/Users/", re.I),
    "Linux home path": re.compile(r"/home/[A-Za-z]"),
    "macOS home path": re.compile(r"(?<![\w.])/Users/[A-Za-z]"),
    "phone number": re.compile(r"(?<![\w.])(?:\+\d{1,3}[ -]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?![\w.])"),
    "e-mail address": re.compile(r"(?<![\w.%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
}
EMAIL_OK = re.compile(r"^(noreply@anthropic\.com|\d+\+[\w-]+@users\.noreply\.github\.com|[\w.+-]+@example\.(com|org))$")
# Files whose job is to hold public sample text (model sample-WAV transcripts, the
# project's own probe sentences); a TM row equal to one of these came from a test run.
SAMPLE_SOURCES = ("backend/tests/opus_samples.py", "backend/scripts/asr_input_quality.py")


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    for name in filter(None, out.decode("utf-8").split("\0")):
        path = REPO / name
        if not path.is_file():  # deleted in the working tree
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # icons
            continue


def findings(text):
    for label, rx in PATTERNS.items():
        for m in rx.finditer(text):
            if label == "e-mail address" and EMAIL_OK.match(m.group(0)):
                continue
            line = text.count("\n", 0, m.start()) + 1
            yield label, line, m.group(0)


def test_patterns_catch_what_they_should():
    """The checks themselves (strings built here so this file stays clean)."""
    home = "C:" + "\\Users\\" + "someone\\x"
    samples = [home, "/home/" + "someone/x", "/Users/" + "someone/x", "a@g" + "mail.com", "555-123-" + "4567",
               "jia" + "ti01", "Gettys" + "burg", "is" + "amb"]
    for s in samples:
        assert list(findings(s)), s
    for ok in ["noreply@anthropic.com", "186099051+SamJ12138@users.noreply.github.com", "@pytest.mark.slow",
               "C:" + "\\Users\\<name>", "v1.2.3", "2026-10-02", "127.0.0.1:8765"]:
        assert not list(findings(ok)), ok


def test_tracked_files_hold_nothing_personal():
    hits = [f"{name}:{line}: {label}: {match!r}" for name, text in tracked_files()
            for label, line, match in findings(text)]
    assert not hits, "\n".join(hits)


@pytest.mark.skipif(not TM.exists(), reason="no local translation memory (CI, fresh clone)")
def test_no_subtitle_text_from_real_sessions(tmp_path):
    for suffix in ("", "-wal"):
        if Path(str(TM) + suffix).exists():
            shutil.copy(str(TM) + suffix, tmp_path / ("tm.db" + suffix))
    con = sqlite3.connect(tmp_path / "tm.db")
    lines = set()
    for table, cols in (("translations", "source_text, translation"),
                        ("corrections", "source_text, original_translation, corrected_translation")):
        try:
            for row in con.execute(f"select {cols} from {table}"):
                lines.update(v.strip() for v in row if isinstance(v, str) and len(v.strip()) >= 12)
        except sqlite3.Error:
            pass
    con.close()
    files = dict(tracked_files())
    samples = "\n".join(files.get(s, "") for s in SAMPLE_SOURCES)
    real = [s for s in lines if s not in samples]
    hits = [f"{name}: {s[:40]!r}" for name, text in files.items() for s in real if s in text]
    assert not hits, "\n".join(hits)
