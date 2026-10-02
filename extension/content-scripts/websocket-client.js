/**
 * WebSocket Client for Backend Communication
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

    return new Promise((resolve, reject) => {
      try {
        console.log('[WS] Connecting to', this.serverUrl);
        this.ws = new WebSocket(this.serverUrl);

        this.ws.onopen = () => {
          console.log('[WS] Connected');
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
        this.connecting = false;
        reject(error);
      }
    });
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
    if (!this.connected) {
      // Queue message for later
      if (this.messageQueue.length < this.maxQueueSize) {
        this.messageQueue.push(message);
        console.log('[WS] Message queued (offline)');
      } else {
        console.warn('[WS] Message queue full, dropping message');
      }
      return;
    }

    try {
      this.ws.send(JSON.stringify(message));
      this.stats.messagesSent++;
    } catch (error) {
      console.error('[WS] Send error:', error);
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

    try {
      const message = JSON.parse(data);
      const correlationId = message.correlation_id;

      // Find pending request
      if (correlationId && this.pendingRequests.has(correlationId)) {
        const pending = this.pendingRequests.get(correlationId);
        this.pendingRequests.delete(correlationId);

        // Update latency stats
        const latency = Date.now() - pending.sentAt;
        this.stats.avgLatencyMs = (this.stats.avgLatencyMs * 0.9) + (latency * 0.1);

        if (message.type === 'error') {
          pending.reject(new Error(message.payload?.error || 'Unknown error'));
        } else {
          pending.resolve(message.payload);
        }
      } else if (this.onPushCallback) {
        this.onPushCallback(message);
      } else {
        console.log('[WS] Unsolicited message:', message.type);
      }

    } catch (error) {
      console.error('[WS] Message parse error:', error);
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
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('[WS] Max reconnect attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1), this.maxReconnectDelay);

    console.log(`[WS] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

    setTimeout(() => {
      if (!this.connected && !this.connecting) {
        this.connect().catch(error => {
          console.error('[WS] Reconnect failed:', error);
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

// Export singleton instance
window.subtitleWS = new SubtitleWebSocket();
