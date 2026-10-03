/**
 * glossary.js: the term glossary's client side (observations T13, T14).
 *
 * A term has a canonical spelling in the source language, zero or more "heard as"
 * spellings and a rendering per target language; the backend keeps them and applies them
 * to recognised text and through translation. Two ways in: the Options page table (rows
 * from terms, a term from the form) and Alt+E with a word selected in the recognised line
 * (termFromSelection). The backend calls (list / save / remove) run in the service worker,
 * which has the extension's origin; the content script hands its term to the worker.
 *
 * Pure: loaded by the service worker, the Options page, the content script and node tests.
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

  async function call(path, init, serverUrl, fetchFn) {
    const doFetch = fetchFn || root.fetch;
    try {
      const res = await doFetch(httpBaseFrom(serverUrl) + path, init);
      let data = null;
      try { data = await res.json(); } catch (_) { /* not JSON */ }
      if (!res.ok) {
        const detail = (data && (data.error || data.detail)) || '';
        return { ok: false, error: `HTTP ${res.status}${detail ? ': ' + (typeof detail === 'string' ? detail : JSON.stringify(detail)) : ''}` };
      }
      return { ok: true, ...(data || {}) };
    } catch (e) {
      return { ok: false, error: (e && e.message) || String(e) };
    }
  }

  const pick = (r, key) => (r.ok ? { ok: true, [key]: r[key] } : { ok: false, error: r.error });

  /** GET /glossary -> {ok, terms} | {ok: false, error} (never throws) */
  async function list(serverUrl, fetchFn) {
    return pick(await call('/glossary', { method: 'GET' }, serverUrl, fetchFn), 'terms');
  }

  /** POST /glossary -> {ok, term} | {ok: false, error} */
  async function save(term, serverUrl, fetchFn) {
    return pick(await call('/glossary', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(term) }, serverUrl, fetchFn), 'term');
  }

  /** DELETE /glossary/<id> -> {ok} | {ok: false, error} */
  async function remove(id, serverUrl, fetchFn) {
    const r = await call('/glossary/' + encodeURIComponent(id), { method: 'DELETE' }, serverUrl, fetchFn);
    return r.ok ? { ok: true } : { ok: false, error: r.error };
  }

  // ---- the Options table ----

  /** One row per term: the heard-as spellings joined, a rendering (or '') per language. */
  function rowsOf(terms, langs) {
    return (terms || []).map((t) => ({
      id: t.id,
      sourceLang: t.source_lang,
      canonical: t.canonical,
      heardAs: (t.heard_as || []).join(', '),
      renderings: Object.fromEntries((langs || []).map((l) => [l, (t.renderings || {})[l] || ''])),
    }));
  }

  function splitList(text) {
    return String(text || '').split(/[,\n;]+/).map((s) => s.trim()).filter(Boolean);
  }

  /**
   * The form's values -> the backend's term. Throws with a message for the user when the
   * spelling or the language is missing. An existing row (id) is replaced outright.
   */
  function formToTerm(form) {
    const sourceLang = String(form.sourceLang || '').trim();
    const canonical = String(form.canonical || '').trim();
    if (!sourceLang) throw new Error('Choose the spoken language of the term.');
    if (!canonical) throw new Error('The term needs its correct spelling.');
    const heard = [];
    for (const h of splitList(form.heardAs)) {
      if (h !== canonical && !heard.includes(h)) heard.push(h);
    }
    const renderings = {};
    for (const [lang, text] of Object.entries(form.renderings || {})) {
      const v = String(text || '').trim();
      if (lang && v) renderings[lang] = v;
    }
    const term = { source_lang: sourceLang, canonical, heard_as: heard, renderings };
    if (form.id != null && form.id !== '') { term.id = form.id; term.replace = true; }
    return term;
  }

  // ---- Alt+E on the recognised line ----

  /**
   * selected: the word(s) selected in the recognised line; canonical: what the user typed
   * it should be ('' = the selection was spelled right); rendering: how it should read in
   * renderingLang ('' = none). null when nothing was selected.
   */
  function termFromSelection({ selected, sourceLang, canonical, renderingLang, rendering }) {
    const heard = String(selected || '').trim();
    if (!heard) return null;
    const spelling = String(canonical || '').trim() || heard;
    const renderings = {};
    const r = String(rendering || '').trim();
    if (renderingLang && r) renderings[renderingLang] = r;
    return { source_lang: sourceLang, canonical: spelling, heard_as: heard === spelling ? [] : [heard], renderings };
  }

  const api = { httpBaseFrom, list, save, remove, rowsOf, formToTerm, termFromSelection, splitList };
  root.STGlossary = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
