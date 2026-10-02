/**
 * WebSocket client for the backend's /ws (subtitle cues).
 *
 * Runs in the service worker (one instance per enabled tab, see
 * lib/tab-connections.js), so the handshake's Origin is the extension's
 * chrome-extension://<id>, which the backend allows; a socket opened from a
 * content script would carry the web page's origin and be refused (W8).
 * Content scripts talk to it through content-scripts/backend-port.js.
 *
 * Handles:
 * - Connection to local translation backend
 * - Message queue management
 * - Automatic reconnection
 * - Request/response correlation
 */

class SubtitleWebSocket {
  constructor() {
    this.ws = null;
    this.serverUrl = 'ws://127.0.0.1:8765/ws';
    this.sessionId = null; // obs session of the tab this socket serves (sent in the handshake)
    this.connected = false;
    this.connecting = false;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 10;
    this.reconnectDelay = 1000; // Start with 1 second
    this.maxReconnectDelay = 30000; // Max 30 seconds

    // Pending requests waiting for response
    this.pendingRequests = new Map();
    this.requestTimeout = 30000; // 30 second timeout

    // Message queue for offline buffering
    this.messageQueue = [];
    this.maxQueueSize = 100;

    // Callbacks
    this.onConnectedCallback = null;
    this.onDisconnectedCallback = null;
    this.onErrorCallback = null;
    this.onPushCallback = null; // server-initiated messages (e.g. 'revision')

    // Stats
    this.stats = {
      messagesSent: 0,
      messagesReceived: 0,
      requestsCompleted: 0,
      requestsFailed: 0,
      avgLatencyMs: 0,
      lastConnected: null,
    };
  }

  /**
   * Connect to the backend WebSocket server.
   */
  async connect(url = null) {
    if (this.connected || this.connecting) {
      return;
    }

    if (url) {
      this.serverUrl = url;
    }

    this.connecting = true;
    const obs = globalThis.STObs;
    const t0 = performance.now();
    let opened = false;

    return new Promise((resolve, reject) => {
      try {
        console.log('[WS] Connecting to', this.serverUrl);
        this.ws = new WebSocket(this._urlWithSession(this.serverUrl));

        this.ws.onopen = () => {
          console.log('[WS] Connected');
          opened = true;
          if (obs) obs.log('ext_ws', 'success', { duration_ms: performance.now() - t0, path: '/ws', attempt: this.reconnectAttempts, queued: this.messageQueue.length });
          this.connected = true;
          this.connecting = false;
          this.reconnectAttempts = 0;
          this.reconnectDelay = 1000;
          this.stats.lastConnected = new Date();

          // Send queued messages
          this._flushQueue();

          if (this.onConnectedCallback) {
            this.onConnectedCallback();
          }

          resolve();
        };

        this.ws.onclose = (event) => {
          console.log('[WS] Disconnected:', event.code, event.reason);
          if (obs && opened) {
            const normal = event.code === 1000 || event.code === 1001 || event.code === 1005;
            obs.log('ext_ws', normal ? 'success' : 'fail', {
              action: 'close', path: '/ws', close_code: event.code, pending_rejected: this.pendingRequests.size,
              error_type: normal ? null : 'process', error_message: normal ? null : 'socket closed with code ' + event.code,
            });
          }
          this.connected = false;
          this.connecting = false;

          if (this.onDisconnectedCallback) {
            this.onDisconnectedCallback(event);
          }

          // Reject pending requests
          for (const [id, pending] of this.pendingRequests) {
            pending.reject(new Error('Connection closed'));
          }
          this.pendingRequests.clear();

          // Auto-reconnect
          this._scheduleReconnect();
        };

        this.ws.onerror = (error) => {
          console.error('[WS] Error:', error);
          if (obs) obs.log('ext_ws', 'fail', { duration_ms: performance.now() - t0, path: '/ws', opened, attempt: this.reconnectAttempts, error_type: 'process', error_message: opened ? 'socket error' : 'cannot connect to ' + this.serverUrl });
          this.connecting = false;

          if (this.onErrorCallback) {
            this.onErrorCallback(error);
          }

          reject(error);
        };

        this.ws.onmessage = (event) => {
          this._handleMessage(event.data);
        };

      } catch (error) {
        console.error('[WS] Connection error:', error);
        if (obs) obs.log('ext_ws', 'fail', { path: '/ws', error_type: 'input_invalid', error_message: 'WebSocket constructor: ' + (error.message || error) });
        this.connecting = false;
        reject(error);
      }
    });
  }

