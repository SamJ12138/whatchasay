'use strict';
// Incremental translation: while a line is still being recognised, the backend translates
// its stable prefix and sends it as a `draft`. The overlay draws the draft above the
// growing source text, marked as in progress; the final translation replaces it at the
// endpoint. A draft never overwrites a final translation.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadScripts, EXT } = require('./load.js');
const { fakeDom } = require('./fake-dom.js');

const read = (f) => fs.readFileSync(path.join(EXT, f), 'utf8');
const tr = (lang, text) => ({ [lang]: { lines: [text], single_line: text, display_text: text, status: 'ok' } });

function newOverlay(secondary = 'none') {
  const dom = fakeDom();
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/overlay-rows.js', 'content-scripts/overlay.js'],
    { window: {}, document: dom.document, ResizeObserver: dom.ResizeObserver });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = 'zh';
  overlay.secondaryLang = secondary;
  overlay.init();
  overlay.setShowOriginal(true);  // these tests look at the source line too (off by default since overlay batch 1)
  overlay.setLanguageStatus('manual', { lang: 'en', name: 'English' });
  return overlay;
}

// what is on screen, top to bottom: [{kind, text, draft, inProgress, dimmed}]
function screen(overlay) {
  return overlay.subtitleStack.children
    .map((el) => ({ classes: el.className.split(/\s+/), text: el.textContent }))
    .filter((l) => !l.classes.includes('notice'))
    .map((l) => ({
      kind: ['primary', 'secondary', 'original', 'partial'].find((k) => l.classes.includes(k)),
      text: l.text, draft: l.classes.includes('draft'), inProgress: l.classes.includes('in-progress'),
      dimmed: l.classes.includes('provisional'),
    }));
}

test('a draft translation is drawn above the growing source line, marked as a draft in progress', () => {
  const overlay = newOverlay();
  overlay.showPartial('asr_0', 'where did you put', 'en', 'manual');
  assert.equal(overlay.showDraft('asr_0', tr('zh', '你把'), { sourceLang: 'en', langStatus: 'manual' }), true);
  assert.deepEqual(screen(overlay), [
    { kind: 'primary', text: '你把', draft: true, inProgress: true, dimmed: false },
    { kind: 'partial', text: 'where did you put', draft: false, inProgress: true, dimmed: false },
  ]);

  // a newer draft of the same line replaces the older one
  overlay.showPartial('asr_0', 'where did you put the keys', 'en', 'manual');
  overlay.showDraft('asr_0', tr('zh', '你把钥匙放在'), { sourceLang: 'en', langStatus: 'manual' });
  const s = screen(overlay);
  assert.equal(s.length, 2);
  assert.deepEqual(s[0], { kind: 'primary', text: '你把钥匙放在', draft: true, inProgress: true, dimmed: false });
});

test('at the endpoint the draft stays until the final translation replaces it', () => {
  const overlay = newOverlay();
  overlay.showPartial('asr_0', 'where did you put the keys', 'en', 'manual');
  overlay.showDraft('asr_0', tr('zh', '你把钥匙放在'), { sourceLang: 'en', langStatus: 'manual' });

  // the final caption: its translation is still on its way (tens of milliseconds)
  overlay.showTranslation('asr_0', 'Where did you put the keys?', {}, { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay), [
    { kind: 'primary', text: '你把钥匙放在', draft: true, inProgress: true, dimmed: false },
    { kind: 'original', text: 'Where did you put the keys?', draft: false, inProgress: false, dimmed: false },
  ]);

  overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay), [
    { kind: 'primary', text: '你把钥匙放在哪里了?', draft: false, inProgress: false, dimmed: false },
    { kind: 'original', text: 'Where did you put the keys?', draft: false, inProgress: false, dimmed: false },
  ]);

  // a draft of that line that was still on its way must not replace the final translation
  assert.equal(overlay.showDraft('asr_0', tr('zh', '你把钥匙'), { sourceLang: 'en', langStatus: 'manual' }), false);
  assert.equal(screen(overlay)[0].text, '你把钥匙放在哪里了?');
});

