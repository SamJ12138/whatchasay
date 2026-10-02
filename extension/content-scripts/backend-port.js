/**
 * backend-port.js: the content script's handle on the backend (/ws).
 *
 * Same API as the socket client it replaces (connect, translateCue,
 * translateBatch, submitCorrection, updateConfig, ping, onConnected,
 * onDisconnected, onError, onPush, isConnected, getStats), but the socket
 * itself lives in the service worker (lib/tab-connections.js): this side only
 * holds a chrome.runtime Port. Nothing is opened until connect() is called,
 * and main.js calls it only after the user enabled translation on this tab
 * (E17). If the worker refuses (tab not enabled) the port stays closed; if the
 * worker is restarted the port is reopened with backoff.
 */
(function (root) {
  const REQUEST_TIMEOUT_MS = 35000; // the worker's socket times out at 30 s

  class BackendPort {
    constructor(runtime) {
      this.runtime = runtime || (root.chrome && root.chrome.runtime);
      this.port = null;
      this.serverUrl = null; // kept for API compatibility; the worker reads settings.serverUrl
      this.connected = false;
      this.wanted = false;
      this.refused = null;
      this.nextId = 1;
      this.pending = new Map();
      this.retryMs = 1000;
      this.retryTimer = null;
      this.keepaliveTimer = null;
      this.onConnectedCallback = null;
      this.onDisconnectedCallback = null;
      this.onErrorCallback = null;
      this.onPushCallback = null;
      this.stats = { messagesSent: 0, messagesReceived: 0, requestsCompleted: 0, requestsFailed: 0, avgLatencyMs: 0 };
    }

    connect(url = null) {
      if (url) this.serverUrl = url;
      this.wanted = true;
      this.refused = null;
      this._open();
      return Promise.resolve();
    }

    disconnect() {
      this.wanted = false;
      clearTimeout(this.retryTimer);
      const port = this.port;
      if (port) {
        try { port.disconnect(); } catch (_) {}
        this._portGone(port);
      }
    }

    translateCue(cue, options = {}) { return this._request('translateCue', [cue, options]); }
    translateBatch(cues, options = {}) { return this._request('translateBatch', [cues, options]); }
    submitCorrection(...args) { return this._request('submitCorrection', args); }
    updateConfig(config) { return this._request('updateConfig', [config]); }
    ping() { return this._request('ping', [], 5000); }

    onConnected(cb) { this.onConnectedCallback = cb; }
    onDisconnected(cb) { this.onDisconnectedCallback = cb; }
    onError(cb) { this.onErrorCallback = cb; }
    onPush(cb) { this.onPushCallback = cb; }
    isConnected() { return this.connected; }
    getStats() { return { ...this.stats, refused: this.refused }; }

    _open() {
      if (this.port || !this.wanted || !this.runtime) return;
      let port;
      try {
        port = this.runtime.connect({ name: 'st-ws' });
      } catch (e) {
        this._scheduleReopen(); // extension reloaded / worker unavailable
        return;
      }
      this.port = port;
      port.onMessage.addListener((m) => this._onMessage(m));
      port.onDisconnect.addListener(() => {
        void (this.runtime && this.runtime.lastError);
        this._portGone(port);
      });
      const sid = root.STObs && root.STObs.getSession ? root.STObs.getSession() : null;
      port.postMessage({ op: 'connect', sessionId: sid });
      clearInterval(this.keepaliveTimer);
      this.keepaliveTimer = setInterval(() => this._post({ op: 'keepalive' }), 20000);
    }

    _post(msg) {
      if (!this.port) return false;
      try { this.port.postMessage(msg); this.stats.messagesSent++; return true; } catch (_) { return false; }
    }

    _portGone(port) {
      if (this.port !== port) return;
      this.port = null;
      clearInterval(this.keepaliveTimer);
      this._setConnected(false);
      for (const [, p] of this.pending) p.reject(new Error('Connection closed'));
      this.pending.clear();
      if (this.wanted && !this.refused) this._scheduleReopen();
    }

    _scheduleReopen() {
      clearTimeout(this.retryTimer);
      const delay = this.retryMs;
      this.retryMs = Math.min(this.retryMs * 2, 30000);
      this.retryTimer = setTimeout(() => this._open(), delay);
    }

    _setConnected(on) {
      if (this.connected === on) return;
      this.connected = on;
      if (on) {
        this.retryMs = 1000;
        if (this.onConnectedCallback) this.onConnectedCallback();
      } else if (this.onDisconnectedCallback) {
        this.onDisconnectedCallback();
      }
    }

    _onMessage(m) {
      if (!m || typeof m !== 'object') return;
      this.stats.messagesReceived++;
      switch (m.op) {
        case 'state':
          if (m.reason === 'disabled' || (m.reason && /top frame/.test(m.reason))) this.refused = m.reason;
          this._setConnected(!!m.connected);
          break;
        case 'reply': {
          const p = this.pending.get(m.id);
          if (!p) return;
          this.pending.delete(m.id);
          if (m.ok) {
            p.resolve(m.payload);
          } else {
            const err = new Error(m.error || 'Unknown error');
            err.errorType = m.errorType || null;
            p.reject(err);
          }
          break;
        }
        case 'push':
          if (this.onPushCallback) this.onPushCallback(m.message);
          break;
        case 'error':
          if (this.onErrorCallback) {
            const err = new Error(m.error || 'Unknown error');
            err.errorType = m.errorType || null;
            this.onErrorCallback(err);
          }
          break;
      }
    }

    _request(method, args, timeout = REQUEST_TIMEOUT_MS) {
      if (!this.port) return Promise.reject(new Error('Not connected'));
      const id = this.nextId++;
      const sentAt = Date.now();
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          this.pending.delete(id);
          this.stats.requestsFailed++;
          reject(new Error(`Request timeout after ${timeout}ms`));
        }, timeout);
        this.pending.set(id, {
          resolve: (v) => {
            clearTimeout(timer);
            this.stats.requestsCompleted++;
            this.stats.avgLatencyMs = this.stats.avgLatencyMs * 0.9 + (Date.now() - sentAt) * 0.1;
            resolve(v);
          },
          reject: (e) => { clearTimeout(timer); this.stats.requestsFailed++; reject(e); },
        });
        if (!this._post({ op: 'request', id, method, args })) {
          this.pending.get(id).reject(new Error('Not connected'));
          this.pending.delete(id);
        }
      });
    }
  }

  root.BackendPort = BackendPort;
  if (typeof module !== 'undefined' && module.exports) module.exports = { BackendPort };
  // never replace a port main.js already holds (the scripts can be injected twice: D2)
  if (root.chrome && root.chrome.runtime && typeof root.document !== 'undefined' && !root.subtitleWS) {
    root.subtitleWS = new BackendPort(root.chrome.runtime);
  }
})(typeof globalThis !== 'undefined' ? globalThis : self);