  /** Append the tab's obs session id to the handshake (browsers cannot set WS headers). */
  _urlWithSession(url) {
    const sid = this.sessionId || (globalThis.STObs && globalThis.STObs.getSession());
    if (!sid) return url;
    return url + (url.includes('?') ? '&' : '?') + 'session_id=' + encodeURIComponent(sid);
  }

  /**
   * Disconnect from the server.
   */
  disconnect() {
    this.reconnectAttempts = this.maxReconnectAttempts; // Prevent auto-reconnect
    if (this.ws) {
      this.ws.close(1000, 'User disconnected');
      this.ws = null;
    }
    this.connected = false;
  }

  /**
   * Send a translation request for a single cue.
   */
  async translateCue(cue, options = {}) {
    const correlationId = this._generateId();

    const message = {
      type: 'cue',
      correlation_id: correlationId,
      payload: {
        cue: {
          cue_id: cue.cueId,
          text: cue.text,
          start_time: cue.startTime,
          end_time: cue.endTime,
        },
        target_languages: options.targetLanguages,
        skip_post_edit: options.skipPostEdit || false,
      }
    };

    return this._sendRequest(correlationId, message);
  }

  /**
   * Send a batch translation request.
   */
  async translateBatch(cues, options = {}) {
    const correlationId = this._generateId();

    const message = {
      type: 'batch',
      correlation_id: correlationId,
      payload: {
        cues: cues.map(cue => ({
          cue_id: cue.cueId,
          text: cue.text,
          start_time: cue.startTime,
          end_time: cue.endTime,
        })),
        target_languages: options.targetLanguages,
        skip_post_edit: options.skipPostEdit !== false, // Default true for batch
      }
    };

    return this._sendRequest(correlationId, message);
  }

  /**
   * Submit a user correction.
   */
  async submitCorrection(cueId, sourceText, originalTranslation, correctedTranslation, sourceLang = null) {
    const correlationId = this._generateId();

    const message = {
      type: 'correction',
      correlation_id: correlationId,
      payload: {
        cue_id: cueId,
        source_text: sourceText,
        source_lang: sourceLang,
        original_translation: originalTranslation,
        corrected_translation: correctedTranslation,
      }
    };

    return this._sendRequest(correlationId, message);
  }

  /**
   * Update server configuration.
   */
  async updateConfig(config) {
    const correlationId = this._generateId();

    const message = {
      type: 'config',
      correlation_id: correlationId,
      payload: config
    };

    return this._sendRequest(correlationId, message);
  }

  /**
   * Ping the server.
   */
  async ping() {
    const correlationId = this._generateId();

    const message = {
      type: 'ping',
      correlation_id: correlationId,
      payload: {}
    };

    return this._sendRequest(correlationId, message, 5000); // 5 second timeout for ping
  }

  /**
   * Set callback for connection established.
   */
  onConnected(callback) {
    this.onConnectedCallback = callback;
  }

  /**
   * Set callback for disconnection.
   */
  onDisconnected(callback) {
    this.onDisconnectedCallback = callback;
  }

  /**
   * Set callback for errors.
   */
  onError(callback) {
    this.onErrorCallback = callback;
  }

  /**
   * Set callback for server-push messages that are not replies to a request
   * (currently: {type:'revision'} carrying a refined translation).
   */
  onPush(callback) {
    this.onPushCallback = callback;
  }

  /**
   * Get connection status.
   */
  isConnected() {
    return this.connected;
  }

  /**
   * Get statistics.
   */
  getStats() {
    return { ...this.stats };
  }

  // ============ Private Methods ============

  /**
   * Send a request and wait for response.
   */
  _sendRequest(correlationId, message, timeout = null) {
    return new Promise((resolve, reject) => {
      const timeoutMs = timeout || this.requestTimeout;

      // Set up timeout
      const timer = setTimeout(() => {
        this.pendingRequests.delete(correlationId);
        this.stats.requestsFailed++;
        reject(new Error(`Request timeout after ${timeoutMs}ms`));
      }, timeoutMs);

      // Store pending request
      this.pendingRequests.set(correlationId, {
        resolve: (data) => {
          clearTimeout(timer);
          this.stats.requestsCompleted++;
          resolve(data);
        },
        reject: (error) => {
          clearTimeout(timer);
          this.stats.requestsFailed++;
          reject(error);
        },
        sentAt: Date.now(),
      });

      // Send message
      this._sendMessage(message);
    });
  }

