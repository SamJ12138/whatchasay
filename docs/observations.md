# Observations (observability pass, 2026-10-01)

Scope: Phase 1 added logging only. The Status column tracks Phase 2 (`open` = not addressed yet; otherwise the
batch/commit that addressed the row). Every row says what the code does today, how it now shows up
in the run log (`backend/logs/run_<run_id>.jsonl`; stage names in `docs/pipeline-stages.md`), and what I would change in
Phase 2 (test first). File:line refers to the code after the observability commit.

Evidence runs: run 2 = `run_20261001T213426-fe0c84` (HTTP probes, `/ws` probe, three e2e WAVs, two llama-server kill
tests, a headless-Chromium caption-path run), `run_20261001T213642-e5b310` (start with `ollama` unimportable),
`run_20261001T213643-44d518` (`scripts/spike_hymt.py`). Run 1 (`run_20261001T212755-eba250`) was the same sequence
before one obs bug was fixed (see "Bugs I introduced"). Both runs used a scratch translation memory, not
`backend/data/translation_memory.db` (checksums unchanged).

---

## 1. Swallowed and silently degraded paths

`degraded` = the run log line carries `context.degraded`, which the failure report counts.

### Translation (backend)

| # | Where | What happens today | Now logged as | What I would change | Status |
|---|---|---|---|---|---|
| T1 | `translation/base_translator.py:262-268` | No engine supports the pair → **input text returned as the translation**, no error | `mt_call` skip `input_invalid`, degraded | Return an explicit "untranslated" result; never cache or store it; tell the UI | fixed, Batch 2: status `untranslated` (source only as placeholder), not stored/cached |
| T2 | `base_translator.py:300-304` | Engine raised and no other engine supports the pair → input returned | `mt_call` fail (the engine) + `mt_call` skip, degraded | Same as T1 | fixed, Batch 2: status `error` + error_type, no text, not stored |
| T3 | `base_translator.py:291-311` | Engine raised → silent retry on the next engine (HY-MT → OPUS). Caller never learns which engine answered; OPUS output is stored at the same quality 0.8 | `mt_call` fail + `mt_call` success with `fallback_for` | Put the engine name in the result; store fallback output with lower quality or not at all | fixed, Batch 2: status `fallback` + engine; stored at quality 0.6, replaced by the next primary result |
| T4 | `translation/pipeline.py:203-207` | Exception from the router → `outs = texts`: **source text used as the translation**, then cached and written to the TM | `translate` fail, degraded | Mark the item failed; skip cache and TM | fixed, Batch 2: router exception → `error` targets, nothing stored |
| T5 | `pipeline.py:113-117` | Whole-batch exception → error result whose translations are the source text, `notes.error` set; `/translate` still answers 200 | `translate` fail, degraded | Propagate as an error result the UI can show; don't fill translations with the source | fixed, Batch 2: every target `error` with no text; notes.error + error_type |
| T6 | `pipeline.py` step 4 (`tm_store` via `asyncio.create_task`) | Any of T1–T5 output is **persisted to the TM at quality 0.8**; TM is checked before MT, so the bad row is served forever (PROJECT_REPORT §10 #2) | `tm_store` success with `equals_source=true`, degraded | Refuse to store translation == source for src≠tgt, store only engine-confirmed output; add a TM cleanup | fixed, Batch 2: only ok/fallback stored; legacy rows equal to the source are not served |
| T7 | `pipeline.py` `tm_store` tasks | Fire-and-forget tasks: an SQLite error only shows as asyncio's "Task exception was never retrieved" | `tm_store` fail `process` (via `obs.traced`) | Await or attach a done-callback that logs; keep a bounded write queue | fixed, Batch 2: TM writes awaited before the reply; failures logged with error_type |
| T8 | `main.py` `TranslateResponse` (:463-467, :475) | `/translate` drops `notes`, so HTTP callers cannot see `notes.error` on a degraded result (the `sw→en` probe got its Swahili input back with 200) | the `translate`/`mt_call` lines | Return `notes` / an `untranslated` flag | fixed, Batch 2: `/translate` returns per-target status, notes, degraded |
| T9 | `pipeline.py:290`, `:295`; `main.py:275`; `websocket_handler.py:168` | Refiner timeout (INFO), failure (WARNING), and in `/ws/asr` any refine failure at **DEBUG** only | `refine` fail `timeout` / classified, degraded | One log level for all three; count refiner misses in `/metrics` | open |
| T10 | `translation/refiner.py:48-69` (`_parse`) | Unparseable LLM output → "no change", nothing logged | `refine` fail `parse`, degraded | Keep, but log and count | open |
| T11 | `translation/language_detection.py:123`, `:176` | `LangDetectException` → default language (DEBUG) | `lang_detect` fail `input_invalid`, degraded | Restrict detection to supported / likely languages; low confidence → ask the engine or keep the cue's declared language | fixed, Batch 2: candidate set + confidence floor + session hint (settings.lang_detect) |
| T12 | `language_detection.py` (no exception) | **Misdetection**: "It is raining again." → `tl` (Tagalog, 0.71). No engine supports tl→en / tl→bn → T1 → the English text is shown as the Bengali line and stored as `tl→en` and `tl→bn` rows | `lang_detect` success `lang=tl` + T1 + T6 lines | Same as T11; treat a detected language outside the configured source set as "unknown" and retry with script rules | fixed, Batch 2: short/low-confidence Latin text takes the session hint |

### Translator subprocess (llama-server)

| # | Where | What happens today | Now logged as | What I would change | Status |
|---|---|---|---|---|---|
| P1 | `translation/hymt_translator.py:209`, `:213` | `--log-disable` and stdout/stderr → DEVNULL: a crash leaves only an exit code | `translator_process` fail `early_exit` / `exit` with `exit_code` | Write the child's stderr to `logs/llama-server_<run_id>.log` | fixed, Batch 5: child stdout+stderr drained into a pipe; last 50 lines on the exit / early_exit / health_timeout fail line; --log-disable dropped |
| P2 | `hymt_translator.py:261` + `_start` | **Crash race (new):** a request that arrives after the child died but before Windows reaps it sees `poll() is None`, skips the restart, gets connection refused after ~2 s, falls back to OPUS (T3), and that output is stored in the TM. The restart happens one request later. Reproduced in both runs: 2.36 s / 2.39 s for the first request (OPUS), 1.43 s / 1.74 s for the second (restart). With 1 s between kill and request the first request restarts the child (1.25 s / 1.26 s) | `mt_call` fail `process` `WinError 10061` → `mt_call` success `fallback_for=hymt` → `translator_process` start `restart` | On a connection error, check `poll()` again (or wait briefly) and restart before falling back; add a health ping before reusing a child that has been idle | fixed, Batch 5: a request that finds the child dead/unreachable waits for it to exit (or stops it), restarts, waits for /health (bounded, hymt_restart_timeout_s) and retries once before any fallback |
| P3 | `hymt_translator.py:264-269` | Any warmup failure sets `_load_error`; `supports()` is False **for the life of the process**, so all traffic goes to OPUS or T1 | `translator_process` fail `warmup`, degraded | Retry with backoff; expose the state in `/health/json` (it is in `mt_engines.hymt.error`) and in the UI | fixed, Batch 5: warmup retried (3x, doubling pause); then paused with a doubling retry window (15 s → 300 s) instead of disabled for the process |
| P4 | `hymt_translator.py:88-90` (`_cuda_dll_dirs`) | `import torch` failure → CUDA DLL dir silently not added → the child may fail with only an exit code | `translator_process` skip `cuda_dlls` | Log the PATH used; fail early with a clear message | open |
| P5 | `hymt_translator.py:293-299` | Multi-line model output silently cut to the first line | `mt_call` skip `parse`, degraded | Keep, but count; consider joining lines for long inputs | open |
| P6 | `base_translator.py` `_run` (empty check) | Empty completion passed on as the translation (blank subtitle, stored) | `mt_call` fail `parse` | Treat empty as failure (T1 path without storing) | fixed, Batch 2: empty output = engine failure (fallback or `error`), never stored |
| P7 | `hymt_translator.py:246-253` (`shutdown`) | terminate/kill exceptions swallowed | `translator_process` fail `stop` | Keep, logged | open |
| P8 | `hymt_translator.py:176` (health poll) | Poll errors swallowed while the model loads | `last_poll_error` in the `ready` line | Fine as is | open |
| P9 | `hymt_translator.py` first spawn | **New:** the first spawn spends **2.46 s before `Popen`** (mostly `import torch` inside `_cuda_dll_dirs`); restarts spend ~0 ms. The INFO line "ready … (1.1–1.9 s)" measures only Popen→healthy | `translator_process` start → success `ready` timestamps | Resolve the torch lib dir without importing torch (`importlib.util.find_spec`) | fixed, Batch 5: torch lib dir found with importlib.util.find_spec (no import); spawn→Popen 1.2 s → 1-4 ms (pre_popen_ms on the ready line) |

### WebSockets and HTTP (backend)

| # | Where | What happens today | Now logged as | What I would change | Status |
|---|---|---|---|---|---|
| W1 | `main.py:333-337` (`/ws/asr` text) | Unknown message type is ignored with no reply | `ws_receive` skip `input_invalid` | Reply `{type:'error'}` like `/ws` does | open |
| W2 | `main.py:305-307` | Odd-length binary frame: last byte dropped by `pcm16_to_float32` | `asr_chunk` fail `input_invalid` | Reject the frame; this is also where the documented-but-missing 8-byte timestamp header would land | open |
| W3 | `main.py` `create_session` (now wrapped) | Unsupported language → exception escapes the endpoint after `accept()` | `ws_connection` fail | Validate query params, send `error`, close 4001 | open |
| W4 | `main.py` `_handle_events` | `send` / `translate_final` tasks are never awaited; a failed send is "never retrieved" | `ws_reply` fail `process` | Track tasks per connection; cancel on close | open |
| W5 | `websocket_handler.py:100-103`, `:115-119` | Send to a gone connection → `False`; send error → ERROR + `False`; every caller ignores the return value | `ws_reply` skip / fail `process`, degraded | Stop work for closed connections (drop pending batch items) | open |
| W6 | `websocket_handler.py:391`, `:447` | `correction` / `config` handler errors are returned as `{status:'error'}` **inside a `result` envelope**; the client (`websocket-client.js`) resolves it as success, so `main.js` updates its cache as if the correction were saved | `tm_store` fail (correction) / `config` fail | Send an `error` envelope | fixed, Batch 2: handler errors use the error envelope with error_type; client surfaces unsolicited errors |
| W7 | `websocket_handler.py:403` | Per-connection `config` overwrites the server-wide default target languages (known bug #6). Seen in both runs: the `/ws` probe changed it to `["en","bn"]`, then the headless-Chromium tab changed it to `["en","zh"]` | `config` success `scope=global` with `before` / `after` | Keep per-connection only | fixed, Batch 4: /ws config is per connection (targets, refiner, line limits, hint); server defaults untouched (cloud keys still global) |
| W8 | `main.py:131` | CORS reflects any origin with credentials (known bug #5) | `http` lines show the request but not the Origin | Allow only `chrome-extension://<id>` and the backend's own origin | fixed, Batch 3: Origin allow-list (extension id + own loopback) on HTTP and both WebSockets, 403 before upgrade; CORS never reflects; loopback bind enforced |
| W9 | `main.py:440-442` | Ollama probe in `/health/json`: `except … pass` | `health_probe` fail, degraded | Fine, logged | open |
| W10 | `main.py:50`, `:55` | MT / ASR warmup failure: server starts anyway | `startup` fail, degraded | Fine; surface in `/health/json` | open |

### ASR (backend)

| # | Where | What happens today | Now logged as | What I would change | Status |
|---|---|---|---|---|---|
| A1 | `asr/session.py:193` | LID exception → WARNING, treated as undecided | `lid` fail, degraded | Fine, logged | fixed, Batch 4: LID exceptions → lid status `error` (error_type), confirmed false |
| A2 | `asr/session.py:200-206` | After two undecided attempts the provisional language is kept **and reported with `confirmed: true`** | `lid` skip `input_invalid`, degraded | Send `confirmed: false, source: 'fallback'` | fixed, Batch 4: undecided → lid status `fallback`, source fallback, confirmed false; captions carry lang_status |
| A3 | `asr/lid.py` `identify` | LID answer outside the allowed set → None (INFO) | `lid` skip with `detected` | Fine, logged | fixed, Batch 4: outside the allowed set counts as undecided → `fallback`, never confirmed |
| A4 | `asr/session.py:100` | `RuntimeError` while picking a provisional language → next language tried | not logged (benign) | — | open |
| A5 | `asr/__init__.py:51`, `:60` | Advertised cloud ASR / Whisper engines do not exist; ImportError at DEBUG (known) | `startup` skip `process`, degraded | Remove the UI options or implement | open |
| A6 | `asr/sherpa_engine.py:275` | Per-language warmup failure → WARNING; that language loads on first session (seconds of delay) | `startup` fail `asr_model`, degraded | Fine, logged | open |

### Startup / environment

| # | Where | What happens today | Now logged as | What I would change | Status |
|---|---|---|---|---|---|
| S1 | `translation/__init__.py` → `post_editor.py:19` | `ollama` missing → `ModuleNotFoundError` → `run.py` "Server error", exit 1 (known bug, clean install) | `startup` fail `process`, `missing_module=ollama` | Remove the import of dead `post_editor`, or declare `ollama` | open |
| S2 | `config.py:24`, `:35` (`_detect_cuda`) | Any probe error → CPU; HY-MT silently skipped | `startup` skip `cuda_detect`, degraded | Log at WARNING with the reason | open |
| S3 | `translation/cuda_dlls.py:29`, `:49` | Only used by `spike_hymt.py`; errors swallowed | `startup` skip `cuda_dlls` | Delete with the spike | open |
| S4 | `scripts/spike_hymt.py:18` | `llama_cpp` not installed → script fails; the app does not need it | `dependency` skip `llama_cpp` at every backend start; the spike's own failure is a script-level traceback, not in the run log | Delete the spike or move it to `llama-server` | open |
| S5 | `translation/post_editor.py` | 7 `except` clauses in dead v1 code (never called in v2) | **not instrumented** on purpose | Delete the file | open |
| S6 | `cache/translation_memory.py:135-171` | Every TM hit runs UPDATE + COMMIT on the hot path; SQLite errors propagate into T5 | `tm_lookup` duration / fail `process` | Batch use-count updates off the hot path | open |

### Extension

| # | Where | What happens today | Now logged as | What I would change | Status |
|---|---|---|---|---|---|
| E1 | `offscreen.js:51` (`emit`) | `sendMessage(...).catch(() => {})`: captions lost if the worker cannot receive | `ext_relay` fail (first + every 100th) | Fine, logged | open |
| E2 | `offscreen.js:157` (`onPcmFrame`) | Socket not OPEN → audio frame dropped, no counter | `ext_ws_send` skip summary per 100 drops | Buffer a short window during reconnect | open |
| E3 | `offscreen.js:225-227` | Comment says "retry once"; it retries every 1.5 s forever, each retry a new backend session (LID reset) | `ext_ws` start `reconnect` per attempt | Backoff with a cap; reuse the session id (it already does now) | open |
| E4 | `offscreen.js:247` | Unparseable server message dropped | `ext_ws_receive` fail `parse` | Fine, logged | open |
| E5 | `offscreen.js:265`, `:270` | In `auto` mode, on-device and backend translations are both rendered (duplicate) | `ext_translate_ondevice` success `duplicate_with_backend=true` | Suppress the backend translation when the on-device one succeeded | open |
| E6 | `offscreen.js:293-309` | Translator API missing / pack not downloaded → silent fall back to backend | `ext_translate_ondevice` skip / fail | Fine, logged | open |
| E7 | `offscreen.js` `stopCapture` | Each teardown step's error swallowed | one `ext_capture` line `action=stop` listing errors | Fine, logged | open |
| E8 | `background.js:67` (`asrUrlFrom`) | Bad `serverUrl` → silently falls back to 127.0.0.1:8765 | not logged | Validate in Options | open |
| E9 | `background.js:268` (`getAsrStatus`) | Offscreen status query errors swallowed | not logged (polled by the popup; would be noise) | — | open |
| E10 | `background.js:341`, `:403`, `:409` | `tabs.sendMessage(...).catch(() => {})` for overlay toggle, settings broadcast, `PAGE_LOADED` | **not logged on purpose**: fails for every tab without the content script (chrome:// pages etc.) | Broadcast only to tabs that registered | open |
| E11 | `content-scripts/websocket-client.js:349` | Offline queue full (100) → message dropped (console.warn) | `ext_ws_send` fail `process` | Fine, logged | open |
| E12 | `websocket-client.js:107` | On close every pending request is rejected; the cue shows untranslated | `ext_ws` fail `close` with `pending_rejected`; `ext_cue_request` fail | Re-send idempotent cue requests after reconnect | open |
| E13 | `websocket-client.js:422` | Gives up after 10 reconnects for the life of the page | `ext_ws` fail `give_up` | Keep retrying slowly while the page has video | open |
| E14 | `content-scripts/main.js:253` | Not connected → source text shown untranslated, no request | `ext_cue_request` skip `process`, degraded | Show a notice | open |
| E15 | `main.js:283` | Request failure (timeout / closed) → source text shown | `ext_cue_request` fail, degraded | Same | open |
| E16 | `main.js:88` | `message` listener accepts cues from any frame or origin (known, PROJECT_REPORT §10 #15) | not logged | Check `e.origin` / `e.source` | fixed, Batch 3: window messages only from the page's own origin; sub-frames relay cues via the service worker |
| E17 | `main.js:78` | Connects to the backend on every top-level page (seen in the run: one `ws_connection` per page load) | `ext_ws` success per page | Connect only when a video is present | fixed, Batch 3: per-tab enable (popup); no detection or connection before; the /ws socket lives in the service worker |

---

## 2. New findings (not in PROJECT_REPORT)

1. **Crash race in llama-server restart** (P2): the first request after a crash can silently go to OPUS and be stored in
   the TM; the child restarts one request later. Reproduced twice; a 1 s delay avoids it.
2. **Language misdetection drops translations** (T12): "It is raining again." is detected as Tagalog; both targets
   come back as the English source with no error, and two bad TM rows are written. Only the caption and `/translate`
   paths detect language (ASR finals carry their language).
3. **First llama-server spawn costs 2.46 s before the process starts** (P9), because `import torch` runs inside
   `_cuda_dll_dirs`.
4. **First language detection costs ~200 ms** on the hot path (`lang_detect` p95 229 ms in run 2; later calls 0–2 ms),
   because langdetect loads its profiles lazily.
5. **Correction and config errors reach the client as success** (W6). Read-only finding, not exercised.
6. **`/translate` hides degraded results** (T8): `notes` is not part of the response model.
7. **A provisional language can be reported as confirmed** (A2).
8. Live en→bn translation took **1,044 ms** after the final in run 1 (831 ms in run 2); zh→bn 824 / 678 ms. Bengali
   output is still the one that crosses the 1 s target.

## 3. What PROJECT_REPORT.md got wrong (once instrumented)

| PROJECT_REPORT says | What the logs show |
|---|---|
| §8 #10, §10 #12: "llama-server crash recovery works (restarted on the next request)" | Only if the request arrives after the dead child is reaped. Otherwise the next request fails over to OPUS after ~2 s of connection-refused (and is stored in the TM), and the restart happens on the request after that (P2). Without logs a 1.6–2.4 s first request looks the same in both cases, so the intake's reading of its own run cannot be confirmed |
| §3 runtime flow step 2 and §8 #2: "start llama-server … translate 'Hello' (1.9 s)" / "HY-MT ready … in 1.9 s" | 1.9 s (1.1 s in run 2) is only Popen→healthy. Spawn including the pre-Popen torch import is 3.6–3.9 s, and the full HY-MT warmup (spawn + "Hello") 4.0 s (P9) |
| §8 #11: `/ws` batch of 2 "180 ms" listed as a working result | Judged by latency only. In the reconstructed probe one of the two batch cues came back untranslated with no error (T12) |
| §4.4 / §4.3: invalid JSON or unknown type → `error` envelope | True for `/ws`. On `/ws/asr` an unknown text message type is silently ignored (W1) |
| §8 "Chrome extension in a browser: Not run" | The caption path has now run in a real (headless) Chromium build, end to end, with records from content script, options page and backend joined by one session id. The audio path (tabCapture) is still not run: it needs a user gesture |

Everything else I could check against the logs held: startup 11.6–11.9 s, the three LID switches (en→bn 561 / 607 ms,
en→zh 128 / 149 ms), the ollama import failure, the global config leak, the music/SFX passthrough, 54 tests passing
with the server up.

## 4. Known bugs left as they are (Phase 2, test first)

- Failed / impossible translations stored as real translations (T1–T6, PROJECT_REPORT §10 #2).
- Open CORS with credentials, foreign-origin WebSocket accepted (W8, §10 #5).
- A tab's `config` changes the server-wide target languages (W7, §10 #6).
- Clean-install import failure: `ollama` imported but undeclared (S1, §10 #1); `llama_cpp` needed only by the broken
  spike (S4, §10 #4).

## 5. Bugs I introduced and fixed in this pass

- `ObsMiddleware` reset the session ContextVar before writing the `http` line, so HTTP lines had `session_id = null`
  even with `X-Session-Id` set (run 1). Fixed by writing the line before the reset (run 2 shows the ids).

## 6. Limits of the instrumentation

- Records from the extension are appended when `/obs` receives them, so a run log is not sorted by `ts`.
- Records buffered in a suspended service worker or a closed popup can be lost (fire-and-forget by design; the popup
  and options pages flush on `pagehide`).
- Per-frame stages (`ws_receive`, `asr_chunk`, `ext_ws_send`) have one line per 100 frames; their p50/p95 in the
  report are over 100-frame means.
- A micro-batch can mix cues from several tabs; its backend lines carry the first cue's session and list all
  sessions in `context.sessions`.
- Graceful shutdown (`shutdown`, `translator_process` stop) was not exercised: the backend was stopped with
  `taskkill /T /F`, which skips `atexit`.
- The audio path's extension stages (`ext_capture`, `ext_ws_send`, `ext_relay`, live `ext_render`) are instrumented but
  were not run in a browser.
  **Status (Phase 2, Batch 0):** run end to end without a human click by `backend/scripts/e2e_extension.py` (`--path audio`)
  (Playwright Chromium, `--allowlisted-extension-id` instead of the action-click grant, real tab audio from a `<video>`
  playing a sample WAV); slow test `tests/test_e2e_extension.py`.
- Subtitle text is never logged (lengths and ids only); API keys are redacted from error messages (`key=`, `Bearer`,
  `AIza…`, `gsk_…`, `sk-…`) because httpx puts the request URL, which carries the Google and Gemini keys, into its
  errors.

## 7. Phase 2 status by batch

| Batch | Commit | Rows addressed | Notes |
|---|---|---|---|
| 0 | chore: phase-1 diff audit, audio-path harness | §6 audio path not run | `docs/phase1-diff-audit.md`: one phase-1 behaviour change (lifespan aborted startup when the pipeline failed to build) reverted with a test |
| 1 | test: inventory, fakes, in-process app client, node tests for extension | none (scaffolding) | fast suite no longer needs the server or models; `docs/test-inventory.md` |
| 2 | fix: no failed translation is stored or reported as success (T1-T8, T12, W6) | T1-T8, T11, T12, W6, P6 | per-target status ok/fallback/untranslated/error on `/translate`, `/ws`, `/ws/asr`; TM `engine` column (additive migration) |
| 3 | fix: origin checks on HTTP, ws and postMessage; localhost bind (W8, E16, E17) | W8, E16, E17 | `/ws` socket moved from the content script to the service worker (a content-script socket carries the page origin); browser harness `--path security` (8 checks) |
| 4 | fix: per-session config; honest LID status (W7, A1-A3) | W7, A1, A2, A3 | `lang_status` on lid/partial/final: provisional, confirmed, manual, fallback, error; `confirmed:true` only for confirmed/manual |
| 5 | fix: translator process restart race, warmup retry, stderr capture, fast spawn | P1, P2, P3, P9 | real P9 (fresh process, warm disk): spawn→Popen 1187-1236 ms → 1.1-3.8 ms; Popen→ready unchanged ~1.1 s |
