# Deciding the spoken language earlier than whisper-tiny: alternatives measured

With a right page prior the first confirmed subtitle comes 1.3-2.3 s after play (`docs/latency.md`,
"Language prior and parallel recognizers"), and most of that is language ID waiting for 1.0 s of
voiced audio: whisper-tiny's first attempt. Shorter windows were measured unsafe with whisper-tiny
(`docs/page-prior.md`). This page measures two other ways to decide at 0.5 or 0.75 s, offline, before
anything is built. **Nothing is picked; nothing beat whisper-tiny at 0.5 or 0.75 s (below).**

## What was measured

**Audio.** The starts of `docs/page-prior.md`: the seven benchmark clips (NASA, VOA Helix, VOA
Norway, VOA Taiwan, Maasranga, Wikitongues, the YouTube film scene) and the three models' sample
WAVs, each from 0, 0.3, 0.7, 1.5, 3, 6 and 10 s where the file is long enough: **65 starts, 46
Mandarin or Bengali and 19 English**. A window of 0.5 / 0.75 / 1.0 s is the buffer up to the point
where that much voiced audio (40 ms frames at or above `lid_min_rms`) has been heard, silence
included, as the session's language ID sees it.

**Methods.**

- **whisper-tiny / base / small, constrained**: whisper's language-token probabilities renormalised
  over en/zh/bn, as `asr/lid.py` does for tiny today (sherpa-onnx's int8 exports, run with
  onnxruntime, one thread).
- **sherpa-onnx `SpokenLanguageIdentification`** with the same three exports: sherpa-onnx's own
  spoken-language-ID API. Its answer is whisper's unconstrained top language, with no probabilities.
- **Recognizer agreement**: the three streaming recognizers (the parallel window already runs them)
  fed the same audio from the start in 40 ms frames, decoding what is ready, with no flush. That is
  what the session's parallel recognizers have at that moment. Each is scored by its own output:
  the mean token log-probability (sherpa-onnx exposes it as `ys_probs`) and the share of its words
  that are real words of its language (the 50,000 most frequent words per language from
  hermitdave/FrequencyWords; for Mandarin, matches of 2+ characters). Language = the recognizer
  with the best score; confidence = its margin over the next recognizer.
- **Not measured: 3D-Speaker and ECAPA language ID.** sherpa-onnx's spoken-language-ID models are
  whisper only (tiny, base, small, medium; its documentation lists no others). 3D-Speaker's
  language-ID models (CAM++, ERes2Net; Apache-2.0) are Mandarin/English only: they cannot say Bengali,
  and they are not sherpa-onnx models. The ECAPA language-ID model people usually mean is
  SpeechBrain's VoxLingua107 one, also not a sherpa-onnx model.

**Scoring.** "Right" = the method's top answer is the spoken language. "Accepted" = the answer the
session would take at a first attempt with today's floors: English at 0.97 or more, Mandarin or
Bengali at 0.6 or more (whisper); for agreement, a margin floor set from these data so that no
wrong answer passes. "Misfires" = wrong answers that would have been accepted. Time per call is
on this laptop (i9-13900H), one thread, nothing else running. Size is the int8 encoder + decoder
on disk.

## Results

| Method | Size (MB) | ms per call, median (p95) | 0.5 s: right / accepted / misfires | 0.75 s: right / accepted / misfires | 1.0 s: right / accepted / misfires |
|---|---|---|---|---|---|
| **whisper-tiny (today)** | 121 | 21.5 (52) | 40 / 32 / **6** | 47 / 44 / 1 | **53 / 51 / 0** |
| whisper-base | 152 | 37 (95) | 42 / 35 / 3 | 45 / 41 / 3 | 53 / 51 / 1 |
| whisper-small | 357 | 124 (296) | 43 / 42 / 3 | 47 / **46** / **0** | 50 / 49 / 1 |
| sherpa SLID, tiny | 121 | 91 (217) | 30 right, 10 wrong, 25 other languages | 37 / 6 / 22 | 36 / 3 / 26 |
| sherpa SLID, base | 152 | 185 (424) | 31 / 12 / 22 | 38 / 8 / 19 | 44 / 6 / 15 |
| sherpa SLID, small | 357 | 619 (1324) | 38 / 15 / 12 | 43 / 13 / 9 | 47 / 7 / 11 |
| agreement, log-probability | 0 (already running) | - (reads the result) | 8 / 0 / 0 (50 undecided) | 27 / 0 / 0 (23 undecided) | 40 / 0 / 0 (11 undecided) |
| agreement, real-word share | 0 (already running) | - (reads the result) | 7 / 0 / 0 (51 undecided) | 23 / 0 / 0 (24 undecided) | 28 / 0 / 0 (12 undecided) |

