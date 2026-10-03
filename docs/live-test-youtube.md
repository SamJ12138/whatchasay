# Live test on a YouTube video (2026-10-02)

The real pipeline on a real YouTube page: the unpacked extension in Playwright's Chromium (headful), tab capture of
the page's own `<video>`, the backend on this laptop with the default engines (Zipformer ASR, whisper-tiny language
ID, OPUS-MT), everything on the CPU (Intel i9-13900H, `SUBTITLE_TRANSLATION__DEVICE=cpu`), spoken language
**Auto-detect**, target English unless the speech is English (then Chinese). No consent wall or bot check appeared;
no ad played before or during the runs. The full transcript is not stored anywhere: the harness keeps the first five
translated lines and counts the rest.

| | |
|---|---|
| Video | "10,000 রাজভোগের Order \| Ora Char Jon \| Movie Scene \| Prosenjit \| Abhishek Chatterjee \| Debashree" |
| Channel | Bengali Movies with English Subtitle (`@angelmoviessubtitle`) |
| URL | https://www.youtube.com/watch?v=-tpVpbIxFmI |
| Speech | Bengali (film dialogue, two to three speakers, room noise) |

```
backend/scripts/e2e_extension.py --start-backend --path audio --url https://www.youtube.com/watch?v=-tpVpbIxFmI \
    --source auto --targets auto --lines 5 --hold 90 --timeout 60 --headful --size 960x540 --font-size 28 --record DIR
```

(with `SUBTITLE_TRANSLATION__DEVICE=cpu` and `SUBTITLE_ASR__MODELS_DIR` / `SUBTITLE_ASR__PUNCT_DIR` set). The video
is paused and put back to 0:00, capture starts, then the video plays; times below are from that play.

## Before: whisper-tiny's own answer (commit `455a343`)

| Run | run_id | Detected language | What whisper-tiny said | Result |
|---|---|---|---|---|
| Run 1 | `run_20261002T132801-3f08e6` | none | (no capture) | harness bug: its readiness check was a JavaScript syntax error, so no video was ever "ready" |
| Run 2 | `run_20261002T133353-85b0c2` | Bengali, attempt 1 (3.96 s of audio) | `bn` | captions and translations, but into English and Chinese (harness bug: `--targets` was not applied), summary lost (GBK console) |
| Run 3 | `run_20261002T133703-391c87` | none: fallback, English kept | `ne` (3.92 s), then `hi` (6.4 s) | Bengali speech run through the English recognizer, nothing translated |
| Run 4 | `run_20261002T133849-8532a3` | none: fallback, English kept | `hi` (3.92 s), then `hi` (6.36 s) | same as run 3 |

Two of the three complete runs (2-4) got the language wrong: whisper-tiny's argmax over its ~100 languages was a
neighbour of Bengali, outside the supported set, so the session kept its provisional English.

## After: language ID constrained to {en, zh, bn} (commit `f0e40e7`)

The choice is now the most probable of English, Mandarin and Bengali in whisper-tiny's distribution, renormalised,
if it is at least 0.6 (`asr.lid_min_confidence`); whisper's unconstrained top 3 is logged on every call (`lid_scores`).

| Run | run_id | Detected language | whisper-tiny top 3 (unconstrained) | Constrained bn | Time to first subtitle | First Bengali caption / first translation | Translated cues | translate p50 | translate p95 |
|---|---|---|---|---|---|---|---|---|---|
| A | `run_20261002T140941-8f9b8b` | Bengali, confirmed at 5.3 s (attempt 1) | hi 0.63, bn 0.27, as 0.04 | 0.999 | 1.0 s | 5.3 s / 10.7 s | 8 | 43.6 ms | 148.1 ms |
| B | `run_20261002T141143-7eb376` | Bengali, confirmed at 5.5 s (attempt 1) | hi 0.66, bn 0.10, es 0.08 | 0.982 | 1.1 s | 5.5 s / 5.5 s | 10 | 35.2 ms | 115.8 ms |
| C | `run_20261002T141338-35728b` | Bengali, confirmed at 5.4 s (attempt 1) | hi 0.44, es 0.23, bn 0.09 | 0.974 | 1.1 s | 5.4 s / 5.4 s | 10 | 34.7 ms | 114.5 ms |

