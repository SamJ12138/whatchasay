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

## Before and after (2026-10-02)

Three browser runs per clip on each side, the same harness and settings on both: `line_latency_table.py`, spoken
language Auto-detect, one target (English; Chinese for English speech), translation on the CPU, the tab's audio
output already running when the clip starts (`--prime-audio`). **Before** is the code of commit `0d5e875` (the
measurement, nothing else changed), run from an export of that commit with `--backend-dir` / `--extension`.
**After** is the code of the commit that adds this table. Each cell is the range over the three runs; a run's own
numbers are in its run log (`backend/logs/run_<run_id>.jsonl`, `failure_report.py` section 6).

| Clip | | run_ids | Lines | First display p50 / max (s) | First translated text p50 / max (s) | Final translation p50 / max (s) | First confirmed subtitle (s) | Segments, run 1 (s) | Translate calls per s | Translate p50 / p95 (ms) | Non-append revisions per line |
|---|---|---|---|---|---|---|---|---|---|---|---|
| English sample | before | `20261002T170809-9e62b1`, `20261002T170843-cd7a43`, `20261002T170917-027089` | 1 | 0.47-0.48 / 0.47-0.48 | 6.60-6.62 / 6.60-6.62 | 0.88-0.94 / 0.88-0.94 | 3.90-4.02 | 6.4 | 0.15 | 61-65 / 61-65 | 0 (one translation per line) |
| English sample | after | `20261002T171744-200726`, `20261002T171818-4a7764`, `20261002T171853-faffb7` | 1 | 0.46-0.48 / 0.46-0.48 | 2.63-2.65 / 2.63-2.65 | 0.88-0.89 / 0.88-0.89 | 3.25-3.28 | 6.4 | 0.60 | 34-38 / 58-62 | 3.00 |
| Mandarin sample | before | `20261002T170952-c56411`, `20261002T171028-feb87f`, `20261002T171104-564457` | 4 | 0.30 / 5.17-5.28 | 1.64 / 5.25-5.37 | 0.79-0.80 / 1.54-1.65 | 6.04-6.15 | 5.88, 1.6, 2.24, 1.6 | 0.40 | 18-20 / 42-48 | 0 (one translation per line) |
| Mandarin sample | after | `20261002T171929-bc8de6`, `20261002T172000-a16b08`, `20261002T172034-42665c` | 5 | 0.42 / 1.66-1.76 | 1.40-1.72 / 1.90-2.29 | 0.84 / 0.88-0.92 | 2.52-2.63 | 2.24, 0.96, 1.6, 2.24, 1.6 | 0.70-0.90 | 15-17 / 28-33 | 0.00-0.20 |
| Bengali sample | before | `20261002T171140-3e837c`, `20261002T171216-37630f`, `20261002T171251-10dffc` | 1 | 3.85-3.92 / 3.85-3.92 | 7.85-7.87 / 7.85-7.87 | 1.09-1.10 / 1.09-1.10 | 4.10-4.18 | 8.48 | 0.14 | 69-78 / 69-78 | 0 (one translation per line) |
| Bengali sample | after | `20261002T172108-b7cf8d`, `20261002T172142-44fe59`, `20261002T172216-126b88` | 2 | 0.62 / 2.12-2.64 | 2.72-3.29 / 3.84-3.86 | 1.06-1.08 / 2.95-2.96 | 2.35-2.88 | 4.44, 3.84 | 0.72 | 24-27 / 49-51 | 1.00 |
| Live clip (Bengali film scene) | before | `20261002T171325-0c2002`, `20261002T171414-a14a6b`, `20261002T171504-b67c48` | 2-3 | 0.62-0.75 / 5.62-5.75 | 10.34-10.44 / 10.44-10.45 | 0.26-0.89 / 0.44-1.29 | 5.26-5.36 | 10.4, 1.28, 10.24 | 0.10-0.15 | 91-103 / 100-114 | 0 (one translation per line) |
| Live clip (Bengali film scene) | after | `20261002T172251-c2cd00`, `20261002T172334-5a744f`, `20261002T172417-59775b` | 6 | 0.70-0.75 / 2.51-2.56 | 1.99-2.04 / 2.54-2.58 | 1.29-1.33 / 1.35-1.38 | 2.13-2.17 | 2.08, 2.8, 5.04, 2.12, 2.84, 4.6 | 0.80 | 23-28 / 51-67 | 1.50 |

Read with the definitions at the top: "first display" and "first translated text" count from the audio of the
line's first word, "final translation" from the audio of its last word, "first confirmed subtitle" from the start
of playback.

**What moved.**

- **First translated text**: 6.6 -> 2.6 s (English sample), 7.9 -> 2.7-3.3 s (Bengali sample), 10.3-10.4 -> 2.0 s
  (live clip), and on the Mandarin sample the slowest line 5.3 -> 1.9-2.3 s. This is the draft translation of the
  line's stable prefix (Batch 2) on lines that are no longer than 6 s (Batch 1). It is a draft: on the live clip it
  is rewritten 1.5 times per line on average before the final translation stands (budget: under 2); on the one
  long English sentence 3 times.
- **First confirmed-language subtitle** (Auto-detect): 5.3 -> 2.1-2.2 s on the live clip, 6.0-6.2 -> 2.5-2.6 s
  (Mandarin sample), 4.1-4.2 -> 2.4-2.9 s (Bengali sample), and 3.9-4.0 -> 3.3 s for English, which is still
  confirmed on the full window only (the faster call and the model loaded at startup are the gain there).
- **Lines**: the live clip's 2-3 run-on lines (10.4 s and 10.2 s) are 6 lines of 2.1-5.0 s; the Bengali sample's
  8.5 s sentence is 2 lines.
- **First display** of the source text did not change where it was already at the recognizer's chunk lag
  (0.3-0.75 s). The "max" column was the first line waiting for language ID (3.9-5.7 s on Mandarin and Bengali
  speech); it is 1.7-2.6 s now.
- **Final translation after the last word** is unchanged on the samples (0.8-1.1 s) and 1.3 s on the live clip,
  whose "before" lines were closed by the 10 s cut, not by a pause. One line of the Bengali sample gets its final
  3.0 s after its last word: it is the first half of a sentence cut by the 6 s limit, and that cut is only made
  once the sentence has run past 6 s.
- **Translation calls** rose from 0.10-0.40 to 0.60-0.90 per second of clip. Translate latency on the CPU with
  drafts running: p50 15-38 ms, p95 28-67 ms (bound: 150 ms; the highest p95 in the tuning runs was 130 ms).

