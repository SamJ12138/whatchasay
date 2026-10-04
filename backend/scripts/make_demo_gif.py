"""Make docs/demo.gif (and optionally docs/screenshot.png) from a live run, never by hand.

1. Source clip: data/samples/demo.webm if it exists, else the public-domain NASA ScienceCasts video
   "The Zero Gravity Coffee Cup" from Wikimedia Commons (SHA-256 checked), cut to 0:09-0:39.5.
2. The browser harness (e2e_extension.py --path audio --video --record) plays it in Chromium with the
   unpacked extension and a backend on CPU (SUBTITLE_TRANSLATION__DEVICE=cpu); Playwright records the tab.
3. A window of the recording becomes the GIF with ffmpeg's two-pass palette (imageio-ffmpeg).

    python backend/scripts/make_demo_gif.py --models-root <dir with asr/ and punct/> [--screenshot]

--clip FILE [--clip-start S --clip-len S] --source LANG --targets LANGS uses another video file instead
(cut the same way when a start and length are given; the clip's license is the caller's business, NOTICE.md
lists the ones used), e.g. docs/demo-fastest.gif from the fastest clip of the benchmark in docs/latency.md:

    python backend/scripts/make_demo_gif.py --clip clips/zh-voa-norway.webm --source auto --targets auto --out docs/demo-fastest.gif

--recording FILE --start S skips steps 1-2 and converts a recording the harness already made
(e2e_extension.py --record, e.g. a --url live test; docs/live-test-youtube.md has the command):

    python backend/scripts/make_demo_gif.py --recording rec/<id>.webm --start 41.5 --seconds 8 --out docs/demo-youtube.gif

Needs a Python with playwright and imageio-ffmpeg (run it with that Python); the backend's venv runs the
backend. Prints a JSON summary (size, duration, window, harness summary, run_id).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
CLIP_URL = "https://upload.wikimedia.org/wikipedia/commons/d/dc/ScienceCasts-_The_Zero_Gravity_Coffee_Cup.webm"
CLIP_SHA256 = "6f36a1821d112bbe50798498a17dde909f9a3cee1ee41ee609ee9cc249e00a76"
CLIP_START, CLIP_LEN = 9.0, 30.5


def ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def cut(full: Path, clip: Path, start: float, length: float) -> Path:
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-i", str(full),
         "-t", str(length), "-c:v", "libvpx-vp9", "-b:v", "1500k", "-deadline", "realtime", "-cpu-used", "8",
         "-c:a", "libopus", "-b:a", "96k", str(clip)])
    return clip


def source_clip(work: Path, clip: Path | None = None, start: float | None = None, length: float | None = None) -> Path:
    """--clip: that file, cut when a start and length are given; else the NASA clip."""
    if clip is not None:
        if start is None and length is None:
            return clip
        return cut(clip, work / ("clip" + clip.suffix), start or 0.0, length or 1e9)
    own = REPO / "data" / "samples" / "demo.webm"
    if own.exists():
        return own
    full = work / "source.webm"
    req = urllib.request.Request(CLIP_URL, headers={"User-Agent": "whatchasay-demo/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(full, "wb") as f:
        f.write(r.read())
    digest = hashlib.sha256(full.read_bytes()).hexdigest()
    if digest != CLIP_SHA256:
        raise SystemExit(f"downloaded clip has SHA-256 {digest}, expected {CLIP_SHA256}")
    return cut(full, work / "clip.webm", CLIP_START, CLIP_LEN)


def to_gif(video: Path, out: Path, start: float, seconds: float, width: int, fps: int, colors: int) -> None:
    pal = out.with_suffix(".palette.png")
    vf = f"fps={fps},scale={width}:-1:flags=lanczos"
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(seconds), "-i", str(video),
         "-vf", f"{vf},palettegen=max_colors={colors}:stats_mode=diff", str(pal)])
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(seconds), "-i", str(video),
         "-i", str(pal), "-lavfi", f"{vf}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle", str(out)])
    pal.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models-root", type=Path, default=BACKEND / "data" / "models")
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "demo.gif")
    ap.add_argument("--screenshot", action="store_true", help="also write docs/screenshot.png")
    ap.add_argument("--seconds", type=float, default=12.0, help="GIF length (max 12)")
    ap.add_argument("--lead", type=float, default=3.0, help="seconds of partial captions before the first translation")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--fps", type=int, default=6)
    ap.add_argument("--colors", type=int, default=128)
    ap.add_argument("--size", default="960x540", help="browser viewport and recording size")
    ap.add_argument("--font-size", type=int, default=28)
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--recording", type=Path, help="convert this harness recording instead of making one")
    ap.add_argument("--start", type=float, help="--recording: GIF window start (s into the recording)")
    ap.add_argument("--clip", type=Path, help="play this video file instead of the NASA clip")
    ap.add_argument("--clip-start", type=float, help="--clip: cut it from here (s)")
    ap.add_argument("--clip-len", type=float, help="--clip: ...for this long (s)")
    ap.add_argument("--source", default="en", help="spoken language given to the extension (auto = detect)")
    ap.add_argument("--targets", default="zh", help="target languages (auto = English, Chinese for English speech)")
    ap.add_argument("--hold", type=float, default=22.0, help="seconds of captioning after the first translation")
    args = ap.parse_args()
    seconds = min(args.seconds, 12.0)
    if args.recording:
        if args.start is None:
            ap.error("--recording needs --start")
        to_gif(args.recording, args.out, args.start, seconds, args.width, args.fps, args.colors)
        print(json.dumps({"ok": True, "gif": str(args.out), "bytes": args.out.stat().st_size, "seconds": seconds,
                          "window_start_s": args.start, "fps": args.fps, "width": args.width,
                          "recording": args.recording.name}))
        return 0

    work = Path(tempfile.mkdtemp(prefix="st-demo-"))
    clip = source_clip(work, args.clip, args.clip_start, args.clip_len)
    rec = work / "rec"
    rec.mkdir()
    env = {**os.environ, "SUBTITLE_TRANSLATION__DEVICE": "cpu",
           "SUBTITLE_ASR__MODELS_DIR": str(args.models_root / "asr"),
           "SUBTITLE_ASR__PUNCT_DIR": str(args.models_root / "punct")}
    cmd = [sys.executable, str(BACKEND / "scripts" / "e2e_extension.py"), "--start-backend", "--port", str(args.port),
           "--path", "audio", "--source", args.source, "--targets", args.targets, "--video", str(clip), "--record", str(rec),
           "--size", args.size, "--font-size", str(args.font_size), "--hold", str(args.hold), "--timeout", "30"]
    if args.screenshot:
        cmd += ["--screenshot", str(REPO / "docs" / "screenshot.png")]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    if not summary.get("ok") or not summary.get("video"):
        print(json.dumps({"ok": False, "harness": summary, "stderr": proc.stderr[-2000:]}))
        return 1
    # start a little before the first translation, so the GIF shows words arriving, then translations
    start = max(0.0, summary["first_translation_s"] - args.lead)
    to_gif(Path(summary["video"]), args.out, start, seconds, args.width, args.fps, args.colors)
    print(json.dumps({"ok": True, "gif": str(args.out), "bytes": args.out.stat().st_size, "seconds": seconds,
                      "window_start_s": round(start, 2), "fps": args.fps, "width": args.width, "run_id": summary.get("run_id"),
                      "recording": summary["video"], "harness": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