- **Time to first subtitle** is the first line in the overlay after the play started (read every 250 ms). For the
  first 5 seconds that line comes from the provisional English recognizer, so it is the wrong language; after the
  language ID the overlay is reset and the Bengali captions start, with the buffered audio replayed. (Since
  `d6cb5e2` that line is drawn dimmed under *Detecting language…*; numbers from this page in the last section.)
- **Translate p50 / p95** are the backend's `translate` stage in each run log (`backend/scripts/failure_report.py
  backend/logs/run_<run_id>.jsonl`); over all 28 translations: p50 41.3 ms, p95 115.8 ms. Run A's first sentence ran
  10 s without a pause and was cut there (the 10 s utterance limit), hence its late first translation.
- Language ID itself took 862-896 ms per call (`lid` stage), up from 177-560 ms with sherpa-onnx's own call: every
  window is padded to whisper's 30 s. It runs once per session while the provisional captions continue.
- The audio sessions left no rows in the translation memory (before 0, after 0 in every run).

## Five translated lines (run C, `run_20261002T141338-35728b`)

As shown on screen: the Bengali as the recognizer heard it, and its English translation.

| # | Bengali (recognised) | English (OPUS-MT, CPU) |
|---|---|---|
| 1 | একটা রাজবুক দেখা তো | A royal book. |
| 2 | এর থেকে বড়সাইজের হয় না এর থেকে তো বয়সাইতে হয় না কী মশায় আপনার এত বড় দোকান আরেক থেকে বড় চাই যে রাজবুক হবে না দিনকাল যা পড়েছে বোঝে নিত | It doesn't have to be bigger than that of a mosquito who wants to grow up in your mosquito shop so big that it will not be a good time to buy the royal book. |
| 3 | এর চেয়ে বড় করতে গেলে অনেক দাম পড়ে যায় | It's too expensive to raise it. |
| 4 | অর্ডার না দিলে হয় না কিন্তু কাল যে আমার দশ হাজার রাজবুক লাগবে দশ হাজার | Not if you don't order it, but tomorrow I need ten thousand royalbooks. |
| 5 | কি রে হবে | What's going on? |

What went wrong in them: the sweet in the title, রাজভোগ (*rajbhog*), was recognised as রাজবুক ("royal book")
every time; মশায় ("sir") became "mosquito"; line 2 is several sentences spoken without a pause, which OPUS-MT
turns into one sentence that loses the meaning. Lines 3 and 4 carry the scene (it would cost too much to make them
bigger; "I need ten thousand tomorrow").

## A glossary entry or a correction for রাজভোগ (2026-10-02, after `d6cb5e2`)

The task: add রাজভোগ → rajbhog the way a user would, run the live test once more, compare line 1.

**The glossary cannot do it.** There is nowhere to enter a term: the Options page has no glossary, and Alt+E edits a
whole translated line. The translation memory has a `glossary` table and two functions for it
(`backend/app/cache/translation_memory.py`), but nothing calls them and the translation pipeline never reads the
table. An entry would not act on this error anyway: the recognizer never writes রাজভোগ (below), and the translator
does not know the word even when it is spelled right. `docs/observations.md` T13 has the details and what the fix
would be.

**What the user path does offer: Alt+E on line 1.** Same command as above with `--correct --correct-text "Show me a
rajbhog." --tm FILE` (a scratch translation memory kept between the runs), then the live test again with that file.

