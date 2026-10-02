/**
 * ws-protocol.js: pure parsing of server frames on /ws (no DOM, no chrome.*),
 * shared by the content-script client and node tests (extension/tests).
 *
 * parseServerMessage(data, isPending) classifies one text frame:
 *   {kind:'reply',   correlationId, payload, message}  answer to a request we are waiting for
 *   {kind:'error',   correlationId, error, errorType, message}  error envelope for a waiting request
 *   {kind:'push',    message}                           anything else (e.g. {type:'revision'})
 *   {kind:'invalid', error}                             not JSON / not an object
 * isPending(correlationId) -> bool tells whether a request is waiting for that id.
 */
(function (root) {
  function parseServerMessage(data, isPending) {
    let message;
    try {
      message = JSON.parse(data);
    } catch (e) {
      return { kind: 'invalid', error: e.message || String(e) };
    }
    if (!message || typeof message !== 'object' || Array.isArray(message)) {
      return { kind: 'invalid', error: 'server frame is not a JSON object' };
    }
    const cid = message.correlation_id;
    const waiting = cid != null && typeof isPending === 'function' && !!isPending(cid);
    if (waiting && message.type === 'error') {
      const p = message.payload || {};
      return { kind: 'error', correlationId: cid, error: p.error || 'Unknown error', errorType: p.error_type || null, message };
    }
    if (waiting) return { kind: 'reply', correlationId: cid, payload: message.payload, message };
    return { kind: 'push', message };
  }

  const api = { parseServerMessage };
  root.STWsProtocol = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
