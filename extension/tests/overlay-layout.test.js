'use strict';
// Overlay batch 1 (docs/overlay/README.md): the layout math of the compact overlay, pure
// (lib/overlay-layout.js), and the overlay's defaults: translation only, the source line
// smaller and dimmer when it is on, lines capped and truncated from the start, the font
// sized from the video's height, a rounded box behind the text.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./load.js');
const { fakeDom, FakeElement } = require('./fake-dom.js');

const L = require('../lib/overlay-layout.js');

// ---- fitLines: wrap by words, keep the newest, ellipsis at the start ----

test('short text is one line, unchanged', () => {
  assert.deepEqual(L.fitLines('Where did you put the keys?', { maxLines: 2, maxChars: 42 }),
    { lines: ['Where did you put the keys?'], truncated: false });
});

test('text over the line limit wraps at word boundaries, at most 42 characters per line', () => {
  const text = 'High above our planet, in the realm of satellites and space stations,';
  const r = L.fitLines(text, { maxLines: 2, maxChars: 42 });
  assert.deepEqual(r, { lines: ['High above our planet, in the realm of', 'satellites and space stations,'], truncated: false });
  for (const l of r.lines) assert.ok(l.length <= 42, l);
});

test('text over 2 lines keeps the newest words and starts with an ellipsis, never dropping the end', () => {
  const words = [];
  for (let i = 1; i <= 30; i++) words.push('word' + i);
  const r = L.fitLines(words.join(' '), { maxLines: 2, maxChars: 42 });
  assert.equal(r.truncated, true);
  assert.equal(r.lines.length, 2);
  assert.ok(r.lines[0].startsWith('…'), r.lines[0]);
  assert.ok(r.lines[1].endsWith('word30'), r.lines[1]);
  assert.ok(!r.lines.join(' ').includes('word1 '), 'the oldest words are the ones dropped');
  for (const l of r.lines) assert.ok(l.length <= 42, l);
});

test('CJK text wraps by characters at about 20 per line and truncates the same way', () => {
  const text = '你把钥匙放在哪里了我找了一整天都没有找到它们可能在厨房的桌子上';  // 31 characters
  const r = L.fitLines(text, { maxLines: 2, maxChars: 20, cjk: true });
  assert.equal(r.truncated, false);
  assert.deepEqual(r.lines.map((l) => l.length), [20, 11]);
  const long = text + text;  // 62
  const t = L.fitLines(long, { maxLines: 2, maxChars: 20, cjk: true });
  assert.equal(t.truncated, true);
  assert.ok(t.lines[0].startsWith('…'));
  assert.ok(long.endsWith(t.lines[1]));
  assert.ok(t.lines.every((l) => l.length <= 20));
});

test('a single word longer than a line is cut, not left to overflow', () => {
  const r = L.fitLines('a'.repeat(100), { maxLines: 2, maxChars: 42 });
  assert.ok(r.lines.every((l) => l.length <= 42));
  assert.equal(r.truncated, true);
});

test('isCJK names the scripts that wrap by character', () => {
  assert.equal(L.isCJK('zh'), true);
  assert.equal(L.isCJK('ja'), true);
  assert.equal(L.isCJK('ko'), true);
  assert.equal(L.isCJK('en'), false);
  assert.equal(L.isCJK('bn'), false);
  assert.equal(L.isCJK(null), false);
});

// ---- fontPx: relative to the video's height, with a floor ----

test('the font is 4.5% of the video height by default, never under the minimum', () => {
  assert.equal(L.fontPx(540, { scalePct: 4.5, minPx: 14 }), 24.3);
  assert.equal(L.fontPx(200, { scalePct: 4.5, minPx: 14 }), 14);     // a small embed
  assert.equal(L.fontPx(1080, { scalePct: 4.5, minPx: 14 }), 48.6);
  assert.equal(L.fontPx(null, { scalePct: 4.5, minPx: 14, fallbackPx: 20 }), 20);  // no video known
  assert.equal(L.fontPx(0, { scalePct: 4.5, minPx: 14, fallbackPx: 20 }), 20);
});