test('each target has its own draft, and a final target replaces only its own', () => {
  const overlay = newOverlay('bn');
  overlay.showPartial('asr_0', 'where did you put', 'en', 'manual');
  overlay.showDraft('asr_0', tr('zh', '你把'), { sourceLang: 'en', langStatus: 'manual' });
  overlay.showDraft('asr_0', tr('bn', 'তুমি কোথায়'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay).map((l) => [l.kind, l.text, l.draft]),
    [['primary', '你把', true], ['secondary', 'তুমি কোথায়', true], ['partial', 'where did you put', false]]);

  overlay.showTranslation('asr_0', 'Where did you put the keys?', {}, { sourceLang: 'en', langStatus: 'manual' });
  overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay).map((l) => [l.kind, l.text, l.draft]),
    [['primary', '你把钥匙放在哪里了?', false], ['secondary', 'তুমি কোথায়', true], ['original', 'Where did you put the keys?', false]]);
});

test('the next line starts without the previous line\'s draft', () => {
  const overlay = newOverlay();
  overlay.showPartial('asr_0', 'where did you put the keys', 'en', 'manual');
  overlay.showDraft('asr_0', tr('zh', '你把钥匙放在'), { sourceLang: 'en', langStatus: 'manual' });
  overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  // the rolling rows: the previous line's final stays in row 1 while the next line's draft
  // streams in row 2; the source row shows the next line's words
  overlay.showPartial('asr_1', 'i will be', 'en', 'manual');
  assert.deepEqual(screen(overlay).map((l) => [l.kind, l.text, l.draft]), [
    ['primary', '你把钥匙放在哪里了?', false], ['partial', 'i will be', false]]);
  overlay.showDraft('asr_1', tr('zh', '我会'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay).map((l) => [l.kind, l.text, l.draft]), [
    ['primary', '你把钥匙放在哪里了?', false], ['primary', '我会', true], ['partial', 'i will be', false]]);
});

test('drafts of a recognizer whose language is not confirmed are dimmed, and go when it is replaced', () => {
  const overlay = newOverlay();
  overlay.setLanguageStatus(null);
  overlay.setLanguageStatus('provisional', { lang: 'en', name: 'English' });
  overlay.showPartial('asr_0', 'a cut the rush', 'en', 'provisional');
  overlay.showDraft('asr_0', tr('zh', '切'), { sourceLang: 'en', langStatus: 'provisional' });
  assert.ok(screen(overlay).every((l) => l.dimmed));

  overlay.discardProvisional();                               // backend 'reset': detection chose Bengali
  overlay.setLanguageStatus('confirmed', { lang: 'bn', name: 'Bengali' });
  assert.deepEqual(screen(overlay), []);
  // a draft of the replaced recognizer that was still on its way is dropped
  assert.equal(overlay.showDraft('asr_0', tr('zh', '切书'), { sourceLang: 'en', langStatus: 'provisional' }), false);
  assert.deepEqual(screen(overlay), []);
});

test('an untranslated or failed draft target is not drawn', () => {
  const overlay = newOverlay();
  overlay.showPartial('asr_0', 'where did you', 'en', 'manual');
  overlay.showDraft('asr_0', { zh: { lines: ['where did you'], single_line: 'where did you', display_text: 'where did you', status: 'untranslated' } },
    { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay).map((l) => l.kind), ['partial']);
});

test('the draft style is visibly not final', () => {
  const css = newOverlay()._getStyles();
  const rule = (sel) => (css.match(new RegExp(sel.replace(/\./g, '\\.') + '\\s*\\{([^}]*)\\}')) || [])[1] || '';
  assert.match(rule('.subtitle-line.draft'), /font-style:\s*italic/);
  assert.match(rule('.subtitle-line.draft'), /opacity:\s*0\.[0-9]+/);
});

test('the content script draws drafts and counts them; the offscreen document relays them', () => {
  const main = read('content-scripts/main.js');
  const live = main.slice(main.indexOf('function handleLiveEvent'), main.indexOf('// Latency bookkeeping'));
  const arm = live.slice(live.indexOf("case 'draft'"), live.indexOf('break;', live.indexOf("case 'draft'")));
  assert.match(arm, /overlay\.showDraft\(/);
  assert.match(arm, /lineLatency\.translation\([^;]*draft: true/);
  // on-device translation (Chrome's Translator) replaces the backend's translations, drafts included
  const off = read('offscreen.js');
  const handler = off.slice(off.indexOf('async function handleServerMessage'), off.indexOf('async function translatorFor'));
  assert.match(handler, /case 'draft':\s*case 'translation':/);
});
