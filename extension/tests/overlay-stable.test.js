'use strict';
// Overlay batch 3 (docs/overlay/README.md): the block never changes height while text
// streams. Its height is reserved (two rows per text role) and partial text is updated
// in place, in the same element, so nothing jumps while a line is being recognised.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./load.js');
const { fakeDom, FakeElement } = require('./fake-dom.js');

const tr = (lang, text) => ({ [lang]: { lines: [text], single_line: text, display_text: text, status: 'ok' } });

function fakeVideo(w, h) {
  const v = new FakeElement('video');
  v.getBoundingClientRect = () => ({ left: 0, top: 0, right: w, bottom: h, width: w, height: h, x: 0, y: 0 });
  return v;
}

function newOverlay(secondary = 'none') {
  const dom = fakeDom();
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/overlay-layout.js', 'content-scripts/overlay.js'],
    { window: { innerWidth: 1280, innerHeight: 720, addEventListener() {} }, document: dom.document, ResizeObserver: dom.ResizeObserver });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = 'zh';
  overlay.secondaryLang = secondary;
  overlay.init();
  overlay.setLanguageStatus('manual', { lang: 'en', name: 'English' });
  overlay.setVideoElement(fakeVideo(960, 540));
  return overlay;
}

const LONG = 'High above our planet, in the realm of satellites and space stations, the familiar rules do not apply';

test('the stack reserves two rows per text role and keeps that height whatever is drawn', () => {
  const overlay = newOverlay();
  const reserved = overlay.reservedHeight();
  const font = overlay.roleStyle('primary').fontPx;
  assert.ok(reserved >= 2 * font * 1.25, `${reserved} holds two rows of ${font}px`);
  assert.equal(overlay.subtitleStack.style.height, `${reserved}px`);
  overlay.showDraft('asr_0', tr('zh', '你把'), { sourceLang: 'en', langStatus: 'manual' });
  assert.equal(overlay.subtitleStack.style.height, `${reserved}px`);
  overlay.showTranslation('asr_0', 'src', tr('zh', LONG), { sourceLang: 'en', langStatus: 'manual' });   // two rows
  assert.equal(overlay.subtitleStack.style.height, `${reserved}px`);
  assert.equal(overlay.blockHeight(), reserved);   // placement sees the same height
});

test('the source row and a secondary language add their own reserved rows', () => {
  const alone = newOverlay().reservedHeight();
  const withSource = newOverlay();
  withSource.setShowOriginal(true);
  assert.ok(withSource.reservedHeight() > alone);
  const two = newOverlay('en');
  assert.ok(two.reservedHeight() > alone);
  assert.ok(two.reservedHeight() >= 2 * alone - 1, 'two translation roles: twice the rows');
});

test('a growing partial line is updated in the same element, not rebuilt', () => {
  const overlay = newOverlay();
  overlay.setShowOriginal(true);
  overlay.showPartial('asr_0', 'where did', 'en', 'manual');
  const el = overlay.subtitleStack.children.find((e) => e.className.includes('partial'));
  assert.ok(el);
  for (const text of ['where did you', 'where did you put', 'where did you put the keys']) {
    overlay.showPartial('asr_0', text, 'en', 'manual');
    const now = overlay.subtitleStack.children.find((e) => e.className.includes('partial'));
    assert.equal(now, el, 'the same element');
    assert.equal(now.textContent, text);
  }
});

test('a newer draft of the same line is updated in place; the final and the next line get fresh elements', () => {
  const overlay = newOverlay();
  overlay.showDraft('asr_0', tr('zh', '你把'), { sourceLang: 'en', langStatus: 'manual' });
  const draft = overlay.subtitleStack.children[0];
  overlay.showDraft('asr_0', tr('zh', '你把钥匙放在'), { sourceLang: 'en', langStatus: 'manual' });
  assert.equal(overlay.subtitleStack.children[0], draft);
  assert.equal(draft.textContent, '你把钥匙放在');
  overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  const final = overlay.subtitleStack.children[0];
  assert.notEqual(final, draft);
  assert.ok(!final.className.includes('draft'));
  overlay.showDraft('asr_1', tr('zh', '我会'), { sourceLang: 'en', langStatus: 'manual' });
  const next = overlay.subtitleStack.children[0];
  assert.notEqual(next, final, 'another cue: a fresh element (its listeners belong to the old cue)');
  assert.equal(next.dataset.cueId, 'asr_1');
});

test('the rows pack at the bottom over a video and at the top below it', () => {
  const css = newOverlay()._getStyles();
  // the rule whose selector is exactly `sel` (not one that merely ends with it)
  const rule = (sel) => (css.match(new RegExp('\\n\\s*' + sel.replace(/[.\-]/g, '\\$&') + '\\s*\\{([^}]*)\\}')) || [])[1] || '';
  assert.match(rule('.subtitle-stack'), /justify-content:\s*flex-end/);
  assert.match(rule('.mode-page .subtitle-stack'), /justify-content:\s*flex-start/);
});
