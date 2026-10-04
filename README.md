# whatchasay

[![CI](https://github.com/SamJ12138/whatchasay/actions/workflows/ci.yml/badge.svg)](https://github.com/SamJ12138/whatchasay/actions/workflows/ci.yml)

I built this for couples who do not share a first language, because my girlfriend
watches videos I cannot follow and I watch ones she cannot, and most of them either
have no subtitles or have subtitles that are wrong in ways that matter. whatchasay
listens to whatever is playing in a Chrome tab and lays live translated subtitles over
it, so the two of you can sit on the same couch, watch the same thing, and laugh at
the same moment instead of one person explaining the joke to the other afterwards.
It runs entirely on your own machine, and nothing you watch leaves your laptop unless
you decide it should.

![Demo: a Mandarin news reader on Voice of America is captioned live and translated into English over the video](docs/demo-fastest.gif)

*A Voice of America Mandarin news item (public domain, credited in `NOTICE.md`), captioned and translated live on CPU:
the fastest of the six public clips in [the latency benchmark](docs/latency.md#benchmark-across-public-clips-2026-10-03).*

It is for videos and live streams that have no subtitles, lectures, and meetings held in a browser tab in a language
you do not speak. A Chrome extension captures the tab's sound, a local Python program turns English, Mandarin or
Bengali speech into text and translates it, and the extension draws both over the video: the words while they are
being spoken, a first translation about two seconds into a sentence, the final one about a second after it ends.
The extension only talks to the backend on 127.0.0.1, and nothing is sent to a cloud service unless you turn a
cloud provider on yourself (it is off by default).

## Requirements

| | |
|---|---|
| Operating system | **Windows 11: tested** (everything in this README). **Linux:** the backend's fast test suite and the extension tests run in CI on Ubuntu 24.04; the backend with real models and the browser extension have not been run on Linux. **macOS: untested.** |
| Python | 3.10 or newer (tested: 3.10 and 3.14 on Windows, 3.12 in CI on Windows and Ubuntu) |
| Browser | Desktop Chrome or Chromium 116 or newer (the extension uses an offscreen document and tab capture from its service worker). Tested with Google Chrome 153 (fresh profile) and Chromium 145. Other Chromium browsers: untested |
| CPU | Enough on its own. Measured on an Intel i9-13900H: speech recognition takes 2.5 ms per 40 ms of audio, translation 30-140 ms per sentence |
| Memory | The backend uses about 1 GB of RAM with English, Mandarin and Bengali loaded |
| GPU | Optional, only for the optional HY-MT translation engine (NVIDIA, 4 GB+) |
| Disk | 2.3 GB in all: Python packages 1.07 GB (`backend/venv`, CPU PyTorch included) and models 1.22 GB (`backend/data/models/`). Nothing is left in the Hugging Face cache |
| Network | Only for installing and downloading models. Running needs none |

## Quickstart

The commands are the same on every system except where PowerShell (Windows) and bash (Linux, macOS) differ. The
backend's virtual environment lives in `backend/venv`.

**1. Clone** (on Windows into a short path such as `C:\src`: pip fails on paths over 260 characters unless
Windows long paths are enabled)

```
git clone https://github.com/SamJ12138/whatchasay.git
cd whatchasay/backend
```

**2. Virtual environment and packages** (the CPU build of PyTorch is needed once, to convert the translation models;
about 1.5 minutes, 1.07 GB)

PowerShell:

```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

bash (on Debian and Ubuntu, `sudo apt install python3-venv` first):

```
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

**3. Download the models** (2.3 GB: speech models 1.06 GB and OPUS-MT checkpoints 1.24 GB; each checkpoint is
converted to CTranslate2 once and then deleted. 231 seconds in our last measured run, 57 of them for OPUS-MT; it
depends mostly on how fast GitHub serves the speech models to you)

PowerShell: `venv\Scripts\python ..\scripts\download_models.py`

bash: `venv/bin/python ../scripts/download_models.py`

It prints each model as it goes and ends with `Done.`. Warnings about `HF_TOKEN` or `torch_dtype` along the way
are harmless. Everything it downloads has a declared permissive license (`NOTICE.md`).

**4. Start the backend** (leave it running)

PowerShell: `venv\Scripts\python run.py`

bash: `venv/bin/python run.py`

It listens on `http://127.0.0.1:8765` and is ready when it prints `Uvicorn running on http://127.0.0.1:8765` (about
5 seconds). `http://127.0.0.1:8765/health/json` shows the loaded engines; its `device` is `cpu`, or `cuda` if
CTranslate2 finds an NVIDIA GPU, which it then uses for OPUS-MT (`SUBTITLE_TRANSLATION__DEVICE=cpu` keeps it on the
CPU). Stop it with Ctrl+C.

**5. Load the extension**

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose the `extension` folder inside the clone.
4. Optional: click the puzzle-piece icon in the toolbar and pin **Multi-Language Subtitle Translator**.

The extension asks for no site access when it is installed.

**6. Get subtitles**

1. Open any video with speech in English, Mandarin or Bengali (a YouTube video without captions works) and let it
   play.
2. Click the extension's icon. Under *Live Captions*, choose the spoken language or leave *Auto-detect*.
3. Click **Start Live Captions**. The first time, Chrome asks to allow tab capture (Chrome words it as "Read and
   change all your data on all websites"); click **Allow**. The extension captures this tab's audio only, and you
   keep hearing it.

What you will see: the translation only, in a small box below the video on a normal page, or over the bottom of
the picture when the video is fullscreen. Two or three seconds into a sentence a draft translation of its first
words appears in italics, ending in "…"; only the start that successive drafts agree on is shown, so it mostly
grows rather than changes; then the finished sentence's translation replaces it. Each line may wrap once. The spoken words themselves are not shown unless you ask: the popup's *Show the spoken words under
the translation* toggle, or **Alt+O** on the page, adds them under the translation, smaller and dimmer, and the
choice is remembered. A line ends when the speaker pauses, and after 6 seconds at the latest. The default target
languages are English and Chinese; a language equal to the spoken one is skipped. With *Auto-detect*, what is
recognised before the language is known is a guess: it is drawn dimmed above a small *Detecting language…* label,
and the first line at full brightness is in the detected language.

![Live captions on a NASA ScienceCasts video filling the window: the translation into Chinese over the bottom of the picture, the spoken words under it](docs/screenshot.png)

*NASA ScienceCasts, "The Zero Gravity Coffee Cup" (public domain), running on CPU.*

Click **Stop Live Captions** in the popup, or press **Alt+L**, to stop.

## Using it

**Languages.** Speech: English, Mandarin Chinese (also English words inside Mandarin speech) and Bengali, with
automatic detection among the three. Translation targets offered: English, Chinese (Simplified), Bengali, Vietnamese,
Japanese, Korean, Spanish, French, German, Russian, Portuguese and Italian. With the default OPUS-MT engine every
one of them works from English, Mandarin and Bengali speech except Korean and Portuguese, which have no OPUS-MT
model from English; the Settings page greys them out ("no local model for en->ko") for the spoken language chosen
under *Live Captions*. They work with the optional HY-MT or cloud engines.
Directions without a direct model go through English (for example Mandarin to Bengali).

**Target languages.** Extension icon → **Settings** (bottom of the popup) → *Translation*: **Primary** and
**Secondary** language (Secondary can be *None*). Both are translated and shown together. **Alt+S** swaps their order.

**Caption mode** (videos that already have subtitles in another language). Off by default. *Settings → Caption Mode*
turns it on; Chrome then asks for access to the 14 supported video sites (YouTube, Netflix, Prime Video, Disney+,
HBO Max, Max, Twitch, yfsp.tv, Bilibili, iQIYI, Youku, v.qq.com, Viu, Abema), because the extension has to read
the subtitle text those pages show. Even then nothing is read until you switch on **Translate subtitles on this
tab** in the popup for a particular tab. Switching caption mode off gives the site access back.

**Corrections.** Press **Alt+E** while a translation is on screen, edit it, and press Enter (Escape cancels). The
subtitles hold still while you type. The correction is stored in the translation memory and used whenever the same
source sentence comes up again, letter for letter. Page subtitles (caption mode) repeat exactly, so there a
correction holds. Live captions mostly do not: the recognizer rarely writes a sentence the same way twice. For a
word that keeps coming out wrong, teach the glossary instead.

**The glossary.** A term you teach once: a name, a dish, a piece of jargon. A term has its correct spelling in
the spoken language, the spellings the recognizer tends to write for it ("heard as"), and how it should read in
each target language. From then on the heard-as spelling is replaced with the correct one on the recognised line
(an exact spelling, or one within a letter or two of it), and the term goes through translation untouched and
comes out as you said. Two ways to add one: *Settings → Glossary* (a table: add, edit, delete), or on the page:
turn the spoken words on (**Alt+O**), select the misheard word in that line with the mouse, press **Alt+E**, type
what it should be and how it should read, Enter. A running session uses a new term on its next line. The worked
case from `docs/live-test-youtube.md`: in a Bengali film scene the sweet রাজভোগ (*rajbhog*) came out as রাজবুক,
রাজব, কাজ বুক or আজ বুক and was translated as "A royal book." Taught once as correct spelling রাজভোগ, heard as
রাজবুক, shown in English as *rajbhog*, the recognised line reads রাজভোগ and its translation carries *rajbhog* in 3 of
3 live runs (the near spellings are caught too: a letter off, or the word split in two); offline, "একটা রাজবুক দেখা
তো" -> "I've seen a rajbhog." and "ten thousand royal books" later in the scene -> "ten thousand rajbhog". What the
glossary cannot do is mend the rest of a badly heard line. Terms are kept in `backend/data/translation_memory.db`
with your corrections.

![Demo: Bengali dialogue in a film scene on YouTube is captioned live and translated into English over the video](docs/demo-youtube.gif)

*The scene the rajbhog case comes from, "Ora Char Jon" on the YouTube channel Bengali Movies with English Subtitle
(<https://www.youtube.com/watch?v=-tpVpbIxFmI>), run on CPU; the run is in `docs/live-test-youtube.md`.*

**What is remembered.** Live-caption text and its translations are kept in memory only until the session ends.
Page subtitles from caption mode and your corrections are kept in `backend/data/translation_memory.db`; machine
translations are deleted 30 days after their last use. *Settings → Data Management → Clear translation memory*
deletes everything in it (translations, corrections, glossary) and compacts the file.

**The overlay.** It shows the translation only, on two rows: the previous sentence's finished translation on the
upper row and the current sentence's draft (then its final, briefly) on the lower one. A line wraps once at about
42 characters (22 for Chinese, Japanese and Korean); only one longer than two rows keeps its newest words, with "…"
at the start. A
finished translation stays at least 1.5 s, or its length at 15 characters per second, before the next one can push
it off the upper row; if the next sentence finishes sooner, it waits on the lower row until then. In fast dialogue
that hold shortens toward a floor of 1.0 s as more lines queue up, instead of a line being dropped. Its font is
4.5 % of the video's height
(never under 14 px), each line in a rounded box behind the text; *Settings → Display* has the percentage, the
minimum and the box's opacity, and the popup's **A+** / **A-** change the percentage. On a normal page the block
sits just below the video; when the video is fullscreen or fills the window it sits over the bottom of the
picture and moves up while the player's controls are showing. **Drag it** anywhere with the mouse; the place is
remembered for that site (separately for the page and the fullscreen layouts). **The spoken words** (the
recognizer's line, growing as it is spoken) are off by default: the popup's *Show the spoken words under the
translation* toggle or **Alt+O** on the page turns them on, smaller and dimmer under the translation, and the
choice is remembered. `docs/overlay/README.md` has the measurements (how much of the picture the overlay covers,
before and after).

**Keyboard shortcuts.** Alt+L start/stop live captions, Alt+T show/hide the overlay, Alt+S swap the two languages,
Alt+. larger font (the popup also has a smaller-font button), Alt+E edit the current translation (with a word
selected in the spoken-words line: teach the glossary), Alt+O show/hide the spoken words. Change the first four at `chrome://extensions/shortcuts` (Alt+E and Alt+O are handled on the
page: Chrome allows four changeable shortcuts per extension).

**When something does not work**

| What you see | What to do |
|---|---|
| "Failed: Cannot connect to the backend at ..." or "backend disconnected" in the popup | Is `run.py` still running? `http://127.0.0.1:8765/health/json` must answer |
| "Live captions need permission to capture this tab's audio" | Click **Start Live Captions** in the popup and allow it (Alt+L cannot ask; it opens the popup) |
| Start fails on a `chrome://` page or the Chrome Web Store | Chrome does not allow capturing those; use a normal web page |
| Captions but no translation | The target equals the spoken language, or it is greyed out in Settings (no route from that language) |
| "No audio reaching capture (DRM site or paused video)" | The video is paused, or the site protects its audio |
| Port 8765 is taken | `run.py --port 8766`, then set the server address in **Settings → Connection** |
| The backend log says a model is missing | Run step 3 again; it skips what is already there |
| pip fails with "No such file or directory" and a hint about long paths (Windows) | Clone into a shorter path (step 1), or enable Windows long paths |

## Optional extras

**HY-MT on an NVIDIA GPU.** Tencent's HY-MT1.5-1.8B translates every direction between 36 languages, Bengali
better than OPUS-MT, in 0.2-0.5 s per sentence on an RTX 4060 Laptop GPU. It is under the Tencent HY Community
License, which is not open source: among other terms it does not apply in, and the model must not be used in, the
European Union, the United Kingdom and South Korea. The download script shows the license summary and downloads
nothing until you accept it:

```
venv\Scripts\python -m pip install -r requirements-gpu.txt --extra-index-url https://download.pytorch.org/whl/cu128
venv\Scripts\python ..\scripts\download_models.py --hymt --accept-hymt-license
```

Then start the backend with `SUBTITLE_MT__ENGINE=hymt` (in `backend/.env`, see Configuration). The script fetches
the Windows CUDA build of llama.cpp's `llama-server`; on Linux, put a `llama-server` binary into
`backend/bin/llama/` yourself. If the GPU, the model or `llama-server` is missing, the backend says so in one line and
uses OPUS-MT.

**Cloud providers.** Off by default. With `SUBTITLE_CLOUD__ENABLED=true` and a key in `backend/.env` the backend
sends the subtitle text (never audio) to the provider you configure: Google Cloud Translation
(`translation.googleapis.com`) or Azure Translator for translation, and Groq or Gemini for the optional second-pass
refinement. Keys live only in `backend/.env`; the extension never stores or sends them. At startup the backend logs
which provider will receive text, and *Settings → Cloud Providers* shows the same line.

**The larger Mandarin model.** The default Mandarin model is Apache-2.0. A larger one with a lower error rate on read
Mandarin (CER 1.91 against 3.04 on AISHELL-1) declares no license at all and was trained on data that includes
WenetSpeech, which is released for non-commercial use. It is opt-in:
`download_models.py --mandarin-large` prints this notice and downloads nothing without
`--accept-mandarin-large-terms`; then set `SUBTITLE_ASR__ZH_MODEL=large`. Do not redistribute it.

## How it works

![Demo: English speech in a NASA video is captioned live and translated into Chinese over the video](docs/demo.gif)

*NASA ScienceCasts, "The Zero Gravity Coffee Cup" (public domain): English speech captioned live and translated into
Chinese on a laptop CPU, no GPU and no cloud. Recorded by the browser harness, not by hand.*

```
 Chrome tab ──audio──> offscreen document ──16 kHz PCM, 40 ms frames──┐       (WebSocket, 127.0.0.1)
 (you keep hearing it)   (tab capture)                                  v
                                                    ┌─────────────── backend (Python) ───────────────┐
                                                    │ Zipformer streaming ASR (sherpa-onnx, CPU)     │
                                                    │   en / zh / bn, spoken-language ID (whisper-   │
                                                    │   tiny), partial words as you hear them        │
                                                    │ a line ends at a pause, at 6 s at the latest   │
                                                    │ English: casing + punctuation (CNN-BiLSTM)     │
                                                    │ OPUS-MT (CTranslate2 int8, CPU) per target,    │
                                                    │   via English when there is no direct model:   │
                                                    │   drafts of the open line, then the final      │
                                                    └────────────────────────┬───────────────────────┘
 overlay on the video  <── content script <── service worker <──partial / draft / final / translation──┘
```

Page subtitles (caption mode) take the other way in: the content script reads the cue, the service worker sends it
to the backend's `/ws`, and the translation comes back the same way. Details, and the mechanisms that are easy to
break (service-worker lifetime, site access, what is stored, cloud keys): `docs/architecture-notes.md`. Every stage
and its failure modes: `docs/pipeline-stages.md`.

**Streaming.** Nothing waits for the end of a sentence except the final translation. The recognizer decodes in
chunks (0.32 s for English and Mandarin, 0.64 s for Bengali) and every partial result goes to the overlay, which
draws the growing line as a line in progress. A line ends when the recognizer hears 0.6 s of silence, at a pause
it missed (a gap of 0.5 s between two words, seen once the next word arrives), or after 6 seconds (then at its
widest pause), so a translation never has to wait for a ten-second run of dialogue. While a line is open, the words that were the
same in three partial results in a row count as stable; once they are three words (or 40 % of the line so far) that
stable part is translated (at most once every 1.5 s), and of the result only the start the last two drafts agree
on is shown as a draft (local agreement); the final translation replaces it when the line ends. From the moment
captions start, the extension keeps the tab's audio output running with an inaudible tone, so a video played
afterwards is captured from its first sample.

**Language detection.** With *Auto-detect*, the page gives the first guess: when you start captions the extension
reads the title, channel name and description (Han or Bengali script, or Latin), YouTube's own caption language
and default audio track, and the language detected on the same channel before, and the backend starts the
recognizer of the likeliest language when the page makes it 60 % likely (else English). All three recognizers
listen until spoken-language ID (whisper-tiny) confirms one: one answer after the first second of speech confirms
the page's guess (English only when whisper is 97 % sure, because it says English on the first second of Mandarin
and Bengali too), two agreeing answers switch to another language, and the confirmed recognizer's text is then
already on screen, nothing replayed. On the benchmark clips' own pages the first subtitle in a confirmed language
came 1.3-2.3 s after play where the page was right (median per clip; 1.9-3.7 s before); where it was wrong, 3.1 s
on one clip (2.4 s before) and 1.9 s on another (3.0 s before). Under one second was the goal and is not reached:
the first answer needs a second of speech. Evaluation and numbers: `docs/page-prior.md`, `docs/latency.md`.

**Measured latency**, per subtitle line: before the streaming work, after it, and now, with the draft follow-ups
(Windows 11, i9-13900H, everything on the CPU; three runs per clip in Chromium with the real extension,
Auto-detect, one target language; "before" is the same measurement on the earlier code). Definitions, every run
with its `run_id`, the tuning and what got worse: `docs/latency.md`.

| Clip | First source text after the line's first word | First translated text after the line's first word | Final translation after the line's last word | First confirmed-language subtitle after the start | Draft rewrites (non-append revisions) per line |
|---|---|---|---|---|---|
| English sample (6.6 s, one sentence) | 0.5 -> 0.3 s | 6.6 -> 2.6 -> 2.5 s | 0.9 s | 3.9-4.0 -> 3.3 -> 3.4 s | 3 -> 2 |
| Mandarin sample (10 s, slow, mixed with English) | 0.3 -> 0.4 s | 1.6 -> 1.4-1.7 -> 1.6-1.8 s (slowest line 5.3 -> 1.9-2.3 -> 2.4-2.9 s) | 0.8 s | 6.0-6.2 -> 2.5-2.6 -> 2.5 s | 0-0.2 -> 0-0.25 |
| Bengali sample (7 s, one sentence) | 3.9 -> 0.6 -> 0.4-0.7 s | 7.9 -> 2.7-3.3 -> 3.3 s | 1.1 -> 1.2 s | 4.1-4.2 -> 2.4-2.9 -> 2.3-2.8 s | 1 |
| Live clip (20 s of Bengali film dialogue) | 0.6-0.75 -> 0.7-0.75 -> 0.8 s | 10.3-10.4 -> 2.0 -> 2.4-2.5 s | 0.3-0.9 -> 1.3 -> 1.3-1.4 s | 5.3-5.4 -> 2.1-2.2 -> 2.1-2.2 s | 1.5 -> 0.8-1.0 |

Read it with these in mind. The source text was never late: partial results reached the overlay before this work
too; what waited for the whole sentence was the translation. The first translated text is a draft of the
sentence's first words; it used to be rewritten, not extended, when the next draft arrived (the translation of a
longer prefix is a different sentence, most of all from verb-final Bengali), so now only the start that the last
two drafts agree on is shown, and a draft is not shown before the stable part of the line has three words (or
40 % of it): that is the 0.4-0.5 s the live clip's first translated text gave back, and the last column, which
counts the rewrites with the final translation included, fell from 1.5 to 0.8-1.0 per line. The last "first
confirmed subtitle" numbers are measured without the harness starting the tab's audio for the extension, as a
viewer gets it. The live clip's lines used to be closed by a 10-second cut, which is why its final translation
looks slower than before: it waits for a real pause. Translation calls are 0.45-0.7 per second of speech; one
call takes 17-49 ms (p50) and 25-62 ms (p95) on the CPU. Speech recognition takes about 2 ms per 40 ms frame and
one language-ID attempt 15-45 ms.

The same measurement over six public clips, two per spoken language (public domain or CC BY, credited in
`NOTICE.md`; the benchmark table in `docs/latency.md`): first translated text 1.4 s after a line's first word on
two Mandarin news readers (the headline GIF is the faster of them), 1.4-1.6 s on English narration, 2.1 s on a
Bengali speaker who pauses at every clause and 2.7-2.9 s on a Bengali news report. The speaker's rate and pauses
explain most of the spread: a draft needs three stable words, so a fast reader gets one sooner, and a pause closes
the line, so a slow speaker's final translation arrives before any draft would (0.2 rewrites per line). The rest
is the Bengali model's 0.64 s decode chunk.

Two findings from building it:

- **OPUS-MT ran on and on because the input lacked an end-of-sentence token.** Converted Marian models expect the
  source to end with `</s>`, which Hugging Face's tokenizer appends silently; without it the decoder kept going until
  the length cap, so the Bengali for "I'll be back" came out in English 27 times as long as the source, repeating
  itself. Adding `</s>` (and the `>>cmn_Hans<<` target token for English to Chinese) fixed it and halved the median
  translation time (66.6 ms to 30.4 ms); decoding settings had nothing to do with it.
- **A llama-server restart race silently fell back.** When the optional HY-MT engine's child process crashed, the next
  request could arrive after the process died but before Windows reaped it; it then waited two seconds for a refused
  connection and was answered by OPUS-MT, and that fallback was stored as a normal translation. Only the structured
  run log showed it (`docs/observations.md`, P2, run `run_20261001T213426-fe0c84`). The backend now restarts the child
  and retries once, within a bounded wait, before it falls back, and a fallback is marked as one and never stored in
  place of the real answer.

## Configuration

Every setting of the extension and of the backend (environment variables, or `backend/.env`), with its default:
[docs/configuration.md](docs/configuration.md).

## Development

Tests, from `backend/` (install the test tools once: `venv\Scripts\python -m pip install -r requirements-dev.txt`):

| Suite | Command (PowerShell; bash: `venv/bin/python`) | What it needs | Time |
|---|---|---|---|
| Fast | `venv\Scripts\python -m pytest` | nothing: fake MT and ASR engines (`tests/fakes.py`), a fake llama-server (`tests/fake_llama_server.py`), the real app in-process, scratch data paths | ~30 s |
| Slow | `venv\Scripts\python -m pytest -m slow` | the downloaded models, and for the browser tests a Python with Playwright (`pip install playwright`, `playwright install chromium`) named in `ST_PLAYWRIGHT_PYTHON` | ~4 min |
| Extension | `cd ..\extension` then `node --test` | Node 22 | ~1 s |

**Pre-commit hook.** Run `python scripts/install-hooks.py` once per clone. From then on every `git commit` runs
`scripts/hooks/pre-commit`: the fast suite and the extension tests, and a red result refuses the commit (it tests the
files as they are on disk, so stage everything you changed). It needs `backend/venv` with `requirements-dev.txt` and
Node on PATH.

The fast suite never touches `backend/data/`; the slow tests read models from `backend/data/models` or from
`ST_MODELS_ROOT`.

**Browser harness.** `backend/scripts/e2e_extension.py` loads the unpacked extension into Chromium with
Playwright and drives one path end to end: `--path audio` (a page plays a sample and live captions must deliver a
translated cue to the page), `captions`, `security`, `sw-idle` (Chrome's real 30 s service-worker idle timeout,
driven over raw CDP because Playwright keeps workers alive), `permissions`, `clear-memory`, `cloud-keys`. Add
`--start-backend` to start a backend with scratch data. `--video FILE --record DIR --screenshot FILE` produce the
demo and the screenshot in this README. `--url URL --targets auto --lines 5` live-captions a real page's video
(`docs/live-test-youtube.md`); it stops with exit code 2 on a consent wall or bot check instead of getting past it.
`--prime-audio` starts the tab's audio output from the page before the clip plays; the extension does that itself
now (`docs/observations.md`, A11), so the flag is only for comparisons.
`--correct --correct-text TEXT` corrects the first translated line with Alt+E and reports whether the page's video
was disturbed by the typing; `--tm FILE` keeps the scratch translation memory between two runs (never a file under
`backend/data`). `--geometry` reads the overlay's lines and block against the video's rectangle every tick (coverage,
placement, `--partial-updates N` the block's box at N partial-text changes), `--shots DIR --shot-name NAME`
screenshots three moments, `--layout page|fill` chooses the harness page (a player in a page column, or the video
filling the viewport), `--fullscreen` puts a `--url` page's player into fullscreen, `--show-source` turns the spoken
words on, `--hover-controls` keeps the mouse over the player so its control bar shows (`docs/overlay/README.md`).

**Run logs and the failure report.** Every backend run writes one JSON line per event to
`backend/logs/run_<run_id>.jsonl` (stage, event, duration, error type; the extension's events arrive through
`POST /obs`; subtitle text is never logged, only lengths). `/health/json` names the current `run_id`.
`venv\Scripts\python scripts\failure_report.py logs\run_<run_id>.jsonl` summarises a run: failures by stage and
error type, per-stage p50/p95, per-session totals, and per subtitle line the latency the viewer felt and how often
its draft translation was rewritten (`docs/latency.md`). The stage names are in `docs/pipeline-stages.md`; known
swallowed and degraded paths are in `docs/observations.md`.

**Other scripts:** `backend/scripts/make_demo_gif.py` (the demo GIFs and the screenshot above, from a recorded live
run; `--clip FILE --source --targets` for another clip, as for `docs/demo-fastest.gif`),
`backend/scripts/line_latency_table.py` (the latency tables: N browser runs per clip;
`--backend-dir` / `--extension` measure another checkout's code the same way),
`backend/scripts/clip_speech_stats.py` (how fast a clip's speaker talks and pauses, from the recognizer's token
times: the speech-rate columns of the benchmark table in `docs/latency.md`), `backend/scripts/latency_report.py`
(lags of a clip streamed straight to the backend, no browser), `backend/scripts/opus_quality.py`
(OPUS-MT output and speed over the six en/zh/bn directions), `backend/scripts/asr_input_quality.py`
(`docs/asr-input-quality.md`), `backend/scripts/clean_install_check.py` (fresh venv from the requirements files, then
the fast suite), `scripts/check_large_files.py` (nothing large or binary gets committed).

CI ([runs](https://github.com/SamJ12138/whatchasay/actions/workflows/ci.yml)): the fast suite with coverage, the extension tests and the clean-install check on Ubuntu and
Windows (`.github/workflows/ci.yml`). `DEVLOG.md` is the project history; every change appends to its change log.

## Status and limitations

The five a first-time user meets soonest; the full list is in [docs/limitations.md](docs/limitations.md).

- Tested end to end on Windows 11 only. Linux runs the fast and extension tests in CI; macOS is untested.
- Speech recognition covers English, Mandarin and Bengali only. Bengali recognition is the weakest of the three (the
  Bengali model's word error rate is about 18-21 %), and Bengali names come out misspelled.
- With *Auto-detect*, the first second or two (1.3-2.3 s on the benchmark pages when the page's title or YouTube's
  caption language points to the spoken language; 1.8-4.5 s when it points elsewhere, e.g. an English title on
  Bengali speech) are recognised in the page's language, or in English, until detection has heard enough. That text
  is dimmed under *Detecting language…* and replaced if the language turns out to be another one; choosing the
  spoken language avoids the wait.
- A draft translation is the translation of the part of a sentence heard so far. Only the start that the last two
  drafts agree on is shown, so it grows rather than changes under your eyes, but it is short (about two words on
  the live clip) and the final translation still replaces it, which rewrites the line 0.8 times per line on the
  live clip and twice on a long English sentence. `SUBTITLE_ASR__DRAFT_STABLE_PARTIALS=0` turns drafts off,
  `SUBTITLE_ASR__DRAFT_AGREE_K=1` shows every draft whole.
- Sites that protect their audio (DRM) give silence; the popup then says no audio is reaching the capture.

## License

The code is under the MIT License (`LICENSE`). The models the scripts download are not part of this repository and
keep their own licenses, listed in `NOTICE.md`: OPUS-MT models (Apache-2.0, one CC-BY-4.0 with attribution),
sherpa-onnx and its speech models (Apache-2.0), Whisper tiny (MIT), and the optional HY-MT model (Tencent HY
Community License) and Mandarin model (no license declared).
