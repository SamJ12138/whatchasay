'use strict';
// The rolling two-row layout (docs/overlay/README.md): row 1 holds the previous line's
// final translation, row 2 the current line's draft (or its final, briefly). A final
// stays at least max(1.5 s, chars / 15 per s) before it can be pushed out of row 1; if
// the next line finalises sooner, row 1 is held and row 2 shows the newer final until
// the hold ends. Pure state machine (lib/overlay-rows.js) with an explicit clock.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./load.js');
const { fakeDom, FakeElement } = require('./fake-dom.js');

const R = require('../lib/overlay-rows.js');

const view = (rows) => {
  const v = rows.view();
  return [v.row1 ? v.row1.cueId : null, v.row2 ? `${v.row2.cueId}:${v.row2.kind}` : null];
};

test('a final line goes to row 1 and row 2 clears for the next draft', () => {
  const rows = R.createRows();
  rows.open('a', true, 0.5);                         // a's draft
  assert.deepEqual(view(rows), [null, 'a:draft']);
  rows.final('a', 20, 2.0);
  assert.deepEqual(view(rows), ['a', null]);
  rows.open('b', false, 2.3);                        // b's first words (no draft text yet)
  assert.deepEqual(view(rows), ['a', 'b:draft']);
  rows.open('b', true, 3.6);
  assert.deepEqual(view(rows), ['a', 'b:draft']);
});

test('the minimum display is max(1.5 s, chars / 15 per s)', () => {
  const rows = R.createRows();
  assert.equal(rows.minDisplay(10), 1.5);
  assert.equal(rows.minDisplay(45), 3);
  assert.equal(rows.minDisplay(0), 1.5);
  const slow = R.createRows({ minDisplayS: 2, charsPerS: 10 });
  assert.equal(slow.minDisplay(10), 2);
  assert.equal(slow.minDisplay(45), 4.5);
});

test('a line that finalises before the hold ends waits in row 2; row 1 moves on when the hold ends', () => {
  const rows = R.createRows();
  rows.final('a', 45, 10.0);                         // held until 13.0
  rows.open('b', true, 10.5);
  rows.final('b', 20, 11.0);                         // too soon: a is held
  assert.deepEqual(view(rows), ['a', 'b:final']);
  assert.equal(rows.nextWake(), 13.0);
  assert.equal(rows.tick(12.9), false);
  assert.deepEqual(view(rows), ['a', 'b:final']);
  assert.equal(rows.tick(13.0), true);
  assert.deepEqual(view(rows), ['b', null]);
  assert.equal(rows.nextWake(), null);
  // b's own hold runs from when it appeared (11.0): until 12.5, already over
  rows.final('c', 10, 13.2);
  assert.deepEqual(view(rows), ['c', null]);
});

test('a draft of the next line waits while row 2 holds a final, and takes row 2 when the hold ends', () => {
  const rows = R.createRows();
  rows.final('a', 45, 10.0);                         // held until 13.0
  rows.final('b', 20, 11.0);
  rows.open('c', true, 11.5);                        // nowhere to go yet
  assert.deepEqual(view(rows), ['a', 'b:final']);
  rows.tick(13.0);
  assert.deepEqual(view(rows), ['b', 'c:draft']);
});

test('fast speech: a third final inside the hold pushes the oldest out; the newest is always on screen', () => {
  const rows = R.createRows();
  rows.final('a', 60, 10.0);                         // held until 14.0
  rows.final('b', 20, 10.6);
  rows.final('c', 20, 11.2);
  assert.deepEqual(view(rows), ['b', 'c:final']);
  // b appeared at 10.6, so it is held until 12.1; c moves up then
  assert.equal(rows.nextWake(), 12.1);
  rows.tick(12.1);
  assert.deepEqual(view(rows), ['c', null]);
});

test('a revision of a final updates its length without moving it', () => {
  const rows = R.createRows();
  rows.final('a', 20, 10.0);
  rows.final('a', 50, 10.4);                         // the second target, or a refinement
  assert.deepEqual(view(rows), ['a', null]);
  assert.equal(rows.nextWake(), null);
  rows.final('b', 10, 11.0);                         // a is held until 13.33 now
  assert.deepEqual(view(rows), ['a', 'b:final']);
});

