'use strict';
// The term glossary (observations T13, T14). Two ways in: the Options page table (add /
// edit / delete, lib/glossary.js talks to the backend's /glossary) and Alt+E with a word
// selected in the recognised line (the overlay's term editor: "heard as X, should be Y,
// shown as Z"). Both go through the service worker, which posts with the extension's origin.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadScripts } = require('./load.js');
const { fakeDom } = require('./fake-dom.js');

const EXT = path.resolve(__dirname, '..');
// one line ending, so a fixed-width slice of a source holds the same text on a CRLF checkout (Windows CI)
const read = (f) => fs.readFileSync(path.join(EXT, f), 'utf8').replace(/\r\n/g, '\n');
const Gl = require('../lib/glossary.js');

const TERM = { source_lang: 'bn', canonical: 'রাজভোগ', heard_as: ['রাজবুক'], renderings: { en: 'rajbhog' } };

// ---- the backend client (used by the worker and the Options page) ----

test('save posts the term to /glossary and list / remove use GET and DELETE', async () => {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, init });
    return { ok: true, status: 200, json: async () => (init && init.method === 'GET' || !init || !init.method ? { terms: [TERM] } : { status: 'saved', term: { id: 7, ...TERM } }) };
  };
  const saved = await Gl.save(TERM, 'ws://127.0.0.1:8765/ws', fetch);
  assert.deepEqual(saved, { ok: true, term: { id: 7, ...TERM } });
  assert.equal(calls[0].url, 'http://127.0.0.1:8765/glossary');
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].init.body), TERM);
  const listed = await Gl.list('ws://127.0.0.1:8765/ws', fetch);
  assert.deepEqual(listed, { ok: true, terms: [TERM] });
  assert.equal(calls[1].url, 'http://127.0.0.1:8765/glossary');
  const removed = await Gl.remove(7, 'ws://127.0.0.1:8765/ws', fetch);
  assert.equal(removed.ok, true);
  assert.equal(calls[2].url, 'http://127.0.0.1:8765/glossary/7');
  assert.equal(calls[2].init.method, 'DELETE');
});

test('the client reports failures instead of throwing', async () => {
  const bad = await Gl.save(TERM, 'ws://127.0.0.1:8765/ws', async () => ({ ok: false, status: 422, json: async () => ({ detail: 'x' }) }));
  assert.equal(bad.ok, false);
  assert.match(bad.error, /422/);
  const down = await Gl.list('ws://127.0.0.1:8765/ws', async () => { throw new Error('Failed to fetch'); });
  assert.equal(down.ok, false);
  assert.match(down.error, /Failed to fetch/);
});

// ---- the Options table: rows from terms, a term from the form ----

test('rows show one term per line with its heard-as spellings and a rendering per target language', () => {
  const rows = Gl.rowsOf([{ id: 1, ...TERM }, { id: 2, source_lang: 'en', canonical: 'Kubernetes', heard_as: ['cooper netties', 'kubernetis'], renderings: {} }], ['en', 'zh']);
  assert.deepEqual(rows, [
    { id: 1, sourceLang: 'bn', canonical: 'রাজভোগ', heardAs: 'রাজবুক', renderings: { en: 'rajbhog', zh: '' } },
    { id: 2, sourceLang: 'en', canonical: 'Kubernetes', heardAs: 'cooper netties, kubernetis', renderings: { en: '', zh: '' } },
  ]);
});

test('the form becomes a term: trimmed, heard-as split on commas and newlines, duplicates and empties dropped', () => {
  const term = Gl.formToTerm({ sourceLang: 'bn', canonical: ' রাজভোগ ', heardAs: 'রাজবুক, রাজব\nরাজবুক, , রাজভোগ', renderings: { en: ' rajbhog ', zh: '  ' } });
  assert.deepEqual(term, { source_lang: 'bn', canonical: 'রাজভোগ', heard_as: ['রাজবুক', 'রাজব'], renderings: { en: 'rajbhog' } });
  assert.throws(() => Gl.formToTerm({ sourceLang: 'bn', canonical: '  ', heardAs: 'x', renderings: {} }), /spelling/);
  assert.throws(() => Gl.formToTerm({ sourceLang: '', canonical: 'x', heardAs: '', renderings: {} }), /language/);
  const withId = Gl.formToTerm({ id: 4, sourceLang: 'en', canonical: 'K8s', heardAs: '', renderings: {} });
  assert.equal(withId.id, 4);
  assert.equal(withId.replace, true);   // an edit replaces the lists; a new term from Alt+E extends them
});