| | run_id | Line 1, Bengali (recognised) | Line 1, English on screen |
|---|---|---|---|
| Before | `run_20261002T145420-461f73` | একটা রাজবুক দেখান্ত | A Royal Book Shower |
| Correction | same run | (Alt+E on that line) | typed: Show me a rajbhog. Saved: `/stats` `user_corrections` 0 → 1 |
| After (one rerun, 90 s) | `run_20261002T145713-909850` | একটা রাজবুক দেখা তো এর থেকে বড়সায় জের হয় না এর থেকে তো বয়সাই হয় না কী মশায় আপনার এত বড় দোকান আরেক থেকে বড় চাই যে রাজব | Seeing a book is no big deal, you're not even older than it's yours in a mosquito that wants to be bigger than that of the old man. |

**Line 1 was not fixed.** A correction is stored for the source sentence exactly as it was recognised, and it is used
again only when that sentence comes back letter for letter. The recognizer wrote the same two seconds of speech
differently in every pass:

| Pass | What it wrote for the scene's first sentence |
|---|---|
| run C above (`run_20261002T141338-35728b`) | একটা রাজবুক দেখা তো |
| `run_20261002T145420-461f73` | একটা রাজবুক দেখান্ত |
| `run_20261002T145640-dd1c94` | একটা রাজবুক দেখান্ত এর থেকে বড়সায় জের হয় না এর থেকে তো বয়স হয় না (run together with the next sentence) |
| `run_20261002T145713-909850` (the rerun) | the 10-second line in the table, cut by the utterance limit in the middle of a word ("…যে রাজব", then "গাবে না…") |
| the recorded 20 s through the recognizer alone, offline | কাজ বুক দেখা তো |

The recorded audio played from a local page three times (spoken language set to Bengali twice, Auto-detect once)
lost the first sentence at the start of capture and wrote the rest differently each time, so no setting was found
in which a stored sentence returns. Corrections hold where
the source text repeats exactly: page subtitles in caption mode, and a sentence the recognizer happens to write the
same way twice (`tests/test_live_corrections.py`). `docs/observations.md` T14.

**Found by the first Alt+E run: typing the correction operated YouTube's player.** The keys bubbled out of the overlay
to the page, where `m` mutes, space pauses and `j` rewinds ten seconds; after "Show me a rajbhog." the video was
muted, paused and at 0:00 (`run_20261002T145420-461f73`: before `{muted: false, paused: false, t: 6.8}`, after
`{muted: true, paused: true, t: 0.04}`). Fixed (the edited line stops its key events; observations E18), and run
again on a fresh scratch memory: `run_20261002T145640-dd1c94`, video untouched (`t` 8.45 → 8.82, not muted, not
paused). That second run's line 1 was the merged sentence, so the rerun above used the first run's memory, whose
correction is on the short sentence.

**Provisional text on this page** (the overlay read through CDP every 250 ms, three runs): first caption 1.1-1.25 s
after play, dimmed, Latin script (the English recognizer's guess), in 15-16 samples per run; language confirmed at
5.1-5.3 s; the first undimmed line appeared in that same sample; no undimmed line before it and no dimmed line or
label after it (381 samples in the 90 s rerun).

Two offline checks on what a fix has to deal with (scratch scripts, not in the repository):

- Recognizer biasing (sherpa-onnx hotwords, `modified_beam_search`, hotword রাজভোগ) on the recorded 20 s: no effect at
  score 1.5; at 2.5 and 4.0 the first sentence disappeared; রাজভোগ never came out.
- OPUS-MT bn→en on line 1 with the word replaced: রাজভোগ (spelled right) → "See you in a bar."; `rajbhog` put into
  the Bengali sentence → "I've met a rajbug". Line 4 the same way: "ten thousand kings", "ten thousand rajbeg".

## After the latency batches (2026-10-02, `docs/latency.md`)

One more live run on the same page after the streaming work (bounded lines, draft translations, earlier language
ID), the run `docs/demo-youtube.gif` is cut from. Same command, plus `--prime-audio` (the tab's audio output is
already running when the video starts, as over a video that is already playing; without it the first sentence does
not reach the capture, observations A11), 40 s of captions kept running after the first translation.

| | Before (runs A-C above) | After (`run_20261002T172649-3fac9e`) |
|---|---|---|
| Detected language | Bengali, confirmed at 5.3-5.5 s (attempt 1) | Bengali, confirmed at 1.6 s (two early attempts agreeing; constrained bn 0.99) |
| First subtitle in the confirmed language | 5.3-5.5 s after play | 1.6 s after play |
| First translation on screen | 5.4-10.7 s after play | 1.9 s after play |
| Lines in the first 45 s | 8-10 translated cues in 90 s, one of them 9.6 s long | 13 lines, the longest 6.2 s from the end of the line before it |
| First translated text after a line's first word (p50 / slowest) | not measured (a line's translation came after its last word) | 2.0 s / 2.6 s |
| Final translation after a line's last word (p50 / slowest) | not measured | 1.1 s / 1.5 s |
| Draft rewrites per line (non-append revisions) | none (no drafts) | 1.2 (16 over 13 lines) |
| Translate calls, p50 / p95 | 8-10, 34.7-43.6 ms / 114.5-148.1 ms | 31, 25.9 ms / 56.2 ms |
| Translation memory rows written | 0 | 0 |

