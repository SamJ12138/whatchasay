/**
 * corrections.js: user corrections (Alt+E) from a tab that has no caption-path socket,
 * i.e. a tab running only live captions. The content script cannot call the backend
 * itself (a page-origin request is refused), so it hands the correction to the service
 * worker, which posts it here to the backend's POST /corrections with the extension's
 * origin. Tabs with caption translation on send corrections over their /ws socket.
 *
 * Pure: loaded by the service worker and node tests.
 */
(function (root) {
  const DEFAULT_BASE = 'http://127.0.0.1:8765';

  function httpBaseFrom(serverUrl) {
    try {
      const u = new URL(serverUrl);
      if (u.protocol !== 'ws:' && u.protocol !== 'wss:' && u.protocol !== 'http:' && u.protocol !== 'https:') return DEFAULT_BASE;
      const scheme = u.protocol === 'wss:' || u.protocol === 'https:' ? 'https:' : 'http:';
      return `${scheme}//${u.host}`;
    } catch (_) {
      return DEFAULT_BASE;
    }
  }

  function payloadOf(c) {
    return {
      cue_id: c.cueId,
      source_text: c.sourceText,
      source_lang: c.sourceLang || null,
      original_translation: c.originalTranslation || {},
      corrected_translation: c.correctedTranslation || {},
    };
  }

  /** POST the correction; resolves {ok:true} or {ok:false, error}, never throws. */
  async function submit(correction, serverUrl, fetchFn) {
    const doFetch = fetchFn || root.fetch;
    try {
      const res = await doFetch(httpBaseFrom(serverUrl) + '/corrections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payloadOf(correction)),
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).error || ''; } catch (_) { /* not JSON */ }
        return { ok: false, error: `HTTP ${res.status}${detail ? ': ' + detail : ''}` };
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, error: (e && e.message) || String(e) };
    }
  }

  const api = { httpBaseFrom, payloadOf, submit };
  root.STCorrections = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
