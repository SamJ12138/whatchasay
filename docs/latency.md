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

## Batch 2: incremental translation (2026-10-02)

While a line is still open the backend translates its **stable prefix** and sends it as a `draft`; the overlay
draws the draft (italic, with the in-progress ellipsis) above the growing source text, and the final translation
replaces it at the endpoint. A word is stable once it was the same in `SUBTITLE_ASR__DRAFT_STABLE_PARTIALS`
consecutive partial results; the prefix is re-translated, the newest one only, at most once per
`SUBTITLE_ASR__DRAFT_DEBOUNCE_MS` (`asr/draft.py`; a draft is never sent after its line's final). The MT engine is
untouched: a draft is an ordinary translation call on a shorter text.

**Flicker budget.** Per line, the content script counts every change of the translation on screen and how many of
those were not a pure append (`non_append_revisions`: the new text does not start with the old one, closing
punctuation aside), the final replacing the last draft included. Live clip, three browser runs per setting:

| Stable partials N | Debounce | run_ids | Drafts per line | Non-append revisions per line (per line, run 1) | First translated text p50 / max (s) | Translate calls per s | Translate p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|
| no drafts (Batch 1) | | `20261002T162441-af85dd`, `20261002T162526-fdb562`, `20261002T162610-0569c1` | 0 | 0 | 4.01-4.66 / 5.10-7.07 | 0.2-0.25 | 33-44 / 43-65 |
| 2 (the brief's default) | 300 ms (the brief's default) | `20261002T163619-ccbc59`, `20261002T163704-a7332d`, `20261002T163750-8be492` | 3.8-4.2 | **3.8-4.2** (3, 6, 3, 4, 5) | 1.40-1.43 / 2.46-2.67 | 1.2-1.3 | 25-26 / 40-43 |
| **3 (default)** | **1500 ms (default)** | `20261002T164037-570655`, `20261002T164122-a50e20`, `20261002T164207-4d16db` | 1.8 | **1.6** (1, 3, 1, 2, 1) | 2.07 / 2.90-2.92 | 0.7 | 26-28 / 43-130 |
| 3 | 2000 ms | `20261002T164252-57bae2`, `20261002T164337-c5add8`, `20261002T164421-c262a8` | 1.4-2.0 | 1.2-1.75 (1, 2, 1, 1, 1) | 2.07-2.28 / 3.39-3.44 | 0.6 | 25-28 / 45-68 |

What the numbers say, and they are not flattering:

- **Nearly every new draft rewrites the one before it.** Non-append revisions equal the number of drafts in every
  run. The translation of a longer prefix is a different sentence, not the old one plus words: Bengali puts the verb
  last ("অর্ডার না দিলে হয়" -> "If you don't order me.", one word later "অর্ডার না দিলে হয় না" -> "You don't have
  to order me."), and OPUS-MT starts over each time. The same holds for the other directions (simulated with the
  real recognizer and MT on the sample WAVs: English to Chinese 9.3 non-append revisions per line at 2 / 300 ms, 3.3
  at 3 / 1500 ms; Mandarin to English 11 and 3). So the two settings do not make drafts steadier, they make them
  rarer: the budget "under 2 per line" is met by showing fewer than two drafts per line.
- **Chosen defaults: N = 3, debounce 1500 ms.** 1.6 non-append revisions per line on the live clip in 3 of 3 runs
  (budget: under 2), the first translated text 2.07 s after the line's first word (0.65 s later than with 2 / 300 ms,
  1.9-2.6 s earlier than without drafts). 2000 ms was no better in the worst run (1.75) and updates later.
- **The first draft of a line is usually one word** ("from", "Key" for কী, "Day"): the stable prefix when the
  third partial arrives. It is on screen about 1.3 s before the next draft replaces it. A minimum prefix length
  would remove these; it is not built (the brief asked for N and the debounce).
- **Translation calls** went from 0.2-0.25 per second of clip to 0.7 (1.2-1.3 at 2 / 300 ms). Translate latency with
  drafts running: p50 26-28 ms, p95 43-130 ms on the CPU, under the 150 ms bound in all nine runs; drafts are short
  texts, so the median fell. The final translation after the last word did not move (1.29-1.38 s p50).

## Batch 3: earlier language confirmation (2026-10-02)

With Auto-detect nothing in the right language can be shown before spoken-language ID confirms it. What changed
(`asr/session.py`, `asr/lid.py`; the constrained-set rule and the 0.6 confidence floor are as they were):

