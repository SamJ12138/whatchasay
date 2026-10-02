# DEVLOG — Subtitle Translator

Single source of truth for architecture decisions, measured numbers and known issues.
**Convention:** every session that changes the project appends an entry to the change log at the bottom.

---

## 1. What the project is

Chrome MV3 extension + local FastAPI backend that overlays translated subtitles on any web video.
Two paths:

1. **Existing subtitles** (DOM/TextTrack cues) → backend translation → overlay. Target ≈100 ms per cue.
2. **Live captions** (no subtitles): tab audio → streaming ASR → translation → overlay. Target < 1 s end to end.

Languages: English, Mandarin Chinese, Bengali first-class (speech + text, all directions). Others translation-only.
Local-first; optional cloud tiers behind user API keys.

## 2. Architecture (v2, 2026-09-11)

```
extension/background.js   tabCapture.getMediaStreamId (user gesture) → offscreen doc; relays caption events to the tab (frameId 0)
extension/offscreen.js    getUserMedia(tab) → native-rate passthrough (user keeps hearing) + 16 kHz AudioWorklet → 40 ms Int16 frames
                          → WebSocket /ws/asr (lives here, immune to SW idle kill); optional Chrome Translator API (Tier 0)
extension/content-scripts main.js (cues, live events, revisions), overlay.js (partial/final/revised/notice lines), detector, ws client
backend/app/asr/          engine.py (protocol), sherpa_engine.py (Zipformer en/zh/bn, endpointing), lid.py (whisper-tiny LID),
                          session.py (provisional-language start, LID confirm/switch with replay, partial rate limit)
backend/app/translation/  base_translator.py (router + OPUS-MT CTranslate2 int8 with en-pivot), hymt_translator.py (HY-MT1.5-1.8B
                          via llama-server subprocess, 2 slots), cloud_translator.py (Google/Azure), refiner.py (async LLM, 0.8 s deadline),
                          pipeline.py (fast path only; refine_batch produces revision 2)
backend/app/main.py       /ws (cues, micro-batched 30 ms), /ws/asr (live), /health/json, /metrics (p50/p95), /debug (mic test page)
```

Message flow for live captions: `lid(provisional)` → `partial`* → `final` → `translation` per target (arrive independently,
extension merges per cue) → optional `revision`. `reset` tells the overlay to drop provisional captions after a language switch.

## 3. Engine decisions (why)

| Need | Chosen | Rejected / notes |
|---|---|---|
| Streaming ASR en/zh/bn on a laptop | **sherpa-onnx Zipformer transducers** (CPU, ~13 ms per 40 ms frame, fixed ~0.3–0.4 s delay, built-in endpointing) | Whisper: not streaming (LocalAgreement ≈3.3 s, AlignAtt 1–1.5 s) and **WER ≈75 % on Bengali**; Parakeet/Nemotron/Kyutai/Voxtral/Qwen3-ASR: no Bengali; in-browser Whisper WebGPU: 0.9–1.3 s interim on an M5 |
| Bengali ASR model | `sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09` (Apache-2.0, WER 17.9–20.6) | `mozilla-ai/whisper-large-v3-bn` (WER 9.65) kept for the optional delayed "accuracy mode" |
| Language auto-detect | sherpa-onnx spoken-language-ID (whisper-tiny) once per session on ~2.5 s of voiced audio; **recognizer starts immediately with a provisional language** and switches with replay if LID disagrees | Waiting for LID before recognising cost 5–6 s of blank captions in the first e2e run |
| Translation en/zh/bn (default, Phase 2b D1) | **OPUS-MT on CTranslate2 int8, CPU**, beam 2, X→en→Y pivot, verified model table; en→bn via community `shhossain/opus-mt-en-to-bn`; source ends with `</s>`, en→zh with `>>cmn_Hans<<` | permissive licenses (NOTICE.md), no GPU, p50 30 ms over the six en/zh/bn directions; the old table routed fr/pt/it/ar→zh to `*-en` models (English output) |
| Translation, optional GPU engine | **Tencent HY-MT1.5-1.8B** GGUF via official `llama-server` CUDA build (36 langs incl. bn, any-to-any), Q4_K_M; only with `SUBTITLE_MT__ENGINE=hymt` after `scripts/download_models.py --hymt --accept-hymt-license` | Tencent HY Community License (territorial limits, AUP pass-through): not a default (D1). NLLB is CC-BY-NC; small general LLMs are worse than NMT into Bengali; `llama-cpp-python` wheels crash (no AVX-512) or predate the HunYuan arch |
| LLM post-editing | Async refiner only (Ollama / Groq / Gemini), 0.8 s deadline, revision 2 pushed to the UI; skipped for Bengali | Synchronous Qwen2.5-7B post-edit cost seconds per batch |
| Tab audio capture | tabCapture → offscreen document → AudioWorklet | `getDisplayMedia` from a content script (share picker each time, Chromium bug 40885587 mutes the tab), ScriptProcessorNode |
| Mandarin ASR model (Phase 3) | `sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20` (Apache-2.0, also handles English words in Mandarin speech) | `zipformer-zh-int8-2025-06-30` (lower CER, no declared license): opt-in with `--mandarin-large` |
| Cloud tiers | Google / Azure translate, Groq / Gemini refine (cloud ASR never implemented) | OpenAI transcribe (no bn), AssemblyAI Pro (no bn), DeepL (slowest, bn brand-new) |

## 4. Measured numbers (RTX 4060 Laptop 8 GB, i9-13900H, 16 GB RAM, Windows 11, 2026-09-11)

**Speech (Zipformer, CPU):** 40 ms frame processed in ~13 ms (en/zh) – 22 ms (bn). First partial at 0.8–1.1 s of audio;
partial lag vs audio clock ≈ +0.02 s. Final emitted ≈ 0.6–0.8 s after the speaker pauses. LID compute 95–570 ms.
Recognizers load in 1.2–2.6 s each → all three are warmed at startup.

**Translation (HY-MT1.5-1.8B, llama-server, GPU):**

| Direction | Q8_0 | Q4_K_M (default) |
|---|---|---|
| en→zh short (7 tok) | 85–210 ms | 178 ms |
| en→zh medium (12 tok) | 134–271 ms | 196 ms |
| en→bn short (28 tok) | 308–399 ms | 314 ms |
| en→bn medium (49 tok) | 509–613 ms | 430 ms |
| en→bn long (62–76 tok) | 887 ms | 534 ms |
| zh→en / bn→en | ~175 ms | — |
| bn→zh | 127 ms | — |

OPUS-MT CT2 warm: 18 ms (en→zh). HTTP `/translate` en→{zh,bn} round-trip: 376 ms server, 523 ms incl. curl.

**End to end (e2e_ws_asr.py, 40 ms frames at real-time pace):** captions from 1.1 s wall in auto mode; final caption
lag −0.05 s vs audio clock; translation of a *long* 20-word sentence into en+bn arrived 0.6 s (en) / 1.1 s (bn, Q8) after
the final. Typical 5–12-word subtitle sentences land inside the 1 s target; very long Bengali outputs are the only case over.

## 5. Known issues / future work

- Streaming models emit no punctuation and no casing. Phase 3: English is restored before translation
  (`asr/punctuation.py`, `docs/asr-input-quality.md`); Mandarin and Bengali are not (no measured gain). The bilingual
  Mandarin model still writes English words in UPPERCASE.
- Lines are bounded since 2026-10-02 (`SegmentRules`): they end at the endpoint's 0.6 s of silence, at a 0.5 s pause
  between tokens, or at 6 s / 48 tokens (cut at the widest gap between words). The recognizer's own reset (rule3,
  30 s) still cuts inside a word. A cut at the length limit can separate a clause from its verb (observations A9).
- Provisional-language start shows wrong-language text until LID switches (about 5 s on the YouTube live test;
  cleared by `reset`). Since 2026-10-02 it is drawn dimmed under a "Detecting language…" label.
- DRM sites: tab capture likely yields silence; only a "No audio" notice is shown (not verified on Netflix).
- Chrome Translator API (Tier 0) path is implemented but not yet benchmarked; needs Chrome 138+ and a user-gesture pack download from the popup.
- Cloud ASR and an accuracy-mode Whisper engine were never implemented; Phase 3 removed their registry stubs and
  settings.
- `docs/LAUNCHER.md` still describes the v1 Ollama post-editor (removed with the launchers in Phase 3 Batch B).
- Both Q8_0 and Q4_K_M GGUFs are on disk (3 GB); delete `data/models/mt/HY-MT1.5-1.8B-Q8_0.gguf` if space matters.

## 6. How to verify

