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
