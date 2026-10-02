/**
 * tab-connections.js: the service worker's per-tab backend connections (E17, W8).
 *
 * - A tab talks to the backend only after the user enabled translation on it
 *   (popup -> SET_TAB_ENABLED). Enabled tab ids are kept in
 *   chrome.storage.session so a restarted service worker remembers them.
 * - The tab's top-frame content script opens a Port named 'st-ws'
 *   (content-scripts/backend-port.js). For an enabled tab the worker creates one
 *   SubtitleWebSocket for it; the socket lives in the worker, so the /ws
 *   handshake carries the extension's origin, not the web page's.
 * - A port from a tab that is not enabled, or from a sub-frame, is told
 *   {op:'state', connected:false, reason} and closed; no socket is created.
 *
 * Port protocol
 *   content -> worker: {op:'connect', sessionId}
 *                      {op:'request', id, method, args}   method in REQUEST_METHODS
 *                      {op:'keepalive'}                   (keeps the worker awake)
 *   worker -> content: {op:'state', connected, reason?}
 *                      {op:'reply', id, ok, payload | error, errorType}
 *                      {op:'push', message}               server push (revision)
 *                      {op:'error', error, errorType}     unsolicited server/socket error
 *
 * Pure: chrome.* is injected (createSocket, getServerUrl, persist), so node tests
 * drive it with fakes.
 */
(function (root) {
  const REQUEST_METHODS = ['translateCue', 'translateBatch', 'submitCorrection', 'updateConfig', 'ping'];

  class TabConnections {
    constructor({ createSocket, getServerUrl, persist, log } = {}) {
      this.createSocket = createSocket;
      this.getServerUrl = getServerUrl || (async () => 'ws://127.0.0.1:8765/ws');
      this.persist = persist || (() => {});
      this.log = log || (() => {});
      this.enabled = new Set();
      this.conns = new Map(); // tabId -> {port, socket}
      this.ready = Promise.resolve();
    }

    /** Restore enabled tab ids (a promise of an array); ports wait for it. */
    restoreFrom(promise) {
      this.ready = Promise.resolve(promise).then((ids) => {
        for (const id of ids || []) this.enabled.add(id);
      }).catch(() => {});
      return this.ready;
    }

    isEnabled(tabId) {
      return this.enabled.has(tabId);
    }

    setEnabled(tabId, on) {
      if (on) {
        this.enabled.add(tabId);
      } else {
        this.enabled.delete(tabId);
        this.close(tabId, 'disabled');
      }
      this.persist([...this.enabled]);
      this.log(on ? 'enable' : 'disable', tabId);
    }

    forget(tabId) {
      if (this.enabled.delete(tabId)) this.persist([...this.enabled]);
      this.close(tabId, 'tab closed');
    }

    async attachPort(port) {
      // Listeners first, synchronously: a worker woken by this port (it was stopped
      // for idleness) gets the port's first message right after onConnect, while
      // the enabled tab ids are still being restored; a message with no listener is
      // lost. Early messages are held and replayed once the port is accepted.
      const early = [];
      let conn = null;
      let gone = false;
      port.onMessage.addListener((m) => { if (conn) this._onPortMessage(conn, m); else early.push(m); });
      port.onDisconnect.addListener(() => {
        gone = true;
        if (conn && this.conns.get(conn.tabId) === conn) this.close(conn.tabId, 'port closed');
      });
      await this.ready;
      if (gone) return;
      const sender = port.sender || {};
      const tabId = sender.tab && sender.tab.id;
      if (tabId == null || sender.frameId !== 0) {
        return this._refuse(port, 'only the top frame of a tab may connect');
      }
      if (!this.isEnabled(tabId)) {
        return this._refuse(port, 'disabled');
      }
      this.close(tabId, 'replaced');
      conn = { port, socket: null, tabId };
      this.conns.set(tabId, conn);
      for (const m of early.splice(0)) this._onPortMessage(conn, m);
    }

    _refuse(port, reason) {
      try { port.postMessage({ op: 'state', connected: false, reason }); } catch (_) {}
      try { port.disconnect(); } catch (_) {}
      this.log('refuse', port.sender && port.sender.tab && port.sender.tab.id, reason);
    }

    _post(conn, msg) {
      try { conn.port.postMessage(msg); } catch (_) {}
    }

    async _onPortMessage(conn, m) {
      if (!m || typeof m !== 'object') return;
      if (this.conns.get(conn.tabId) !== conn) return;
      if (m.op === 'connect') {
        if (conn.socket) {
          if (conn.socket.isConnected()) this._post(conn, { op: 'state', connected: true });
          return;
        }
        const s = this.createSocket();
        s.sessionId = m.sessionId || null;
        s.onConnected(() => this._post(conn, { op: 'state', connected: true }));
        s.onDisconnected(() => this._post(conn, { op: 'state', connected: false }));
        s.onPush((message) => this._post(conn, { op: 'push', message }));
        s.onError((err) => this._post(conn, {
          op: 'error',
          error: String((err && (err.message || err.type)) || 'socket error'),
          errorType: (err && err.errorType) || 'process',
        }));
        conn.socket = s;
        const url = await this.getServerUrl();
        s.connect(url).catch(() => {}); // the socket retries on its own; state messages tell the tab
        return;
      }
      if (m.op === 'request') {
        if (!REQUEST_METHODS.includes(m.method)) {
          return this._post(conn, { op: 'reply', id: m.id, ok: false, error: 'unknown method ' + m.method, errorType: 'input_invalid' });
        }
        if (!conn.socket) {
          return this._post(conn, { op: 'reply', id: m.id, ok: false, error: 'Not connected', errorType: 'process' });
        }
        try {
          const payload = await conn.socket[m.method](...(Array.isArray(m.args) ? m.args : []));
          this._post(conn, { op: 'reply', id: m.id, ok: true, payload });
        } catch (e) {
          this._post(conn, { op: 'reply', id: m.id, ok: false, error: (e && e.message) || String(e), errorType: (e && e.errorType) || null });
        }
      }
      // 'keepalive': receiving it is the point (resets the worker's idle timer)
    }

    close(tabId, reason) {
      const conn = this.conns.get(tabId);
      if (!conn) return;
      this.conns.delete(tabId);
      if (conn.socket) {
        try { conn.socket.disconnect(); } catch (_) {}
      }
      if (reason !== 'port closed') {
        try { conn.port.postMessage({ op: 'state', connected: false, reason }); } catch (_) {}
        try { conn.port.disconnect(); } catch (_) {}
      }
      this.log('close', tabId, reason);
    }
  }

  const api = { TabConnections, REQUEST_METHODS };
  root.STTabConnections = TabConnections;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
