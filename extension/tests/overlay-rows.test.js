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
  rows.final('b', 20, 11.0);                         // too soon: a is held; b is on screen in row 2, so a keeps its whole hold
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

test('fast speech: a third final makes the second wait unseen and shortens row 1\'s hold toward the floor', () => {
  const rows = R.createRows();
  rows.final('a', 60, 10.0);                         // nominal 4.0 s: alone, held until 14.0
  assert.equal(rows.nextWake(), null);
  rows.final('b', 30, 10.6);                         // on screen in row 2: a keeps its hold
  assert.deepEqual(view(rows), ['a', 'b:final']);
  assert.equal(rows.nextWake(), 14.0);
  rows.final('c', 20, 11.2);                         // before this batch: a pushed out here, after 1.2 s
  assert.deepEqual(view(rows), ['a', 'c:final']);    // now: c is the newest on screen, b waits unseen...
  assert.deepEqual(rows.view().waiting, ['b']);
  assert.equal(rows.nextWake(), 12.5);               // ...and a's hold is half-way to the floor: 2.5 s
  assert.equal(rows.drops(), 0);
  rows.tick(12.5);
  assert.deepEqual(view(rows), ['b', 'c:final']);    // b next, in order; its 0.6 s in row 2 count: nominal 2.0, until 13.9
  assert.ok(Math.abs(rows.nextWake() - 13.9) < 1e-9, rows.nextWake());
  rows.tick(13.9);
  assert.deepEqual(view(rows), ['c', null]);
});

test('two lines waiting unseen bring the hold to the floor, never under it; they take row 1 in order', () => {
  const rows = R.createRows();
  rows.final('a', 60, 10.0);
  rows.final('b', 30, 10.3);
  rows.final('c', 20, 10.6);                         // b unseen: a's hold 2.5 s
  assert.equal(rows.nextWake(), 12.5);
  rows.final('d', 20, 10.9);                         // b and c unseen: the floor; a has had 0.9 s, so it stays to 11.0
  assert.deepEqual(view(rows), ['a', 'd:final']);
  assert.deepEqual(rows.view().waiting, ['b', 'c']);
  assert.equal(rows.nextWake(), 11.0);
  rows.tick(11.0);
  assert.deepEqual(view(rows), ['b', 'd:final']);    // b: one unseen (c): nominal 2.0 -> 1.5, of which 0.3 s in row 2 earlier: until 12.2
  assert.ok(Math.abs(rows.nextWake() - 12.2) < 1e-9, rows.nextWake());
  rows.tick(12.2);
  assert.deepEqual(view(rows), ['c', 'd:final']);    // c: nothing unseen: nominal 1.5, 0.3 s of it earlier: until 13.4
  assert.ok(Math.abs(rows.nextWake() - 13.4) < 1e-9, rows.nextWake());
  rows.tick(13.4);
  assert.deepEqual(view(rows), ['d', null]);
  assert.equal(rows.drops(), 0);
});

