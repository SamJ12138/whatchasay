# Page prior: what a video's page says about its spoken language

With Auto-detect the first recognizer is English, and a switch to Mandarin or Bengali needs two
agreeing language-ID attempts (1.0 s and 1.5 s of speech), so the first subtitle in a confirmed
language came 2.0-2.6 s after play on the Mandarin and Bengali clips and 2.7-3.7 s on the English
ones (English is confirmed on the full 2.5 s window only, `docs/latency.md`). A page usually says
something about its language before any audio is heard. This page measures what, on 23 pages,
before anything is built on it.

## How it was measured

`backend/scripts/page_prior_eval.py` fetches each page as a desktop Chrome with an English
interface would (`Accept-Language: en-US`), reads the candidate signals and prints the language
each one implies, out of English, Mandarin and Bengali (anything else implies nothing). The spoken
language is given per page; the script scores every signal against it.

| Signal | What it is | How a language is read from it |
|---|---|---|
| `yt_caption_asr` | YouTube: the auto-generated caption track's language (YouTube's own speech recognition) | its language code |
| `yt_caption_tracks` | YouTube: the uploaded caption tracks | their language, if they all have one |
| `yt_audio_track` | YouTube: the default audio track of a video that has several (dubbed videos) | the track id's language (`en-US.4`) |
| `yt_default_audio_language` | YouTube: a `defaultAudioLanguage` field anywhere in the page | its language code |
| `title_script` | the title (YouTube: the player's; else `og:title` or `<title>`) | Han or Bengali script if the text has 2 or more such characters (the more of the two), else Latin (3+ letters) = English |
| `title_script_majority` | the same title | the script with the most characters |
| `description_script` | the description (YouTube: the player's; else `og:description` / meta description) | as `title_script` |
| `channel_script` | YouTube: the channel name | as `title_script` |
| `page_script` | title, channel name and description as **one** vote (one uploader wrote all three) | the first of them in Han or Bengali script; else the title's Latin = English |
| `html_lang` | `<html lang>` | its language code |
| `og_locale` | `<meta property="og:locale">` | its language code |

**The pages.** The seven benchmark clips' source pages: the six Wikimedia Commons file pages of
`docs/latency.md` "Benchmark across public clips" and the YouTube film scene of
`docs/live-test-youtube.md`; plus 16 YouTube videos picked for this, five per spoken language and
one more found on the way (below). Seven of the YouTube titles are not in the spoken language, on
purpose: English-titled Mandarin dramas, interviews and vlogs, Chinese-titled re-uploads of English
talks, romanised or English-titled Bengali vlogs. The sample is therefore much harder than everyday
viewing, where the title is mostly in the spoken language; read the rates below as a lower bound.
YouTube also translates titles for the viewer: two videos had an English title in an en-US search
and the original one on the watch page (the TED talk's Chinese title in the search, Prothom Alo's
Bengali title on the page), so what the extension sees depends on the viewer's YouTube language.

**The spoken language was not taken from the page.** Each YouTube video was played through the
real extension with the recognizer of the presumed language (`e2e_extension.py --url ...
--start-at 30 --source <lang> --show-source`) and the recognised text was read: coherent text in
that language confirms it, garbage (or English words out of the bilingual Mandarin model) refutes
it (the run ids are in the table). That check changed the sample: the "ENG SUB" drama
`ij4eTi79Ajo` turned out to be dubbed into English ("FINALLY WE CAN TAKE A BREAK FOR A WHILE"; kept
as the extra English page, replaced by an original-audio drama); Mandarin Corner `IuvULixwQiE` opens
with English narration at 0:30, and from it and from the VOA Bangla stream `8OQgk8SkBJA` the test
browser got no audio at 2:00-4:00 (three tries each), so both were replaced by videos of the same
kind (an English-titled Mandarin vlog, a Bangladeshi newspaper's video). Neither YouTube's caption
language nor our own language ID could have served as the reference: on the Bengali drama
`1W-eMwOEZVI` both say English (whisper-tiny's unconstrained top 3 there: Telugu, Malayalam,
Hindi; renormalised over en/zh/bn that is English 0.93-0.96), and the Bengali recognizer writes
plain Bengali dialogue ("আমার সবকিছু পরে পড়ে গেলে…").

## Results

Every page (✗: names another language than the spoken one; -: present but names none of the three;
empty: not on that page; the last column is the prior of the next section):

| Page | Spoken (how checked) | Page script | YouTube caption language | YouTube audio track | Title (any non-Latin / majority) | `<html lang>` | Prior: favourite |
|---|---|---|---|---|---|---|---|
| [English, NASA (Commons)](https://commons.wikimedia.org/wiki/File:ScienceCasts-_The_Zero_Gravity_Coffee_Cup.webm) | English (benchmark, 3 of 3 detected) | en |  |  | en / en | en | en 0.65 |
| [English, VOA Helix (Commons)](https://commons.wikimedia.org/wiki/File:VOA%E2%80%99s_Matt_Dibble_reports_on_Helix_electric_aircraft_%E2%80%93_VOA_News.webm) | English (benchmark, 3 of 3 detected) | en |  |  | en / en | en | en 0.65 |
| [Mandarin, VOA Norway (Commons)](https://commons.wikimedia.org/wiki/File:2011-07-26_%E7%BE%8E%E5%9B%BD%E4%B9%8B%E9%9F%B3%E6%96%B0%E9%97%BB-_%E6%8C%AA%E5%A8%81%E9%83%A8%E9%95%BF%E7%A7%B0%E8%AD%A6%E6%96%B9%E5%8F%8D%E5%BA%94%E7%B2%BE%E5%BD%A9.webm) | Mandarin (benchmark, 3 of 3 detected) | zh |  |  | zh / en ✗ | en ✗ | zh 0.85 |
| [Mandarin, VOA Taiwan (Commons)](https://commons.wikimedia.org/wiki/File:%E5%8F%B0%E6%B9%BE%E5%87%86%E5%A4%87%E8%BF%8E%E6%88%98%E5%BC%BA%E5%8F%B0%E9%A3%8E.webm) | Mandarin (benchmark, 3 of 3 detected) | zh |  |  | zh / en ✗ | en ✗ | zh 0.85 |
| [Bengali, Maasranga (Commons)](https://commons.wikimedia.org/wiki/File:%E0%A6%9A%E0%A6%BF%E0%A6%95%E0%A6%BF%E0%A7%8E%E0%A6%B8%E0%A6%BE%E0%A6%B0_%E0%A6%9C%E0%A6%A8%E0%A7%8D%E0%A6%AF_%E0%A6%A2%E0%A6%BE%E0%A6%95%E0%A6%BE%E0%A6%AF%E0%A6%BC_%E0%A6%A4%E0%A6%B0%E0%A6%BF%E0%A6%95%E0%A7%81%E0%A6%B2.webm) | Bengali (benchmark, 3 of 3 detected) | bn |  |  | bn / en ✗ | en ✗ | bn 0.85 |
| [Bengali, Wikitongues (Commons)](https://commons.wikimedia.org/wiki/File:WIKITONGUES-_Sanjoy_speaking_Bengali.webm) | Bengali (benchmark, 3 of 3 detected) | en ✗ |  |  | en ✗ / en ✗ | en ✗ | en 0.65 ✗ |
| [Bengali film scene (YouTube)](https://www.youtube.com/watch?v=-tpVpbIxFmI) | Bengali (`docs/live-test-youtube.md`) | bn | bn | - | bn / en ✗ | en ✗ | bn 0.98 |
| [CCTV news (zh title)](https://www.youtube.com/watch?v=6wKj4RxZ-tw) | Mandarin (run `20261003T234906-283e25`) | zh | - | - | zh / zh | en ✗ | zh 0.85 |
| [VOA Chinese (zh title)](https://www.youtube.com/watch?v=aUBYYtK5dMo) | Mandarin (run `20261003T234941-b2b6fa`) | zh | - | - | zh / zh | en ✗ | zh 0.85 |
| [FatSongsong cooking vlog (en title)](https://www.youtube.com/watch?v=32nb60sDz7k) | Mandarin (run `20261004T000834-2db891`) | zh | - | - | en ✗ / en ✗ | en ✗ | zh 0.85 |
| [YOUKU drama, "ENG SUB" (en title)](https://www.youtube.com/watch?v=Iv4ptLWGkLc) | Mandarin (run `20261004T000250-fe1b1f`) | zh | - | - | en ✗ / en ✗ | en ✗ | zh 0.85 |
| [Better in Chinese Streettalk (en title)](https://www.youtube.com/watch?v=YCIFNPMu0hs) | Mandarin (run `20261003T235125-2a295a`) | zh | - | - | en ✗ / en ✗ | en ✗ | zh 0.85 |
| [BBC News (en title)](https://www.youtube.com/watch?v=BjoSdBR3Rig) | English (run `20261004T000322-b24a26`) | en | en | - | en / en | en | en 0.94 |
| [English conversation lesson (en title)](https://www.youtube.com/watch?v=v07kPyPkLYw) | English (run `20261004T000359-7a5b6b`) | en | en | - | en / en | en | en 0.94 |
| [TED re-upload, bilingual subs (zh title)](https://www.youtube.com/watch?v=gN9dlisaQVM) | English (run `20261004T000434-d5d31a`) | zh ✗ | en | - | zh ✗ / zh ✗ | en | none (under 0.6) |
| [Jobs Stanford speech re-upload (zh title)](https://www.youtube.com/watch?v=qQuNLzX90_8) | English (run `20261004T000506-6664c7`) | zh ✗ | en | en | zh ✗ / zh ✗ | en | en 0.84 |
| [TED official (title English on the watch page, Chinese in search results)](https://www.youtube.com/watch?v=H14bBuluwB8) | English (run `20261004T000540-a56b45`) | en | en | - | en / en | en | en 0.94 |
| [BBC News Bangla (bn title)](https://www.youtube.com/watch?v=O_0ktrSPGWI) | Bengali (run `20261003T235830-0c1dc7`) | bn | - | - | bn / bn | en ✗ | bn 0.85 |
| [Bangla natok (bn + Latin title)](https://www.youtube.com/watch?v=1W-eMwOEZVI) | Bengali (run `20261003T235905-2e3b0c`) | bn | en ✗ | - | bn / en ✗ | en ✗ | none (under 0.6) |
| [Durga Pujo vlog (romanised Bengali title)](https://www.youtube.com/watch?v=zTC-FeZtMSE) | Bengali (run `20261003T235940-25309e`) | en ✗ | en ✗ | - | en ✗ / en ✗ | en ✗ | en 0.94 ✗ |
| [Prothom Alo news (title Bengali on the watch page, English in search results)](https://www.youtube.com/watch?v=8UR3uqOMJpY) | Bengali (run `20261004T000913-dbe1b7`) | bn | bn | - | bn / bn | en ✗ | bn 0.98 |
| [Kolkata food vlog (en title)](https://www.youtube.com/watch?v=xdbeC1GfcZU) | Bengali (run `20261004T000137-a99ea5`) | en ✗ | bn | - | en ✗ / en ✗ | en ✗ | bn 0.63 |
| [short drama dubbed into English ("ENG SUB" title)](https://www.youtube.com/watch?v=ij4eTi79Ajo) | English (run `20261003T235051-98ea5b`) | en | - | en | en / en | en | en 0.94 |

Per signal (23 pages; the YouTube signals on the 17 YouTube pages):

| Signal | Pages | Present | Names the spoken language | Names another | Right when present |
|---|---|---|---|---|---|
| `yt_caption_asr` | 17 | 10 | 8 | 2 | 80% |
| `yt_caption_tracks` | 17 | 2 | 1 | 1 | 50% |
| `yt_audio_track` | 17 | 2 | 2 | 0 | 100% |
| `yt_default_audio_language` | 17 | 0 | 0 | 0 | - |
| `title_script` | 23 | 23 | 15 | 8 | 65% |
| `title_script_majority` | 23 | 23 | 10 | 13 | 43% |
| `description_script` | 23 | 16 | 12 | 4 | 75% |
| `channel_script` | 17 | 17 | 8 | 9 | 47% |
| `page_script` | 23 | 23 | 18 | 5 | 78% |
| `html_lang` | 23 | 23 | 8 | 15 | 35% |
| `og_locale` | 23 | 0 | 0 | 0 | - |

`page_script` split by what it found: a Han or Bengali text named the spoken language on 12 of
14 pages (the two misses: Chinese-titled re-uploads of English talks, "TED 中英雙語字幕" and the
Jobs speech, made for Chinese learners of English); a Latin-only page named English rightly on 6 of
9 (the misses: the Wikitongues "Sanjoy speaking Bengali" file name, a romanised-Bengali vlog title,
an English-titled Bengali food vlog). The description alone (`description_script`, 12 of 16 when
present) and the channel name alone (8 of 17) are weaker than the three together.

## Decision: which signals get weight, and how much

- **Dropped: `<html lang>` and `og:locale`.** `<html lang>` is the site's interface language on
  every page here (English on YouTube and Commons, whatever is spoken): right only when the speech
  happens to be English, 8 of 23. `og:locale` is on none of the pages.
- **Dropped: the uploaded caption tracks and `defaultAudioLanguage`.** Uploaded caption tracks
  name one language on 2 of 17 pages (one of them the subtitles' language, not the speech's); a
  page with many tracks names none. `defaultAudioLanguage` is a YouTube Data API field and is not
  in the watch page at all (0 of 17).
- **Dropped: the title's majority script.** Mixed titles ("10,000 রাজভোগের Order | Ora Char Jon |
  Movie Scene | …", "File:台湾准备迎战强台风.webm - Wikimedia Commons") have more Latin letters than
  anything else; the majority rule is right on 10 of 23, the any-non-Latin rule on 15.
- **Kept, as one vote: the page's script** (`page_script`, right on 18 of 23): the title, channel
  name and description come from the same uploader, so they are one vote, not three. Weight = its
  measured accuracy, split by what it found: **0.85** for a Han or Bengali text (12 of 14), **0.65**
  for Latin only (6 of 9).
- **Kept: YouTube's caption language** (`yt_caption_asr`, on 10 of 17 YouTube pages, right on 8):
  weight **0.8**. Its two misses are Bengali videos it labels English.
- **Kept: YouTube's default audio track** (on 2 of 17, right on both): weight **0.8**. Rare, but
  the only signal that sees a dub: the "ENG SUB" drama's title promises Chinese audio with English
  subtitles, and its default track is English.
- **Added: the channel memory** (Batch 1): the language that language ID last confirmed on the
  same YouTube channel (elsewhere: the same site), weight **0.97** (0.9 until 2026-10-05, see
  "Channel memory" below), halved for every time it turned out wrong on that channel or site, for
  good (a channel that switches languages stops counting).

**How they combine.** Naive Bayes over {en, zh, bn} from a flat start: a signal that names language
L with weight a multiplies L by a and each other language by (1 - a) / 2; the prior is the
normalised result. A Han or Bengali text alone gives that language 0.85; a Latin title alone gives
English 0.65; YouTube's caption language with a Latin title gives English 0.94; a Chinese title
against an English caption language gives 0.56 to Mandarin, under the threshold. The backend
starts its first recognizer in the prior's favourite when it reaches **0.6**
(`SUBTITLE_ASR__LID_PRIOR_THRESHOLD`), else in English as before.

With these weights the prior is used on 21 of the 23 pages and names the spoken language on 19
(last column of the table). The two under the threshold are the TED re-upload and the Bengali
drama (Chinese or Bengali title against YouTube's English caption language): they start as today.
The two wrong ones are both an English prior on Bengali speech (the Wikitongues file name, the
Durga Pujo vlog), whose first recognizer is English, as it is today without a prior. What an
English prior adds is that one sure English answer from language ID confirms it early, and that
is where the risk sits; the next section bounds it.

## The decision rules, checked against whisper-tiny offline

The rule asked for with a prior: confirming the prior's language needs one attempt
above the confidence floor (0.6); switching away from it needs two agreeing early attempts, as
today. Run offline on the seven clips and the three sample WAVs, each from 7 start offsets (0, 0.3,
0.7, 1.5, 3, 6, 10 s, where the file is long enough: 46 Mandarin or Bengali starts, 19 English;
the real model, constrained to en/zh/bn, on the session's own schedule), it
holds for Mandarin and Bengali and fails for English in both directions:

- **English as the prior's language, one attempt above 0.6:** on Mandarin or Bengali speech the
  first attempt (1.0 s of speech) said English at 0.71-0.97 from 7 of 46 starts (the Bengali news
  10 s in 0.84, the film scene 0.7 s and 6 s in 0.85 and 0.86, Wikitongues 3 s and 10 s in 0.94 and
  0.97, the Mandarin sample 1.5 s and 6 s in 0.71 and 0.94): an English-titled Mandarin or Bengali
  video would have been locked in English, which never happens today (English is confirmed on the
  full window only). On English speech the first attempt said English at 0.98 or more from 18 of
  19 starts (the 19th: 0.75, then 0.98 half a second later). So **a favoured English is confirmed
  early only at 0.97 or more** (`SUBTITLE_ASR__LID_PRIOR_FLOOR_EN`): of all the early English
  answers on Mandarin or Bengali speech, one reaches it (Wikitongues 10 s in, third attempt, 1.00),
  and that start ends in English today as well (0.99 on the full window). So on the Wikitongues
  clip, one of the two pages with a wrong English prior, the prior changes the outcome at none of
  its 7 starts.
- **Switching away from a Mandarin or Bengali prior to English on two agreeing early attempts:**
  the same habit gave two English answers in a row on Mandarin and Bengali speech from 5 starts
  (the Mandarin sample 6 s in 0.94 then 0.90, the film scene 6 s in, the Bengali news 10 s in,
  Wikitongues 0.7 s and 10 s in): a right Mandarin or Bengali prior would have been thrown away for
  English. Today an early English answer is never accepted, because English is the first guess.
  So **with a prior, English is still accepted only from the full window on**, as today; the other
  languages switch on two agreeing early attempts, as today.
- **Mandarin or Bengali as the prior's language, one attempt above 0.6:** no attempt on English
  speech said Mandarin or Bengali at 0.6 or more (no early attempt at all); on Bengali speech two
  said Mandarin, 0.72 and 0.75 (Wikitongues 1.5 s and 3 s in, second attempt), a case only a
  Chinese-titled Bengali page would reach.

**Shorter first windows do not help.** At 0.5, 0.6 or 0.75 s of speech, 10 of the 46 Mandarin and
Bengali starts got English at 0.97 or more, and one English start got Mandarin at 0.60 (NASA, 0.7 s
in, at 0.6 s). The first attempt stays at 1.0 s of speech, which bounds the best case: a
subtitle confirmed at the first attempt is on screen about 1 s of speech plus the capture and the
first partial after the video starts to play (`docs/latency.md`, "Language prior").

## Channel memory: why 0.97 (2026-10-05)

The memory is what the second visit to a channel adds, so it has to win where the page is wrong.
The one YouTube page here whose prior is wrong and above the threshold is the cold case, the Durga
Pujo vlog: romanised title (`pageScript:en`, 0.65) and YouTube's caption language English
(`captionAsr:en`, 0.8), Bengali speech. Against those two a remembered Bengali at 0.9 lost: the prior
came out en 0.61, bn 0.37, English was the first recognizer and Bengali was reached by a switch at
the second attempt (browser test `backend/tests/test_channel_memory_browser.py`, run before the
change). For the memory to reach the 0.6 threshold against both, its weight has to be at least
0.959; it is now **0.97** (bn 0.68 on that page).

The cost is symmetric: a wrong memory now outweighs a right page of two signals. A channel
remembered as English opening a video with a Bengali title and YouTube's Bengali caption language
gets bn 0.58, under the threshold, so the session starts in English as it did before any prior,
and language ID decides as before. That costs one session: the miss replaces the remembered
language with the confirmed one and halves its weight (0.485, on its own under the threshold;
0.243 after a second miss). A wrong page, by contrast, is wrong on every visit to that video and
usually on the channel's other videos.

What the memory learns is only as good as language ID's answer on the first visit. On the cold
case's channel whisper-tiny confirmed English or Mandarin on 9 of 10 stretches of its other videos
(right prior or not: talking over music, English words in Bengali sentences), and on the cold case
itself from 0:30 it confirmed Bengali in 5 of 6 runs on 2026-10-04 but Mandarin in 3 of 6 first
visits on 2026-10-05 (`docs/latency.md`, "Channel memory on a second visit"). A first visit that confirms
the wrong language leaves a wrong memory for the second; the miss count then limits it to one
session.

Re-running: `python backend/scripts/page_prior_eval.py --pages docs/page-prior-pages.tsv --cache DIR`
(the 23 pages with their spoken languages; the fetched HTML is not kept in the repository, and
YouTube pages change: view counts, translated titles, caption tracks added later).
