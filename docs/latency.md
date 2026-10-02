# Latency: what the viewer feels

Per subtitle line, measured where the text is drawn (the content script), not inside the backend.

## Definitions

| Name | From | To |
|---|---|---|
| `first_display_ms` | the audio of the line's **first** word reaching the backend | the first time any text of that line is on screen (source text, or a draft translation) |
| `final_ms` | the audio of the line's **last** word reaching the backend | the final translation on screen (every target; a line with nothing to translate: its final caption) |
| `first_translation_ms` | the audio of the line's first word | the first translated text of that line on screen |
| first confirmed subtitle | the video starting to play (harness) / the session's first audio frame (`since_session_ms`) | the first subtitle in a confirmed language on screen (Auto-detect) |

`first_translation_ms` is not in the brief's two; it is logged because it is the wait of a viewer who cannot read
the spoken language, and the first two do not show it.

**How it is measured.** The recognizer reports a timestamp for every token. `asr/session.py` keeps the wall-clock
arrival time of every audio frame and puts on each partial, final and translation message the arrival time of the
audio of the line's first and last token (`w_first`, `w_last`, epoch seconds) and of the session's first frame
(`w_session`). The content script runs on the same machine and clock; when it draws text it subtracts
(`content-scripts/line-latency.js`) and logs one `line_latency` record per line (`kind=line`) and one per session
(`kind=first_confirmed`). `backend/scripts/failure_report.py` section 6 summarises them per run and per session.
After a language switch the audio is replayed into the new recognizer; the times still point at the original
arrival of that audio, so the wait caused by detection is counted.

**What is not in the numbers.** Tab capture, resampling and the socket hop before the backend (the audio's arrival
at the backend is the zero point), and the browser's paint after the DOM update (under one frame). A token's
timestamp is the frame in which the recognizer emitted it, which is the word's position in the audio to within a
few tens of milliseconds.

**How the tables are produced.** `backend/scripts/line_latency_table.py` plays each clip three times through the
browser harness (`e2e_extension.py --path audio --video CLIP --source auto --targets auto`): the real extension in
Chromium, tab capture of a page playing the clip, a backend started for each run (so each run has its own `run_id`
and run log), translation on the CPU, target English (Chinese when the speech is English). Lines written by the
provisional English recognizer before the language was confirmed, and discarded at the switch, are not counted.
Machine: Windows 11, Intel i9-13900H, CPU only.

Clips: the three sample WAVs are `test_wavs/0.wav` of the English, Mandarin and Bengali recognizer models (6.6 s,
10.1 s and 7.0 s; one to three sentences). The live clip is the first 20 s of the film scene of
`docs/live-test-youtube.md`, recorded locally from the playing video (`ST_LID_CLIP_WAV`; not in the repository).

## Baseline (2026-10-02, commit `fb4fc2f` + the measurement itself, before any change to the pipeline)

One row per run; the `run_id` names the backend's run log (`backend/logs/run_<run_id>.jsonl`).

