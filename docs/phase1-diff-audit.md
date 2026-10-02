# Phase-1 diff audit (`git diff fffcdda f9758f0`)

Scope: every hunk of the observability commit that is **not** one of: an `obs` import, an `obs.log` / `obs.span` /
`obs.traced` / `STObs.log` call, session-id plumbing, or the `/obs` endpoint (+ its extension relay). Files that are new
and purely observability (`backend/app/obs.py`, `extension/obs.js`, `backend/scripts/failure_report.py`, the three docs)
are out of scope. 25 source files were read hunk by hunk.

Verdict column: **same** = no behaviour difference on any path; **changed** = observable behaviour differs.

## Backend

| File / hunk | What it changed | Verdict |
|---|---|---|
| `main.py` lifespan, final `startup success` line | Evaluated `(await get_pipeline()).base_translator.engines` **after** the warmup `try/except`. If the pipeline failed to build (the exception the baseline logs and survives), this second `get_pipeline()` retried the init and raised out of `lifespan`: **the server no longer started** where the baseline did | **changed (failure path) → reverted** in Batch 0: the line now reads the already-built pipeline (`pipeline._pipeline`) and logs `mt_engines=None` otherwise. Test: `tests/test_startup.py` |
| `main.py` `_log_optional_deps()` | `importlib.util.find_spec` for six modules at startup; logs only | same (find_spec on top-level names does not import) |
| `main.py` `ObsMiddleware` (pure ASGI, added after CORS so it is outermost) | Times every HTTP request, sets the session ContextVar from `X-Session-Id`, re-raises exceptions | same (no response is altered; exceptions propagate unchanged) |
| `main.py` `ws_asr`: `create_session` wrapped in `try/except … raise` | log then re-raise | same |
| `main.py` `ws_asr`: `send()` wrapped in `try/except … raise` | log then re-raise | same |
| `main.py` `ws_asr`: empty / odd-length frame checks | log only; the frame still goes to `feed()` | same |
| `main.py` `ws_asr`: `run_in_executor(None, obs.run_in_context(session.feed, …))` | copies the ContextVar context into the pool thread | same (session-id plumbing) |
| `main.py` `ws_asr`: `close_code` bookkeeping, `Summary` flushes in `finally` | log only | same |
| `websocket_handler.py` `ConnectionManager.connect(…, session_id)` | stores the session id in `connection_info` | same (plumbing) |
| `websocket_handler.py` `send_message`: `info` lookup moved above `try` | none (dict lookup cannot raise) | same |
| `websocket_handler.py` `MicroBatcher._process_batch`: `sessions` / `ctx` computed before `try`, ContextVar set/reset in `finally` | if `ctx` raised, the error envelopes would not be sent; it cannot raise (dict lookups, `max` over a non-empty batch, guarded `if batch`) | same |
| `websocket_handler.py` `_handle_config`: `before = list(…)` | copy for the log | same |
| `translation/pipeline.py` `source_lang = cue.source_lang or detect(...)[0]` → `if/else` + span | identical truthiness; `conf` only read for the log | same |
| `translation/pipeline.py` `tm_lookup` span: `tm["translation"].strip()` | inside the span; the column is `NOT NULL`, so it cannot raise | same |
| `translation/pipeline.py` `asyncio.create_task(obs.traced(...))` | wraps the TM write in a span that logs and **re-raises**, so the task still fails exactly as before ("never retrieved") | same |
| `translation/pipeline.py` `tally` dict, `_process_batch(…, tally=None)` | counters for the log | same |
| `translation/base_translator.py` `_run`: `empty` / `same` counters, `sp.fail("parse", …)` | log only; output passed on unchanged | same |
| `translation/base_translator.py` fallback `lambda` → named `_fallback()` in a span | same call, same arguments | same |
| `translation/hymt_translator.py` `_start` split into `_start` (spawn bookkeeping + log) and `_start_child` (unchanged body) | `_spawns` counter, `_stopping` set | same |
| `translation/hymt_translator.py` watcher thread `_watch` (`proc.wait()` in a daemon thread) | A second thread now waits on the child. Windows: `wait()` and `poll()` both use `WaitForSingleObject`, concurrent use is safe and `returncode` is set the moment the child exits. POSIX: the blocked `wait()` holds `_waitpid_lock`, so `poll()` returns `None` until the child exits, then sees the code — the same answer it would give. No restart decision changes | same; **kept: required** — it is the only way to log the exit code of a child that dies on its own (P1/P2 evidence) |
| `translation/hymt_translator.py` `ensure_llama_server` body in `try/except … raise` | log then re-raise | same |
| `translation/hymt_translator.py` `shutdown`: `_stopping.add(pid)`, logs | log only | same |
| `translation/hymt_translator.py` `_one`: `dropped = result.count("\n")` | count for the log | same |
| `config.py` `_detect_cuda`: `return bool(...)` → `found = …; return found`; `from . import obs` inside the function | same value; `obs` imports nothing from `app` (no cycle) | same |
| `asr/session.py` `buffered_s = …` | value for the log | same |
| `asr/sherpa_engine.py` `warmup`: new `else:` branch | log only | same |
| `translation/cuda_dlls.py`, `language_detection.py`, `refiner.py`, `asr/__init__.py`, `asr/lid.py` | log calls inside existing `except` / `if` branches | same |
| `run.py` | log calls; `obs.log_path()` only formats a path | same |
| `tests/conftest.py` `SUBTITLE_OBS_DIR` default | test runs log to `logs/pytest/` | same (test harness only) |

