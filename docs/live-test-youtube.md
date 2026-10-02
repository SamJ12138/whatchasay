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
  language ID the overlay is reset and the Bengali captions start, with the buffered audio replayed.
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