// ---- the overlay with these rules ----

const tr = (lang, text) => ({ [lang]: { lines: [text], single_line: text, display_text: text, status: 'ok' } });

function fakeVideo(width, height) {
  const v = new FakeElement('video');
  v.getBoundingClientRect = () => ({ left: 0, top: 0, right: width, bottom: height, width, height, x: 0, y: 0 });
  return v;
}

function newOverlay(opts = {}) {
  const dom = fakeDom();
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/overlay-layout.js', 'content-scripts/overlay.js'],
    { window: { innerWidth: 1280, innerHeight: 720, addEventListener() {} }, document: dom.document, ResizeObserver: dom.ResizeObserver });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = opts.primary || 'zh';
  overlay.secondaryLang = opts.secondary || 'none';
  overlay.init();
  overlay.setLanguageStatus('manual', { lang: 'en', name: 'English' });
  return overlay;
}

function lines(overlay) {
  return overlay.subtitleStack.children
    .map((el) => ({ classes: el.className.split(/\s+/), text: el.textContent }))
    .filter((l) => !l.classes.includes('notice'))
    .map((l) => ({ kind: ['primary', 'secondary', 'original', 'partial'].find((k) => l.classes.includes(k)), text: l.text }));
}

test('by default only the translation is drawn: no source line, no partial source text', () => {
  const overlay = newOverlay();
  assert.equal(overlay.showOriginal, false);
  overlay.showPartial('asr_0', 'where did you put the keys', 'en', 'manual');
  assert.deepEqual(lines(overlay), []);
  overlay.showDraft('asr_0', tr('zh', '你把钥匙放在'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(lines(overlay).map((l) => l.kind), ['primary']);
  overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(lines(overlay), [{ kind: 'primary', text: '你把钥匙放在哪里了?' }]);
});

test('a line with no translation and no draft still shows its words, so the viewer is never left with nothing', () => {
  const overlay = newOverlay();
  overlay.showTranslation('asr_0', 'Where did you put the keys?', {}, { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(lines(overlay), [{ kind: 'original', text: 'Where did you put the keys?' }]);
});

test('with the source line on, it is drawn under the translation, smaller and dimmer', () => {
  const overlay = newOverlay();
  overlay.setShowOriginal(true);
  overlay.showPartial('asr_0', 'where did you put the keys', 'en', 'manual');
  assert.deepEqual(lines(overlay).map((l) => l.kind), ['partial']);
  overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(lines(overlay).map((l) => l.kind), ['primary', 'original']);
  const primary = overlay.roleStyle('primary'), original = overlay.roleStyle('original');
  assert.ok(original.fontPx < primary.fontPx, `${original.fontPx} < ${primary.fontPx}`);
  assert.ok(original.opacity < primary.opacity);
  overlay.setShowOriginal(false);
  assert.deepEqual(lines(overlay).map((l) => l.kind), ['primary']);
});

test('a long translation wraps once (rowsPerLine 2), the oldest words cut with an ellipsis beyond that', () => {
  const overlay = newOverlay();
  const words = [];
  for (let i = 1; i <= 30; i++) words.push('word' + i);
  overlay.showTranslation('asr_0', 'src', tr('zh', words.join(' ')), { sourceLang: 'en', langStatus: 'manual' });
  const two = lines(overlay)[0].text.split('\n');
  assert.equal(two.length, 2);
  assert.ok(two[0].startsWith('…') && two[1].endsWith('word30'));
  assert.ok(two.every((r) => r.length <= 42));
  // one visual row per line when asked (rowsPerLine 1): the compact layout
  overlay.updateSettings({ rowsPerLine: 1 });
  overlay.showTranslation('asr_0', 'src', tr('zh', words.join(' ')), { sourceLang: 'en', langStatus: 'manual' });
  const rows = lines(overlay)[0].text.split('\n');
  assert.equal(rows.length, 1);
  assert.ok(rows[0].startsWith('…') && rows[0].endsWith('word30'), rows[0]);
  assert.ok(rows[0].length <= 42);
});

test('a growing partial source line is truncated from the start, never from the end', () => {
  const overlay = newOverlay();
  overlay.setShowOriginal(true);
  const words = [];
  for (let i = 1; i <= 40; i++) {
    words.push('w' + i);
    overlay.showPartial('asr_0', words.join(' '), 'en', 'manual');
    const text = lines(overlay).find((l) => l.kind === 'partial').text;
    const rows = text.split('\n');
    assert.ok(rows.length <= 2, text);
    assert.ok(text.endsWith('w' + i), text);
    assert.ok(rows.every((r) => r.length <= 42), text);
  }
  assert.ok(lines(overlay)[0].text.startsWith('…'));
});

test('CJK translations wrap at the CJK limit', () => {
  const overlay = newOverlay();
  overlay.updateSettings({ maxCharsCJK: 20, maxCharsLatin: 42, rowsPerLine: 2 });
  overlay.showTranslation('asr_0', 'src', tr('zh', '你把钥匙放在哪里了我找了一整天都没有找到它们可能在厨房的桌子上'), { sourceLang: 'en', langStatus: 'manual' });
  const rows = lines(overlay)[0].text.split('\n');
  assert.deepEqual(rows.map((r) => r.length), [20, 11]);
});

test('the font follows the video height: 4.5% of it, at least the minimum, the px setting without a video', () => {
  const overlay = newOverlay();
  assert.equal(overlay.roleStyle('primary').fontPx, 20);  // no video yet: the fontSize setting
  overlay.setVideoElement(fakeVideo(960, 540));
  assert.equal(overlay.roleStyle('primary').fontPx, 24.3);
  overlay.setVideoElement(fakeVideo(320, 180));
  assert.equal(overlay.roleStyle('primary').fontPx, 14);
  overlay.updateSettings({ fontScalePct: 6, fontMinPx: 12 });
  assert.equal(overlay.roleStyle('primary').fontPx, 12);
  overlay.setVideoElement(fakeVideo(960, 540));
  assert.equal(overlay.roleStyle('primary').fontPx, 32.4);
});

test('the background is a rounded box behind the text only, with the configured opacity', () => {
  const overlay = newOverlay();
  let css = overlay._getStyles();
  assert.match(css, /\.subtitle-line\s*\{[^}]*width:\s*fit-content/);
  assert.match(css, /\.subtitle-line\s*\{[^}]*border-radius/);
  assert.match(css, /\.subtitle-line\s*\{[^}]*rgba\(0, 0, 0, 0\.6\)/);
  overlay.updateSettings({ overlayBgOpacity: 0.3 });
  css = overlay._getStyles();
  assert.match(css, /\.subtitle-line\s*\{[^}]*rgba\(0, 0, 0, 0\.3\)/);
});

// ---- the remembered choice ----

test('schema 5 turns the source line off once for older settings; a later choice is kept', () => {
  const M = require('../lib/settings-migration.js');
  assert.deepEqual(M.applyOverlayDefaults({ showOriginal: true, schemaVersion: 4 }, 4), { showOriginal: false, schemaVersion: 4 });
  const kept = { showOriginal: true, schemaVersion: 5 };
  assert.equal(M.applyOverlayDefaults(kept, 5), kept);
  assert.equal(M.applyOverlayDefaults(null, 4), null);
});

test('the content script lists the layout module before the overlay, so the overlay can use it', () => {
  const { CONTENT_SCRIPT_FILES } = require('../lib/caption-mode.js');
  assert.ok(CONTENT_SCRIPT_FILES.indexOf('lib/overlay-layout.js') >= 0, 'lib/overlay-layout.js is injected');
  assert.ok(CONTENT_SCRIPT_FILES.indexOf('lib/overlay-layout.js') < CONTENT_SCRIPT_FILES.indexOf('content-scripts/overlay.js'));
});