Out of 65 starts. The sherpa SLID rows give right / wrong (another of en, zh, bn) / another language
altogether, because its answer has no confidence to put a floor on. The agreement rows' "accepted" is
0 because the zero-misfire floor is above every right answer (next section).

**Recognizer agreement: the recognizers have not said enough yet.** At 0.5 s the English recognizer
had written a token on none of the 65 starts, the Mandarin one on 9 and the Bengali one on 7. The
English and Mandarin models decode in 0.32 s chunks and the Bengali one in 0.64 s chunks, plus
their look-ahead. At 0.75 s it was 17 / 29 / 23 starts, at 1.0 s 35 / 34 / 34. With a single
recognizer talking there is nothing to compare, and its being first says little: wrong answers of
that kind are what push the zero-misfire floor above every right answer. Requiring two recognizers
with output left 18 decided starts at 0.75 s (15 right). The margin that lets none of the 3 wrong
ones through (0.87 nats) keeps 3 of the 15. Log-probabilities are also not comparable across three
models with different vocabularies (500 pieces each for English and Bengali, 6,257 for the bilingual
Mandarin model).
The real-word share is worse still: the English recognizer turns Bengali into real English words
("Could budge of blue shot?" in a browser run on a Bengali vlog).

**sherpa-onnx's API answers in all of whisper's languages.** On the 78 Bengali windows the tiny model
said Bengali 6 times; mostly it said English, Hindi, Malayalam, Korean or Telugu. Small says Bengali
on 27 of them. The constrained answer (today's `lid.py`) is better at the same model size,
and 4-5x faster (`lid.py` pads the audio by 0.5 s; sherpa's call costs what a much longer window
would, consistent with whisper's usual 30 s).

**whisper-small at 0.75 s is the closest call.** Its top answer is right exactly as often as
whisper-tiny's at the same window (47 of 65). It is accepted 2 more times (46 against 44) with no
misfire against tiny's 1. But the two models disagree on 14 starts and split them 8 to 6, so two
starts is within what another 65 starts could reverse. Against today's operating point
(whisper-tiny at 1.0 s: 51 accepted, 0 misfires) it accepts 5 fewer. Its inference also eats most
of the quarter second it would save. A decision at 0.75 s plus 124 ms (296 ms p95) is ready at
0.87 s (1.05 s p95), against 1.02 s (1.05 s p95) for whisper-tiny at 1.0 s plus 21.5 ms. The call
runs synchronously in the session's `feed()`, so a 0.3 s call would also hold up that frame's
captions. Being 3x the size, it would add 236 MB to the download.

**whisper-tiny's early English.** Mandarin or Bengali heard as English at 0.97 or more: 6 starts at
0.5 s, 1 at 0.75 s, none at 1.0 s. At 1.0 s, with today's floors, whisper-tiny misfires on none of
the 65 starts. The early-English risk `docs/page-prior.md` describes (7 of 46 at 0.71-0.97) sits
under the 0.97 floor and costs confirmations, not wrong ones.

## Decision

Batch 3 was to wire in an alternative only if it beat whisper-tiny at 0.5 or 0.75 s with no more
misfires than today. Nothing does:
- Recognizer agreement has too little output by 0.75 s.
- sherpa-onnx's own API is unconstrained and slower.
- whisper-base misfires more.
- whisper-small only ties tiny at 0.75 s, and its inference time cancels the earlier window.

whisper-tiny at 1.0 s stays the first attempt. What would move the first confirmed subtitle is a
language-ID model that is right at 0.5 s and costs about as much as whisper-tiny. None of the
options sherpa-onnx ships is one.

## Re-running

```
python backend/scripts/lid_alternatives.py --clips clips.json --models-root <asr models folder> \
    --whisper <folder>/sherpa-onnx-whisper-base --whisper <folder>/sherpa-onnx-whisper-small \
    --wordlists <folder with en_50k.txt, zh_cn_50k.txt, bn_50k.txt> --out results.json
python backend/scripts/lid_alternatives.py --table results.json
```

`clips.json` maps a name to `[spoken language, 16 kHz mono WAV]`. The benchmark WAVs and the film
scene's audio are not in the repository (`docs/latency.md` names their sources). The whisper base and
small exports come from sherpa-onnx's `asr-models` release (207 MB and 639 MB archives). The word lists
come from hermitdave/FrequencyWords (CC BY-SA 4.0, used only for this measurement). Nothing is
written but `--out`.
