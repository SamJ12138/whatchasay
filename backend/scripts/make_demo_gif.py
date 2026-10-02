"""Make docs/demo.gif (and optionally docs/screenshot.png) from a live run, never by hand.

1. Source clip: data/samples/demo.webm if it exists, else the public-domain NASA ScienceCasts video
   "The Zero Gravity Coffee Cup" from Wikimedia Commons (SHA-256 checked), cut to 0:09-0:39.5.
2. The browser harness (e2e_extension.py --path audio --video --record) plays it in Chromium with the
   unpacked extension and a backend on CPU (SUBTITLE_TRANSLATION__DEVICE=cpu); Playwright records the tab.
3. A window of the recording becomes the GIF with ffmpeg's two-pass palette (imageio-ffmpeg).

    python backend/scripts/make_demo_gif.py --models-root <dir with asr/ and punct/> [--screenshot]

Needs a Python with playwright and imageio-ffmpeg (run it with that Python); the backend's venv runs the
backend. Prints a JSON summary (size, duration, window, harness summary).
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


def source_clip(work: Path) -> Path:
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
    clip = work / "clip.webm"
    run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(CLIP_START), "-i", str(full),
         "-t", str(CLIP_LEN), "-c:v", "libvpx-vp9", "-b:v", "1500k", "-deadline", "realtime", "-cpu-used", "8",
         "-c:a", "libopus", "-b:a", "96k", str(clip)])
    return clip


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
    args = ap.parse_args()
    seconds = min(args.seconds, 12.0)

    work = Path(tempfile.mkdtemp(prefix="st-demo-"))
    clip = source_clip(work)
    rec = work / "rec"
    rec.mkdir()
    env = {**os.environ, "SUBTITLE_TRANSLATION__DEVICE": "cpu",
           "SUBTITLE_ASR__MODELS_DIR": str(args.models_root / "asr"),
           "SUBTITLE_ASR__PUNCT_DIR": str(args.models_root / "punct")}
    cmd = [sys.executable, str(BACKEND / "scripts" / "e2e_extension.py"), "--start-backend", "--port", str(args.port),
           "--path", "audio", "--source", "en", "--targets", "zh", "--video", str(clip), "--record", str(rec),
           "--size", args.size, "--font-size", str(args.font_size), "--hold", "22", "--timeout", "30"]
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
                      "window_start_s": round(start, 2), "fps": args.fps, "width": args.width, "harness": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
