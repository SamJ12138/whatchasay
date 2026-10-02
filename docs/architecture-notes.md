# Architecture notes

Mechanisms that are easy to break without noticing, with the test that holds each one.

## 1. Service-worker lifetime and the caption path's `/ws` socket

**Where the sockets live.** The caption path's `/ws` socket lives in the MV3 service worker (`lib/websocket-client.js`,
one per enabled tab, `lib/tab-connections.js`), because a socket opened by a content script carries the web page's
Origin and the backend refuses it (Phase 2a, W8). The audio path's `/ws/asr` socket lives in the offscreen document,
which is not subject to the worker's idle timeout; only its caption relay goes through the worker
(`chrome.runtime.sendMessage`, which wakes a stopped worker).

**Chrome's rules** (Chrome 110+): a service worker is stopped after 30 s without extension events. Every extension
event or API call resets that timer, including a message on a `runtime.Port`; since Chrome 116 sending or receiving
on a WebSocket also does. A worker with DevTools attached is never stopped for idleness. That last rule is why the
Playwright harness cannot test this: Playwright attaches to every service worker (measured: the same worker instance
after 45 s of silence).

**Mechanism: keepalive, with reconnect as the backstop.**

1. *Keepalive.* While a tab has translation enabled, its content script (`content-scripts/backend-port.js`) holds a
   Port named `st-ws` to the worker and posts `{op:'keepalive'}` on it every 20 s. Each message is an extension
   event, so a quiet caption session (no cues for minutes) keeps its worker and its socket. Tabs without translation
   enabled hold no port and send nothing.
2. *Reconnect.* The worker can still be stopped: timers in hidden tabs are throttled (to once a minute after a few
   minutes in the background), Chrome restarts workers on update, a worker can crash. Then the Port disconnects, and
   the content script reopens it after 1 s (doubling to 30 s while it fails). `runtime.connect` wakes the worker,
   which restores the enabled tab ids from `chrome.storage.session`, accepts the port, opens a new `/ws` socket and
   reports `connected`; the content script then re-sends its `config` (target languages, source hint, line limits),
   since `/ws` config is per connection. A refused port (tab disabled, sub-frame) is not reopened.
3. *The wake-up race.* A woken worker receives the port's first message (`{op:'connect'}`) right after `onConnect`,
   while it is still restoring the enabled tab ids, and a Port message that arrives with no listener is lost (the tab
   would then never get a socket). `TabConnections.attachPort` registers its listeners synchronously and replays
   messages that arrive before the port is accepted (Phase 2b Batch 0; `extension/tests/sw-lifetime.test.js`).

Cost: one resident worker while at least one tab has caption translation enabled. Cues that arrive during a
reconnect (about 1-2 s) are shown untranslated (E14); the backend sees a new `/ws` connection.

**Test.** `backend/scripts/e2e_extension.py --path sw-idle` (slow test
`test_caption_session_survives_service_worker_idle_timeout`) drives Chromium over raw CDP with nothing attached to the
worker; worker stops and starts are read from `Target.setDiscoverTargets` events, which do not attach. One tab, three
phases:

| Phase | What happens | Measured (2026-10-02) |
|---|---|---|
| A | 40 s with no cues, keepalive running | worker not stopped; the next cue is translated |
| B | keepalive timer cleared in the content script's own world, then silence | Chrome stops the worker after 30.3 s (the real idle timeout); the next cue is translated by a restarted worker over a new socket |
| C | worker stopped from DevTools (`ServiceWorker.stopAllWorkers`) | the next cue is translated |

Phase B also proves the test is not vacuous: without DevTools on the worker, Chrome does stop it. A copy of the
extension whose content script never reopens its port fails phase B (mutation check, Batch 0 commit).

## 2. Translation memory schema migrations

The TM (`backend/app/cache/translation_memory.py`, SQLite) keeps its schema version in `PRAGMA user_version`
(`SCHEMA_VERSION`). Databases from before versioning are recognised by their columns (no `engine` column = 1,
`engine` column = 2). Before any migration the database is copied to `<name>.bak-<old version>` next to it with
SQLite's online backup API (rows still in the `-wal` file included), and the copy is logged (`tm_migration`
start / success with `from_version`, `to_version`, `backup`). An existing backup is never overwritten (the new one
gets a timestamp suffix). If the backup fails, the migration does not run and the TM does not start. A new database
is created at the current version without a backup. Tests: `backend/tests/test_tm_migration.py` (scratch databases
only).

## 3. Site access: what the extension may read, and when (D2)