| | Before | Now |
|---|---|---|
| First attempt | after 2.5 s of voiced audio | after 1.0 s of voiced audio (`SUBTITLE_ASR__LID_FIRST_WINDOW_S`) |
| Next attempts | one more, 1.5 s of voiced audio later | every 0.5 s of audio, voiced or not (`SUBTITLE_ASR__LID_RETRY_STEP_S`), three early attempts in all; then the full window at 2.5 s of voiced audio (`SUBTITLE_ASR__LID_WINDOW_S`) and one more at 4.0 s |
| What an early attempt may decide | (there was none) | only a language **other than the first guess**, and only when two attempts in a row give it. whisper-tiny answers "en" for noise and for the first second of Mandarin and Bengali samples, with confidence up to 0.99, so English (the first guess) is still confirmed on the full window only |
| One call | 0.14 s (0.86 s in the live runs): the window was padded to whisper's 30 s | 0.012-0.02 s: the window is the audio plus 0.5 s. On short audio the short window is at least as confident (Bengali sample, 1.5 s: 0.69 for English with 30 s of padding, 0.83 for Bengali without) |
| Model load | on the first attempt (0.55 s in a live run, stalling the audio loop) | at startup |

**Decision time, offline** (the real session and model; seconds of audio fed when the language was decided; start
offsets 0 / 0.3 / 0.7 / 1.5 / 3.0 s into each file; `tests/test_lid_real.py` holds the clip and sample claims):

| Input | Before | Now | Result |
|---|---|---|---|
| Live clip (Bengali) | 3.92 / 4.28 / 4.24 / 3.88 / 3.48 | 1.92 / 2.72 / 3.44 / 2.56 / 1.80 | bn, the same in all |
| Bengali sample | 3.16 / 2.88 / 2.68 / 3.08 / 3.24 | 2.08 / 2.32 / 1.60 / 1.56 / 1.96 | bn, the same |
| Mandarin sample `0.wav` | 4.84 / 4.56 / 4.56 / 5.20 / 4.44 | 2.24 / 1.92 / 1.60 / 5.20 / 4.44 | zh, zh, zh, zh, en (the last window is English words); the same before and now |
| Mandarin sample `3.wav` | 3.16 / 2.84 / 2.52 / 2.76 / 4.56 | 2.16 / 2.36 / 1.52 / 1.52 / 1.80 | zh, the same |
| English samples `0.wav`, `1.wav` | 2.52-3.04 | unchanged (the first guess waits for the full window) | en, the same |

30 of 30 cases give the same language as before; none is decided later.

**First confirmed-language subtitle on the live clip, in the browser** (seconds after the clip starts to play; 3 runs
each; the tab's audio output already running, see below):

| | run_ids | Detected | First confirmed subtitle (s) |
|---|---|---|---|
| Before (code of commit `0d5e875`) | `20261002T171325-0c2002`, `20261002T171414-a14a6b`, `20261002T171504-b67c48` | bn, confirmed | 5.26, 5.31, 5.36 |
| Now | `20261002T170515-fdf93f`, `20261002T170558-b9e40e`, `20261002T170641-33eb36` | bn, confirmed | **2.14, 2.15, 2.14** (2.54-2.57 s after the session's first audio frame) |

Under 3 s in 3 of 3 runs, the same detection result. The first subtitle is the clip's first sentence, replayed into
the Bengali recognizer the moment the language is confirmed.

**A finding about the measurement.** Without the tab's audio output already running, the same three runs give
3.62, 3.62 and 3.69 s (before: 5.43-5.58 s), and not because detection is slower: language ID confirmed Bengali at
about 2.3 s, but the clip's first sentence (0-0.68 s) never reached the capture, so there was nothing to show until
the next sentence (from 2.68 s) produced its first partial. Chrome starts a silent tab's audio output with the first
sample and the capture stream begins a few hundred milliseconds later; the sentence was missing in 17 of the 18
browser runs of Batches 1-3 made that way and present in every run with the output already running
(observations A11). The harness option `--prime-audio` plays a near-silent tone on the page before the clip
starts, which is the situation of a viewer who turns captions on over a video that is already playing;
`line_latency_table.py` uses it (and `--no-prime` gives the other case). The first baseline table above was
measured without it; the before / after table below uses it on both sides.

English speech is not confirmed earlier (3.9-4.2 s as before): its text is on screen from 0.3-0.5 s, dimmed, and
undimmed in place.