| Clip | run_id | Lines | First display p50 / max (s) | First translation p50 / max (s) | Final p50 / max (s) | First confirmed subtitle (s) | Segments (s) | Translate calls (per s) | Translate p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|---|---|
| English sample | `20261002T155834-2f9eb1` | 1 | 0.48 / 0.48 | 6.62 / 6.62 | 0.98 / 0.98 | 4.16 | 7.2 | 1 (0.15) | 70.9 / 70.9 |
| English sample | `20261002T155909-40ac32` | 1 | 0.33 / 0.33 | 6.77 / 6.77 | 0.93 / 0.93 | 4.15 | 7.2 | 1 (0.15) | 58.6 / 58.6 |
| English sample | `20261002T155942-6c73d9` | 1 | 0.29 / 0.29 | 6.73 / 6.73 | 0.97 / 0.97 | 3.98 | 7.2 | 1 (0.15) | 60.2 / 60.2 |
| Mandarin sample | `20261002T160016-a5a8f6` | 3 | 0.39 / 5.04 | 3.35 / 5.14 | 0.86 / 1.50 | 5.95 | 5.24, 3.84, 1.28 | 3 (0.3) | 39.9 / 43.1 |
| Mandarin sample | `20261002T160050-b39a66` | 3 | 0.38 / 5.09 | 2.29 / 6.18 | 0.85 / 0.86 | 6.02 | 6.84, 2.24, 1.28 | 3 (0.3) | 30.7 / 42.5 |
| Mandarin sample | `20261002T160126-21c4ed` | 3 | 0.39 / 5.05 | 2.30 / 6.18 | 0.86 / 0.86 | 5.99 | 6.84, 2.24, 1.28 | 3 (0.3) | 32.1 / 40.7 |
| Bengali sample | `20261002T160202-082f05` | 1 | 3.75 / 3.75 | 7.87 / 7.87 | 1.15 / 1.15 | 4.04 | 7.84 | 1 (0.14) | 89.8 / 89.8 |
| Bengali sample | `20261002T160236-79d459` | 1 | 3.90 / 3.90 | 7.86 / 7.86 | 1.09 / 1.09 | 4.16 | 7.84 | 1 (0.14) | 70.9 / 70.9 |
| Bengali sample | `20261002T160310-39de05` | 1 | 3.85 / 3.85 | 7.86 / 7.86 | 1.14 / 1.14 | 4.17 | 7.84 | 1 (0.14) | 70.5 / 70.5 |
| Live clip (Bengali film scene) | `20261002T160344-79384f` | 2 | 0.74 / 2.73 | 7.87 / 9.81 | 0.35 / 0.69 | 5.58 | 10.4, 10.24 | 2 (0.1) | 87.5 / 106.3 |
| Live clip (Bengali film scene) | `20261002T160433-3195ce` | 2 | 0.74 / 2.62 | 7.91 / 9.81 | 0.39 / 0.69 | 5.47 | 10.4, 10.24 | 2 (0.1) | 100.6 / 127.1 |
| Live clip (Bengali film scene) | `20261002T160523-254767` | 2 | 0.75 / 5.12 | 9.81 / 10.39 | 0.31 / 0.69 | 5.43 | 10.4, 10.24 | 2 (0.1) | 90.2 / 107.1 |

"Segments" are the lengths of the finals in the detected language (`segment` records, `audio_s`). "Translate calls"
counts the backend's `translate` records and divides by the clip's length.

What the baseline says:

- **The source text is not late.** Once the language is known, the first text of a line is on screen 0.3-0.75 s
  after its first word (English 0.29-0.48 s, Mandarin 0.38-0.39 s, Bengali 0.74-0.75 s): the recognizer's partial
  results already reach the overlay, drawn in italics as the line in progress. The recognizers decode in chunks
  (English and Mandarin 0.32 s, Bengali 0.64 s), which is most of that wait.
- **The translation is what waits for the whole sentence.** The first translated text arrives 6.6-6.8 s after the
  first word on the English sample (one 6.6 s sentence), 7.9 s on the Bengali sample and 7.9-10.4 s on the live
  clip, whose two lines are 10.4 s and 10.2 s long (observations A9). Translating itself takes 31-101 ms (p50).
- **After the last word** the final translation is up in 0.3-1.15 s (one Mandarin line: 1.5 s): the endpoint's 0.6 s of silence (seen only on
  decode-chunk boundaries) plus one translation. The live clip's 0.3-0.4 s lines were cut by the 10 s limit, not by
  a pause.
- **With Auto-detect the first lines wait for language ID.** On Mandarin and Bengali speech nothing in the right
  language can be shown before detection confirms: first confirmed subtitle 4.0-4.2 s (samples that start with
  speech), 6.0 s (Mandarin sample), 5.4-5.6 s (live clip), so the first line's first display is 2.6-5.1 s.
  English speech is recognised by the provisional recognizer from the start (first text at 0.3-0.5 s, dimmed) and
  undimmed at 4.0-4.2 s.

## Batch 1: bounded segments (2026-10-02)

