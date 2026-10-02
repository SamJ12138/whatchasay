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
| Translation en/zh/bn | **Tencent HY-MT1.5-1.8B** GGUF via official `llama-server` CUDA build (36 langs incl. bn, any-to-any). Default quant **Q4_K_M** | OPUS-MT has no en→bn / zh↔bn; NLLB is CC-BY-NC; small general LLMs are worse than NMT into Bengali; `llama-cpp-python` wheels crash (illegal instruction, no AVX-512) or predate the HunYuan arch |
| Translation fallback | OPUS-MT on CTranslate2 int8 CPU, beam 2, X→en→Y pivot, verified model table; en→bn via community `shhossain/opus-mt-en-to-bn` (30–45 ms) so every en/zh/bn direction works on CPU too | The old table routed fr/pt/it/ar→zh to `*-en` models (English output) |
| LLM post-editing | Async refiner only (Ollama / Groq / Gemini), 0.8 s deadline, revision 2 pushed to the UI; skipped for Bengali | Synchronous Qwen2.5-7B post-edit cost seconds per batch |
| Tab audio capture | tabCapture → offscreen document → AudioWorklet | `getDisplayMedia` from a content script (share picker each time, Chromium bug 40885587 mutes the tab), ScriptProcessorNode |
| Cloud tiers | Gladia / ElevenLabs (bn streaming), Google / Azure translate, Groq / Gemini refine | OpenAI transcribe (no bn), AssemblyAI Pro (no bn), DeepL (slowest, bn brand-new) |

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

- Streaming models emit no punctuation and no casing (English model outputs UPPERCASE); a light punctuation restorer
  would improve both readability and translation quality.
- Utterances longer than 10 s are force-split (rule3) mid-sentence.
- Provisional-language start shows a second or two of wrong-language text before LID switches (cleared by `reset`).
- DRM sites: tab capture likely yields silence; only a "No audio" notice is shown (not verified on Netflix).
- Chrome Translator API (Tier 0) path is implemented but not yet benchmarked; needs Chrome 138+ and a user-gesture pack download from the popup.
- Cloud ASR engine (`cloud_engine.py`) is referenced by the registry but not implemented yet (registry skips it gracefully).
- Accuracy-mode Whisper engine (`whisper_engine.py`) is referenced but not implemented yet.
- `docs/TRAINING.md` and `docs/LAUNCHER.md` still describe the v1 Ollama post-editor.
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
- Research (3 agents) + plan; backup of v1 source at `../backup-2026-09-11-pre-redesign.zip`.
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
- Docs: README, docs/SETUP.md, launchers (no Ollama, GPU wheel install), this DEVLOG.
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
  (verified: `<extension-id>` on this machine); more ids via `SUBTITLE_SERVER__EXTENSION_IDS`. CORS
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