## Extension

| File / hunk | What it changed | Verdict |
|---|---|---|
| `manifest.json` `obs.js` added before the content scripts; `offscreen.html`, `popup.html`, `options.html` load `obs.js` | a new script in each context | same; **required** to log at all |
| `background.js` `import './obs.js'` | the worker is already `"type": "module"` | same |
| `background.js` `startAsr` body moved into `try/catch(e){log; throw e}` with a `step` variable | same awaits in the same order (`getSettings` → `stopAsr` → `getMediaStreamId` → `ensureOffscreen` → `ASR_START`); the error is re-thrown | same |
| `background.js` `startAsr` / `stopAsr`: `sessionId` in `ASR_START`, `asrState`, `ASR_STATE` | plumbing | same |
| `background.js` `ASR_EVENT` with no tab id: now `logRelayFail(...)` | previously nothing happened; still nothing is sent | same |
| `background.js` `OBS_BATCH` message case | content-script relay for `/obs` | same (part of the `/obs` path) |
| `background.js` `.catch(() => {})` → `.catch(e => log)` (6 places) | still swallowed | same |
| `content-scripts/main.js` `handleNewCue`: `ext_caption_detect` log calls `detector.getMethod()` **before** the translation request | would throw if `getMethod` were missing; it exists (`subtitle-detector.js:207`) and is already called at `main.js:444` | same |
| `content-scripts/main.js` fallback `obs` stub when `window.STObs` is missing | no-op object | same |
| `content-scripts/websocket-client.js` `new WebSocket(this._urlWithSession(url))` | adds `?session_id=` to the handshake URL | same (plumbing; the backend ignores unknown query params) |
| `content-scripts/websocket-client.js` `opened` flag, `_scheduleReconnect` `if (obs && this.ws)` | gates log lines only | same |
| `offscreen.js` `startCapture` split into `startCapture` (session, logs) + `startCaptureSteps(msg, setStep)` (old body) | same steps, same order; error re-thrown | same |
| `offscreen.js` `connectWs`: `session_id` query param; `settled` flag | plumbing; `settled` only gates log lines, `reject` paths unchanged | same |
| `offscreen.js` `stopCapture`: `catch (_) {}` → `catch (e) { errs.push(...) }`, `obs.flush()` | still swallowed | same |
| `offscreen.js` / `popup.js` / `options.js` top-level `STObs.init(...)` | would throw if `obs.js` failed to load; it is loaded by the same HTML | same |
| `options.js` `fetch(..., { headers: obs.headers() })` on `/health/json`, `/stats`, `/cache/clear` | adds an `X-Session-Id` request header. A custom header makes the request non-simple, but extension pages with host permissions (`<all_urls>`) are not subject to CORS, so no preflight is involved | same. **Note for Batch 3**: once CORS is restricted, `X-Session-Id` and `Content-Type` must stay in the allowed headers for the `127.0.0.1` debug page |

## Result

- One behaviour change found (`main.py` lifespan, failure path only). Reverted in the Batch 0 commit with
  `tests/test_startup.py` (RED: `RuntimeError: pipeline init failed` raised out of `lifespan` at `main.py:60`).
- One structural change kept because logging needs it: the llama-server watcher thread (exit codes of a child that
  dies on its own). It does not change any restart decision.
- Everything else is log-only or session-id plumbing.
