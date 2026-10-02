/**
 * obs.js: structured log for the extension (observability only).
 *
 * Same record shape as backend/app/obs.py:
 *   {ts, run_id, session_id, layer:'extension', stage, event, duration_ms,
 *    error_type, error_message, context}
 * run_id here is this page/worker instance; the backend keeps it as
 * context.ext_instance and stamps its own run_id.
 *
 * Every record is written to console.debug with the prefix [ST-OBS] and
 * buffered. Every 2 s the buffer is shipped, fire-and-forget:
 *   - extension pages and the service worker POST it to <backend>/obs
 *   - content scripts (relay mode) hand it to the service worker with
 *     chrome.runtime.sendMessage({type:'OBS_BATCH'}), which posts it
 * Nothing here awaits the network or throws; a lost batch is acceptable.
 *
 * error_type: input_invalid | external_api | parse | timeout | process | unknown
 * In the extension, failures of or connections to the local backend are
 * 'process' (a local process), matching the backend's use for llama-server.
 *
 * Loaded as a classic script (content scripts, offscreen/popup/options pages)
 * and imported by the module service worker; it only sets globalThis.STObs.
 */
(function (root) {
  if (root.STObs) return;

  const PREFIX = '[ST-OBS]';
  const FLUSH_MS = 2000;
  const MAX_BUF = 500;
  const ERROR_TYPES = ['input_invalid', 'external_api', 'parse', 'timeout', 'process', 'unknown'];

  function hex(n) {
    try {
      const a = new Uint8Array(n);
      crypto.getRandomValues(a);
      return Array.from(a, b => b.toString(16).padStart(2, '0')).join('');
    } catch (_) {
      return Math.random().toString(16).slice(2, 2 + n * 2);
    }
  }

  const state = {
    context: 'unknown',
    sessionId: null,
    endpoint: 'http://127.0.0.1:8765/obs',
    relay: false,
    buf: [],
    dropped: 0,
    timer: null,
    instance: 'ext-' + hex(4),
  };

  function newSessionId(prefix) {
    return (prefix || 'ext') + '-' + hex(4);
  }

  function init(opts) {
    opts = opts || {};
    if (opts.context) state.context = opts.context;
    if (opts.sessionId) state.sessionId = opts.sessionId;
    if (opts.endpoint) state.endpoint = opts.endpoint;
    state.relay = !!opts.relay;
    try {
      if (typeof addEventListener === 'function') addEventListener('pagehide', flush);
    } catch (_) {}
  }

  function setSession(id) { state.sessionId = id || null; }
  function getSession() { return state.sessionId; }

  /** ws://host:port/whatever -> http://host:port/obs */
  function setEndpointFromWs(wsUrl) {
    try {
      const u = new URL(wsUrl);
      u.protocol = u.protocol === 'wss:' ? 'https:' : 'http:';
      u.pathname = '/obs';
      u.search = '';
      state.endpoint = u.toString();
    } catch (_) {}
  }

  function headers(extra) {
    const h = Object.assign({}, extra || {});
    if (state.sessionId) h['X-Session-Id'] = state.sessionId;
    return h;
  }

  /** log(stage, 'start'|'success'|'fail'|'skip', {duration_ms, error_type, error_message, session_id, ...context}) */
  function log(stage, event, fields) {
    try {
      const f = Object.assign({}, fields || {});
      const rec = {
        ts: new Date().toISOString(),
        run_id: state.instance,
        session_id: f.session_id || state.sessionId,
        layer: 'extension',
        stage,
        event,
        duration_ms: typeof f.duration_ms === 'number' ? Math.round(f.duration_ms * 10) / 10 : null,
        error_type: f.error_type == null ? null : (ERROR_TYPES.includes(f.error_type) ? f.error_type : 'unknown'),
        error_message: f.error_message == null ? null : String(f.error_message).slice(0, 500),
        context: null,
      };
      delete f.duration_ms; delete f.error_type; delete f.error_message; delete f.session_id;
      rec.context = Object.assign({ ctx: state.context }, f);
      console.debug(PREFIX, JSON.stringify(rec));
      push(rec);
    } catch (_) {}
  }

  function push(rec) {
    if (state.buf.length >= MAX_BUF) {
      state.buf.shift();
      state.dropped++;
    }
    state.buf.push(rec);
    if (!state.timer) {
      try { state.timer = setInterval(flush, FLUSH_MS); } catch (_) {}
    }
  }

  /** Service worker side of relay mode. */
  function ingest(events) {
    if (!Array.isArray(events)) return;
    for (const e of events.slice(0, MAX_BUF)) push(e);
  }

  function flush() {
    try {
      if (!state.buf.length && !state.dropped) return;
      const events = state.buf.splice(0, state.buf.length);
      if (state.dropped) {
        events.push({
          ts: new Date().toISOString(), run_id: state.instance, session_id: state.sessionId, layer: 'extension',
          stage: 'ext_obs', event: 'skip', duration_ms: null, error_type: 'unknown',
          error_message: state.dropped + ' record(s) dropped: buffer full', context: { ctx: state.context },
        });
        state.dropped = 0;
      }
      if (state.relay) {
        const p = chrome.runtime.sendMessage({ type: 'OBS_BATCH', events });
        if (p && p.catch) p.catch(() => {});
        return;
      }
      const body = JSON.stringify({ events });
      fetch(state.endpoint, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
        body,
        keepalive: body.length < 60000,
      }).catch(() => {});
    } catch (_) {}
  }

  root.STObs = { init, log, newSessionId, setSession, getSession, setEndpointFromWs, headers, flush, ingest };
})(typeof globalThis !== 'undefined' ? globalThis : self);