**Where quality dropped.** Shorter lines are easier on the translator in dialogue (live clip, "এর চেয়ে বড় করতে গেলে
অনেক দাম পড়ে যায়" -> "It's too expensive to raise it." in every run, where "before" it sat inside a 10 s run-on that
came out as "It doesn't matter if you want to raise a lot of prices for what has been read day by day, but the next
time I have ten thousand."). Two things got worse, both Bengali to English:

| | Before | After |
|---|---|---|
| A sentence longer than 6 s is cut at its widest pause and the halves are translated separately (Bengali sample, `20261002T171140-3e837c` / `20261002T172108-b7cf8d`) | "Meanwhile, Mr. Tugkol Orbin Kejriwal and his team are tweeting in an hour-to-hour for credit." | "Meanwhile, Mr. Tugkol Orbin Kejriwal and his team" / "Tweeting at your hour for credit": the second half has lost its subject |
| A short misrecognised line now stands alone, and OPUS-MT invents a sentence for it (live clip, first line, `20261002T172251-c2cd00`; recognised as "কাজ বুক দেখান্ত"; it is line 1 of the live test, "Show me a rajbhog.") | part of the 10 s run-on: "Today the book doesn't have to be larger than it is today, …" | "We were married in July 1955." (2 of 3 runs; "I'll see you today." in the third) |

English to Chinese and Mandarin to English: the final translations of the sample lines are the same before and
after (English sample: identical; Mandarin sample: the first line is no longer run together with the next word).

## Local agreement for drafts (2026-10-02)

Every draft rewrote the previous one (Batch 2 above), so the flicker budget was met only by showing fewer drafts.
Now the backend shows, of each draft translation, only the **longest common prefix of the line's last K draft
translations** (`asr/draft.py` `LocalAgreement`, `SUBTITLE_ASR__DRAFT_AGREE_K`; words, CJK characters; punctuation
glued to a word does not break the agreement). The first draft of a line is shown as it is: waiting for a second
one would cost a whole debounce interval (1.5 s), more than the 0.5 s this batch may lose. When the last K agree on
nothing, what is on screen stays (the final replaces it); when they agree on the same prefix again nothing is
re-sent. The shown text is therefore append-only except when the agreed prefix itself shrinks (the last drafts
agree on less than the ones before); `tests/test_draft_translation.py` holds that on a scripted sequence. The run
log's `draft` records carry the words of the raw and of the shown translation; `line_latency_table.py` has the
column "Words per draft".

Three browser runs per clip and setting, `line_latency_table.py` (no priming by the harness, see above), the same
N = 3 / 1500 ms underneath; each cell the range over the runs:

| Clip | K | run_ids | Lines | Drafts per line | Words per draft | Non-append revisions per line | First translated text p50 / max (s) | First display p50 (s) | Final p50 (s) |
|---|---|---|---|---|---|---|---|---|---|
| Live clip (Bengali film scene) | 1 (none: the current N=3 / 1500 ms) | `20261002T205149-8e975a`, `20261002T205231-e39749`, `20261002T205313-19b0cf` | 5-6 | 1.83-2.20 | 4.27-6.00 | **1.83-2.00** | 2.07-2.07 / 2.37-2.42 | 0.78-0.78 | 1.32-1.33 |
| Live clip (Bengali film scene) | 2 | `20261002T205856-64577f`, `20261002T205937-c337de`, `20261002T210019-128750` | 5 | 0.80-1.20 | 1.67-2.00 | **0.80** | 2.07-2.07 / 2.39-2.40 | 0.78-0.78 | 1.32-1.35 |
| Live clip (Bengali film scene) | 3 | `20261002T210559-e148ea`, `20261002T210640-6f0879`, `20261002T210721-de1b93` | 6 | 0.83 | 2.00 | **0.83** | 2.07-2.07 / 2.41-2.45 | 0.78-0.78 | 1.32-1.33 |
| English sample | 1 (none: the current N=3 / 1500 ms) | `20261002T211501-7b0ef5`, `20261002T211535-40fdb6`, `20261002T211608-c6298d` | 1 | 3.00 | 9.67 | **3.00** | 2.46-2.53 / 2.46-2.53 | 0.30-0.35 | 0.91-0.97 |
| English sample | 2 | `20261002T211642-7ba649`, `20261002T211715-559106`, `20261002T211748-068dba` | 1 | 2.00 | 6.00 | **2.00** | 2.46-2.48 / 2.46-2.48 | 0.30-0.31 | 0.91-0.96 |
| English sample | 3 | `20261002T211822-3c86e8`, `20261002T211856-b54047`, `20261002T211930-0c3882` | 1 | 1.00 | 5.00 | **1.00** | 2.46-2.50 / 2.46-2.50 | 0.30-0.35 | 0.91-0.96 |
| Mandarin sample | 1 (none: the current N=3 / 1500 ms) | `20261002T205538-a9033f`, `20261002T205611-1143fa`, `20261002T205644-fecc1e` | 4-5 | 0.20-0.50 | 1.00 | **0.00-0.25** | 1.39-1.72 / 2.36-2.92 | 0.34-0.41 | 0.80-0.83 |
| Mandarin sample | 2 | `20261002T210242-70cda4`, `20261002T210315-0fceb0`, `20261002T210347-466946` | 5 | 0.20 | 1.00 | **0.00** | 1.63-1.64 / 2.36-2.37 | 0.41-0.42 | 0.83-0.84 |
| Mandarin sample | 3 | `20261002T210942-20e16b`, `20261002T211015-8e80ca`, `20261002T211046-32f05f` | 4-5 | 0.00-0.25 | 1.00 | **0.00** | 1.83-1.98 / 2.92-2.92 | 0.41-0.41 | 0.80-0.91 |
| Bengali sample | 1 (none: the current N=3 / 1500 ms) | `20261002T205716-3e9fca`, `20261002T205749-81cea3`, `20261002T205822-d91de9` | 2 | 1.50 | 5.33-6.00 | **1.00-1.50** | 3.26-3.28 / 3.97-3.97 | 0.74-0.74 | 1.17-1.25 |
| Bengali sample | 2 | `20261002T210419-d17174`, `20261002T210453-aaa67e`, `20261002T210526-0b6dfa` | 2 | 1.00 | 4.50 | **1.00** | 3.26-3.29 / 3.92-4.01 | 0.70-0.78 | 1.16-1.25 |
| Bengali sample | 3 | `20261002T211119-d67226`, `20261002T211151-57a7e0`, `20261002T211225-d3fd29` | 2 | 0.50 | 2.00 | **0.50** | 3.26-3.26 / 3.97-4.00 | 0.74-0.78 | 1.25-1.28 |

**Chosen default: K = 2.** On the live clip the non-append revisions go from 1.83-2.00 per line to **0.80** (the
budget was "under 2"), the first translated text does not move (p50 2.07 s, max 2.39-2.40 s), and the first
display and the final are unchanged; the price is shorter drafts (1.7-2.0 words on screen per draft instead of
4.3-6.0: the agreed start of a verb-final sentence is short). English to Chinese: 3 -> 2 rewrites of the one long
sentence, first text 2.46-2.49 s as before. K = 3 gains nothing more on the live clip (0.83), halves the Bengali
sample's words per draft again (2.0 against 4.5) and leaves the Mandarin sample almost without drafts; it is better
only on the English sentence (1 rewrite, 5 characters per draft, first text up to 0.25 s later). The
"first translated text" differences on the Mandarin sample (1.39-1.98 s across all settings) come from which lines
got a draft at all (0-0.5 per line), not from the agreement. Translate calls are not changed by K: the agreement
runs on the translation's result.

## Minimum draft length (2026-10-02)

The first draft of a line was usually one word ("from", "Key" for কী, "Day"), the stable prefix when the third
partial arrived, on screen for about 1.3 s before the next draft replaced it. A draft is now sent only once the
line's stable prefix has **3 words (CJK: 4 characters) or 40 % of the words heard so far in the line**, whichever
comes first (`asr/draft.py` `draft_long_enough`, applied where the session marks the stable prefix;
`SUBTITLE_ASR__DRAFT_MIN_WORDS` 3, `..._MIN_CJK_CHARS` 4, `..._MIN_FRACTION` 0.4). "Tokens" of the brief are
read as the words of the newest partial (the recognizer's sub-word pieces are not what the viewer sees); the
share rule means a one-word draft is still possible on a line of two words.

Live clip, 3 browser runs each, K = 2 underneath (`backend/logs`' `draft` records: the first draft sent per line):

| | run_ids | Lines with a draft | One-word first drafts | Words of the first drafts | First translated text p50 / max (s) | Drafts per line | Words per draft | Non-append revisions per line |
|---|---|---|---|---|---|---|---|---|
| Before (K = 2, no minimum) | `20261002T205856-64577f`, `20261002T205937-c337de`, `20261002T210019-128750` | 12 | **4 of 12** | 1, 1, 2, 2 / 1, 2, 2, 3 / 1, 2, 2, 3 | 2.07 / 2.39-2.40 | 0.80-1.20 | 1.67-2.00 | 0.80 |
| (K = 1, no minimum) | `20261002T205149-8e975a`, `20261002T205231-e39749`, `20261002T205313-19b0cf` | 13 | 5 of 13 | 1, 1, 2, 2 / 1, 1, 2, 3, 3 / 1, 2, 2, 3 | 2.07 / 2.37-2.42 | 1.83-2.20 | 4.27-6.00 | 1.83-2.00 |
| After (minimum) | `20261002T212823-a9c55a`, `20261002T212904-118671`, `20261002T212943-0de960` | 13 | **0 of 13** | 2, 3, 3, 6, 8 / 3, 6, 6, 8 / 5, 6, 6, 6 | 2.42-2.74 / 3.25-3.30 | 1.00-1.20 | 4.50-4.80 | 0.80-1.00 |

The one-word first drafts are gone: 4 of the 12 lines that got a draft (K = 2; 5 of 13 without agreement) started
with one word, none of 13 now, and the shortest first draft has two words. The first translated text comes 0.35-0.67 s later at p50 (2.07 -> 2.42-2.74 s) and up to 0.9 s later at
the worst line: the one word it used to show carried nothing, and the first draft now has 2-8 words (4.5-4.8 words
per draft against 1.7-2.0). Translate calls 0.8 -> 0.65-0.7 per second; non-append revisions 0.8-1.0 per line.

## A11 fixed: the first sentence without the harness's priming (2026-10-02)

The extension now keeps the captured tab's audio output running itself (`lib/capture-prime.js`: an inaudible tone
from the content script from the moment live captions start, and the worker reports the start only once it runs;
`docs/observations.md` A11), so the harness's `--prime-audio` is no longer needed for an honest first-subtitle
number and `line_latency_table.py` no longer passes it: the clip is played after captions are started, as a viewer
would, and nothing else runs on the page. The live-clip row, rerun that way (3 runs; the row above was measured
with the harness priming the page):

| Clip | run_id | Lines | First display p50 / max (s) | First translated text p50 / max (s) | Final translation p50 / max (s) | First confirmed subtitle (s) | Segments (s) | Translate calls per s | Translate p50 / p95 (ms) | Non-append revisions per line |
|---|---|---|---|---|---|---|---|---|---|---|
| Live clip (Bengali film scene), no priming by the harness | `20261002T204134-a84c67` | 5 | 0.78 / 2.37 | 2.07 / 2.40 | 1.32 / 1.42 | 2.10 | 2.08, 2.64, 6.12, 4.0, 4.64 | 0.80 | 33 / 67 | 2.0 |
| | `20261002T204217-9c05eb` | 6 | 0.78 / 2.42 | 2.08 / 3.92 | 1.41 / 2.84 | 2.16 | 2.08, 2.64, 4.32, 2.4, 2.8, 4.64 | 0.80 | 33 / 1495 | 1.5 |
| | `20261002T204300-4dfbeb` | 6 | 0.78 / 2.38 | 2.07 / 2.42 | 1.33 / 1.41 | 2.12 | 2.08, 2.72, 4.36, 2.36, 2.8, 4.64 | 0.85 | 29 / 53 | 1.67 |

The first segment is the clip's first sentence (2.08 s, as in the primed runs) in 3 of 3; without the fix it was
missing in 17 of 18 unprimed runs and the first confirmed subtitle came at 3.62-3.69 s. First confirmed subtitle
2.10-2.16 s (primed: 2.13-2.17 s), first translated text p50 2.07-2.08 s (1.99-2.04 s), first display p50 0.78 s
(0.70-0.75 s): the same picture within run-to-run noise. Run 2 had one translate call of 1.5 s (its p95; the
line's final came 2.84 s after its last word): a one-off stall on the CPU, the only call over 100 ms among the 49
of the three runs (the next longest: 70 ms). On the YouTube page itself (3 runs, `20261002T203832-86ccb0`, `20261002T203930-46ebb6`,
`20261002T204010-93b09e`, no priming) the first line is the clip's opening sentence in 3 of 3, first confirmed
subtitle 1.87, 2.28 and 1.64 s after play.

## Where the numbers stand (2026-10-02, after the follow-ups)

All four clips once more on the finished code (lines wrap once, the extension primes the capture, local agreement
K = 2, minimum draft length), `line_latency_table.py`, 3 browser runs per clip, no priming by the harness:

| Clip | run_id | Lines | First display p50 / max (s) | First translation p50 / max (s) | Final p50 / max (s) | First confirmed subtitle (s) | Segments (s) | Translate calls (per s) | Translate p50 / p95 (ms) | Drafts per line | Words per draft | Non-append revisions per line |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Live clip (Bengali film scene) | `20261002T213218-755d4d` | 6 | 0.78 / 2.39 | 2.42 / 3.29 | 1.40 / 1.45 | 2.14 | 2.08, 2.64, 4.32, 2.44, 2.8, 4.64 | 14 (0.7) | 30.6 / 62.1 | 0.83 | 5.2 | 0.83 |
| Live clip (Bengali film scene) | `20261002T213300-fff9f7` | 6 | 0.78 / 2.42 | 2.45 / 3.25 | 1.33 / 1.41 | 2.18 | 2.08, 2.72, 4.32, 2.4, 2.8, 4.64 | 14 (0.7) | 27 / 42.1 | 1 | 4.5 | 1 |
| Live clip (Bengali film scene) | `20261002T213341-0a83d0` | 6 | 0.78 / 2.36 | 2.39 / 3.21 | 1.31 / 1.41 | 2.09 | 2.08, 2.72, 4.28, 2.4, 2.8, 4.64 | 14 (0.7) | 28.1 / 58.1 | 1 | 4.5 | 1 |
| English sample | `20261002T213422-21c245` | 1 | 0.34 / 0.34 | 2.49 / 2.49 | 0.96 / 0.96 | 3.4 | 7.84 | 4 (0.6) | 32.2 / 54.5 | 2 | 6 | 2 |
| English sample | `20261002T213455-5470ea` | 1 | 0.34 / 0.34 | 2.49 / 2.49 | 0.97 / 0.97 | 3.38 | 7.84 | 4 (0.6) | 32.2 / 56.8 | 2 | 6 | 2 |
| English sample | `20261002T213528-162739` | 1 | 0.30 / 0.30 | 3.97 / 3.97 | 0.92 / 0.92 | 3.36 | 7.84 | 3 (0.45) | 48.5 / 54.2 | 2 | 8.5 | 2 |
| Mandarin sample | `20261002T213601-7cba81` | 4 | 0.33 / 1.78 | 1.55 / 2.92 | 0.80 / 0.96 | 2.46 | 3.64, 3.84, 2.24, 1.28 | 7 (0.7) | 19.1 / 26.4 | 0.5 | 1.5 | 0.25 |
| Mandarin sample | `20261002T213634-ba04e6` | 4 | 0.42 / 1.51 | 1.77 / 2.92 | 0.80 / 0.92 | 2.5 | 2.24, 3.84, 2.24, 1.28 | 7 (0.7) | 20.1 / 28 | 0.25 | 1 | 0 |
| Mandarin sample | `20261002T213706-e810b6` | 5 | 0.41 / 1.53 | 1.63 / 2.36 | 0.83 / 0.92 | 2.49 | 2.24, 2.56, 1.28, 2.24, 1.28 | 7 (0.7) | 16.6 / 25.5 | 0.2 | 1 | 0 |
| Bengali sample | `20261002T213738-312eef` | 2 | 0.74 / 2.42 | 3.26 / 3.97 | 1.25 / 3.06 | 2.78 | 4.32, 3.96 | 5 (0.72) | 24.1 / 40.3 | 1 | 4.5 | 1 |
| Bengali sample | `20261002T213810-7e3d9c` | 2 | 0.38 / 1.93 | 3.25 / 3.60 | 1.20 / 2.67 | 2.28 | 4.72, 3.6 | 5 (0.72) | 23.2 / 39.1 | 1 | 4.5 | 1 |
| Bengali sample | `20261002T213843-f3ee9c` | 2 | 0.74 / 2.45 | 3.30 / 3.96 | 1.24 / 3.06 | 2.81 | 4.32, 3.96 | 5 (0.72) | 24.6 / 39.1 | 1 | 4.5 | 1 |

Against the "Before and after" table above (the latency batches): first translated text p50 is 2.4-2.5 s on the
live clip (was 2.0 s: the minimum draft length), 2.5 s on the English sample (one run 4.0 s, where the first draft
came late), 1.6-1.8 s on the Mandarin sample and 3.3 s on the Bengali sample (unchanged); non-append revisions
per line 0.8-1.0 on the live clip (was 1.5), 2 on the English sentence (was 3), 0-0.25 and 1 on the samples;
first confirmed subtitle 2.1-2.2 s on the live clip without any priming by the harness (was 2.1-2.2 s with it,
3.6-3.7 s without); first display and the final translation after the last word as before (0.3-0.8 s; 0.8-1.4 s).

## Benchmark across public clips (2026-10-03)

The tables above measure the project's own sample WAVs and one film scene. This one measures six public clips,
two per spoken language, chosen for clear speech and a license that allows the use (public domain, or CC BY 3.0;
read on each Wikimedia Commons file page; every clip is credited in `NOTICE.md`). Nothing in the pipeline changed
since "Where the numbers stand" (code of commit `a346a9b`; this commit adds only scripts and documents).

**The clips.** Each was taken from Wikimedia Commons as published, transcoded to 16 kHz mono WAV (an excerpt where
the file is longer than 90 s) and played by the harness as before. The speech-rate columns come from
`backend/scripts/clip_speech_stats.py`: the clip's own recognizer run offline over the WAV, its token timestamps
kept; a word is a token that starts one (Mandarin: every character), a pause is a gap of at least 0.3 s between
consecutive tokens, and "pauses >= 0.5 s" are the ones at which the pause rule closes a line
(`SUBTITLE_ASR__SPLIT_GAP_S`).

| Clip | What it is | License | Used | Words (zh: characters) / min over the speech | ...while speaking | Pauses >= 0.3 s | Median pause (s) | Longest pause (s) | Share of the time paused | Pauses >= 0.5 s |
|---|---|---|---|---|---|---|---|---|---|---|
| English, NASA | NASA ScienceCasts "The Zero Gravity Coffee Cup" (2013): one narrator, read prose over animation; the clip of the README's GIF and screenshot | public domain (PD-NASA) | 0:09-0:39.5, 30.5 s | 162 | 230 | 16 | 0.46 | 1.0 | 30 % | 6 |
| English, VOA Helix | Voice of America, "Matt Dibble reports on Helix electric aircraft" (2024): a field news report, narration with street interviews, a parade in the background | public domain (PD-USGov-VOA) | whole, 89.2 s | 137 | 226 | 45 | 0.36 | 10.1 | 40 % | 15 |
| Mandarin, VOA Norway | 美国之音 (VOA Chinese) news, "挪威部长称警方反应精彩" (2011): one studio news reader, continuous | public domain (PD-USGov-VOA) | whole, 72.0 s | 277 | 392 | 45 | 0.40 | 0.76 | 29 % | 13 |
| Mandarin, VOA Taiwan | 美国之音 (VOA Chinese) news, "台湾准备迎战强台风" (2013): one studio news reader, continuous | public domain (PD-USGov-VOA) | whole, 40.1 s | 275 | 430 | 32 | 0.42 | 0.68 | 36 % | 7 |
| Bengali, Maasranga | Maasranga News (Bangladesh), "চিকিৎসার জন্য ঢাকায় তরিকুল" (2018): a TV news report, anchor then reporter | CC BY 3.0 | 0:00-1:00, 60 s | 127 | 165 | 19 | 0.40 | 2.5 | 23 % | 8 |
| Bengali, Wikitongues | Wikitongues, "Sanjoy speaking Bengali" (2016): one speaker talking about his childhood to the camera, unhurried, with pauses to think | CC BY 3.0 | 0:00-1:15, 75 s | 72 | 184 | 45 | 0.92 | 2.5 | 61 % | 34 |

**How it was run.** `backend/scripts/line_latency_table.py`, defaults: 3 browser runs per clip, the real extension
in Chromium, a backend started per run, spoken language Auto-detect, one target (English; Chinese for English
speech), translation on the CPU, no `--prime-audio` (the extension primes the capture itself since A11), the clip
played after captions are started. Windows 11, Intel i9-13900H, CPU only. Columns as defined at the top; a run's
own numbers are in `backend/logs/run_<run_id>.jsonl`. The runs were made in two batches: the first was cut short
by the machine running out of memory after the first Bengali run (the five runs that failed to start are not in
the table; nothing was retried selectively), and the five missing Bengali runs were made 40 minutes later.

| Clip | run_id | Detected | Lines | First display p50 / max (s) | First translation p50 / max (s) | Final p50 / max (s) | First confirmed subtitle (s) | Segments (s) | Translate calls (per s) | Translate p50 / p95 (ms) | Drafts per line | Words per draft | Non-append revisions per line |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| English, NASA | `20261003T210630-84e65c` | en | 7 | 0.46 / 0.50 | 1.44 / 3.37 | 1.03 / 2.88 | 3.69 | 3.76, 3.36, 2.88, 6.72, 2.68, 3.92, 5.76 | 24 (0.79) | 25.3 / 62.1 | 1.29 | 5.11 | 0.86 |
| English, NASA | `20261003T210731-0cb6c2` | en | 7 | 0.46 / 0.47 | 1.48 / 3.50 | 1.05 / 2.89 | 3.69 | 3.72, 3.48, 2.88, 6.72, 2.64, 3.96, 5.76 | 24 (0.79) | 25.1 / 50.9 | 1.29 | 5.56 | 0.86 |
| English, NASA | `20261003T210830-15c6b8` | en | 7 | 0.47 / 0.48 | 1.46 / 3.49 | 1.05 / 2.87 | 3.69 | 3.72, 3.48, 2.88, 6.72, 2.64, 3.96, 5.76 | 24 (0.79) | 25.3 / 61.6 | 1.29 | 5.56 | 0.86 |
| English, VOA Helix | `20261003T210928-a53dbb` | en | 20 | 0.46 / 0.49 | 1.46 / 4.99 | 1.03 / 4.33 | 2.71 | 5.12, 3.52, 2.56, 1.72, 2.32, 4.96, 3.92, 5.8, 1.28, 4.64, 4, 3.84, 3.2, 3.36, 4, 1.6, 4.36, 6, 3.84, 4.48 | 60 (0.67) | 29.9 / 51.2 | 1.25 | 4.72 | 0.75 |
| English, VOA Helix | `20261003T211123-ff49d7` | en | 19 | 0.46 / 0.50 | 1.62 / 4.99 | 1.02 / 4.34 | 2.75 | 5.92, 5.92, 1.72, 2.32, 4.96, 4.28, 5.48, 1.28, 5.12, 3.84, 3.84, 3.2, 3.36, 4, 1.6, 4.32, 5.2, 4.48, 4.16 | 58 (0.65) | 27.9 / 62.2 | 1.26 | 4.67 | 0.95 |
| English, VOA Helix | `20261003T211318-52b199` | en | 19 | 0.46 / 0.48 | 1.46 / 4.98 | 1.02 / 4.34 | 2.74 | 5.12, 3.52, 2.56, 1.72, 2.32, 4.96, 3.92, 5.8, 1.28, 4.64, 4, 3.84, 3.2, 3.36, 4, 4.68, 6, 3.84, 4.48 | 59 (0.66) | 30.5 / 63 | 1.26 | 4.83 | 0.84 |
| Mandarin, VOA Norway | `20261003T211513-dddec5` | zh | 17 | 0.41 / 1.59 | 1.41 / 3.30 | 0.84 / 2.80 | 2.23 | 6.88, 1.36, 2.24, 3.56, 4.4, 3.24, 6.08, 6.08, 5.12, 4.16, 6.72, 3.84, 3.28, 5.12, 2.24, 2.24, 5.44 | 58 (0.81) | 33.9 / 70.9 | 1.47 | 2.6 | 1.18 |
| Mandarin, VOA Norway | `20261003T211653-13966f` | zh | 17 | 0.41 / 1.59 | 1.40 / 4.14 | 0.86 / 4.01 | 2.25 | 5.92, 2.28, 2.24, 3.56, 4.44, 2.64, 6.36, 6.08, 5.12, 4.16, 6.72, 3.84, 3.28, 5.12, 2.24, 2.24, 5.44 | 58 (0.81) | 36.2 / 65.2 | 1.53 | 2.96 | 1.18 |
| Mandarin, VOA Norway | `20261003T211833-3b72c7` | zh | 16 | 0.38 / 1.56 | 1.40 / 3.46 | 0.87 / 3.07 | 2.24 | 5.92, 4.52, 3.56, 4.44, 5.48, 3.56, 6.08, 5.12, 4.16, 6.72, 3.8, 3.28, 5.12, 2.24, 2.24, 5.44 | 56 (0.78) | 35.9 / 63.4 | 1.62 | 2.88 | 1.12 |
| Mandarin, VOA Taiwan | `20261003T212013-087857` | zh | 9 | 0.39 / 1.61 | 1.71 / 4.85 | 0.85 / 4.12 | 2.26 | 7.08, 2.32, 4.88, 4.8, 4.56, 3.88, 3.32, 2.84, 5.44 | 34 (0.85) | 39.1 / 67.2 | 1.67 | 4.2 | 1.11 |
| Mandarin, VOA Taiwan | `20261003T212121-7e3d61` | zh | 9 | 0.42 / 1.59 | 1.71 / 4.60 | 0.85 / 4.41 | 2.28 | 7.04, 3.36, 3.88, 2.56, 2.2, 4.6, 3.88, 6.52, 5.44 | 33 (0.82) | 37.2 / 70.4 | 1.56 | 4 | 0.89 |
| Mandarin, VOA Taiwan | `20261003T212229-39991a` | zh | 10 | 0.38 / 1.57 | 1.40 / 3.80 | 0.82 / 3.08 | 2.23 | 7.08, 3.36, 3.88, 2.56, 4.04, 2.6, 3.88, 3.32, 2.84, 5.44 | 33 (0.82) | 33.3 / 58.8 | 1.6 | 3.31 | 1 |
| Bengali, Maasranga | `20261003T212337-0d52ba` | bn | 14 | 0.78 / 1.72 | 2.73 / 4.51 | 1.49 / 3.51 | 2.06 | 4.88, 3.36, 5.16, 4.64, 4.68, 4.28, 3.92, 4.04, 2.64, 4.48, 4.6, 2.76, 3.6, 2.08 | 42 (0.7) | 33.1 / 66 | 1.29 | 4.33 | 1.21 |
| Bengali, Maasranga | `20261003T221202-5a4559` | bn | 14 | 0.78 / 1.68 | 2.88 / 4.70 | 1.48 / 3.70 | 2.02 | 4.88, 2.76, 5.32, 4.64, 5.68, 2.96, 3.92, 4.04, 3.68, 3.36, 2.88, 2.84, 2.8, 5.92 | 40 (0.67) | 28.8 / 55.2 | 1.07 | 5.2 | 1 |
| Bengali, Maasranga | `20261003T221330-91a181` | bn | 14 | 0.78 / 1.69 | 2.74 / 5.29 | 1.45 / 4.34 | 2.02 | 4.88, 2.76, 5.32, 4.64, 5.68, 2.96, 3.92, 2.84, 4.92, 3.36, 2.88, 2.88, 2.8, 5.92 | 39 (0.65) | 29.7 / 63.4 | 1.07 | 5.53 | 1.07 |
| Bengali, Wikitongues | `20261003T221456-832e9e` | bn | 29 | 0.71 / 1.64 | 2.07 / 2.96 | 1.11 / 1.43 | 2.63 | 1.92, 1.28, 1.92, 3.2, 3.36, 2, 2.56, 1.92, 3, 1.56, 2.56, 3.2, 1.92, 1.28, 2.56, 4.48, 2.56, 3.84, 3.2, 1.28, 3.2, 1.28, 2.56, 3.84, 1.92, 3.84, 1.92, 2.56, 2.56 | 38 (0.51) | 17.4 / 31.9 | 0.28 | 2.5 | 0.21 |
| Bengali, Wikitongues | `20261003T221635-e7645e` | bn | 28 | 0.78 / 1.81 | 2.08 / 2.96 | 1.11 / 1.43 | 2.14 | 4.64, 1.92, 3.2, 3.4, 1.88, 2.56, 1.92, 3.04, 1.56, 1.28, 3.2, 1.92, 1.28, 2.56, 4.48, 2.56, 3.84, 3.2, 1.28, 1.92, 1.28, 2.56, 3.84, 4.48, 1.28, 1.92, 2.56, 2.56 | 38 (0.51) | 17.9 / 34.1 | 0.32 | 2.56 | 0.21 |
| Bengali, Wikitongues | `20261003T221813-7ad340` | bn | 29 | 0.70 / 1.02 | 2.07 / 2.96 | 1.12 / 1.43 | 2.09 | 1.92, 1.28, 1.92, 3.2, 3.36, 2, 2.56, 1.92, 3, 1.56, 2.56, 3.2, 1.92, 1.28, 2.56, 4.48, 2.56, 3.84, 3.2, 1.28, 3.2, 1.28, 2.56, 3.84, 1.92, 3.84, 1.92, 2.56, 2.56 | 37 (0.49) | 18.6 / 32.7 | 0.24 | 2.57 | 0.17 |



Per clip, the range over its three runs (p50 of the clip's lines in each run):

| Clip | Detected | Lines | First translated text p50 (s) | Final translation p50 (s) | Non-append revisions per line | Drafts per line | First confirmed subtitle (s) | Speech: words (zh: characters) / min, median pause |
|---|---|---|---|---|---|---|---|---|
| **Mandarin, VOA Norway** | zh, 3 of 3 | 16-17 | **1.40-1.41** | 0.84-0.87 | 1.12-1.18 | 1.47-1.62 | 2.23-2.25 | 277, 0.40 s |
| Mandarin, VOA Taiwan | zh, 3 of 3 | 9-10 | 1.40-1.71 | 0.82-0.85 | 0.89-1.11 | 1.56-1.67 | 2.23-2.28 | 275, 0.42 s |
| English, NASA | en, 3 of 3 | 7 | 1.44-1.48 | 1.03-1.05 | 0.86 | 1.29 | 3.69 | 162, 0.46 s |
| English, VOA Helix | en, 3 of 3 | 19-20 | 1.46-1.62 | 1.02-1.03 | 0.75-0.95 | 1.25-1.26 | 2.71-2.75 | 137, 0.36 s |
| Bengali, Wikitongues | bn, 3 of 3 | 28-29 | 2.07-2.08 | 1.11-1.12 | 0.17-0.21 | 0.24-0.32 | 2.09-2.63 | 72, 0.92 s |
| Bengali, Maasranga | bn, 3 of 3 | 14 | 2.73-2.88 | 1.45-1.49 | 1.00-1.21 | 1.07-1.29 | 2.02-2.06 | 127, 0.40 s |

**The fastest clip is the Mandarin VOA Norway news item**: first translated text 1.40-1.41 s after a line's first
word in 3 of 3 runs (the Taiwan item ties in one run and is 1.71 s in two). `docs/demo-fastest.gif` in the README
is a recorded run of it (run `20261003T222146-78982f`, the whole file with its picture, `make_demo_gif.py --clip`,
Auto-detect, target English: Chinese confirmed 2.18 s after play, first translated text p50 2.0 s over its 6
lines in 22 s; one run with the video decoded in the browser as well).

**Speech rate and pauses explain much of the difference between clips:**

- **A draft needs a stable prefix of three words (four characters in Mandarin), and the faster the speaker, the
  sooner it is there.** The two VOA news readers speak 275-277 characters a minute (390-430 while speaking) with
  no pause longer than 0.8 s: four characters are in the air within a second, the third partial that confirms them
  follows at the recognizer's 0.32 s chunk, and the first draft is on screen 1.4 s after the line began. The
  English narrators (137-162 words a minute) reach three stable words a little later: 1.44-1.62 s. The same rule
  cuts the other way on the Bengali news report (127 words a minute, but the Bengali recognizer decodes in 0.64 s
  chunks, so three partials in a row take 1.9 s): 2.7-2.9 s.
- **Long pauses close lines, and a closed line's final translation comes sooner than a draft would.** The
  Wikitongues speaker pauses every other second (45 pauses in 75 s, median 0.92 s, 61 % of the time; 34 of them at
  or over the 0.5 s that ends a line), so his 75 s are 28-29 short lines (1.3-4.5 s) and most of them end before
  the three-partial rule has anything to show: 0.24-0.32 drafts per line, and the "first translated text" is in
  most lines the final translation itself, 2.07 s after the first word. That is faster than the Bengali news
  report's drafts (2.7-2.9 s) and nearly free of rewrites: 0.17-0.21 non-append revisions per line against
  1.0-1.2 everywhere else. Slow, paused speech is the easy case for this design; the final translation after the
  last word (1.11 s) is also the fastest Bengali number in the table.
- **Continuous speech runs into the 6 s cut.** The news readers' lines are closed by the length rule as often as
  by a pause (Norway: lines of 5.1-6.9 s in every run; Taiwan: a 7.1 s first line), and a line cut by the length
  rule gets its final only once it has run past 6 s: that is the 2.8-4.4 s "final max" of the news clips against
  1.4 s on the Wikitongues clip. The pause rule needs 0.5 s between words; a news reader rarely gives it.
- **The language's chunk size sits under everything.** First display of a line's source text is 0.38-0.47 s for
  English and Mandarin and 0.70-0.78 s for Bengali (0.32 against 0.64 s decode chunks), the final after the last
  word 0.82-1.05 s against 1.11-1.49 s. Within a language the clips differ by speech rate and pauses; between
  languages by the chunk (observations A9).
- **Noise costs recognition, not latency.** The Helix report's parade and street interviews gave the recognizer
  the clip's worst text ("SAUCILLATO CALIFORNIA" for Sausalito), but its latencies sit with the NASA narration
  (1.46-1.62 s), and its 10 s stretch without recognised words is a gap in the table's lines, not a slow line.

**Auto-detect.** Every clip was confirmed in its language in 3 of 3 runs: Mandarin 2.23-2.28 s after play,
Bengali 2.02-2.63 s, English 2.71-2.75 s (Helix) and 3.69 s (NASA, whose first words are slower; English is
confirmed on the full window only). Translation calls 0.49-0.85 per second of clip, p50 17-39 ms, p95 32-71 ms on
the CPU.

**What came out** (run 1 of each, the first lines, errors included; the full lines are not kept, the run logs hold
lengths only): Norway, "…commended the police for last Friday's shooting bombing, which killed 76 people." /
"On Tuesday, the Norwegian Minister of Justice, Mr. Krutstobogait, said:"; Taiwan, "Let's continue and see if
Taiwan is ready to host a powerful typhoon."; NASA, "在卫星和空间站领域" / "中午的天空和晚上一样黑"; Helix,
"有行军乐队和多彩的浮标,但也有" / "飞车"; Maasranga, "Tobikul Islam was brought to Dhaka for better treatment";
Wikitongues, "My name is Sanjay." / "Ami" / "Calcutta" / "I was born in Kolkata and": one short line per
pause, and a one-word line where he paused inside a sentence ("Ami" is the word আমি, "I", which OPUS-MT left as
it was: a one-word line is the price of cutting at every pause).

## Language prior and parallel recognizers (2026-10-04)

Until now Auto-detect always started with the English recognizer, and a switch to Mandarin or Bengali needed two
agreeing language-ID attempts (after 1.0 and 1.5 s of speech), so a Mandarin or Bengali viewer waited 2.0-2.6 s
for the first subtitle in a confirmed language, and English, confirmed on the full 2.5 s window only, 2.7-3.7 s.
Three changes (`docs/page-prior.md` has the evaluation behind the first):

- **A prior from the page and from memory.** On Start the extension reads the page (`lib/language-prior.js`): the
  script of the title, channel name and description as one vote, YouTube's own caption language and default audio
  track, and the language that language ID last confirmed on the same YouTube channel (elsewhere: the same site),
  and sends `{en, zh, bn}` probabilities in the `/ws/asr` handshake. The first recognizer is the prior's favourite
  when it reaches 0.6 (`SUBTITLE_ASR__LID_PRIOR_THRESHOLD`), else English as before.
- **Rules for language ID with a prior.** One attempt above the floor confirms the favourite (English: at 0.97 or
  more, `SUBTITLE_ASR__LID_PRIOR_FLOOR_EN`); switching away from it needs two agreeing early attempts as before, and
  English is a switch target only from the full window on, as before. Both exceptions come from whisper-tiny's habit
  of answering English, at up to 0.97, on the first second of Mandarin and Bengali (`docs/page-prior.md`).
- **Parallel recognizers.** Until the language is confirmed every recognizer hears the audio (at most 5 s,
  `SUBTITLE_ASR__PARALLEL_WINDOW_S`): at confirmation the confirmed one's line is already there and is sent again,
  confirmed (the overlay undims it at that moment instead of at the next partial), or the confirmed one takes over
  with what it wrote, without replaying the buffered audio. This needed one thread per recognizer
  (`SUBTITLE_ASR__NUM_THREADS` 2 -> 1), see "CPU" below.

**How it was measured.** Every clip played on its source page, so that the page prior is the real one: the six
benchmark clips on their Wikimedia Commons file pages (the harness clicks the page's play button, `--click
a.mw-tmh-play`; NASA from 0:09 as in the benchmark) and the film scene on YouTube; plus a cold case, a YouTube
channel never seen with a title in the wrong language (the Durga Pujo vlog of `docs/page-prior.md`: romanised
Bengali title, YouTube's caption language English, Bengali speech, from 0:30). Three browser runs per clip and side
(six for the two clips whose prior is wrong), no priming by the harness, a fresh browser profile every run (so the
channel memory is empty: only the page counts), Auto-detect, target English (Chinese for English speech).
**Before** is the code of commit `8d1abdb`, run from an export of it with `--backend-dir` / `--extension` and the
same harness. "First confirmed subtitle" counts from the video starting to play; "decided at attempt" is the
language-ID attempt that confirmed (1 = after 1.0 s of speech, 4 = the full window, 5 = the one after it).

| Clip (page) | Prior (votes) | Prior right? | First confirmed subtitle before (s) | After (s) | After: decided at attempt | Detected before / after | run_ids before | run_ids after |
|---|---|---|---|---|---|---|---|---|
| English, NASA | en 0.65 (pageScript:en) | yes | 3.71, 3.73, 3.70 (median 3.71) | 1.91, 1.90, 1.87 (median 1.90) | 1, 1, 1 | en, en, en / en, en, en | `20261004T004830-d28d66`, `20261004T004900-bf22df`, `20261004T004931-a46d0b` | `20261004T004658-37dd78`, `20261004T004729-5794a7`, `20261004T004759-3b61c8` |
| English, VOA Helix | en 0.65 (pageScript:en) | yes | 2.76, 2.83, 2.81 (median 2.81) | 2.74, 1.71, 2.29 (median 2.29) | 4, 2, 3 | en, en, en / en, en, en | `20261004T005131-2c0e87`, `20261004T005200-2a6b28`, `20261004T005231-24ed00` | `20261004T005003-93b71c`, `20261004T005033-047df4`, `20261004T005100-339184` |
| Mandarin, VOA Norway | zh 0.85 (pageScript:zh) | yes | 2.30, 2.31, 2.32 (median 2.31) | 1.73, 1.68, 2.18 (median 1.73) | 1, 1, 2 | zh, zh, zh / zh, zh, zh | `20261004T005440-b95459`, `20261004T005513-c46cfe`, `20261004T005544-77a67a` | `20261004T005302-1bb530`, `20261004T005335-e980f1`, `20261004T005407-72dfe5` |
| Mandarin, VOA Taiwan | zh 0.85 (pageScript:zh) | yes | 2.23, 2.28, 2.26 (median 2.26) | 1.62, 1.59, 1.57 (median 1.59) | 1, 1, 1 | zh, zh, zh / zh, zh, zh | `20261004T005751-cefe3f`, `20261004T005821-967eaf`, `20261004T005853-723890` | `20261004T005615-20a409`, `20261004T005647-562964`, `20261004T005718-261a81` |
| Bengali, Maasranga | bn 0.85 (pageScript:bn) | yes | 2.08, 2.05, 2.06 (median 2.06) | 1.50, 1.48, 1.51 (median 1.50) | 1, 1, 1 | bn, bn, bn / bn, bn, bn | `20261004T010054-402a84`, `20261004T010126-2fe13a`, `20261004T010158-5d63a2` | `20261004T005925-cbd5a9`, `20261004T005953-440c44`, `20261004T010023-d7c7f7` |
| Bengali, Wikitongues | en 0.65 (pageScript:en) | no | 2.11, 2.44, 2.01, 3.16, 4.71, 2.36 (median 2.40) | 3.07, 3.12, 4.53, 2.02, 3.08, 2.97 (median 3.08) | 4, 4, 5, 2, 4, 4 | bn, en, bn, bn, bn, bn / bn, bn, bn, bn, bn, bn | `20261004T010357-e12978`, `20261004T010424-e924a3`, `20261004T010453-59094c`, `20261004T011323-3f8b6b`, `20261004T011350-f0febf`, `20261004T011419-431480` | `20261004T010229-0a0f0a`, `20261004T010300-0fc800`, `20261004T010327-168cba`, `20261004T011203-79d95d`, `20261004T011230-fe319b`, `20261004T011258-07a54b` |
| Bengali film scene (live clip) | bn 0.98 (captionAsr:bn, pageScript:bn) | yes | 1.86, 1.86, 2.34 (median 1.86) | 1.29, 1.28, 1.32 (median 1.29) | 1, 1, 1 | bn, bn, bn / bn, bn, bn | `20261004T010641-edfba5`, `20261004T010707-e6eb90`, `20261004T010733-17615e` | `20261004T010519-3c1d44`, `20261004T010546-08a3c7`, `20261004T010614-2db63a` |
| Cold case: Durga Pujo vlog (Bengali, romanised title) | en 0.94 (captionAsr:en, pageScript:en) | no | 4.69, 1.88, 1.24, 2.98, 2.96, 4.65 (median 2.97) | 2.82, 1.82, 4.01, 1.85, 1.83, 1.88 (median 1.86) | 4, 2, 5, 2, 2, 2 | zh, bn, zh, bn, bn, zh / bn, bn, zh, bn, bn, bn | `20261004T010941-20e9ec`, `20261004T011022-1ad95a`, `20261004T011049-42bd78`, `20261004T011621-38d1ce`, `20261004T011653-d01c00`, `20261004T011724-e89241` | `20261004T010801-c1c000`, `20261004T010836-f9c19f`, `20261004T010909-b45e5d`, `20261004T011446-20707c`, `20261004T011517-ba2686`, `20261004T011549-55b1c2` |

**What moved.**

- **Prior right (6 of the 7 benchmark pages, every run):** the first confirmed subtitle came 0.5-1.8 s earlier
  (median against median).
  Mandarin 2.23-2.32 -> 1.57-2.18 s, Bengali 1.86-2.34 -> 1.28-1.51 s, English 3.70-3.73 -> 1.87-1.91 s (NASA) and
  2.76-2.83 -> 1.71-2.74 s (VOA Helix). Language ID decided at its first attempt in 14 of the 18 runs; the others
  are VOA Helix, where whisper-tiny's first answer on the field report's noise was undecided or under the 0.97
  floor in all three runs (0.57 undecided, English 0.74, English 0.88 then 0.96), and one Norway run whose first
  attempt was undecided.
- **Under 1 s: not reached.** The best is the live clip at 1.28-1.32 s. With a right prior the subtitle is on screen
  0.00-0.08 s after the decision (the session's audio at the decision against the first confirmed subtitle's time
  since the session's first frame, every run), and the decision is at the first attempt, after 1.0 s of speech;
  what remains is where the speech starts in the clip (NASA's narrator starts about a second in) and the capture's
  start. A shorter first window would trade that second against
  wrong decisions: at 0.5-0.75 s of speech whisper-tiny said English at 0.97 or more on 10 of 46 Mandarin and
  Bengali starts (`docs/page-prior.md`). It is not built.
- **Prior wrong (Wikitongues: an English file name on Bengali speech).** The first recognizer is English, as
  before, and the rules for this case are the ones before; still, over six runs a side it was slower: median 2.40
  -> 3.08 s. Two early answers in a row agreed on Bengali in 2 of 6 runs before and 1 of 6 after; the other runs
  waited for the full window on both sides, and their windows fell later after (the full window at 2.97-3.12 s
  against 2.36-3.16 s, the same rule on the same page). The clip is a hard one: the speaker mixes English words
  into Bengali and pauses every other second. The language was right in 6 of 6 runs after, 5 of 6 before (one
  confirmed English).
- **Cold case (wrong English prior from the title and YouTube's caption language):** median 2.97 -> 1.86 s, and the
  wrong language (Mandarin) confirmed in 1 of 6 runs after against 3 of 6 before. Not a regression; most of the
  difference is run-to-run luck in whisper-tiny's answers on this clip, both sides use the same switch rule.
- **Parallel recognizers** were stopped at the decision in every run (`asr_parallel` record, reason `confirmed`);
  all 12 language switches after (the two wrong-prior clips) were a parallel recognizer taking over, none replayed
  any audio.

**CPU and memory of the parallel window** (the live clip through the real session offline, real recognizers and
language ID, one 40 ms frame every 40 ms; process CPU time over wall time, so 1.0 = one core):

| | `NUM_THREADS` 2 (before) | `NUM_THREADS` 1 (now) |
|---|---|---|
| one recognizer stream | 1.53 | 0.06 |
| two streams | 3.83 | 0.15 |
| three streams | 6.07 | 0.19 |
| language ID every 0.5 s | 1.57 | 0.09 |
| the whole session during the parallel window (three streams + language ID) | about 6 | **0.18-0.24** |
| the whole session, one recognizer (no parallel window) | about 2.9 | 0.07-0.09 |
| one decode chunk, feed() p95 | 16.5-21.4 ms | 19.5-24.2 ms |
| one language-ID call | 19-20 ms | 29 ms |

With two threads, onnxruntime's thread pools spin between the 40 ms frames: most of the CPU time was waiting, not
decoding, and a single caption session already cost about three cores of the i9 before this work. One thread
costs 3-4 ms per decode chunk and 10 ms per language-ID call; the window costs 0.18-0.24 of one core (the bound
was 0.5). The two extra streams add up to 14 MB to a backend of about 730 MB. The feed() that switches language
takes 27-38 ms with the parallel recognizer against 73-166 ms for the replay.

The headline GIF is not re-recorded: it plays the clip on a local page whose title is English, so its first guess
is English as before, and the confirmation it shows would look the same.

## Channel memory on a second visit (2026-10-05)

The cold case above is a channel never seen. On a second video of the same channel the prior also
carries the channel memory: the language that language ID confirmed there last time. Measured on
the cold case itself (the Durga Pujo vlog: romanised title and YouTube's English caption language,
so the page alone says en 0.94; Bengali speech; from 0:30, as above), opened twice in one browser
profile (harness `--profile DIR`, which keeps the profile and the extension's storage between runs),
so that the memory is the only difference between the visits. Not another video of the channel
for the first visit: on 9 of 10 stretches of its other videos (six videos, Bengali or romanised
titles, from 0:00 to 0:30) whisper-tiny confirmed English or Mandarin, with the prior right or not
(`docs/page-prior.md`, "Channel memory").

**The memory lost before this change.** With its weight at 0.9, a remembered Bengali against that
page gave en 0.61, bn 0.37: English stayed the first recognizer and Bengali came by a switch at
attempt 2 or 4 (browser test `backend/tests/test_channel_memory_browser.py`, red on the old weight).
The weight is now 0.97, the least that beats both wrong signals with a margin (0.959 needed); the
prior on the second visit is bn 0.678 (`captionAsr:en`, `pageScript:en`, `memory:bn`).

| Run | First visit (empty memory): confirmed, attempt, first confirmed subtitle | Second visit: prior favourite | Second visit: confirmed, attempt | Second visit: first confirmed subtitle (s) | run_ids (first / second) |
|---|---|---|---|---|---|
| pair 3 | bn, 2, 1.77 s | bn (memory) | bn, 1 | **1.25** | `20261005T003637-f8c757` / `20261005T003727-19e095` |
| pair 4 | bn, 2, 1.80 s | bn (memory) | bn, 1 | **1.26** | `20261005T003757-1adc37` / `20261005T003829-d99329` |
| seeded 1 | (memory written: bn, hits 1) | bn (memory) | bn, 3 | **2.31** | - / `20261005T004221-8631f4` |
| seeded 2 | (memory written: bn, hits 1) | bn (memory) | bn, 1 | **1.29** | - / `20261005T004255-002855` |
| seeded 3 | (memory written: bn, hits 1) | bn (memory) | bn, 1 | **1.42** | - / `20261005T004332-924cca` |
| pair 1 | **zh**, 4, 4.05 s | zh (a wrong memory) | bn by a switch, 4 | 3.02 | `20261005T003123-804c10` / `20261005T003214-496da2` |
| pair 2 | **zh**, 3, 1.98 s | zh (a wrong memory) | bn by a switch, 2 | 2.15 | `20261005T003443-a86475` / `20261005T003542-8a145b` |

**Second open with a right memory: 1.25-2.31 s, median 1.29 s** (five runs), against 1.77-1.80 s for
the first visit that day and a median of 1.86 s for the cold case on 2026-10-04: in the range of the
right-prior rows above (Bengali runs 1.28-1.51 s, all right-prior runs 1.28-2.74 s). Four of the five confirmed at
the first attempt; seeded 1 waited for the third (whisper-tiny: bn 0.57 under the floor, then zh
0.71, then bn 0.93), never switching. "Seeded" runs had the memory written with `--memory` as a
right first visit leaves it, because the first visit's own answer is whisper-tiny's and varies on
this stretch: Bengali in 5 of 6 runs on 2026-10-04, Mandarin in 3 of 6 first visits on 2026-10-05.

**A wrong memory** (pairs 1 and 2: the first visit confirmed Mandarin) made Mandarin the second
visit's first recognizer; language ID switched to Bengali (2.15 and 3.02 s), and the miss left the
memory as Bengali at half weight (0.485, under the threshold on its own: the browser test
`test_a_memory_wrong_once_drops_under_the_threshold` checks that). A wrong memory costs one
session, then the channel's page decides again until a right confirmation is remembered.