A fresh install has no host permissions, no content scripts and no install-time warning
(`chrome.management.getPermissionWarningsByManifest` returns `[]`; the old manifest showed "Read and change all your
data on all websites"). Default permissions: `storage`, `activeTab`, `offscreen`, `scripting`.

- **Live captions (audio path).** `tabCapture` is an *optional* permission: Chrome words it as "Read and change all
  your data on all websites" (measured: removing it alone empties the warning list), so the popup asks for it inside
  the first click on *Start Live Captions* (`chrome.permissions.request` before the first `await`, while the click's
  user activation lasts); later clicks resolve without a prompt. Alt+L before that opens the popup. The click on the
  action grants `activeTab` for that tab, and the service worker injects the overlay scripts into its top frame with
  `chrome.scripting.executeScript` (`ensureContentScript`).
- **Caption mode (page subtitles).** Off by default (`settings.captionMode`). Switching it on in Options requests the
  14 caption sites (`lib/caption-mode.js` `CAPTION_ORIGINS`, one per site adapter of the subtitle detector) as
  optional host permissions; it is saved as on only if Chrome grants them. The worker then registers the content
  scripts for the granted origins (`chrome.scripting.registerContentScripts`, all frames, `document_idle`,
  persistent) and keeps the registration in step with the setting and with `permissions.onAdded/onRemoved`.
  Switching it off unregisters them and gives the permissions back. A tab is still read only after the user enables
  it in the popup (E17); that enable also works on a site outside the 14 through the popup's `activeTab` grant
  (top frame only, until the tab navigates).
- **No double injection.** A page on a granted site gets the registered scripts at `document_idle`; enabling the tab
  before that would inject them a second time (two backend ports; seen as a flaky sw-idle run).
  `ensureInjected` waits for a loading page's own scripts and injects only into a page that finished loading
  without them (a tab opened before caption mode was switched on); `backend-port.js` never replaces a port it
  already created (`extension/tests/injection.test.js`).

Tests: `extension/tests/manifest.test.js` (permissions, optional sites cover every site adapter, defaults off,
explanation), `e2e_extension.py --path permissions` on the real extension in a fresh profile (no origins, no warnings,
no content scripts; the explanation; the toggle requests exactly the 14 sites and stays off when Chrome does not grant
them; Start requests `tabCapture`). The other browser paths load a scratch copy of the extension whose manifest adds
`http://127.0.0.1/*` and `tabCapture` (automation cannot click the action or answer a permission prompt); the audio
path runs with caption mode off.

## 4. What is remembered, and where (D3)

| Data | Where | How long |
|---|---|---|
| Settings, enabled tabs | Chrome (`storage.local`, `storage.session`) | until changed / the browser session ends |
| Live-caption (audio) text and translations | an in-memory cache owned by the `/ws/asr` connection (`pipeline.audio_session_policy`) | until the session ends; never in the process-wide cache, the refiner context or the TM |
| Page-subtitle (caption mode) translations | translation memory (`backend/data/translation_memory.db`) + process-wide memory cache | TM rows deleted at startup when unused for `tm.retention_days` (30) |
| User corrections | translation memory (`corrections` table + user-corrected rows) | until cleared |

Config (`SUBTITLE_TM__*`): `persist_audio_sessions` (false), `persist_captions` (true; caption cues only reach the
backend when the user turned caption mode on), `retention_days` (30; 0 keeps everything). An audio session still
*reads* the TM (a user correction applies to live captions too) but does not count the use (`get(touch=False)`), so
its reads leave no trace either. `POST /tm/clear` deletes translations, corrections and glossary, then `VACUUM` +
`wal_checkpoint(TRUNCATE)`, so the deleted text is not left in free pages or the WAL; it also empties the in-memory
cache and the refiner context. The options page shows the policy as one sentence (from `/health/json` `privacy.tm`)
and has a *Clear translation memory* button behind a confirmation. Tests: `backend/tests/test_memory_privacy.py`,
browser paths `audio` (TM row count unchanged) and `clear-memory`.

## 5. Cloud providers and keys (D4)

Off by default: `settings.cloud.enabled` is the switch for every cloud provider (translation and the Groq / Gemini
refiners). Keys are read only from the backend's config: environment variables or `backend/.env`
(`SUBTITLE_ENV_FILE` overrides the path; the tests point it at a file that does not exist). Nothing over ws or HTTP
can set a key any more: a `cloud_keys` field on `/ws` or `/ws/asr` config or `POST /config` is ignored and logged
(`config` skip, `ignored: cloud_keys`, field names only). Keys travel in request headers (Google `X-Goog-Api-Key`,
Gemini `x-goog-api-key`, Azure `Ocp-Apim-Subscription-Key`, Groq `Authorization`), never in a URL, because httpx
copies the URL into its error messages and those reached logs and the client's error text. Cloud MT failures are
`CloudMTError("<provider>: HTTP <status>" | "<provider>: <exception class>")`: no URL, no body, no key.
`cloud_translator.cloud_receivers()` lists who will receive subtitle text; the backend logs it once at startup
(`startup` / `component: cloud`) and `/health/json` `privacy.cloud` carries it to the options page. The extension:
settings schema 4 deletes `cloudKeys` (`lib/settings-migration.js`) and leaves a notice that says where keys go now;
no key fields remain. Tests: `backend/tests/test_cloud.py` (mocked httpx transport), `extension/tests/cloud-keys.test.js`,
browser path `cloud-keys` (cloud off and on).
