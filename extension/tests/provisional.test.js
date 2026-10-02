'use strict';
// Auto-detect starts a provisional recognizer (English) while spoken-language ID is
// still listening. Its text must not look like a finished subtitle: until the backend
// reports the language as confirmed (or the user picked it), every live line is drawn
// dimmed (class "provisional") above a small label. When detection switches the
// language, the provisional text is discarded and whatever of it is still on its way
// (a translation of a provisional sentence) is dropped: the first confirmed line is
// the first undimmed one.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadScripts, EXT } = require('./load.js');
const { fakeDom } = require('./fake-dom.js');

const read = (f) => fs.readFileSync(path.join(EXT, f), 'utf8');
const zh = (text) => ({ zh: { lines: [text], single_line: text, display_text: text, status: 'ok' } });

function newOverlay() {
  const dom = fakeDom();
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'content-scripts/overlay.js'],
    { window: {}, document: dom.document, ResizeObserver: dom.ResizeObserver });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = 'zh';
  overlay.secondaryLang = 'none';
  overlay.init();
  overlay.setShowOriginal(true);  // these tests look at the source line too (off by default since overlay batch 1)
  return overlay;
}

// what is on screen: [{kind, text, dimmed}] for caption lines, and the label (or null)
function screen(overlay) {
  // caption lines in the block, notices and the label in the extras (overlay batch 2)
  const all = overlay.subtitleStack.children.concat(overlay.extras ? overlay.extras.children : [])
    .map((el) => ({ classes: el.className.split(/\s+/), text: el.textContent }));
  const label = all.find((l) => l.classes.includes('lang-pending')) || null;
  const lines = all.filter((l) => !l.classes.includes('notice')).map((l) => ({
    kind: ['primary', 'secondary', 'original', 'partial'].find((k) => l.classes.includes(k)),
    text: l.text,
    dimmed: l.classes.includes('provisional'),
  }));
  return { lines, label };
}

test('provisional text is dimmed and labelled "Detecting language…"', () => {
  const overlay = newOverlay();
  overlay.setLanguageStatus('provisional', { lang: 'en', name: 'English' });
  let s = screen(overlay);
  assert.deepEqual(s.lines, []);
  assert.ok(s.label, 'no label while the language is being detected');
  assert.equal(s.label.text, 'Detecting language…');

  overlay.showPartial('asr_0', 'a cut the rush book', 'en', 'provisional');
  s = screen(overlay);
  assert.deepEqual(s.lines, [{ kind: 'partial', text: 'a cut the rush book', dimmed: true }]);
  assert.ok(s.label);

  overlay.showTranslation('asr_0', 'A cut the rush book.', {}, { sourceLang: 'en', langStatus: 'provisional' });
  overlay.showTranslation('asr_0', 'A cut the rush book.', zh('切书'), { sourceLang: 'en', langStatus: 'provisional' });
  s = screen(overlay);
  assert.deepEqual(s.lines, [
    { kind: 'primary', text: '切书', dimmed: true },
    { kind: 'original', text: 'A cut the rush book.', dimmed: true },
  ]);
  assert.ok(s.label);
});

test('a language switch discards provisional text; the first confirmed line is the first undimmed one', () => {
  const overlay = newOverlay();
  const seen = [];  // every caption line drawn, in order
  const snap = () => { for (const l of screen(overlay).lines) seen.push(l); };
  overlay.setLanguageStatus('provisional', { lang: 'en', name: 'English' });
  overlay.showPartial('asr_0', 'a cut the', 'en', 'provisional'); snap();
  overlay.showTranslation('asr_0', 'A cut the rush book.', {}, { sourceLang: 'en', langStatus: 'provisional' }); snap();
  overlay.showPartial('asr_1', 'they call', 'en', 'provisional'); snap();

  // backend: {type:'reset'} then {type:'lid', status:'confirmed', lang:'bn', switched:true}
  overlay.discardProvisional();
  overlay.setLanguageStatus('confirmed', { lang: 'bn', name: 'Bengali' });
  let s = screen(overlay);
  assert.deepEqual(s.lines, [], 'provisional text survived the switch');
  assert.equal(s.label, null, 'label still up after confirmation');

  // still on their way from the discarded recognizer: its utterance ids start at 0 again
  // in the new one, so they would land on the Bengali cues
  overlay.showTranslation('asr_0', 'A cut the rush book.', zh('切书'), { sourceLang: 'en', langStatus: 'provisional' }); snap();
  overlay.showPartial('asr_1', 'they call it', 'en', 'provisional'); snap();
  assert.deepEqual(screen(overlay).lines, [], 'a stale provisional event was drawn after the switch');

  overlay.showPartial('asr_0', 'একটা রাজবুক', 'bn', 'confirmed'); snap();
  overlay.showTranslation('asr_0', 'একটা রাজবুক দেখা তো', {}, { sourceLang: 'bn', langStatus: 'confirmed' }); snap();
  overlay.showTranslation('asr_0', 'একটা রাজবুক দেখা তো', zh('一本皇家的书'), { sourceLang: 'bn', langStatus: 'confirmed' }); snap();

  const firstUndimmed = seen.findIndex((l) => !l.dimmed);
  assert.ok(firstUndimmed > 0, 'nothing was dimmed before the first undimmed line');
  assert.equal(seen[firstUndimmed].text, 'একটা রাজবুক', 'the first undimmed line is not the first confirmed one');
  assert.ok(seen.slice(0, firstUndimmed).every((l) => l.dimmed));
  assert.ok(seen.slice(firstUndimmed).every((l) => !l.dimmed), 'a dimmed line after confirmation');
  assert.ok(seen.slice(firstUndimmed).every((l) => !/rush book|they call/.test(l.text)), 'provisional text shown as confirmed');
});