test('a term from the Alt+E selection: the selected text is a heard-as spelling of what was typed', () => {
  const t = Gl.termFromSelection({ selected: ' রাজবুক ', sourceLang: 'bn', canonical: 'রাজভোগ', renderingLang: 'en', rendering: 'rajbhog' });
  assert.deepEqual(t, TERM);
  // the spelling was right, only the rendering was wrong: no heard-as spelling
  assert.deepEqual(Gl.termFromSelection({ selected: 'রাজভোগ', sourceLang: 'bn', canonical: 'রাজভোগ', renderingLang: 'en', rendering: 'rajbhog' }),
    { source_lang: 'bn', canonical: 'রাজভোগ', heard_as: [], renderings: { en: 'rajbhog' } });
  // nothing typed for the spelling: the selection is the canonical one; no rendering: none stored
  assert.deepEqual(Gl.termFromSelection({ selected: 'Prosenjit', sourceLang: 'en', canonical: '', renderingLang: 'zh', rendering: '' }),
    { source_lang: 'en', canonical: 'Prosenjit', heard_as: [], renderings: {} });
  assert.equal(Gl.termFromSelection({ selected: '  ', sourceLang: 'bn', canonical: 'x', renderingLang: 'en', rendering: '' }), null);
});

// ---- the Options page has the table and wires the client ----

test('the Options page has a Glossary section with the table and an add button, and loads the client', () => {
  const html = read('options/options.html');
  assert.match(html, /<h2 class="section-title">Glossary<\/h2>/);
  assert.match(html, /id="glossaryTable"/);
  assert.match(html, /id="glossaryAdd"/);
  assert.ok(html.indexOf('<script src="../lib/glossary.js"></script>') < html.indexOf('<script src="options.js"></script>'));
  const js = read('options/options.js');
  assert.match(js, /STGlossary\.(list|save|remove)/);
  assert.match(js, /glossaryTable/);
});

// ---- Alt+E with a selection in the recognised line: the overlay's term editor ----

const tr = (lang, text) => ({ [lang]: { lines: [text], single_line: text, display_text: text, status: 'ok' } });

function newOverlay() {
  const dom = fakeDom();
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/overlay-layout.js', 'lib/overlay-rows.js', 'lib/glossary.js', 'content-scripts/overlay.js'],
    { window: { innerWidth: 1280, innerHeight: 720, addEventListener() {} }, document: dom.document, ResizeObserver: dom.ResizeObserver });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = 'en';
  overlay.secondaryLang = 'none';
  overlay._now = () => 10;
  overlay.init();
  overlay.setLanguageStatus('confirmed', { lang: 'bn', name: 'Bengali' });
  overlay.setShowOriginal(true);
  overlay.showTranslation('asr_0', 'একটা রাজবুক দেখা তো', tr('en', 'A royal book.'), { sourceLang: 'bn', langStatus: 'confirmed' });
  return overlay;
}

const lines = (overlay) => overlay.subtitleStack.children.map((el) => el.className);