```
backend\venv\Scripts\python run.py                      # http://127.0.0.1:8765/health/json → device cuda, mt_engines hymt+opus
cd backend && venv\Scripts\python -m pytest -v          # fast suite (no models, no server, no network)
cd backend && venv\Scripts\python -m pytest -m slow     # real Zipformer / HY-MT / extension audio harness
cd extension && node --test                             # extension pure modules
backend\venv\Scripts\python backend\scripts\e2e_ws_asr.py <wav> auto en,zh
```
Browser: load `extension/` unpacked → YouTube video without captions → popup → Start Live Captions.

---

## 10. Change log

### 2026-09-11 — v2 redesign for sub-second live captions (en/zh/bn)
- Research (3 agents) + plan; the v1 source was backed up outside the repository.
- Replaced per-chunk Whisper ASR with sherpa-onnx Zipformer streaming engines + LID + session state machine.
- Replaced HF-transformers OPUS path with CTranslate2 int8 + verified routing table + English pivot; added Bengali everywhere
  (config, detection script range, danda line breaks, extension dropdowns, fonts).
- Added HY-MT1.5-1.8B via llama-server (auto-download of llama.cpp b10909 CUDA build + GGUF); 2 slots; per-target concurrent translation.
- Removed synchronous Ollama post-edit; added async refiner with revision-2 push (`revision` message, `onPush` in ws client, in-place overlay swap).
- Extension: tabCapture + offscreen + AudioWorklet capture, live-caption UI in popup (language select, prepare on-device translation),
  options (Bengali, live captions, refiner, cloud keys), settings-key migration (`primaryLanguage`→`primaryLang`), `frameId:0` relay.
- Fixed bugs: X→zh routes to English models, run.py calling nonexistent warmup, missing `WebSocketDisconnect` import, `chrome.alarms`
  without permission, options page fetching HTML `/health` as JSON, TM upsert clobbering user corrections, corrections stored with
  `source_lang='auto'`, "♪"-only cues sent to MT, passthrough results ignoring per-request targets, batching tied to speed_mode.
- Tests rewritten (old suite was 49/94 red): 53 tests incl. fake-engine pipeline, LID-switch logic, real Zipformer streaming, live integration.
- Docs: README, a setup guide and launchers (no Ollama, GPU wheel install), this DEVLOG (setup guide and launchers
  replaced by the README in Phase 3).
- Follow-up: added CPU en→bn route (community OPUS model) so Bengali output does not require the GPU; zh→bn pivots through English on CPU.

### 2026-10-01 — git baseline + observability (Phase 1: logging only, no behaviour change)
- Project is now a git repo (`main`). `.gitignore` excludes venv, models, llama.cpp binaries, `backend/data/` (real TM
  data), logs, archives; `scripts/check_large_files.py` guards against >10 MB / data files. Baseline commit `fffcdda`.
  What is left out and how to get it: `docs/excluded-from-git.md`.
- `backend/app/obs.py`: one JSON line per event in `backend/logs/run_<run_id>.jsonl` (ts, run_id, session_id, layer,
  stage, event, duration_ms, error_type, error_message, context). Session id comes from the extension (`session_id`
  WS query param, `X-Session-Id` header) and is carried by a ContextVar into executor threads.
- Instrumented backend (startup, http, ws_connection/receive/reply, asr_chunk + ws_receive summaries per 100 frames,
  lid, segment, micro_batch, translate, lang_detect, tm_lookup/store, mt_call, refine, config) and the llama-server
  lifecycle (`translator_process`: spawn/ready/restart with reason/exit code via a watcher thread).
- `extension/obs.js`: same record shape to `console.debug('[ST-OBS]')`, batched every 2 s to the new `POST /obs`
  (content scripts relay through the service worker). Wired into background, offscreen, content scripts, popup, options.
- `backend/scripts/failure_report.py`: stage × error_type, per-stage p50/p95, per-layer and per-session summaries.
- Stage map: `docs/pipeline-stages.md`. Swallowed/degraded paths, new findings, PROJECT_REPORT corrections:
  `docs/observations.md` (notably: llama-server crash race → silent OPUS fallback; "It is raining again." detected as
  Tagalog → untranslated and stored; first llama-server spawn pays 2.5 s of `import torch`).
- Verified: 49 pass / 5 skip (server down), 54 pass (server up); e2e on the three sample WAVs; caption path in headless
  Playwright Chromium with the unpacked extension. Tests now write obs logs to `backend/logs/pytest/`.

