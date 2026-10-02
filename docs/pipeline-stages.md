# Pipeline stages

Every stage of both paths, end to end: who owns it, what goes in and out, how it fails, and which process it runs in.
The `stage` column is the name used in the run log (`backend/logs/run_<run_id>.jsonl`, see `backend/app/obs.py`).
File:line references are to the baseline commit `fffcdda` unless marked (obs) for code added by the observability
commit.

Processes:

| Process | What runs there |
|---|---|
| **ext:content** | content scripts in every frame of every page (`content-scripts/*.js`); only the top frame talks to the backend |
| **ext:sw** | the MV3 service worker `background.js` (Chrome may suspend it after ~30 s idle) |
| **ext:offscreen** | `offscreen.html/js` + the AudioWorklet thread (`pcm-worklet.js`); lives while capture runs |
| **backend** | one uvicorn process, `run.py` → `app.main`; asyncio loop + default thread pool |
| **llama-server** | child process `bin/llama/llama-server.exe` started by `hymt_translator.py` |

Log layers: `extension` (all ext:* processes, shipped to `POST /obs`), `backend`, `subprocess` (llama-server lifecycle,
logged by the backend about the child).

---

## (a) Audio path (live captions)

| # | Stage (log name) | Process | Owner (file : function) | Input | Output | Failure modes visible in code |
|---|---|---|---|---|---|---|
| a1 | Capture start (`ext_capture`) | ext:sw → ext:offscreen | `background.js : startAsr` (:172) → `offscreen.js : startCapture` (:54); Phase 2b: `tabCapture` granted on the popup's first Start click, overlay injected with `ensureContentScript` (activeTab) | user gesture (popup click, Alt+L) + tab id | MediaStream of the tab; 48 kHz passthrough AudioContext; 16 kHz AudioContext + worklet | `tabCapture.getMediaStreamId` rejects without a user gesture or on chrome:// pages; `getUserMedia` fails; worklet module load fails; `ASR_START` reply `{ok:false}` → `Error('Failed to start capture')`; offscreen document already exists with another reason |
| a2 | Framing (part of `ext_ws_send`) | ext:offscreen (AudioWorklet thread) | `pcm-worklet.js : PcmFrameProcessor.process` (:21) | Float32 render quanta | 40 ms Int16 LE frames (640 samples) + RMS, posted to the page | none raised; silence is detected downstream (`offscreen.js:onPcmFrame` warns after 10 s below RMS 0.002) |
| a3 | WebSocket connect (`ext_ws`) | ext:offscreen | `offscreen.js : connectWs` (:143) | `serverUrl` + `source_lang`, `target_langs`, (obs) `session_id` query params | open `/ws/asr` socket; optional `config` with `cloud_keys` | connect error → reject "Cannot connect"; 8 s timeout → reject; on close while capturing → status `disconnected` and reconnect every 1.5 s indefinitely (comment says "retry once"); each reconnect is a new backend session |
| a4 | WS send (`ext_ws_send`) | ext:offscreen | `offscreen.js : onPcmFrame` (:101) | one 40 ms frame | binary WS message | socket not OPEN → frame silently dropped (counter not incremented, nothing logged); no back-pressure |
| a5 | Backend receive (`ws_connection`, `ws_receive`) | backend | `main.py : ws_asr` (:82) | handshake query; binary frames; JSON text `config`/`stop`/`ping` | session object; frames passed to a6 | no ASR engine → `error` + close 4000; `create_session` raises (unsupported language) → unhandled in the endpoint; invalid JSON → `{type:error}`; unknown text type → silently ignored; empty or odd-length frame → odd byte silently dropped (`asr/engine.py:82-83`); disconnect; any other exception → logged, loop ends |
| a6 | ASR (`asr_chunk`) | backend (thread pool) | `asr/session.py : StreamingASRSession.feed` (:158) → `asr/sherpa_engine.py : SherpaSession.feed` (:137) | int16 frame | partial/final events (`AsrEvent`) | sherpa exceptions propagate and end the connection (a5); recognizer shared by all sessions per language (thread safety unproven); partial throttle can hold the last partial (`session._emit` :241) |
| a7 | Language ID (`lid`) | backend (thread pool, inside a6) | `asr/session.py : _collect_for_lid` (:170) → `asr/__init__.py : _lid_identify` → `asr/lid.py : SpokenLanguageId.identify` (:354) | 2.5 s voiced audio (buffer up to 12 s), allowed langs | `lid` message; on disagreement `reset` + recognizer swap + replay | LID exception → WARNING, treated as undecided (:191); result outside allowed langs → None (`lid.py:365`); two undecided attempts → keep provisional language silently (:197-202); first call lazily downloads/loads whisper-tiny |
| a8 | Segment (`segment`) | backend | endpoint rules in `sherpa_engine.SherpaSession.feed` (:151) and `flush` (:164); emitted via `main.py : _handle_events` (:184) | decoder state + trailing silence | `final` message `{utterance_id, text, lang, t0, t1}` | rule3 force-splits utterances longer than 10 s mid-sentence; empty finals dropped; English output UPPERCASE, no punctuation |
| a9 | Translate (`translate`) | backend | `main.py : translate_final.one` (:144) → `translation/pipeline.py : TranslationPipeline.translate_batch` (:96) → `_process_batch` (:124) | final text + one target language (one task per target) | `TranslationResult` | whole-batch exception → error result with the **source text copied into every target** and `notes.error` (:110-113); see a10-a12 |
| a9.1 | Normalize (`normalize`) | backend | `pipeline._process_batch` → `text_normalizer.TextNormalizer.normalize` | raw text | normalized text or passthrough (music / sound effect) | passthrough result has `source_lang='unknown'` |
| a9.2 | Language detect (`lang_detect`) | backend | `translation/language_detection.py : detect_language` | text without declared language (caption path only; ASR finals carry `lang`) | (lang, confidence) | `LangDetectException` → DEBUG log, default language returned (:121, :172) |
| a10 | TM / cache lookup (`tm_lookup`) | backend | memory LRU `cache/memory_cache.py : TranslationCache.get`; SQLite `cache/translation_memory.py : TranslationMemory.get` (:135) | (text, src, tgt) | cached/TM translation or miss | SQLite error propagates → whole batch fails (a9); every TM hit also runs UPDATE + COMMIT on the hot path; a bad row (translation = source) is served forever |
| a11 | MT engine (`mt_call`) | backend → thread pool; HY-MT call goes over HTTP to **llama-server** | `translation/base_translator.py : BaseTranslator.translate_batch` (:234) → `hymt_translator.HyMTEngine.translate_batch_sync` (:226) / `OpusCT2Engine.translate_batch_sync` (:141) / `cloud_translator.*Engine.translate_batch_sync` | list of texts, src, tgt | list of translations | **no engine for the pair → input returned unchanged (:247-249)**; engine exception → one retry on the next engine; no fallback → input returned unchanged (:272-273); HY-MT: HTTP error from llama-server, 20 s timeout, dead child restarted lazily, empty completion returned as is, multi-line output truncated to line 1; OPUS: model conversion needs torch+transformers, repeated words; cloud: 5 s timeout, non-200 → `raise_for_status`, missing JSON keys |
| a12 | TM store (`tm_store`) | backend (fire-and-forget asyncio task) | `pipeline._process_batch` step 4 (:193-197) → `TranslationMemory.set` (:182) | (text, src, tgt, translation, lines, quality 0.8) | upserted row | task exceptions are never awaited (only asyncio's "Task exception was never retrieved"); **failed translations (= source text) are stored as quality 0.8** |
| a13 | Refine (`refine`, optional) | backend (+ Ollama / Groq / Gemini over HTTP) | `main.py : translate_final` (:169) → `pipeline.refine_batch` (:213) → `refiner.py` | fast translations | `revision` message (revision 2) | deadline 0.8 s → INFO; exception → WARNING; unparseable LLM output → empty result, nothing logged (`refiner._parse` :154-161); in `main.py` any refine exception is logged at DEBUG only (:181-182) |
| a14 | WS reply (`ws_reply`) | backend | `main.py : ws_asr.send` (:123) | dict | JSON text frame | socket closed → exception inside a fire-and-forget task (never awaited) |
| a15 | Relay (`ext_relay`) | ext:offscreen → ext:sw → ext:content | `offscreen.js : emit` (:46) → `background.js` `ASR_EVENT` case (:314) → `chrome.tabs.sendMessage(frameId 0)` | server message | `ASR_CAPTION` message to the tab's top frame | both hops `.catch(() => {})`: a suspended worker, closed tab or missing content script drops captions silently |
| a16 | On-device translate (`ext_translate_ondevice`, optional Tier 0) | ext:offscreen | `offscreen.js : translateOnDevice` (:234) | final text | `translation` event with `engine:'chrome'` | Translator API missing / pack not downloaded → silently falls back; in `auto` mode the backend translation is also shown (duplicate) |
| a17 | Render (`ext_render`) | ext:content (top frame) | `content-scripts/main.js : handleLiveEvent` (:319) → `overlay.js : showPartial` (:129) / `showTranslation` (:103) | event | overlay DOM lines | unknown event types ignored; `error` events shown as a notice for 8 s |

## (b) Caption path (existing subtitles)

| # | Stage (log name) | Process | Owner (file : function) | Input | Output | Failure modes visible in code |
|---|---|---|---|---|---|---|
| b1 | DOM / TextTrack read (`ext_caption_detect`) | ext:content (any frame; Phase 2b: only on caption-mode sites the user granted, or a tab enabled through the popup's activeTab) | `subtitle-detector.js : SubtitleDetector._handleCueChange` (:255, TextTrack) / `_checkForSubtitleChanges` (:422, MutationObserver) → `onCue` callback; sub-frames forward with `window.top.postMessage(…, '*')` (`main.js:49`) | page DOM / `video.textTracks` | cue `{cueId, text, startTime, endTime}` | selector does not match → no cues and no signal; postMessage from any origin accepted (`main.js:74`) |
| b2 | Request (`ext_cue_request`) | ext:content (top frame) | `main.js : handleNewCue` (:219) → `websocket-client.js : translateCue` (:141) → `_sendRequest` (:285) → `_sendMessage` (:319) | cue + target langs | `cue` envelope over `/ws`, awaited by correlation id | not connected → source text shown untranslated, no request; offline queue (100) then drop with a console warning; 30 s request timeout; on close all pending requests rejected; reconnect gives up after 10 attempts |
| b3 | Connection (`ext_ws`, `ws_connection`) | ext:content ↔ backend | `websocket-client.js : connect` (:50); `websocket_handler.py : websocket_endpoint` (:450) / `handle_connection` (:257) | URL (+ (obs) `session_id` query) | socket; on open the content script sends a `config` message | **`config` overwrites the global default target languages** (`websocket_handler.py:358`); connection errors logged and loop ends |
| b4 | Backend receive (`ws_receive`) | backend | `websocket_handler.py : WebSocketHandler._process_message` (:275) → `_handle_cue` (:302) | JSON envelope | `PendingCue` | invalid JSON / validation / unknown type → `error` envelope |
| b5 | Micro-batch (`micro_batch`) | backend | `websocket_handler.py : MicroBatcher.add_cue` (:191) → `_process_after_delay` (:197) → `_process_batch` (:217) | cues arriving within 30 ms with the same target set (from any connection) | one `translate_batch` call | exception → `error` envelope to every cue in the batch |
| b6 | Translate / TM / MT / store | backend (+ llama-server) | same as a9–a12 | | | same as a9–a12 |
| b7 | Reply (`ws_reply`) | backend | `websocket_handler.py : deliver_results` (:133) → `ConnectionManager.send_message` (:96); refine pushes `revision` (:150-164) | `TranslationResult` | `result` envelope (+ later `revision`) | send error → logged, returns False, nothing else; refine task failure → WARNING |
| b8 | Render (`ext_render`) | ext:content (top frame) | `main.js : handleNewCue` → `overlay.showTranslation`; `handlePush` → `applyRevision` (:204) | result payload | overlay lines; client cache of 500 texts | request error → source text shown alone, console error |

Other HTTP callers (`/translate`, `/config`, `/stats`, options page `fetch`) are logged as stage `http`, with the
`X-Session-Id` header if the caller sends one. `/translate` runs a9–a12 directly.

## (c) Translator subprocess lifecycle (`translator_process`, layer `subprocess`)

| Step | Owner | What happens | Failure modes |
|---|---|---|---|
| Binary | `hymt_translator.py : ensure_llama_server` | uses `bin/llama/llama-server.exe` (Phase 2b: never downloaded at runtime; `scripts/download_models.py --hymt --accept-hymt-license`) | missing → `FileNotFoundError` naming the script; at startup `hymt_missing()` keeps HY-MT unregistered with one log line |
| Model | `HyMTEngine._ensure_model` | uses `settings.mt.hymt_gguf` (Phase 2b: never downloaded at runtime) | missing → same as Binary |
| Spawn | `HyMTEngine._start` (:145) | free port, PATH += torch CUDA DLL dir, `-ngl 99 -c 2048·np -np 2 --no-webui --log-disable`, stdout/stderr → DEVNULL, no console window | torch import failure silently skips the CUDA DLL dir (`_cuda_dll_dirs` :81); child output is discarded, so a crash leaves only an exit code |
| Health | `_start` poll loop (:163-175) | `GET /health` every 0.25 s until `{"status":"ok"}`, deadline 120 s | child exits → `RuntimeError("exited early with code N")`; deadline → `RuntimeError("did not become healthy in time")`; poll errors swallowed (:171) |
| Warmup | `HyMTEngine.warmup` (:190), called from lifespan via `base_translator.warmup_models` | spawn + translate "Hello" en→zh | any failure sets `_load_error`; `supports()` then returns False **for the rest of the process**, so every pair falls to OPUS or to pass-through |
| Restart | `translate_batch_sync` → `_start` (:227-228) | if `poll()` shows the child dead, a new child is spawned lazily on the next request (new port) | the request that hits a dead child before `_start` notices fails with a connection error and goes to the router fallback; no reason recorded (baseline) |
| Exit | `HyMTEngine.shutdown` (:178) via `atexit` | terminate, wait 5 s, kill | exceptions swallowed (:184-188); `Stop_Subtitle_Translator.bat` kills every `llama-server.exe` on the machine |

Logged events (obs): `start` with `action` = `download` / `spawn` / `restart` (+ `reason`), `success` with `action=ready`
(duration, port, pid, polls), `fail` with `action` = `early_exit` (exit code) / `health_timeout` / `warmup`, and
`exit` lines from a watcher thread that waits on the child: `success` when the backend stopped it, `fail` with the exit
code when it died on its own.

## (d) Startup (`startup`, `dependency`) and shutdown

| Step | Owner | Notes |
|---|---|---|
| Import | `run.py` → uvicorn imports `app.main` → `app.translation/__init__` imports `post_editor` → `import ollama` | `ollama` missing → `ModuleNotFoundError` propagates out of `server.run()` → `run.py:175` "Server error" exit 1 |
| Config | `config.py : _detect_cuda` (:14) | any error → CPU (HY-MT skipped) silently |
| MT warmup | `main.py : lifespan` → `pipeline.warmup_pipeline` → `base_translator.warmup_models` | failure logged, server still starts |
| ASR warmup | `lifespan` → `asr.warmup_asr` → `SherpaZipformerEngine.warmup` (:267) | per-language failure → WARNING; missing optional engines (`cloud_engine`, `whisper_engine`) → DEBUG (`asr/__init__.py:50-58`) |