test('Alt+E with a word selected in the recognised line opens the term editor, prefilled with the selection', () => {
  const overlay = newOverlay();
  const terms = [];
  overlay.onGlossaryTerm((t) => terms.push(t));
  overlay.selectedSourceText = () => ({ text: 'রাজবুক', cueId: 'asr_0' });
  assert.equal(overlay.enableTermEdit(), true);
  const editor = overlay.subtitleStack.children.find((el) => el.className.includes('term-edit'));
  assert.ok(editor, lines(overlay).join(' | '));
  assert.equal(overlay.termEditor.heard, 'রাজবুক');
  assert.equal(overlay.termEditor.fields.canonical.textContent, 'রাজবুক');
  assert.equal(overlay.termEditor.fields.rendering.textContent, '');
  assert.equal(overlay.termEditor.renderingLang, 'en');
  // the user types the right spelling and how it should read in English, then Enter
  overlay.termEditor.fields.canonical.textContent = 'রাজভোগ';
  overlay.termEditor.fields.rendering.textContent = 'rajbhog';
  overlay.termEditor.fields.rendering.dispatch('keydown', { key: 'Enter', preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(JSON.parse(JSON.stringify(terms)), [TERM]);   // built in the overlay's vm context
  assert.equal(overlay.editMode, false);
  assert.ok(!lines(overlay).some((c) => c.includes('term-edit')));
});

test('the term editor stops its keys from reaching the page and Escape cancels without saving', () => {
  const overlay = newOverlay();
  const terms = [];
  overlay.onGlossaryTerm((t) => terms.push(t));
  overlay.selectedSourceText = () => ({ text: 'রাজবুক', cueId: 'asr_0' });
  overlay.enableTermEdit();
  for (const type of ['keydown', 'keypress', 'keyup']) {
    let stopped = false;
    overlay.termEditor.fields.canonical.dispatch(type, { key: 'm', stopPropagation() { stopped = true; }, preventDefault() {} });
    assert.ok(stopped, `${type} reached the page`);
  }
  overlay.termEditor.fields.canonical.dispatch('keydown', { key: 'Escape', preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(terms, []);
  assert.equal(overlay.editMode, false);
});

test('without a selection the term editor does not open, so Alt+E edits the translation as before', () => {
  const overlay = newOverlay();
  overlay.selectedSourceText = () => null;
  assert.equal(overlay.enableTermEdit(), false);
  assert.equal(overlay.editMode, false);
});

test('the recognised line can be selected with the mouse: text selection on, no drag from it', () => {
  const overlay = newOverlay();
  const css = overlay._getStyles();
  assert.match(css, /\.subtitle-line\.(partial|original)[^{]*\{[^}]*user-select:\s*text/);
  const src = read('content-scripts/overlay.js');
  const drag = src.slice(src.indexOf('e.button !== 0 || this.editMode'), src.indexOf('e.button !== 0 || this.editMode') + 600);
  assert.match(drag, /partial|original/);   // a mousedown on the recognised line is a selection, not a drag
});

// ---- the content script and the worker ----

test('the content script tries the term editor first on Alt+E and sends terms through the worker', () => {
  const main = read('content-scripts/main.js');
  const edit = main.slice(main.indexOf("case 'toggle-edit'"), main.indexOf("case 'toggle-edit'") + 900);
  assert.match(edit, /enableTermEdit\(\)/);
  assert.ok(edit.indexOf('enableTermEdit()') < edit.indexOf('enableEditMode('), 'the term editor must be tried before the translation editor');
  assert.match(main, /onGlossaryTerm\(/);
  assert.match(main, /type: 'SUBMIT_GLOSSARY_TERM'/);
});

test('the worker posts SUBMIT_GLOSSARY_TERM with the glossary client, only for tab content scripts', () => {
  const bg = read('background.js');
  assert.match(bg, /import '\.\/lib\/glossary\.js';/);
  const c = bg.slice(bg.indexOf("case 'SUBMIT_GLOSSARY_TERM'"), bg.indexOf("case 'SUBMIT_GLOSSARY_TERM'") + 800);
  assert.ok(c.length > 30, 'no SUBMIT_GLOSSARY_TERM handler');
  assert.match(c, /sender\.tab/);
  assert.match(c, /STGlossary\.save\(/);
});

test('lib/glossary.js is injected before the overlay', () => {
  const src = read('lib/caption-mode.js');
  const files = src.slice(src.indexOf('CONTENT_SCRIPT_FILES = ['), src.indexOf('];', src.indexOf('CONTENT_SCRIPT_FILES = [')));
  assert.ok(files.indexOf("'lib/glossary.js'") > 0 && files.indexOf("'lib/glossary.js'") < files.indexOf("'content-scripts/overlay.js'"), files);
});