test('a line whose draft was shown and that finalises after the hold moves straight to row 1', () => {
  const rows = R.createRows();
  rows.final('a', 10, 10.0);
  rows.open('b', true, 10.8);
  rows.final('b', 10, 12.0);                         // a's hold (1.5 s) is over
  assert.deepEqual(view(rows), ['b', null]);
});

test('a line still being recognised keeps row 2 until its final; a newer line with text replaces a stale draft', () => {
  const rows = R.createRows();
  rows.open('a', true, 1.0);
  rows.open('b', false, 2.0);                        // b's words start before a's translation lands
  assert.deepEqual(view(rows), [null, 'a:draft']);
  rows.final('a', 10, 2.5);
  assert.deepEqual(view(rows), ['a', 'b:draft']);
  rows.open('c', true, 4.0);                         // b never got a translation; c has one
  assert.deepEqual(view(rows), ['a', 'c:draft']);
});

test('removing the newest line clears both rows (silence); removing row 1 alone leaves row 2', () => {
  const rows = R.createRows();
  rows.final('a', 10, 1.0);
  rows.final('b', 10, 3.0);
  assert.deepEqual(view(rows), ['b', null]);
  rows.open('c', true, 3.5);
  rows.remove('b');
  assert.deepEqual(view(rows), [null, 'c:draft']);
  rows.remove('c');
  assert.deepEqual(view(rows), [null, null]);
});

test('a scripted live sequence never shows a final in row 1 for less than its minimum', () => {
  const rows = R.createRows();
  const shown = {};   // cue -> [first, last] in row 1 or row 2 as a final
  let t = 0;
  const log = () => {
    const v = rows.view();
    for (const e of [v.row1, v.row2 && v.row2.kind === 'final' ? v.row2 : null]) {
      if (!e) continue;
      shown[e.cueId] = shown[e.cueId] || [t, t];
      shown[e.cueId][1] = t;
    }
  };
  const chars = {};
  // 12 lines, a new one every 0.6-2.4 s, finals 1 s after the words start, lengths 5-70
  const event = (fn) => { rows.tick(t); log(); fn(); log(); };   // what was up until the event, then after it
  for (let i = 0; i < 12; i++) {
    const cue = 'l' + i;
    chars[cue] = 5 + (i * 13) % 66;
    event(() => rows.open(cue, false, t));
    t += 0.3; event(() => rows.open(cue, true, t));
    t += 0.7; event(() => rows.final(cue, chars[cue], t));
    const gap = 0.6 + (i % 4) * 0.6;
    for (let s = 0.1; s <= gap; s += 0.1) { t += 0.1; event(() => {}); }   // the clock ticks between lines
  }
  for (let s = 0.1; s <= 6; s += 0.1) { t += 0.1; event(() => {}); }
  const minimums = Object.entries(shown).map(([cue, [a, b]]) => ({ cue, shown: b - a, min: rows.minDisplay(chars[cue]) }));
  const tooShort = minimums.filter((m) => m.shown + 0.11 < m.min);   // one tick of slack
  // fast speech can push one line out early (three finals inside one hold); never more
  assert.ok(tooShort.length <= 1, JSON.stringify(tooShort));
  assert.ok(minimums.length === 12);
});

// ---- the overlay draws the rows ----

const tr = (lang, text) => ({ [lang]: { lines: [text], single_line: text, display_text: text, status: 'ok' } });

function fakeVideo(w, h) {
  const v = new FakeElement('video');
  v.getBoundingClientRect = () => ({ left: 0, top: 0, right: w, bottom: h, width: w, height: h, x: 0, y: 0 });
  return v;
}

function newOverlay() {
  const dom = fakeDom();
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/overlay-layout.js', 'lib/overlay-rows.js', 'content-scripts/overlay.js'],
    { window: { innerWidth: 1280, innerHeight: 720, addEventListener() {} }, document: dom.document, ResizeObserver: dom.ResizeObserver });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = 'zh';
  overlay.secondaryLang = 'none';
  let clock = 0;
  overlay._now = () => clock;
  overlay.init();
  overlay.setLanguageStatus('manual', { lang: 'en', name: 'English' });
  overlay.setVideoElement(fakeVideo(960, 540));
  return { overlay, at: (t) => { clock = t; } };
}

