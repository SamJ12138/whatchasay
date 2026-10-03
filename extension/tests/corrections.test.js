'use strict';
// Alt+E corrections on a live-captions-only tab: the tab has no caption-path socket, so the
// content script hands the correction to the service worker, which posts it to the backend's
// POST /corrections (extension origin). Before, handleCorrection returned silently.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const EXT = path.resolve(__dirname, '..');
const read = (f) => fs.readFileSync(path.join(EXT, f), 'utf8');
const C = require('../lib/corrections.js');

const CORRECTION = {
  cueId: 'asr_3', sourceText: 'hello world', sourceLang: 'en',
  originalTranslation: { zh: 'machine' }, correctedTranslation: { zh: '你好，世界' },
};

test('the HTTP base comes from the ws server URL', () => {
  assert.equal(C.httpBaseFrom('ws://127.0.0.1:8765/ws'), 'http://127.0.0.1:8765');
  assert.equal(C.httpBaseFrom('wss://localhost:9000/ws'), 'https://localhost:9000');
  assert.equal(C.httpBaseFrom('not a url'), 'http://127.0.0.1:8765');
});

test('submit posts the backend payload to /corrections', async () => {
  const calls = [];
  const fetch = async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200, json: async () => ({ status: 'saved' }) }; };
  const res = await C.submit(CORRECTION, 'ws://127.0.0.1:8765/ws', fetch);
  assert.deepEqual(res, { ok: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://127.0.0.1:8765/corrections');
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    cue_id: 'asr_3', source_text: 'hello world', source_lang: 'en',
    original_translation: { zh: 'machine' }, corrected_translation: { zh: '你好，世界' },
  });
});

test('submit reports failures instead of throwing', async () => {
  const bad = await C.submit(CORRECTION, 'ws://127.0.0.1:8765/ws', async () => ({ ok: false, status: 422, json: async () => ({}) }));
  assert.equal(bad.ok, false);
  assert.match(bad.error, /422/);
  const down = await C.submit(CORRECTION, 'ws://127.0.0.1:8765/ws', async () => { throw new Error('Failed to fetch'); });
  assert.equal(down.ok, false);
  assert.match(down.error, /Failed to fetch/);
});

test('the content script sends corrections through the worker when the tab has no caption socket', () => {
  const main = read('content-scripts/main.js');
  const handler = main.slice(main.indexOf('async function handleCorrection'), main.indexOf('async function handleCorrection') + 1500);
  assert.ok(!/if \(!connected\) return;/.test(handler), 'corrections are still dropped without a socket');
  assert.match(handler, /type: 'SUBMIT_CORRECTION'/);
});

test('the worker posts SUBMIT_CORRECTION with the corrections module, only for tab content scripts', () => {
  const bg = read('background.js');
  assert.match(bg, /import '\.\/lib\/corrections\.js';/);
  const c = bg.slice(bg.indexOf("case 'SUBMIT_CORRECTION'"), bg.indexOf("case 'SUBMIT_CORRECTION'") + 800);
  assert.ok(c.length > 30, 'no SUBMIT_CORRECTION handler');
  assert.match(c, /sender\.tab/);
  assert.match(c, /STCorrections\.submit\(/);
});

test('editing a live caption survives the captions that keep arriving', () => {
  const main = read('content-scripts/main.js');
  const overlay = read('content-scripts/overlay.js');
  // Alt+E picks the newest cue that has a translation, not the sentence still in progress
  const edit = main.slice(main.indexOf("case 'toggle-edit'"), main.indexOf("case 'toggle-edit'") + 500);
  assert.match(edit, /cue\.translations/);
  // the cue being edited is neither hidden nor dropped by the next sentence (the overlay
  // keeps the previous line in its row 1 and bounds its cues itself; the rolling layout)
  assert.match(main, /overlay\.editMode && overlay\.editCueId === cueId\) \{ scheduleLiveHide/);
  assert.match(overlay, /this\.editMode && this\.editCueId === oldest\) break;/);
  // the overlay does not rebuild the line while it is being typed in
  assert.match(overlay, /this\.editMode && this\._editingLine && this\._editingLine\.isConnected\) return;/);
});


// The bug behind "corrections are never saved": _handleEdit was given the line's role
// ('primary' / 'secondary') and looked it up as a language code, so the corrected
// translation always equalled the original and the backend stored nothing.
test('an edited line yields a correction for its own language', () => {
  const { loadScripts } = require('./load.js');
  const ctx = loadScripts(['content-scripts/overlay.js'], { window: {}, document: {} });
  const overlay = ctx.window.subtitleOverlay;
  const got = [];
  overlay.onCorrection((c) => got.push(c));
  overlay.currentCues.set('asr_1', {
    original: 'Where did you put the keys?', sourceLang: 'en',
    translations: { zh: { single_line: '你把钥匙放在哪里了?' }, bn: { single_line: 'চাবি কোথায়?' } },
  });
  overlay.editMode = true;
  overlay.editCueId = 'asr_1';
  // the line drawn for the primary language (zh); its blur handler passes what _createSubtitleLine got
  const src = require('node:fs').readFileSync(require('node:path').join(EXT, 'content-scripts', 'overlay.js'), 'utf8');
  const blur = src.match(/addEventListener\('blur', \(\) => this\._handleEdit\(line, cueId, ([^)]+)\)\)/)[1];
  const arg = { type: 'primary', langCode: 'zh' }[blur.split('||')[0].trim()];
  overlay._handleEdit({ textContent: '钥匙你放哪儿了?' }, 'asr_1', arg);
  assert.equal(got.length, 1);
  assert.deepEqual({ ...got[0].correctedTranslation }, { zh: '钥匙你放哪儿了?', bn: 'চাবি কোথায়?' });
  assert.deepEqual({ ...got[0].originalTranslation }, { zh: '你把钥匙放在哪里了?', bn: 'চাবি কোথায়?' });
});


// Found by the live test on YouTube: the page's player listens for keys on the document
// (m mutes, j rewinds 10 s, space pauses), and the keys typed into a correction bubbled out
// of the overlay to it. Typing "Show me a rajbhog." left the video muted, paused and at 0:00.
test('keys typed into a correction do not reach the page', () => {
  const { loadScripts } = require('./load.js');
  const { fakeDom } = require('./fake-dom.js');
  const dom = fakeDom();
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/overlay-rows.js', 'content-scripts/overlay.js'],
    { window: {}, document: dom.document, ResizeObserver: dom.ResizeObserver });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = 'en';
  overlay.secondaryLang = 'none';
  overlay.init();
  overlay.showTranslation('asr_0', 'একটা রাজবুক দেখা তো', { en: { single_line: 'A royal book.', display_text: 'A royal book.' } }, { sourceLang: 'bn' });
  overlay.enableEditMode('asr_0');
  const line = overlay.subtitleStack.children.find((el) => el.classList.contains('editing'));
  assert.ok(line, 'no line in edit mode');
  const typed = (type, key) => {
    let stopped = false;
    line.dispatch(type, { key, shiftKey: false, preventDefault() {}, stopPropagation() { stopped = true; } });
    assert.ok(stopped, `${type} "${key}" typed into the correction propagates to the page`);
  };
  for (const type of ['keydown', 'keypress', 'keyup']) {
    for (const key of ['m', 'j', ' ']) typed(type, key);
  }
  typed('keydown', 'Enter');  // ends the edit
});
