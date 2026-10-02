/**
 * ws-protocol.js: pure parsing of server frames on /ws (no DOM, no chrome.*),
 * shared by the content-script client, the overlay and node tests
 * (extension/tests).
 *
 * parseServerMessage(data, isPending) classifies one text frame:
 *   {kind:'reply',   correlationId, payload, message}  answer to a request we are waiting for
 *   {kind:'error',   correlationId|null, error, errorType, message}
 *                    an error envelope (correlationId is null when no request waits for it),
 *                    or a legacy result envelope whose payload is {status:'error'}
 *   {kind:'push',    message}                           anything else (e.g. {type:'revision'})
 *   {kind:'invalid', error}                             not JSON / not an object
 * isPending(correlationId) -> bool tells whether a request is waiting for that id.
 *
 * Each target of a translation carries status ok | fallback | untranslated | error
 * (backend models.TranslatedLine). usableTranslation(t) says whether a target can
 * be shown as a translation; allTargetsOk(translations) whether a result may be
 * cached client-side (fallback/untranslated/error must be asked again later).
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
    const p = message.payload || {};
    if (message.type === 'error') {
      return { kind: 'error', correlationId: waiting ? cid : null, error: p.error || 'Unknown error', errorType: p.error_type || null, message };
    }
    if (waiting && message.type === 'result' && p.status === 'error') {
      // servers before W6 put handler errors inside a result envelope
      return { kind: 'error', correlationId: cid, error: p.error || 'Unknown error', errorType: p.error_type || null, message };
    }
    if (waiting) return { kind: 'reply', correlationId: cid, payload: message.payload, message };
    return { kind: 'push', message };
  }

  function usableTranslation(t) {
    if (!t || typeof t !== 'object') return false;
    const status = t.status || 'ok';
    if (status !== 'ok' && status !== 'fallback') return false;
    return !!(t.display_text || t.single_line);
  }

  function allTargetsOk(translations) {
    const values = Object.values(translations || {});
    return values.length > 0 && values.every(t => t && (t.status || 'ok') === 'ok');
  }

  const api = { parseServerMessage, usableTranslation, allTargetsOk };
  root.STWsProtocol = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