const screen = (overlay) => overlay.subtitleStack.children.map((el) => ({
  row: el.dataset.row, kind: ['primary', 'secondary', 'original', 'partial'].find((k) => el.className.split(/\s+/).includes(k)),
  text: el.textContent, draft: el.className.includes('draft'),
}));

test('the overlay keeps the previous final in row 1 while the next line streams in row 2', () => {
  const { overlay, at } = newOverlay();
  at(1); overlay.showDraft('asr_0', tr('zh', '你把'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay), [{ row: '2', kind: 'primary', text: '你把', draft: true }]);
  at(2); overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay), [{ row: '1', kind: 'primary', text: '你把钥匙放在哪里了?', draft: false }]);
  at(2.5); overlay.showPartial('asr_1', 'i will be', 'en', 'manual');
  at(3.5); overlay.showDraft('asr_1', tr('zh', '我会'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay), [
    { row: '1', kind: 'primary', text: '你把钥匙放在哪里了?', draft: false },
    { row: '2', kind: 'primary', text: '我会', draft: true },
  ]);
  // finalised after the hold: it moves up, row 2 clears
  at(4.5); overlay.showTranslation('asr_1', 'I will be back.', tr('zh', '我会回来的。'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay), [{ row: '1', kind: 'primary', text: '我会回来的。', draft: false }]);
});

test('a final that comes too soon waits in row 2 and moves up when the hold ends', () => {
  const { overlay, at } = newOverlay();
  const long = '这是一个相当长的句子需要一些时间来阅读';   // 19 chars: min 1.5 s
  at(10); overlay.showTranslation('asr_0', 'src', tr('zh', long), { sourceLang: 'en', langStatus: 'manual' });
  at(10.6); overlay.showTranslation('asr_1', 'src', tr('zh', '短句'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay).map((l) => [l.row, l.text]), [['1', long], ['2', '短句']]);
  assert.ok(overlay.rows.nextWake() > 10.6);
  at(11.5); overlay._tickRows();
  assert.deepEqual(screen(overlay).map((l) => [l.row, l.text]), [['1', '短句']]);
});

test('each row is one visual row: a long final keeps its newest words with an ellipsis', () => {
  const { overlay, at } = newOverlay();
  const words = [];
  for (let i = 1; i <= 20; i++) words.push('word' + i);
  at(1); overlay.showTranslation('asr_0', 'src', tr('zh', words.join(' ')), { sourceLang: 'en', langStatus: 'manual' });
  const text = screen(overlay)[0].text;
  assert.ok(!text.includes('\n'));
  assert.ok(text.startsWith('…') && text.endsWith('word20'), text);
  assert.ok(text.length <= 42);
});

test('the source row, when on, is a third dimmer row for the current line only', () => {
  const { overlay, at } = newOverlay();
  overlay.setShowOriginal(true);
  at(1); overlay.showTranslation('asr_0', 'Where did you put the keys?', tr('zh', '你把钥匙放在哪里了?'), { sourceLang: 'en', langStatus: 'manual' });
  assert.deepEqual(screen(overlay).map((l) => [l.row, l.kind]), [['1', 'primary'], ['source', 'original']]);
  at(2.5); overlay.showPartial('asr_1', 'i will be', 'en', 'manual');
  assert.deepEqual(screen(overlay).map((l) => [l.row, l.kind, l.text]), [['1', 'primary', '你把钥匙放在哪里了?'], ['source', 'partial', 'i will be']]);
  const css = overlay._getStyles();
  assert.match(css, /row-source[^{]*\{[^}]*opacity:\s*0\.7/);
});

test('the stack reserves two translation rows (plus the source row when on) and no more', () => {
  const { overlay } = newOverlay();
  const font = overlay.roleStyle('primary').fontPx;
  const h = overlay.reservedHeight();
  assert.ok(h >= 2 * font * 1.2 && h < 3 * font * 1.2, `${h} for ${font}px`);
  assert.ok(h <= 0.15 * 540 - 8, `${h} fits the bottom 15% with the margin`);
  overlay.setShowOriginal(true);
  assert.ok(overlay.reservedHeight() > h);
});