### 2026-10-01/02 — Phase 2a: test-driven correctness, security, isolation, lifecycle, clean install
- Batch 0: `docs/phase1-diff-audit.md` (every non-logging hunk of `f9758f0`); one behaviour change found and reverted
  (lifespan re-called `get_pipeline()` for a log line and aborted startup when the pipeline failed to build;
  `tests/test_startup.py`). Audio-path harness `backend/scripts/e2e_extension_audio.py`: Playwright Chromium + unpacked
  extension + `--allowlisted-extension-id` (tabCapture without the action click) + a `<video>` playing a sample WAV →
  translated cue logged by the content script (`ext_render`) in ~7.5 s; slow test `tests/test_e2e_extension_audio.py`.
  `--use-fake-ui-for-media-stream` breaks the offscreen `getUserMedia({chromeMediaSource:'tab'})` ("Requested device
  not found"); leave it out.

- Batch 1: `docs/test-inventory.md` (what each of the original 54 tests asserts). `pytest.ini`: fast suite by default,
  `-m slow` for the two model tests + the audio harness. The five live-server tests are now in-process
  (`tests/test_app_inprocess.py`, FastAPI `TestClient` + fake engines). Fixtures: `tests/fakes.py` (fake MT engine with
  ok/fail/timeout/source/empty modes, fake ASR engine), `tests/fake_llama_server.py` (llama-server HTTP stub that can be
  told to exit), `app_env` (real app on fakes + scratch TM); the whole test session's data paths point at a scratch dir.
  `HyMTEngine._command()` seam for the stub. Extension: `content-scripts/ws-protocol.js` (pure `/ws` frame parser, used
  by the content-script client) and `node --test` suites for it and `obs.js`.
  The browser harness is now `backend/scripts/e2e_extension.py` with `--path audio|captions` (the caption path: a
  `<video>` with a WebVTT track → detector → `/ws` → overlay, 0.5 s to the first translated cue); slow tests
  `tests/test_e2e_extension.py` (both paths, 45 s with model loads).
- Batch 2 (T1-T8, T11, T12, W6, P6): every target of `/translate`, `/ws` and `/ws/asr` replies carries `status`
  ok | fallback | untranslated | error (+ `engine`, `error_type`, `error`); results carry `degraded`. The router
  (`BaseTranslator.translate_batch_detailed`) never returns the source as a translation: no engine → `untranslated`
  (source only as a placeholder), engine failure/empty output → fallback engine → `fallback`, else `error` with no text;
  an engine echoing its input → `untranslated`. Only ok/fallback reach the TM (fallback at quality 0.6 with the engine
  name; TM `engine` column, additive migration; `set` keeps the better row), only all-ok results reach the memory cache;
  a fallback TM row is re-asked of the primary engine and served as `fallback` if it still fails; legacy rows equal to
  the source are never served. TM writes are awaited before replying and failures logged with `error_type`.
  `/translate` returns `notes` + `degraded`. Language detection rule in `settings.lang_detect` (script first; Latin text
  under 24 chars or below 0.90 confidence or outside the candidate set → session hint; hint from the cue, the `/ws`
  connection's `source_lang_hint`, or `default_hint` "en"); the content script sends the track/page language as the hint.
  `/ws` handler errors (correction, config) now use the error envelope with `error_type`; the extension parser surfaces
  unsolicited errors, treats legacy `{status:"error"}` results as errors, draws only ok/fallback targets and caches only
  all-ok results. `pytest-timeout` added (a hung WebSocket receive now fails the test).
- Batch 3 (W8, E16, E17): `app/security.py` — `OriginGuard` middleware refuses HTTP requests and WebSocket handshakes
  (403 before the upgrade) whose `Origin` is not the extension or the backend's own loopback origin; requests without an
  Origin (curl, scripts) pass. The unpacked extension's id is derived from `../extension`'s path exactly as Chrome does
  (verified against the id Chromium assigned); more ids via `SUBTITLE_SERVER__EXTENSION_IDS`. CORS
  echoes only allowed origins, no credentials. `run.py` refuses a non-loopback `--host`. Extension: the caption path's
  `/ws` socket moved into the service worker (`lib/websocket-client.js` + `lib/tab-connections.js`; content scripts use
  `content-scripts/backend-port.js` over a runtime Port), because a socket opened by a content script carries the web
  page's Origin and would be refused. A tab connects only after the user enables it in the popup ("Translate subtitles
  on this tab"; `SET_TAB_ENABLED` accepted only from extension pages; state in `chrome.storage.session`); no detection
  before that. Window messages are accepted only from the page's own origin (`content-scripts/guards.js`); sub-frame
  detectors relay cues through the worker (`FRAME_CUE`). Browser harness `--path security`: 8 checks pass (old
  extension fails 3: detects on load, accepts a cross-origin cue, no per-tab enable).
- Batch 4 (W7, A1-A3): `/ws` `config` is per connection (`connection_info[...]["config"]` + `session_config()`):
  target languages, source-language hint, refiner preference (a session can ask for or decline revisions regardless
  of the server default; refinement is decided per cue, not per mixed micro-batch), max_lines / max_chars (lines are
  re-broken per session in `format_result(result, layout)`), fast_mode / strict_meaning_lock. No `/ws` message changes
  server-wide settings any more, except cloud keys (shared engines; out of this phase's scope). ASR: every lid / partial /
  final carries `lang_status` = provisional | confirmed | manual | fallback | error; LID that gives up → `fallback`
  (source `fallback`), LID that raised on every attempt → `error` with `error_type`; `confirmed: true` only for confirmed
  and manual. The overlay says "Could not detect the spoken language; assuming …" for fallback/error.
- Batch 5 (P1-P3, P9): llama-server output (stdout+stderr, one pipe, `--log-disable` dropped) is drained by a thread;
  the last 50 lines ride on the `translator_process` fail line (`exit`, `early_exit`, `health_timeout`). A request that
  finds the child dead or unreachable (connect/protocol/read error) waits for it to exit or stops it, restarts it, waits
  for `/health` within `hymt_restart_timeout_s` (20 s) and retries once — only then does the router fall back (the P2
  window, "dead but `poll()` still says running", is reproduced in `tests/test_translator_process.py`). Warmup is
  retried (`hymt_warmup_attempts` 3, doubling pause); after that HY-MT is paused, not disabled: `supports()` is false
  for `hymt_retry_initial_s` (15 s), then tried again, doubling to `hymt_retry_max_s` (300 s); `status()` shows
  `retry_in_s`. P9: the torch wheel's CUDA lib dir is found with `importlib.util.find_spec` (no `import torch`):
  spawn→Popen 1187-1236 ms → 1.1-3.8 ms in a fresh process (`scripts/measure_spawn.py`; the observations' 2.46 s was a
  cold disk cache); Popen→ready unchanged at ~1.1 s, so the DLL path still works.
- Batch 6 (S1, S3-S5): deleted the dead v1 `translation/post_editor.py` (the only `import ollama`, imported at startup
  through `translation/__init__`), the unused `PostEditorOutput` model, the `llama_cpp` spike `scripts/spike_hymt.py` and
  `translation/cuda_dlls.py`. Requirements split: `requirements.txt` (runtime, CPU default), `requirements-gpu.txt`
  (CUDA extras, unchanged), `requirements-dev.txt` (pytest, pytest-asyncio, pytest-timeout, pytest-cov).
  `backend/scripts/clean_install_check.py` = the clean-install proof (fresh venv, both files, `import app.main`, fast
  suite): passes locally on Python 3.10 with the newest packages (FastAPI 0.142, Starlette 1.7). It caught a test-helper
  portability bug (Starlette 1.x reads WebSocket test messages from an anyio stream, not a queue).
- Batch 5 follow-up (found while committing Batch 6): `test_restart_that_fails_falls_back_within_the_bound` was flaky
  (killed child sometimes still answered) and hid a gap: a request that found the child already reaped restarted it
  with the 120 s first-start health budget, not the restart bound. Lazy restarts after the first spawn now use
  `hymt_restart_timeout_s`, and the llama-server client has a 0.5 s connect timeout (Windows takes ~2 s to refuse a
  localhost connect to a dead port), counted as "child gone". The test covers both shapes (reaped / poll still running).
- Batch 7: `.github/workflows/ci.yml` — three jobs on ubuntu-24.04 and windows-2025: backend fast suite with coverage
  (Python 3.12, `pip install -r requirements.txt -r requirements-dev.txt`, coverage.xml uploaded), extension `node --check`
  + `node --test` (Node 22), and the clean-install proof (`backend/scripts/clean_install_check.py`). No model downloads.
  Actions pinned by commit SHA: checkout v7.0.1, setup-python v7.0.0, setup-node v7.0.0, upload-artifact v7.0.1, all
  `using: node24`. Locally green on Windows / Python 3.10 (Python 3.12 and Linux are first exercised by CI in Phase 3).

### 2026-10-02 — Phase 2b: product scope (D1-D4)
Product statement: "live local subtitles for any tab's audio: nothing you hear leaves your machine unless you turn
that on". Reviewer decisions: D1 OPUS-MT on CPU is the default engine, HY-MT optional behind a license gate; D2 the
page-caption path is opt-in with optional host permissions; D3 audio sessions leave no trace in the persistent TM;
D4 cloud providers off by default, keys only in backend config.
- Batch 0 (SW lifetime, TM migration backup): the caption path's `/ws` socket lives in the service worker; how it
  survives the worker's 30 s idle timeout is now written down (`docs/architecture-notes.md` §1) and tested in a real
  browser. Playwright cannot test it: it attaches DevTools to every service worker and an attached worker is never
  stopped (measured: same instance after 45 s idle). New harness path `e2e_extension.py --path sw-idle` drives
  Chromium over raw CDP (websockets) with nothing attached to the worker: A) 40 s quiet with the content script's 20 s
  Port keepalive: worker kept, next cue translated; B) keepalive cleared in the content script's world: Chrome stops
  the worker after 30.3 s, the port reopens, the worker restarts and the next cue is translated over a new socket;
  C) `ServiceWorker.stopAllWorkers`: same. A mutant whose port never reopens fails B and C. Fixed a wake-up race found
  while reading the restart path: `TabConnections.attachPort` awaited the enabled-tab restore before adding its
  listeners, so a woken worker could drop the port's first `{op:'connect'}` (node tests). TM: `PRAGMA user_version`
  schema versions (1 = baseline, 2 = `engine` column; unstamped databases recognised by their columns); before any
  migration the database is copied with SQLite's backup API (WAL rows included) to `<name>.bak-<old version>`, logged
  as `tm_migration`; an existing backup is never overwritten; a failed backup stops the migration.
- Batch 1 (D1, default engine): `SUBTITLE_MT__ENGINE` picks the engine, default `opus` (`engine_order` derived as
  [engine, opus]). HY-MT is optional: configured but absent (no GPU, no GGUF, no llama-server) -> one WARNING line
  naming `scripts/download_models.py --hymt --accept-hymt-license` and OPUS-MT serves; the backend never downloads
  the GGUF or llama-server any more. New `scripts/download_models.py` (backend's Python): Zipformer en/zh/bn +
  whisper-tiny + the four OPUS-MT models for en/zh/bn by default; `--hymt` prints the Tencent HY Community License
  summary and exits 2 unless `--accept-hymt-license`; `--data-dir` / `--bin-dir` / `--skip-*`. OPUS-MT repetition:
  the cause was tokenization, not decoding: the source lacked Marian's `</s>` (Hugging Face's tokenizer adds it), so
  the decoder ran on (bn->en "I'll be back" became 19.5x the source; zh->en said everything twice), and the
  multi-target opus-mt-en-zh needs `>>cmn_Hans<<` (it mixed traditional characters in). Decoding knobs moved to
  settings (beam 2, repetition penalty 1.2, no-repeat 2-gram kept) plus a cap of 3x the source tokens + 4. Bar
  (slow `tests/test_opus_quality.py`): no repeated 3-gram and output <= 3x the source (words; characters for
  Chinese) on the six en/zh/bn directions over the repo's sample sentences. p50 per call over the six directions
  66.6 -> 30.4 ms, p95 251.9 -> 86.8 ms (`scripts/opus_quality.py`, i9-13900H). Still weak: ASR-style upper-case
  literary English, and zh->bn (pivot through English) can drop a clause. `NOTICE.md`: licenses of every downloaded
  model/binary; shhossain/opus-mt-en-to-bn is Apache-2.0; the Mandarin Zipformer declares no license and stays the
  default (the Apache-2.0 alternatives have 1.6-1.9x its CER on aishell-1).