  /**
   * Send a message through WebSocket.
   */
  _sendMessage(message) {
    const obs = globalThis.STObs;
    if (!this.connected) {
      // Queue message for later
      if (this.messageQueue.length < this.maxQueueSize) {
        this.messageQueue.push(message);
        console.log('[WS] Message queued (offline)');
        if (obs) obs.log('ext_ws_send', 'skip', { error_type: 'process', error_message: 'offline; message queued', msg_type: message.type, queue: this.messageQueue.length });
      } else {
        console.warn('[WS] Message queue full, dropping message');
        if (obs) obs.log('ext_ws_send', 'fail', { error_type: 'process', error_message: 'offline queue full (' + this.maxQueueSize + '); message dropped', msg_type: message.type });
      }
      return;
    }

    try {
      this.ws.send(JSON.stringify(message));
      this.stats.messagesSent++;
    } catch (error) {
      console.error('[WS] Send error:', error);
      if (obs) obs.log('ext_ws_send', 'fail', { error_type: 'process', error_message: error.message || String(error), msg_type: message.type, requeued: this.messageQueue.length < this.maxQueueSize });
      // Queue for retry
      if (this.messageQueue.length < this.maxQueueSize) {
        this.messageQueue.push(message);
      }
    }
  }

  /**
   * Handle incoming message.
   */
  _handleMessage(data) {
    this.stats.messagesReceived++;
    const parsed = globalThis.STWsProtocol.parseServerMessage(data, (cid) => this.pendingRequests.has(cid));

    if (parsed.kind === 'invalid') {
      console.error('[WS] Message parse error:', parsed.error);
      if (globalThis.STObs) globalThis.STObs.log('ext_ws_receive', 'fail', { error_type: 'parse', error_message: parsed.error, path: '/ws' });
      return;
    }
    if (parsed.kind === 'error' && parsed.correlationId == null) {
      // an error nobody is waiting for (e.g. a frame the server could not parse): surface it
      console.warn('[WS] Server error:', parsed.error);
      if (globalThis.STObs) globalThis.STObs.log('ext_ws_receive', 'fail', { error_type: parsed.errorType || 'unknown', error_message: 'server error: ' + parsed.error, path: '/ws' });
      if (this.onErrorCallback) {
        const err = new Error(parsed.error);
        err.errorType = parsed.errorType;
        this.onErrorCallback(err);
      }
      return;
    }
    if (parsed.kind === 'reply' || parsed.kind === 'error') {
      const pending = this.pendingRequests.get(parsed.correlationId);
      this.pendingRequests.delete(parsed.correlationId);
      const latency = Date.now() - pending.sentAt;
      this.stats.avgLatencyMs = (this.stats.avgLatencyMs * 0.9) + (latency * 0.1);
      if (parsed.kind === 'error') {
        const err = new Error(parsed.error);
        err.errorType = parsed.errorType;
        pending.reject(err);
      } else {
        pending.resolve(parsed.payload);
      }
      return;
    }
    if (this.onPushCallback) {
      this.onPushCallback(parsed.message);
    } else {
      console.log('[WS] Unsolicited message:', parsed.message.type);
    }
  }

  /**
   * Flush message queue after connection.
   */
  _flushQueue() {
    console.log('[WS] Flushing', this.messageQueue.length, 'queued messages');

    while (this.messageQueue.length > 0) {
      const message = this.messageQueue.shift();
      this._sendMessage(message);
    }
  }

  /**
   * Schedule reconnection attempt.
   */
  _scheduleReconnect() {
    const obs = globalThis.STObs;
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('[WS] Max reconnect attempts reached');
      // disconnect() also sets attempts to max; only a real give-up is a failure
      if (obs && this.ws) obs.log('ext_ws', 'fail', { action: 'give_up', path: '/ws', attempts: this.reconnectAttempts, error_type: 'process', error_message: 'max reconnect attempts reached; no further retries on this page' });
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1), this.maxReconnectDelay);

    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
    if (obs) obs.log('ext_ws', 'start', { action: 'reconnect', path: '/ws', attempt: this.reconnectAttempts, delay_ms: delay });

    setTimeout(() => {
      if (!this.connected && !this.connecting) {
        this.connect().catch(error => {
          console.error('[WS] Reconnect failed:', error);  // logged by onerror
        });
      }
    }, delay);
  }

  /**
   * Generate a unique correlation ID.
   */
  _generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2, 5);
  }
}

globalThis.SubtitleWebSocket = SubtitleWebSocket;
if (typeof module !== 'undefined' && module.exports) module.exports = { SubtitleWebSocket };