The first sentence came out as "একটা রাজবুক দেখেন তো" -> "Look at a royal book." 1.9 s after the video started, and
the 9.6 s run-on of the first test as three lines ("এর থেকে বড় সাইজের হয় না …" -> "It doesn't have to be any older
than this.", "কী মশায় আপনার এত বড় দোকান …" -> "What the mosquito wants you to buy is bigger than that",
"দিনকাল যা পড়েছে বোঝে নিত" -> "I know what they've read."). The mistakes are the recognizer's and the translator's,
as before: রাজভোগ is still heard as রাজবুক, and মশায় ("sir") is still a mosquito.


## After the overlay batches (2026-10-02, `docs/overlay/README.md`)

The run `docs/demo-youtube.gif` is now cut from: the same command plus `--prime-audio --fullscreen` (the player
put into fullscreen with YouTube's `f` key, so the subtitles sit over the bottom of the picture; on the normal
watch page they sit below the player). Run `run_20261002T192730-1b17fa`: Bengali confirmed about 2 s in, the first
subtitle in the confirmed language 1.9 s after play, the first translation on screen 2.4 s after play, 10 lines in
45 s, first translated text 2.1 s after a line's first word (p50), final translation 1.1 s after its last word
(p50), 1.7 draft rewrites per line, no rows written to the translation memory. The pipeline is the one of the
latency batches; only the overlay changed (translation only, two rows, over the bottom 15 % in fullscreen).

## Rolling rows (2026-10-02, `docs/overlay/README.md`)

`docs/demo-youtube.gif` is now cut from run `run_20261002T200449-0fa35c` (same command as the section above:
`--prime-audio --fullscreen`): Bengali confirmed about 2.4 s in, the first translation on screen 2.7 s after play,
11 lines in 45 s, first translated text 1.8 s after a line's first word (p50), final translation 1.2 s after its
last word (p50), 1.6 draft rewrites per line. The previous line's final translation now stays on the upper row
while the next line streams below it: shortest time a final was on screen 2.5 s (0.5 s before the rolling rows,
run `20261002T194945-ea5b15` vs `20261002T200253-469a90`).

## After the draft follow-ups (2026-10-02, `docs/latency.md`)

`docs/demo-youtube.gif` is now cut from run `run_20261002T214030-e46964` (`--fullscreen --record`, no `--prime-audio`:
the extension keeps the tab's audio output running itself since A11 was fixed): the first line is the clip's opening
sentence, Bengali confirmed 2.3 s in, the first translation on screen 2.4 s after play, 11 lines in 45 s, first
translated text 2.7 s after a line's first word (p50), 0.7 draft rewrites per line (local agreement, K = 2, and
no draft before three stable words), lines wrap once instead of losing their start ("What mosquitos are so big
that it won't / turn into a king?"). রাজভোগ is now heard as "A Royal Book Shower"; the mosquito is still a mosquito.