test('a line is dropped only when the floor is reached and the backlog is full', () => {
  const rows = R.createRows({ maxBacklog: 2 });
  rows.final('a', 60, 10.0);
  rows.final('b', 20, 10.2);
  rows.final('c', 20, 10.4);
  rows.final('d', 20, 10.6);                         // a still under the floor; b, c wait; d is the newest
  assert.deepEqual(rows.view().waiting, ['b', 'c']);
  rows.final('e', 20, 10.8);                         // a fourth: the backlog is full, the oldest waiting line goes
  assert.deepEqual(rows.view().waiting, ['c', 'd']);
  assert.deepEqual(view(rows), ['a', 'e:final']);
  assert.equal(rows.drops(), 1);
  assert.equal(R.createRows({ floorS: 0.5 }).minDisplay(10), 1.5);   // the floor caps the scaled hold, not the nominal one
  assert.equal(rows.hold(30, 0), 2);                 // nominal: 30 chars / 15
  assert.equal(rows.hold(30, 1), 1.5);               // one waiting: half-way to the floor
  assert.equal(rows.hold(45, 1), 2);
  assert.equal(rows.hold(45, 2), 1);                 // two waiting: the floor
  assert.equal(rows.hold(45, 5), 1);
  assert.equal(R.createRows({ floorS: 1.2 }).hold(45, 2), 1.2);
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

// The scripted fast-speech sequences: 12 lines, lengths 5-70 chars; a line opens at T, has draft
// text at T+0.3, its final at T+1.0, and the next line opens after a gap: 0.6-2.4 s (the latency
// tests' pace), 0.5 s, 0.2 s, and 0 s (a final every second). The clock ticks every 0.1 s. Before
// the backlog rule (commit 81b940c) a third final inside a hold pushed the oldest line out, however
// briefly it had been seen: drops per 12 lines were 0, 3, 5 and 6; lines shown for less than their
// nominal minimum 0, 3, 5 and 5; none under 1.0 s on these sequences (shortest 1.4 s), but the rule
// allowed it (three finals 0.6 s apart: the first out at 0.6 s). Now the hold moves toward the
// floor with the lines waiting unseen behind row 2 (half-way with one, the floor with two) and a
// line leaves only once it has had that hold: drops 0, 0, 0 and 0; lines under their nominal
// minimum the same 0, 3, 5 and 5 (the shortened hold takes the place of the push-out, and those
// lines had already had 1.4 s or more); none under the floor; a line may wait unseen for a few
// tenths, and is dropped only when more than maxBacklog wait.
function scripted(rows, gapOf) {
  const shown = {};   // cue -> [first, last] seen as a final in row 1 or row 2
  const chars = {};
  const events = [];
  let now = 0;
  let T = 0;
  for (let i = 0; i < 12; i++) {
    const cue = 'l' + i;
    chars[cue] = 5 + (i * 13) % 66;
    events.push([T, () => rows.open(cue, false, now)], [T + 0.3, () => rows.open(cue, true, now)], [T + 1.0, () => rows.final(cue, chars[cue], now)]);
    T += 1.0 + gapOf(i);
  }
  const log = () => {
    const v = rows.view();
    for (const e of [v.row1, v.row2 && v.row2.kind === 'final' ? v.row2 : null]) {
      if (!e) continue;
      shown[e.cueId] = shown[e.cueId] || [now, now];
      shown[e.cueId][1] = now;
    }
  };
  for (let k = 0; now < T + 6; k++) {
    now = Math.round(k * 100) / 1000;
    rows.tick(now);
    log();
    for (const [due, fn] of events) if (Math.abs(due - now) < 1e-6) { fn(); log(); }
  }
  const slack = 0.11;   // one tick
  const lines = Object.keys(chars).map((cue) => ({ cue, visible: shown[cue] ? shown[cue][1] - shown[cue][0] : 0, nominal: rows.minDisplay(chars[cue]) }));
  return {
    lines,
    cutShort: lines.filter((l) => l.visible + slack < l.nominal).length,
    underFloor: lines.filter((l) => l.visible + slack < 1.0).length,
    neverShown: lines.filter((l) => !shown[l.cue]).length,
    drops: rows.drops(),
  };
}

// name, gap between lines, drops before this batch, lines under their nominal minimum before (and now)
const SEQUENCES = [['gaps 0.6-2.4 s', (i) => 0.6 + (i % 4) * 0.6, 0, 0], ['gap 0.5 s', () => 0.5, 3, 3], ['gap 0.2 s', () => 0.2, 5, 5],
                   ['gap 0 s', () => 0.0, 6, 5]];

test('the scripted fast-speech sequences: no line is dropped or shown under the floor; no more are cut short than before', () => {
  assert.equal(SEQUENCES.reduce((n, s) => n + s[2], 0), 14);   // the drops the old rule made on these sequences
  for (const [name, gapOf, , shortBefore] of SEQUENCES) {
    const r = scripted(R.createRows(), gapOf);
    assert.equal(r.drops, 0, name);
    assert.equal(r.underFloor, 0, `${name}: ${JSON.stringify(r.lines)}`);
    assert.equal(r.neverShown, 0, name);
    assert.equal(r.cutShort, shortBefore, `${name}: cut short ${r.cutShort}, before ${shortBefore}: ${JSON.stringify(r.lines)}`);
    assert.equal(r.lines.length, 12);
  }
});

test('a burst of three finals: the first keeps its shortened hold instead of leaving at 0.6 s', () => {
  const rows = R.createRows();
  rows.final('a', 60, 10.0);
  rows.final('b', 20, 10.3);
  rows.final('c', 20, 10.6);                         // before this batch: a was pushed out here, after 0.6 s
  assert.deepEqual(view(rows), ['a', 'c:final']);
  assert.equal(rows.tick(12.4), false);
  assert.equal(rows.tick(12.5), true);               // half-way from 4.0 s to the 1.0 s floor
  assert.deepEqual(view(rows), ['b', 'c:final']);
  assert.equal(rows.drops(), 0);
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

test('a line may wrap once; only beyond two rows does it keep its newest words with an ellipsis', () => {
  const { overlay, at } = newOverlay();
  assert.equal(overlay.settings.rowsPerLine, 2);
  overlay.primaryLang = 'en';                                              // Latin rows of 42 characters
  const two = 'It is too expensive to raise it any larger than this one.';   // 57 chars: two rows of 42
  at(1); overlay.showTranslation('asr_0', 'src', tr('en', two), { sourceLang: 'bn', langStatus: 'manual' });
  let text = screen(overlay)[0].text;
  assert.equal(text.split('\n').length, 2, text);
  assert.ok(!text.includes('…') && text.endsWith('this one.'), text);
  const words = [];
  for (let i = 1; i <= 20; i++) words.push('word' + i);                   // 20 words, 130 chars: over two rows
  at(5); overlay.showTranslation('asr_1', 'src', tr('en', words.join(' ')), { sourceLang: 'bn', langStatus: 'manual' });
  text = screen(overlay).at(-1).text;                                      // asr_0's hold (3.8 s) is over
  assert.equal(text.split('\n').length, 2, text);
  assert.ok(text.startsWith('…') && text.endsWith('word20'), text);
  assert.ok(text.split('\n').every((r) => r.length <= 42));
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

test('the stack reserves two visual rows per line (plus the source row when on) and no more', () => {
  const { overlay } = newOverlay();
  const font = overlay.roleStyle('primary').fontPx;
  const h = overlay.reservedHeight();
  assert.ok(h >= 4 * font * 1.2 && h < 5 * font * 1.2, `${h} for ${font}px`);
  overlay.setShowOriginal(true);
  assert.ok(overlay.reservedHeight() > h);
});

test('the lines pack at the bottom of the reserved box over a video, at its top below one', () => {
  const { overlay } = newOverlay();
  const css = overlay._getStyles();
  // the grid's tracks are sized by their text, so one-row lines sit next to each other and a
  // line that wraps grows into the reserved space; the box itself keeps its height
  assert.match(css, /\.subtitle-stack\s*\{[^}]*grid-template-rows:\s*auto auto;/);
  assert.match(css, /\.subtitle-stack\s*\{[^}]*align-content:\s*end;/);
  assert.match(css, /\.mode-page \.subtitle-stack\s*\{[^}]*align-content:\s*start;/);
});
