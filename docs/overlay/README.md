# The overlay: how much of the video it covers, and where it sits

The subtitle overlay (the source line plus the translation line) covered too much of the video, and streaming
partials made the source line grow while playing. This folder holds the screenshots and the numbers before and
after the overlay work (2026-10-02).

## How it is measured

`backend/scripts/e2e_extension.py --geometry` (with `--shots DIR --shot-name NAME`) runs the real extension in
Chromium and, every 250 ms while captions run, reads the overlay through its closed shadow root with CDP box
models: every subtitle line's border box, the subtitle block's box, and the video element's rectangle.
**Coverage** is the part of the video's area under the caption lines' boxes (the union of the boxes, clipped to
the video; notices and the language label are not counted). Three moments get a screenshot each, the first sample
that qualifies:

| Moment | What qualifies |
|---|---|
| idle | the first sample at least 0.3 s after the video started to play (nothing recognised yet, or the first dimmed words) |
| partial | a line in progress (the source line still being recognised, or a draft translation) of at least 20 characters, with no final translation on screen |
| final | the first final translation on screen (with the source line, when it is on) |

The coverage of each moment is read again right after its screenshot, so the number is what the picture shows.
"Max" is the largest coverage seen in any sample of the run.

Two clips, two layouts each, 960x540 viewport, everything on the CPU:

- **NASA clip** (`make_demo_gif.py`'s public-domain ScienceCasts excerpt, English speech, target Chinese, spoken
  language set to English): the harness's own pages. *Fullscreen* = `--layout fill`, the video fills the viewport,
  as it does in fullscreen. *Normal* = `--layout page`, a 640x360 player in a page column with a header above and
  text below, as on most sites.
- **YouTube** (the live test's film scene, `docs/live-test-youtube.md`, Bengali speech, Auto-detect, target
  English; `--url`, headful): *Normal* = the watch page as it opens; *Fullscreen* = the player put into fullscreen
  with YouTube's `f` key (`--fullscreen`; `document.fullscreenElement` set).

## Before (commit `f7e93d1`)

The overlay was a column of full-width bars fixed at 15 % from the bottom of the *viewport*, whatever the video's
size and place: on a normal page it sat over the lower part of the picture and spilled onto the page below it, and
the source line (the recognizer's partial result) was a bar that grew with every word.

| Clip, layout | run_id | idle | partial | final | max in the run | Screenshots |
|---|---|---|---|---|---|---|
| NASA, fullscreen | `20261002T183246-f424d5` | 0.0 % | 2.8 % | 11.0 % | 20.1 % | `before-nasa-fullscreen-{idle,partial,final}.png` |
| NASA, normal | `20261002T183346-90c440` | 0.0 % | 6.4 % | 19.1 % | 38.1 % | `before-nasa-normal-*.png` |
| YouTube, normal | `20261002T183439-177a62` | 0.0 % | 3.9 % | 7.0 % | 36.5 % | `before-youtube-normal-*.png` |
| YouTube, fullscreen | `20261002T183534-8742a0` | 0.8 % | 1.9 % | 3.3 % | 18.7 % | `before-youtube-fullscreen-*.png` |

Placement, before: in all four runs the block intersected the video in every sample that had a line (95 of 95
on the YouTube page, 95 of 95 on the NASA page), and was never inside the bottom 15 % of the video in the
fullscreen runs (0 of 93 and 0 of 95 samples): it was fixed at 15 % of the *viewport*, so in fullscreen it floated
above the bottom 15 % band, and on a normal page it hung below the player.

![Before: the NASA clip on a normal page, three bars over the lower picture and the page text](before-nasa-normal-final.png)