Partial results were already streamed, so this batch did not change `first_display_ms`; it changed how long a
line stays open, which is what the translation waits for.

**What ends a line now** (`asr/sherpa_engine.py`, `SegmentRules`; settings `SUBTITLE_ASR__*`):

| Setting | Default | Why this value |
|---|---|---|
| `RULE2_MIN_TRAILING_SILENCE` (the silence threshold) | 0.6 s | unchanged. The recognizer only checks it on decode-chunk boundaries (0.64 s for Bengali), so it misses most pauses in dialogue |
| `SPLIT_GAP_S` | 0.5 s | a line also ends before a word whose first token comes this long after the previous token. On the live clip the gaps between sentences are 0.56-0.64 s and the gaps inside sentences 0.40-0.48 s (a hesitation, a breath), so 0.4 s (the first candidate, observations A9) made one- and two-word lines and 0.5 s cuts exactly at the sentences |
| `SPLIT_GAP_RATIO` | 2.5 | the pause must also be 2.5 times the line's median gap between tokens. Without it the slow code-switched Mandarin sample ("…THE DAY AFTER / TOMORROW", a word every 0.4-0.7 s) was cut inside a sentence; fast speech is not affected (median gap 0.12-0.2 s) |
| `SPLIT_MIN_PIECE_S` | 2.0 s | no pause cut before the line is this long: a hesitation after the first words ("কী … মশায়") stays in its sentence. 2.5 s merged two of the clip's sentences |
| `MAX_SEGMENT_S` | 6.0 s | the longest line: past it the line is cut at its widest gap between words (one with `SPLIT_MIN_PIECE_S` before it if there is one), never inside a word. About two subtitle rows of speech; the English sample sentence (5.8 s) stays whole |
| `MAX_SEGMENT_TOKENS` | 48 | the same limit in recognizer tokens (about 6-7 per second of speech), for speech too fast for the time limit to catch |
| `RULE3_MIN_UTTERANCE_LENGTH` | 30 s (was 10) | the recognizer's own hard reset, which cuts inside a word ("…যে রাজব / গাবে না…"); now only a backstop |

The cuts do not reset the recognizer: the tokens before the cut become a final, the ones after it the next line, so
the recognizer keeps its context and no audio is decoded twice. With Auto-detect, the audio buffered during
detection is replayed into the new recognizer in 40 ms frames instead of one block, so sentences inside it end where
they end (and the frame that triggered detection is no longer fed twice).

**Segment length distribution** (recognizer alone, 40 ms frames, the same audio through the old rules and the new;
length = first word to last word of each final; `tests/test_segmentation_real.py` holds the first three rows'
claims):

| Clip | Before: finals (s) | After: finals (s) | Longest wait from a line's first word to its final, before -> after |
|---|---|---|---|
| Live clip (Bengali, 20 s) | 3: 0.68, 9.28, 7.84 | 6: 0.68, 2.76, 4.16, 2.16, 2.64, 3.48 | 9.64 s -> 5.60 s |
| English sample `1.wav` (16.7 s, read prose) | 2: 9.44 (cut inside "to connect her / parent"), 6.24 | 3: 5.40, 3.96, 5.64 | 9.76 s -> 6.52 s |
| Bengali sample `0.wav` | 1: 6.88 | 2: 3.68, 2.80 | 7.84 s -> 6.56 s |
| English sample `0.wav` | 1: 5.84 | 1: 5.84 | 6.84 s -> 6.84 s |
| Mandarin sample `0.wav` | 4: 1.76, 0.20, 3.16, 0.84 | the same | 3.96 s -> 3.96 s |
| Mandarin sample `3.wav` | 1: 4.64 | the same | 5.56 s -> 5.56 s |
| All | 12 finals, median 5.24 s, longest 9.44 s, 4 over 6.5 s | 17 finals, median 3.16 s, longest 5.84 s, none over 6.5 s | |

The live clip's line 2 (9.3 s: "এর থেকে বড় সাইজের হয় না … দিনকাল যা পড়েছে") is now three lines, cut where the speakers
pause, and the rest of the clip two more.