- Batch 2 (D2, caption path opt-in): fresh install = no host permissions, no content scripts, no install warning
  (`getPermissionWarningsByManifest`: `[]`; before: "Read and change all your data on all websites"). Default
  permissions storage / activeTab / offscreen / scripting. `tabCapture` turned out to carry that same all-sites
  warning by itself, so it is an optional permission requested inside the popup's first *Start Live Captions* click
  (conflict with "default permissions = what the audio path needs", decided by the intent "asks for no site
  permissions"). Overlay injected on demand (`ensureContentScript`, activeTab). Caption mode (`settings.captionMode`,
  off) requests the 14 caption sites (`lib/caption-mode.js`; the brief said 13, the detector has 14 adapters) as
  optional host permissions from Options, saved as on only when granted; the worker registers the content scripts
  for the granted origins and unregisters them when it goes off (permissions given back). Popup: per-tab toggle
  disabled with a hint while caption mode is off. Found and fixed while testing: enabling a tab before a granted
  page's registered scripts ran injected them twice (two backend ports; made the sw-idle phase B flaky) ->
  `ensureInjected` waits for a loading page's own scripts, `backend-port.js` never replaces its port. Harness: every
  path but `permissions` loads a scratch copy granted `http://127.0.0.1/*` + `tabCapture` (automation cannot click
  the action or answer a prompt); new `--path permissions` on the real extension; `--lang=en-US`.
  `docs/architecture-notes.md` §3.
- Batch 3 (D3, memory privacy): `pipeline.MemoryPolicy`; `/ws/asr` sessions use `audio_session_policy()`: their own
  `TranslationCache` (cleared when the socket closes), no TM writes, TM reads without touching use_count, nothing in
  the refiner context; `/ws` and `/translate` use `caption_policy()` (TM writes per `tm.persist_captions`). Config
  `tm.persist_audio_sessions` false, `tm.persist_captions` true, `tm.retention_days` 30 (startup sweep of machine rows
  unused that long, logged `tm_retention`; corrections kept). `POST /tm/clear` (translations, corrections, glossary;
  VACUUM + WAL truncate: the text is gone from the files, tested on the bytes) and an Options button behind a
  confirmation; Options states what is stored where in one sentence built from `/health/json` `privacy.tm`. Found
  while testing: concurrent first use of the TM ran `initialize()` twice ("duplicate column name: engine" /
  "database is locked", and a leaked aiosqlite thread hung the test process at exit): now under a lock. Browser:
  the audio path asserts the TM row count is unchanged (with `SUBTITLE_TM__PERSIST_AUDIO_SESSIONS=true` it goes
  0 -> 1 and fails); new `--path clear-memory`. `docs/architecture-notes.md` §4.
- Batch 4 (D4, cloud providers): `cloud.enabled` (false) gates cloud MT and the Groq/Gemini refiners; enabling it
  appends `cloud` to the router order. Keys only from backend config / `backend/.env` (`SUBTITLE_ENV_FILE`); the
  `cloud_keys` paths on `/ws`, `/ws/asr` and `POST /config` are gone (ignored + logged by name). Google and Gemini
  keys moved from the URL (`?key=`) into headers: before, a 403 produced "Client error '403 Forbidden' for url
  '...translate/v2?key=<key>'", which reached logs and the client's error text. `CloudMTError` messages carry the
  provider and status only. Startup logs which provider receives text; `/health/json` `privacy.cloud`; Options shows
  the same line. Extension: settings schema 4 deletes stored keys (`lib/settings-migration.js`) with a notice
  pointing at `backend/.env`; key fields and the unimplemented cloud-ASR option removed; no key leaves the
  extension in any message. `cloud_translator.py` coverage 0% -> 91% (mocked httpx transport). Browser path
  `cloud-keys` (cloud off and on). `docs/architecture-notes.md` §5.
- Batch 5: `docs/README-outline.md` (removed in Phase 3 once the README existed): the README's section list, the product statement, and the quickstart / test
  commands as they stand now (Python 3.10+, CI 3.12; Node 22), for Phase 3. Noticed on the way: the Options page's
  shortcut list is stale (Alt+] / Alt+[; the manifest has Alt+Period and no decrease-font command).

### 2026-10-02 — Phase 3: make it public
- Batch A (pre-publication quality and license fixes):
  - ASR-style input (`docs/asr-input-quality.md`, `backend/scripts/asr_input_quality.py`): raw UPPERCASE English
    from the Zipformer translates clearly worse with OPUS-MT (en->zh "the squalid quarter of the brothels" ->
    有质量的胶片 "quality film"; en->bn unrelated sentences); cased + punctuated text keeps the meaning. Mandarin and
    Bengali: hand punctuation gave no consistent gain. New `asr/punctuation.py`: sherpa-onnx online punctuation
    model `sherpa-onnx-online-punct-en-2024-08-06` (Edge-Punct-Casing, Apache-2.0, 7.5 MB int8), p50 4.4 ms /
    p95 6.2 ms per sentence on one thread; English partials and finals are restored in `StreamingASRSession`
    (`restore_text` hook; finals keep `raw_text`); `asr.punctuation` (on), `asr.punct_dir`. Downloaded by
    `scripts/download_models.py`, never by the backend (missing -> text unchanged, one WARNING).
  - Mandarin default: `sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20` (Apache-2.0); the undeclared-
    license `zh-int8-2025-06-30` is `asr.zh_model=large`, downloaded only with
    `--mandarin-large --accept-mandarin-large-terms` after a notice (configured but absent -> bilingual + one
    WARNING). fp32 copies in the bilingual archive are deleted after extraction (555 -> 201 MB).
  - Options page shortcut list = the manifest's commands + the Alt+E page key, and shows the bound keys
    (`chrome.commands.getAll`).
  - Dead code: `/config/speed_mode` and `features.speed_mode`, `post_edit_batch_size`, `ollama.fallback_model` /
    `max_retries`, the cloud/Whisper ASR stubs and their settings (`asr.whisper_*`, `cloud.asr_provider`, Gladia /
    ElevenLabs keys), the v1 `backend/ollama/Modelfile` and `docs/TRAINING.md`, the `ollama` / `llama_cpp`
    dependency probes, the no-op 8-byte frame-header branch.
  - Tests: fast 168 -> 199, node 43 -> 46, slow 18 -> 20 (real punctuation model; Mandarin through the browser
    harness on the bilingual model). Slow tests can read models from `ST_MODELS_ROOT` (the harness backend gets
    `SUBTITLE_ASR__MODELS_DIR` / `SUBTITLE_ASR__PUNCT_DIR` from it), so new models were tried without writing to
    `backend/data/`.
- Batch B (repo hygiene):
  - Personal-data sweep (tracked files + `git log -p --all`, messages and author/committer): the author's name and
    school address on every commit; the Windows home path in three commit bodies (pasted test output) and in one
    test (`test_security.py`, the path whose extension id was checked against Chromium); a made-up Linux home path in the same
    test; no gmail, phone numbers or other addresses. Subtitle text: the 91 strings in the local translation memory
    were matched against the tree and the history; the 5 found are the repo's own sample sentences (sample-WAV
    transcripts and probe sentences that early test runs wrote into the real TM), nothing from watched videos.
    Fixed in the tree (synthetic paths; the extension-id derivation is now checked against Chrome by every slow
    browser run, `extension_id_derived_ok`); the history is rewritten after this commit with git filter-repo
    (author/committer `Tianyi Jia <186099051+SamJ12138@users.noreply.github.com>`, home paths and the
    home-derived extension id replaced in messages and file contents).
  - `backend/tests/test_repo_hygiene.py`: no local username, school domain, gmail, home paths (Windows, MSYS,
    Linux, macOS), phone numbers or e-mail addresses other than no-reply ones in tracked files; with a local TM,
    no TM text outside the sample files.
  - `LICENSE` (MIT, 2026 Tianyi Jia) for the code; `NOTICE.md` stays for models and libraries.
  - `backend/.env.example`: every setting with its default and one line (the backend reads `backend/.env`;
    `test_env_example.py` checks completeness, that the uncommented file loads to the defaults, and no secrets).
    Writing it found seven settings nothing reads (`translation.max_reading_speed_vi`, `mt.hymt_file`,
    `ollama.temperature`, `cache.fuzzy_match_threshold`, `server.batch_timeout`, `features.enable_corrections`,
    `corrections_file`): removed. `backend/data/` stays wholly ignored (no `.gitkeep`: the backend creates it, and
    nothing may be written there).
  - Removed: the launchers (`Start_/Stop_Subtitle_Translator.bat/.command`, which used `wmic`, and
    `docs/LAUNCHER.md`; the README documents the plain commands), `backend/scripts/spike_zipformer.py`, the
    pointer to the v1 backup outside the repo.
- Two fixes found while writing the README (separate commits): the Options page still offered the never-implemented
  "Accurate but delayed (Whisper, GPU)" engine (removed; a stored choice shows as Auto); the popup's shortcut list
  showed an unbound Alt+Comma (now "Larger Font Alt+.", node test covers the popup).