test('confirmation of the provisional language undims what is on screen', () => {
  const overlay = newOverlay();
  overlay.setLanguageStatus('provisional', { lang: 'en', name: 'English' });
  overlay.showTranslation('asr_0', 'Where did you put the keys?', {}, { sourceLang: 'en', langStatus: 'provisional' });
  overlay.showPartial('asr_1', 'i will be', 'en', 'provisional');
  assert.ok(screen(overlay).lines.every((l) => l.dimmed));

  // no reset: the provisional recognizer was the right one
  overlay.setLanguageStatus('confirmed', { lang: 'en', name: 'English' });
  let s = screen(overlay);
  // the source row shows the newest words (overlay batch 2): the next line's partial
  assert.deepEqual(s.lines, [{ kind: 'partial', text: 'i will be', dimmed: false }]);
  assert.equal(s.label, null);

  // the translation of that sentence was asked for while it was provisional
  overlay.showTranslation('asr_0', 'Where did you put the keys?', zh('你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'provisional' });
  s = screen(overlay);
  assert.deepEqual(s.lines[0], { kind: 'primary', text: '你把钥匙放在哪里了?', dimmed: false });
  assert.ok(s.lines.every((l) => !l.dimmed));
});

test('captions tell the overlay the status themselves (content script injected mid-session)', () => {
  const overlay = newOverlay();
  overlay.showPartial('asr_4', 'still guessing', 'en', 'provisional');
  let s = screen(overlay);
  assert.deepEqual(s.lines, [{ kind: 'partial', text: 'still guessing', dimmed: true }]);
  assert.ok(s.label);
});

test('when detection gives up the text stays dimmed and the label says so, until the user picks a language', () => {
  const overlay = newOverlay();
  overlay.setLanguageStatus('provisional', { lang: 'en', name: 'English' });
  overlay.showPartial('asr_0', 'a cut the', 'en', 'provisional');
  overlay.setLanguageStatus('fallback', { lang: 'en', name: 'English' });
  overlay.showTranslation('asr_0', 'A cut the rush book.', {}, { sourceLang: 'en', langStatus: 'fallback' });
  let s = screen(overlay);
  assert.deepEqual(s.lines, [{ kind: 'original', text: 'A cut the rush book.', dimmed: true }]);
  assert.match(s.label.text, /not detected/i);
  assert.match(s.label.text, /English/);
  assert.ok(s.label.classes.includes('warn'));

  // the user picks Bengali in the popup: the English recognizer's text goes, Bengali is certain
  overlay.setLanguageStatus('manual', { lang: 'bn', name: 'Bengali' });
  s = screen(overlay);
  assert.deepEqual(s.lines, []);
  assert.equal(s.label, null);
  overlay.showPartial('asr_0', 'একটা', 'bn', 'manual');
  assert.deepEqual(screen(overlay).lines, [{ kind: 'partial', text: 'একটা', dimmed: false }]);
});

test('page subtitles (caption mode) and a stopped session are never dimmed or labelled', () => {
  const overlay = newOverlay();
  overlay.showTranslation('cue_1', 'Where did you put the keys?', zh('你把钥匙放在哪里了?'));
  let s = screen(overlay);
  assert.ok(s.lines.length === 2 && s.lines.every((l) => !l.dimmed));
  assert.equal(s.label, null);

  overlay.setLanguageStatus('provisional', { lang: 'en', name: 'English' });
  overlay.showPartial('asr_0', 'a cut the', 'en', 'provisional');
  assert.ok(screen(overlay).label);
  overlay.setLanguageStatus(null);  // live captions stopped before the language was known
  s = screen(overlay);
  assert.equal(s.label, null);
  assert.ok(s.lines.every((l) => !l.dimmed), 'dimmed text left on screen without its label');
});

test('the dimmed style is weaker than an ordinary partial caption', () => {
  const css = newOverlay()._getStyles();
  const rule = (sel) => (css.match(new RegExp(sel.replace(/\./g, '\\.') + '\\s*\\{([^}]*)\\}')) || [])[1] || '';
  const opacity = (sel) => parseFloat((rule(sel).match(/opacity:\s*([\d.]+)/) || [])[1]);
  assert.ok(opacity('.subtitle-line.provisional') < opacity('.subtitle-line.partial'));
  // same specificity as .partial / .original: it has to come later to win
  assert.ok(css.indexOf('.subtitle-line.provisional') > css.indexOf('.subtitle-line.partial'));
  assert.ok(css.indexOf('.subtitle-line.provisional') > css.indexOf('.subtitle-line.original'));
  // the overlay is redrawn on every partial (about 8 times a second): a label that fades in
  // again each time flickers
  assert.match(rule('.subtitle-line.notice'), /animation:\s*none/);
});

test('the content script hands the backend\'s language status to the overlay', () => {
  const main = read('content-scripts/main.js');
  const live = main.slice(main.indexOf('function handleLiveEvent'), main.indexOf('// Latency bookkeeping'));
  const arm = (name) => live.slice(live.indexOf(`case '${name}'`), live.indexOf('break;', live.indexOf(`case '${name}'`)));
  assert.match(arm('partial'), /showPartial\([^;]*ev\.lang_status\)/);
  assert.match(arm('final'), /langStatus: ev\.lang_status/);
  assert.match(arm('revision'), /langStatus: ev\.lang_status/);
  assert.match(arm('reset'), /overlay\.discardProvisional\(\)/);
  assert.match(arm('lid'), /overlay\.setLanguageStatus\(ev\.status/);
  // partials and finals arrive in session order: they carry the current status too
  assert.match(arm('partial'), /syncLanguageStatus\(ev\)/);
  assert.match(arm('final'), /syncLanguageStatus\(ev\)/);
});

test('Alt+E does not offer a provisional line for correction', () => {
  const main = read('content-scripts/main.js');
  const edit = main.slice(main.indexOf("case 'toggle-edit'"), main.indexOf("case 'toggle-edit'") + 600);
  assert.match(edit, /!cue\.provisional/);
});

test('an on-device translation carries the status of its sentence', () => {
  const off = read('offscreen.js');
  const fn = off.slice(off.indexOf('async function translateOnDevice'));
  assert.match(fn, /lang_status: finalMsg\.lang_status/);
});

// ---- the line in progress (streaming partial results) ----

test('a line still being recognised is marked as in progress until its final replaces it', () => {
  const overlay = newOverlay();
  overlay.setLanguageStatus('manual', { lang: 'en', name: 'English' });
  overlay.showPartial('asr_0', 'where did you', 'en', 'manual');
  let els = overlay.subtitleStack.children;
  assert.equal(els.length, 1);
  assert.ok(els[0].className.split(/\s+/).includes('partial'));
  assert.ok(els[0].className.split(/\s+/).includes('in-progress'));
  assert.equal(els[0].dataset.state, 'in-progress');
  assert.equal(els[0].textContent, 'where did you');          // the growing text itself is not altered

  overlay.showPartial('asr_0', 'where did you put the', 'en', 'manual');
  els = overlay.subtitleStack.children;
  assert.equal(els.length, 1);
  assert.equal(els[0].textContent, 'where did you put the');

  overlay.showTranslation('asr_0', 'Where did you put the keys?', {}, { sourceLang: 'en', langStatus: 'manual' });
  els = overlay.subtitleStack.children;
  assert.equal(els.length, 1);
  assert.ok(!els[0].className.split(/\s+/).includes('in-progress'), 'the finished line is still marked in progress');
  assert.equal(els[0].dataset.state, undefined);

  // the next line takes the source row (one text per role, the newest: overlay batch 2)
  overlay.showPartial('asr_1', 'i will be', 'en', 'manual');
  const kinds = overlay.subtitleStack.children.map((el) => el.dataset.state || 'done');
  assert.deepEqual(kinds, ['in-progress']);
});

test('the in-progress mark is visible: italic, greyed, with a trailing ellipsis drawn by the style sheet', () => {
  const css = newOverlay()._getStyles();
  const rule = (sel) => (css.match(new RegExp(sel.replace(/\./g, '\\.') + '\\s*\\{([^}]*)\\}')) || [])[1] || '';
  assert.match(rule('.subtitle-line.partial'), /font-style:\s*italic/);
  assert.match(rule('.subtitle-line.in-progress::after'), /content:\s*'\s*…'/);
});