- Batch C (README): written for someone who has never seen the project (`README.md`): what it does and for whom,
  requirements with exact model sizes (2.29 GB downloaded; 1.22 GB in `backend/data/models` + 1.24 GB Hugging Face
  cache), quickstart for PowerShell and bash, usage (languages, targets, caption mode, corrections, memory,
  shortcuts, troubleshooting), the optional HY-MT / cloud / large-Mandarin extras with their license notes, how it
  works with a measured latency table, the configuration table (generated from `backend/.env.example`; a test keeps
  every setting in both), development, limitations. `docs/SETUP.md` (launchers, old sizes) and
  `docs/README-outline.md` are folded into it. `docs/screenshot.png`: the browser harness's new `--video --record
  --screenshot --hold --size` options on a public-domain NASA clip (credited in `NOTICE.md`), CPU only.
  `/health/json` carries `run_id`; `backend/scripts/latency_report.py` streams clips at real-time pace and reports
  per-clip lags. Run `20261002T110447-172f05` (CPU): partial captions within 0.01 s of their audio, finals
  0.46-0.63 s after the last word (the 0.6 s endpoint silence), translations 27-142 ms after the final.
  Found while documenting: corrections (Alt+E) on a live-captions-only tab are dropped silently (`main.js`
  `handleCorrection` returns when the tab has no caption connection); Korean and Portuguese targets have no OPUS-MT
  route from English. Both listed as limitations, not changed.
- Process note: two backends this phase were started without `SUBTITLE_ASR__MODELS_DIR` (direct harness runs) and the
  runtime auto-download put the new default Mandarin model into `backend/data/models/asr/` (200 MB, nothing else
  in `backend/data/` touched). Harness runs now get the models directory explicitly.
- Batch D (publish): repository name chosen by the owner, `whatchasay` (README title and clone commands updated).
  `gh repo create whatchasay --public --source=. --push`: https://github.com/SamJ12138/whatchasay, default branch
  `main`. First CI run (37027169748) green on all six jobs (backend fast suite, extension tests, clean install;
  ubuntu-24.04 and windows-2025, Python 3.12): 215 passed, 1 skipped (the TM check of the hygiene test: no local
  translation memory on a CI runner). Nothing needed fixing. README CI badge, repository description and topics set.
- Batch E (fresh clone): cloned https://github.com/SamJ12138/whatchasay into an empty folder and followed the README's
  PowerShell quickstart literally with `python` = Python 3.14.2, no pip cache and an empty Hugging Face cache. Run 1
  timings: clone 1.2 s, venv 3.4 s, `pip install -r requirements.txt` 46 s, CPU torch 49 s, `download_models.py`
  158 s (3.2 GB, about 29 MB/s), `run.py` ready in about 5 s. Extension: installed Google Chrome 153 with a fresh
  profile; "Load unpacked" stood in for by CDP `Extensions.loadUnpacked` (Chrome 137+ ignores `--load-extension`;
  `--enable-unsafe-extension-debugging`), the toolbar click and the first-use prompt by a manifest that lists
  `tabCapture` + the video's origin (restored after) and `--allowlisted-extension-id`; Chrome assigned the id the
  backend derives from `../extension`. The public Wikimedia URL of the NASA clip, Auto-detect: first Chinese
  translation on the page 9.7 s after start (the first sentence ends ~9 s in). README errors it found: pip fails on
  Windows paths over 260 characters (the first attempt, in a deep folder); the Hugging Face cache is 2.18 GB, not
  1.24 GB (transformers fetches both `pytorch_model.bin` and an auto-converted `model.safetensors` for three of the
  four models), so the download is 3.2 GB; harmless download warnings; OPUS-MT moves to an NVIDIA GPU when CTranslate2
  sees one; no times stated. All fixed in the README.  Run 2 (new empty folder, same conditions, README as pushed in 468a296): clone 0.7 s, venv 3.3 s, pip 39.1 s,
  torch 37.6 s, download 161.8 s, `run.py` ready ~5 s, first translation in Chrome 153 after 9.4 s; zero edits.
- Batch F (demo GIF): `backend/scripts/make_demo_gif.py`: the public-domain NASA ScienceCasts clip "The Zero Gravity
  Coffee Cup" (Wikimedia Commons, license checked on the file page, SHA-256 pinned, cut to 0:09-0:39.5, 30.5 s;
  `data/samples/demo.webm` did not exist), played by the browser harness (`--video --record --size 960x540
  --font-size 28`, backend with `SUBTITLE_TRANSLATION__DEVICE=cpu`), recorded by Playwright, then a 12 s window
  from 3 s before the first translation, two-pass palette (imageio-ffmpeg, 640 px, 6 fps, 128 colours, no dither):
  `docs/demo.gif`, 4,997,738 bytes, 12.0 s. Credit in `NOTICE.md`. `tests/test_docs_media.py`: README shows the GIF
  and the screenshot, the GIF is at most 8 MB and 12 s (frame delays summed by walking the GIF blocks), every
  relative README link exists. Fresh-clone runs and the demo used a `subst` drive for the scratch folder (the
  scratch path itself is long enough to hit the 260-character limit inside the venv).

### 2026-10-02 — Phase 3 follow-ups
- Pre-commit hook: `scripts/hooks/pre-commit` (sh, LF pinned by `.gitattributes`, tracked executable) runs the fast
  backend suite and `node --test` and refuses the commit on red; `scripts/install-hooks.py` sets
  `core.hooksPath=scripts/hooks`; README Development documents it. Installed in this clone; no commit bypasses it.
  `tests/test_hooks.py` builds throwaway repos with a stand-in backend Python: green lets the commit through, a red
  backend or a red extension suite blocks it.
- Corrections (Alt+E) now work, and on live-captions-only tabs too. Found on the way: they had never been saved
  anywhere: the overlay passed a line's role ('primary' / 'secondary') to `_handleEdit` as if it were a language
  code, so the "corrected" translation always equalled the original and the backend stored nothing (while
  answering "saved"). Fixed (the line's language code). Live-only tabs have no caption socket: the content script
  hands the correction to the service worker (`SUBMIT_CORRECTION`), which posts it with the extension's origin to
  the new `POST /corrections` (`lib/corrections.js`); `save_correction` is shared with `/ws` and clears every
  in-memory cache including running live sessions' own (`pipeline.clear_memory_caches`, weak registry), so the next
  identical sentence in the same session shows the fix. Editing during live captions: Alt+E picks the newest cue
  with a translation; the edited cue is neither hidden (8 s timer) nor dropped by the next sentence, and the
  overlay does not rebuild the line while it is being typed in. Browser harness `--correct` (Alt+E, type, Enter
  while the clip plays; `/stats` `user_corrections` 0 -> 1).
- Target-language picker: `/health/json` `routes` = for each spoken language, the offered targets the loaded engines
  can reach (`BaseTranslator.supports`, pivot included). `lib/target-routes.js` turns that into availability for the
  spoken language chosen in Settings (Auto-detect: greyed only if no spoken language reaches the target, otherwise a
  note naming the missing pairs); the Settings page disables those options, adds "(no route)" and the reason as a
  tooltip ("no local model for en->ko"); a saved choice stays selectable-visible; no backend = nothing greyed.
  With OPUS-MT: Korean and Portuguese, from all three spoken languages (checked in Chromium).
- Model download size: the runtime reads only the converted CTranslate2 folder, so `OpusCT2Engine._convert` now
  downloads each OPUS-MT checkpoint into a private folder next to it (one weights file: `model.safetensors` if the
  repo has it, else `pytorch_model.bin`; plus `*.json`, `*.spm`), converts from that local path and deletes it,
  whatever happens; the user's own Hugging Face cache is not touched. Measured, OPUS-MT part with an empty HF cache
  (`download_models.py --skip-asr`): before 81.6 s and 2.185 GB left in the HF cache; after 57.3 / 57.8 s and
  0 bytes; converted `model.bin` identical (SHA-256) for all four models, to each other and to the ones in use.
  Whole step 3: 2.3 GB downloaded (was 3.2), 231 s in this session's run; disk after install 2.3 GB (was 4.5).


### 2026-10-02 — README opening, live YouTube test, YouTube demo GIF
- Batch 1 (README opening): the first paragraph is the owner's text, verbatim (why the project exists: couples who
  do not share a first language). The old one-line tagline is gone; the paragraph after it no longer repeats the
  "stays on your machine" sentence (its 127.0.0.1 / cloud-off detail stays). `tests/test_docs_media.py` pins the
  opening paragraph and the no-repeat.
- Batch 2 (spoken-language ID constrained to the supported languages; owner's design): the first live runs on the
  YouTube clip (`docs/live-test-youtube.md`) found whisper-tiny calling the Bengali film audio Hindi or Nepali in
  2 of 3 complete runs (`run_20261002T133703-391c87`: `ne` then `hi`; `run_20261002T133849-8532a3`: `hi` twice;
  `run_20261002T133353-85b0c2` got `bn`). Both answers are outside {en, zh, bn}, so the session kept the
  provisional English (`fallback`), the English target was skipped, nothing was translated. `asr/lid.py` now runs
  the same whisper-tiny ONNX files with onnxruntime (whisper's log-mel front end in numpy, encoder + one decoder
  step after start-of-transcript) to get its probability for every language, and picks the argmax over the
  allowed languages, renormalised, if it reaches `asr.lid_min_confidence` (0.6); below it the session stays
  provisional exactly as an undecided attempt did. Every call logs stage `lid_scores` (new, `docs/pipeline-stages.md`
  a7.1): whisper's unconstrained top 3, the constrained distribution, the best one, its confidence, the floor.
  Measured on the session's real LID windows (both attempts, starts 0 / 0.3 / 0.7 s): en sample constrained en >=
  0.999, zh sample zh >= 0.951, bn sample bn 0.738-0.942, the clip bn 0.967-0.999 while whisper's own top is `hi`
  in 5 of 6 windows (up to 0.82). The onnxruntime front end agrees with sherpa-onnx's own answer except two near
  ties. Tests: `tests/test_lid_constrained.py` (fast: the choice, the floor, `lid_scores` on every call, the
  metadata tables, the mel shape, the setting) and `tests/test_lid_real.py` (slow: the three sample WAVs and the
  clip through the real session, 3 runs each, all must confirm the right language; RED before: the clip gave
  fallback / bn / fallback). The clip's first 20 s (16 kHz WAV) were recorded locally from the playing video in the
  browser and are not in the repository (`ST_LID_CLIP_WAV`; skipped without it). `docs/observations.md` A7 and a
  README limitation: whisper-tiny language ID is unreliable on Bengali; set the spoken language in the popup.
  Harness fixes found on the way (in the next batch): `--targets` was never applied (the extension rebuilds
  `targetLanguages` from `primaryLang` / `secondaryLang`), and the summary line could not be printed on a GBK
  console once it held Bengali.
- Batch 3 (live test on YouTube, `docs/live-test-youtube.md`): browser harness `--url URL` (audio path): the test
  copy of the extension is granted the page's origin (standing in for the toolbar click), the page's first
  `<video>` is waited for (no ad showing: YouTube's `.ad-showing`), paused, put back to `--start-at`, captured, then
  played; a consent page, a Google "sorry" page, "confirm you're not a bot" or "before you continue" ends the run
  with exit code 2 and `blocked` in the summary, nothing is clicked. `--lines N` reads the overlay through CDP
  (`DOM.describeNode` with `pierce`, the overlay's shadow root is closed) and keeps the first N translated lines
  with their originals, plus first-caption / first-translation times; later cues are only counted. `--targets auto`
  = English, switched to Chinese (UPDATE_SETTINGS, like the Settings page) if the speech is English. Summary gains
  `run_id`, `lid`, `detected_lang`, `page`. Three harness bugs found by the live runs, each with a test first
  where it could fail fast: the readiness check's JavaScript was a syntax error (a Python implicit string
  concatenation left `}}` in it; every check failed and looked like "no playable video"; now a test parses the page
  scripts with Node); `--targets` had never been applied (the harness wrote `targetLanguages`, which the
  extension's settings migration rebuilds from `primaryLang` / `secondaryLang` on every read, so every run got the
  defaults en + zh: earlier tests passed because the extra target equalled the spoken language and was skipped);
  the summary line could not be printed on a GBK console once it held Bengali (now ASCII-escaped JSON).
  Results: before the LID fix, 2 of 3 complete runs fell back to English (whisper-tiny: `ne`/`hi`, `hi`/`hi`);
  after it, 3 of 3 confirmed Bengali on the first attempt (constrained bn 0.974-0.999; whisper's own top 3 still
  led by `hi`), first subtitle 1.0-1.1 s after play (provisional English recognizer), Bengali captions from
  5.3-5.5 s, translations from 5.4-10.7 s, translate p50 34.7-43.6 ms / p95 114.5-148.1 ms per run. Five lines
  from run `run_20261002T141338-35728b` with what went wrong in them (রাজভোগ heard as রাজবুক, "royal book").
  Noticed: LID now takes ~0.87 s per call (30 s padding), was 0.18-0.56 s; not changed.
- Batch 4 (YouTube demo GIF): `docs/demo-youtube.gif`, 9.0 s, 640 px, 6 fps, 128 colours, 2,670,229 bytes: the
  Playwright recording of live run C (`run_20261002T141338-35728b`, the run whose five lines are in
  `docs/live-test-youtube.md`), window 19.5-28.5 s of the recording, where Bengali words arrive and two translations
  land ("It's too expensive to raise it.", "...tomorrow I need ten thousand royalbooks."), through the same
  two-pass palette path. `make_demo_gif.py --recording FILE --start S` converts a harness recording without making
  a new run. README: the GIF and its credit (title, channel, URL, "run on CPU") directly under the opening
  paragraph; the NASA GIF and its caption moved to "How it works". `NOTICE.md`: the excerpt belongs to its rights
  holders, is not under the MIT License, and is shown only to demonstrate the software. `tests/test_docs_media.py`:
  the GIF's place and credit, at most 5 MB and 5-10 s, the NASA GIF inside "How it works".


### 2026-10-02 — Provisional subtitles, glossary check on a real error, segmentation note
- Batch 1 (provisional subtitles; owner's rule: until language ID confirms, the overlay must not show the
  provisional recognizer's text as if it were final). Chosen: **dimmed with a label**, not hidden. Why: when the
  provisional language is right (English speech, the default first guess) the captions still start under a second
  after the speech, which is the product's goal, and at confirmation they are undimmed in place; hiding would have
  delayed every English session by the 4-5 s detection takes to protect the other languages from a few seconds of
  nonsense, which dimming plus the label already marks as a guess. Overlay state (`overlay.js`):
  `setLanguageStatus(status, {lang, name})` from the backend's `lid` message and from every partial / final
  (`lang_status`); while the status is not `confirmed` / `manual`, cue and partial lines get the class `provisional`
  (opacity 0.45) above a `lang-pending` label: "Detecting language…" while provisional, "Language not detected,
  assuming English. Pick it in the popup." (warn colour, permanent) after `fallback` / `error`, which replaces the
  6 s warning notice. On confirmation of the same language the text on screen is undimmed; on a switch the backend's
  `reset` discards it (`discardProvisional`). Found on the way: a translation of a provisional sentence could
  arrive after the switch and be drawn at full brightness, on the new recognizer's cue when the ids matched (every
  recognizer numbers its utterances from 0); `/ws/asr` translation and revision messages (and the on-device ones)
  now carry the `lang_status` of their sentence, and the overlay drops uncertain ones whose language is not the
  confirmed recognizer's (`_treatment`). Alt+E and double-click skip provisional lines; dimmed text is removed
  when the session stops; notice lines no longer fade in again on every redraw (the label flickered at 8 redraws
  per second). Tests, RED first: node `extension/tests/provisional.test.js` (10, the real render path on a fake
  DOM, `tests/fake-dom.js`); fast `test_ws_asr_translation_carries_the_language_status_of_its_sentence`; harness
  `--lines` now also keeps a `dimming` summary (`DimmingLog`: per 250 ms overlay sample, dimmed / label / whether
  the content script had rendered a confirmed language; scripts and times only, never text), fast tests for it;
  slow `test_first_confirmed_subtitle_is_the_first_undimmed_one[en-zh, bn-en]` in Chromium (RED before:
  `undimmed_before_confirmed` from 1.63 s with confirmation at 4.27 s; after, bn sample: 11 dimmed samples, all
  Latin script, first undimmed line Bengali at 4.54 s = the confirmation). `docs/observations.md` A8.
- Batch 2 (glossary on a real error; `docs/live-test-youtube.md`, last section). Task: add রাজভোগ → rajbhog through
  the normal user path, rerun the live test once, compare line 1. Result: **not possible, and line 1 was not
  fixed.** (a) The glossary is storage only: a table and two functions in `cache/translation_memory.py` that nothing
  calls; no endpoint, no Options field, the pipeline never reads it (observations T13). (b) The path that exists,
  Alt+E on line 1: before "একটা রাজবুক দেখান্ত" → "A Royal Book Shower" (`run_20261002T145420-461f73`), typed "Show me a
  rajbhog.", saved (`user_corrections` 0 → 1); the one rerun with that memory (`run_20261002T145713-909850`) wrote
  line 1 as a 10 s run-on, so the stored sentence never came back and nothing changed. The recognizer wrote the same
  two seconds five different ways in five passes, and three local playbacks of the recorded audio differed again
  (T14): sentence-level corrections cannot follow live ASR; they hold for page subtitles. (c) Found by the first
  Alt+E run and fixed here, test first: keys typed into a correction reached YouTube's player (video muted, paused,
  back at 0:00); the edited line now stops keydown / keypress / keyup (`overlay.js`; observations E18); second live
  run `run_20261002T145640-dd1c94`: video untouched. Harness: `--correct-text`, `--tm FILE` (scratch memory kept
  between runs, refused under `backend/data`), summary `correction` = {target cue, typed text, video state before /
  after, `page_untouched`}; its test page has player shortcuts (m, space, j) so the slow Alt+E test fails when
  typing leaks. Offline checks for the fix (scratch, not in the repo): sherpa-onnx hotwords for রাজভোগ did nothing
  at score 1.5 and lost the first sentence at 2.5 / 4.0; OPUS-MT bn→en does not know the correctly spelled word
  ("bar", "kings") and mangles a Latin spelling in the source ("rajbug", "rajbeg"), so a glossary needs both a
  replacement on the recognised text and a placeholder through MT. Live page, provisional text (Batch 1): dimmed
  from 1.1-1.25 s, first undimmed line at the 5.1-5.3 s confirmation, no violation in 381 samples.
- Batch 3 (segmentation, recorded only; owner: do not fix now). `docs/observations.md` A9: Bengali finals run
  sentences together (line 2 of the live test: 9.6 s of dialogue, several sentences, as one final; in the rerun a
  10.4 s first final cut by the 10 s limit inside a word; OPUS-MT then loses the meaning). Stage responsible:
  `segment` (a8), the endpoint rules in `sherpa_engine.SherpaSession.feed`; nothing before `translate` splits
  Bengali text (English has `punctuate`).
  Mechanism, measured offline on the recorded 20 s: the Bengali model decodes in 0.64 s chunks
  (`decode_chunk_len` 64) and utterances end only on chunk boundaries (after each run's first finals every
  `audio_s` in the run logs is a multiple of 0.64 s), so pauses under about 1.2 s can be missed; the token gaps
  inside line 2 are 0.64 s (twice), 0.44 s, 0.40 s. Rule 2 at 0.3 s split the later lines but not line 2.
  Second cause, Auto-detect only: on a language switch the buffered audio (4-5 s) is replayed to the new
  recognizer in one `feed()` call and the endpoint is looked at once per call, so nothing can end inside the
  replay; offline, the first 4.0 or 5.3 s fed as one block turns the first sentence and the next ones into one
  10.4 s final (the rerun's line 1), in 40 ms frames the first sentence stays its own 2.1 s final. Candidate
  fixes: split a final at token-timestamp gaps of about 0.4 s or more before MT, with a minimum piece length;
  replay the detection buffer in frames; alternatives in the row.
- Batch 4 (README). Step 2 did not produce a correction or glossary entry that fixes the mistranscription on a
  rerun, so the example under "Using it" is the real one with its real outcome: রাজভোগ heard as রাজবুক, line 1
  "A Royal Book Shower", Alt+E "Show me a rajbhog.", saved, not used when the scene was played again. The
  Corrections paragraph no longer says a correction is reused "in live captions and in caption mode alike": it is
  reused when the source sentence returns letter for letter, which page subtitles do and live captions mostly do
  not. Two limitations added (no glossary; Bengali run-on lines, A9). `tests/test_docs_media.py` pins the example.
  Tallies: fast 278, slow 27 (12 browser), node 71.


### 2026-10-02 — Subtitles that keep up with the scene (latency batches)
- Batch -1 (README): the worked correction example under "Using it" is replaced by one limitation line (corrections
  are matched on the whole recognised sentence and rarely reused on live speech; a term-level glossary is planned,
  observations T13 / T14). The full case stays in `docs/live-test-youtube.md`; `tests/test_docs_media.py` pins both.
- Batch 0 (measure what the viewer feels; `docs/latency.md`). Two latencies per subtitle line, measured where the
  text is drawn: `first_display_ms` (audio of the line's first word -> first text of the line on screen) and
  `final_ms` (audio of its last word -> final translation on screen); also `first_translation_ms` (first word ->
  first translated text) and, per session, the first confirmed-language subtitle. Mechanism: `SherpaSession` reads
  the recognizer's token timestamps (`AsrEvent.first_word_t` / `last_word_t`), `StreamingASRSession` keeps every
  frame's arrival time and stamps partial / final / translation messages with `w_first`, `w_last`, `w_session`
  (wall clock of that audio's arrival; after a language switch still the original arrival, not the replay), the
  content script subtracts when it draws (`content-scripts/line-latency.js`, stage `line_latency`, pipeline stage
  a17.1), `failure_report.py` section 6 summarises per run and session, the browser harness's summary carries
  `line_latency`, and `backend/scripts/line_latency_table.py` runs the harness N times per clip and prints the
  table. No behaviour change. Baseline, 3 runs each on the three sample WAVs and the live clip (run_ids in
  `docs/latency.md`): first display 0.29-0.75 s once the language is known, first translated text 6.6-10.4 s after
  the first word, final translation 0.3-1.5 s after the last word, first confirmed subtitle 4.0-6.0 s, the live
  clip's two lines 10.4 s and 10.2 s long. **Conflict with the brief:** "a line appears only after the recognizer
  closes the segment" is true of the translation, not of the source text: partial results already reached the
  overlay (italic line). Seen on the way, not changed here: with Auto-detect, a settings change during a session
  (the harness's `--targets auto` switch) sends `config` with `source_lang: auto`, which puts a confirmed session
  back to provisional and runs detection again (en sample: confirmed at 4.27 s, provisional again at 4.55 s).
  Tallies: fast 286, node 79.
- Batch 1 (bounded segments; commit "feat: streaming partials and bounded segments"). Partial results already
  streamed (Batch 0), so the work was segmentation. `SherpaSession` (`asr/sherpa_engine.py`, `SegmentRules`) closes a
  line, without resetting the recognizer, (1) before a word that starts `asr.split_gap_s` (0.5 s) after the previous
  token, once the line spans `asr.split_min_piece_s` (2 s) and the gap is `asr.split_gap_ratio` (2.5) times the line's
  median token gap, and (2) past `asr.max_segment_s` (6 s) or `asr.max_segment_tokens` (48), at its widest gap between
  words; `asr.rule3_min_utterance_length` 10 -> 30 s (backstop; it cuts inside words). Observation A9 folded in:
  (a) is rule (1), measured at 0.5 s not 0.4 s (0.4 s cut the clip inside sentences and made one-word lines);
  (b) the detection buffer is replayed in 40 ms frames, and the frame that triggered detection is no longer fed a
  second time after the replay (found here: 40 ms of doubled audio at every language switch). Tokens of the
  Zipformer models mark a word's first piece with a leading space, not the sentencepiece underline; CJK characters
  are words of their own. Segment lengths, recognizer alone, clip + five sample WAVs: 12 finals (median 5.24 s,
  longest 9.44 s, four over 6.5 s) -> 17 finals (median 3.16 s, longest 5.84 s, none over 6.5 s); the live clip's
  3 finals (0.68, 9.28, 7.84 s) -> 6 (0.68, 2.76, 4.16, 2.16, 2.64, 3.48 s), line 2 cut at its sentences. In the
  browser on the live clip (6 runs): 2 lines per run -> 4-6, first translated text p50 7.9-9.8 s -> 4.0-4.7 s after
  the first word. Overlay: the line in progress carries class `in-progress` (`data-state`), drawn in italics with a
  trailing ellipsis from the style sheet. Latency tracker: a line cut off a longer one counts its first display
  from when its first word was first drawn (inside the longer line's partial). **Conflicts with the brief:** the
  slow test "first_display_ms drops" could not be written honestly for this batch (partials were already shown at
  the decode-chunk lag, 0.3-0.75 s); `tests/test_segmentation_real.py` asserts what does drop, the wait from a
  line's first word to its final (9.76 -> 6.52 s on the English sample, 9.64 -> 5.60 s on the clip), and that first
  display stays under 1 s before and after. Tallies: fast 301, slow 30, node 82.
- Batch 2 (incremental translation; commit "feat: incremental translation of stable prefixes"). While a line is open,
  `StreamingASRSession` keeps the raw text of its last N partials and puts the stable prefix (`asr/draft.py`
  `stable_prefix`: leading words unchanged in N consecutive partials, CJK characters as words, restored like the
  caption) on each partial as `stable_text`; `/ws/asr` hands it to a `DraftScheduler` (newest prefix only, one start
  per debounce, nothing after the line's final) and sends the result as a `draft` message; the overlay draws it
  above the growing source text (`showDraft`: class `draft in-progress`, per target, kept at the final caption until
  that target's final translation arrives, never over a final translation, dimmed and discarded with a provisional
  recognizer); the offscreen document suppresses drafts like translations when Chrome's on-device translator
  handles the session. Flicker: `line-latency.js` counts per line the changes of the displayed translation and the
  ones that are not a pure append (`translation_revisions`, `non_append_revisions`, `drafts`); in
  `failure_report.py` section 6, the harness summary and the latency table. Measured on the live clip, 3 browser
  runs per setting (`docs/latency.md`): with the brief's defaults (N 2, 300 ms) 3.8-4.2 non-append revisions per
  line, because nearly every draft rewrites the previous one (verb-final Bengali; the same in the other directions);
  chosen defaults `asr.draft_stable_partials` 3 and `asr.draft_debounce_ms` 1500: 1.6 per line in 3 of 3 runs, first
  translated text p50 4.0-4.7 s -> 2.07 s after the first word, translate calls 0.2-0.25 -> 0.7 per second, translate
  p95 43-130 ms on the CPU (bound 150). The MT engine is untouched. **Conflict with the brief:** its defaults
  (2 / 300 ms) miss its own flicker budget by a factor of two; the budget is met only by showing fewer drafts.
  Not built: a minimum prefix length (a line's first draft is usually one word). Tallies: fast 315, node 92.
- Batch 3 (earlier language confirmation; commit "perf: earlier language confirmation"). Spoken-language ID now runs
  first after 1.0 s of voiced audio (`asr.lid_first_window_s`), then every 0.5 s of audio, voiced or not
  (`asr.lid_retry_step_s`; three early attempts), then on the full window as before (`asr.lid_window_s` 2.5 s
  voiced, once more at 4.0 s). An early attempt can only switch to a language other than the first guess, and only
  when two attempts in a row give it: whisper-tiny says "en" for noise and for the first second of Mandarin and
  Bengali (up to 0.99), so the first guess is confirmed on the full window only. The constrained-set rule and the
  0.6 floor are unchanged. `lid.py` no longer pads to 30 s (window = audio + 0.5 s): 0.14 s (0.86 s live) -> about
  0.015 s per call, and at least as confident on short audio; the model is loaded at startup (it cost the first
  attempt 0.55 s). Offline, 30 start offsets over six inputs: the same language as before in all, decided 1-2.9 s
  earlier for Mandarin and Bengali (live clip 3.92 -> 1.92 s of audio), English unchanged. Browser, live clip, 3 of
  3 runs: first confirmed-language subtitle 5.26-5.36 s -> 2.14-2.15 s after play, Bengali confirmed in all.
  Found on the way: (1) observations A10, fixed: a `config` message with `source_lang: auto` (sent on any settings
  change) restarted detection of a confirmed session; (2) observations A11, open: when a silent tab starts to play,
  its first few hundred milliseconds do not reach the capture, and the live clip, which opens with speech, lost its
  first sentence in 17 of 18 browser runs; the harness gained `--prime-audio` (a near-silent tone before the clip,
  as over a video already playing), used by `line_latency_table.py`, and `--backend-dir` / `--extension` to measure
  an older commit's code the same way. Without priming the after number is 3.62-3.69 s (the language is confirmed
  at about 2.3 s, but there is no captured speech to show before the second sentence). Tallies: fast 327, slow 35,
  node 92.
- Batch 4 (verify and document). `docs/latency.md`, "Before and after": the table rerun on the final code and, for a
  like-for-like "before", on an export of commit `0d5e875` run with the same harness (`--backend-dir`,
  `--extension`, `--prime-audio`), 3 browser runs per clip on each side, 24 run_ids. First translated text after a
  line's first word: 6.6 -> 2.6 s (English sample), 7.9 -> 2.7-3.3 s (Bengali sample), 10.3-10.4 -> 2.0 s (live
  clip); first confirmed-language subtitle 5.3 -> 2.1-2.2 s (live clip), 6.0-6.2 -> 2.5-2.6 s (Mandarin sample),
  3.9-4.0 -> 3.3 s (English); first display of the source text unchanged at the recognizer's chunk lag; final
  translation after the last word unchanged on the samples (0.8-1.1 s), 1.3 s on the live clip (its lines used to
  be closed by the 10 s cut); non-append revisions per line 1.5 (live clip), 3 (the one long English sentence);
  translate calls 0.1-0.4 -> 0.6-0.9 per second, p95 28-67 ms on the CPU. Quality drops shown there, both Bengali
  to English: a sentence over 6 s is cut and its second half loses its subject; a short misrecognised line that now
  stands alone got an invented translation ("We were married in July 1955."). README: "How it works" has the
  streaming paragraph and the before / after table (the old WebSocket-level table is gone; `latency_report.py`
  stays as a script), Quickstart says what a draft is, limitations gained the draft rewrites and the invented
  translation. `docs/demo-youtube.gif` regenerated from a post-change live run on the YouTube page
  (`run_20261002T172649-3fac9e`, recorded by the harness with `--prime-audio`, no consent wall or ad): 9.5 s,
  640 px, 6 fps, 2,967,933 bytes, from 0.1 s before the video starts: Bengali confirmed at 1.6 s, first translation
  at 1.9 s after play (first live test: 5.4-10.7 s); 13 lines in 45 s, 1.2 non-append revisions per line
  (`docs/live-test-youtube.md`, last section). Tallies: fast 329, slow 35, node 92.


### 2026-10-02 — A smaller overlay, where the video is not (overlay batches)
- Batch 0 (see it first; `docs/overlay/README.md`). The browser harness gained `--geometry` (every tick: the
  overlay's lines and block with their CDP box models against the video's rectangle, through the closed shadow
  root; `summary.overlay` with the coverage = union of the lines' boxes clipped to the video, placement counts and
  the block's box at the first N partial-text changes, `--partial-updates N`), `--shots DIR --shot-name NAME` (a
  screenshot at the first qualifying sample of three moments: idle 0.3 s after play, a line in progress of 20+
  characters with no final translation up, the first final translation), `--layout page` (a 640x360 player in a
  page column, next to the existing fill page; both pages have a hover control bar `#controls`), `--fullscreen`
  (YouTube's `f` key, trusted, `document.fullscreenElement` checked) and `--hover-controls`. Pure parts tested in
  `tests/test_harness_url.py`. Baseline, four runs (run_ids in the README), coverage at the final moment / max in
  the run: NASA fullscreen 11.0 / 20.1 %, NASA normal page 19.1 / 38.1 %, YouTube normal 7.0 / 36.5 %, YouTube
  fullscreen 3.3 / 18.7 %; the block intersected the video in every sample with a line, in both layouts, and sat
  15 % up the *viewport*, never in the bottom 15 % of the video in fullscreen. 12 screenshots
  `docs/overlay/before-*.png` (3.3 MB). The harness's models root for these runs is a scratch folder with the
  punctuation model (`backend/data/` has none and is never written).
- Batch 1 (show less; commit "feat: translation-only default, compact overlay"). `lib/overlay-layout.js` (pure):
  `fitLines` wraps by words (CJK: characters) at `maxCharsLatin` 42 / `maxCharsCJK` 22 per row, at most
  `maxLines` 2 rows, and past that keeps the newest words with an ellipsis at the start, never dropping the end;
  `fontPx` = `fontScalePct` (4.5) % of the video element's height, at least `fontMinPx` (14), the old `fontSize`
  px only while no video is known. The overlay: `showOriginal` defaults to **false** (translation only; the
  partial source text is not drawn either), `setShowOriginal`, `roleStyle(role)` (the source line at 0.8x the
  font and 0.7 opacity), every line through `_fit` (the edited line keeps the whole text), a rounded box behind
  the text only (`inline-block; width: fit-content`, `rgba(0,0,0, overlayBgOpacity 0.6)`, padding 0.15em 0.5em,
  line-height 1.25), the font set on `setVideoElement`; a cue with no translation and no draft still shows its
  words. main.js finds the largest `<video>` (the detector's for caption mode), re-checked every second while
  something is on screen; Alt+O toggles the source line (content-script shortcut like Alt+E: the manifest already
  has Chrome's four suggested keys); A+ / A- now change `fontScalePct` by 0.5 and are remembered
  (`UPDATE_SETTINGS`). Popup: "Show the spoken words under the translation (Alt+O)" toggle. Options: font size as
  a percentage of the video height, the minimum px, the box opacity; the px slider stays as the no-video
  fallback; `saveSettings` no longer hard-codes `showOriginal: true` (it had: a Settings save would have undone
  the choice). Settings schema 5 (`applyOverlayDefaults` in `lib/settings-migration.js`): the source line is
  turned off once for settings from an older schema, then the user's choice is kept. Harness: `--show-source`.
  Node tests `tests/overlay-layout.test.js` (layout math, defaults, migration, injection order); the older
  overlay tests turn the source on explicitly. Tallies: fast 334, node 109.
