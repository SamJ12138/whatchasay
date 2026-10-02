'use strict';
// Follow-up 1: the target-language picker offers only targets that have a translation
// route from the session's spoken language; the others are greyed out with a reason.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const R = require('../lib/target-routes.js');
const EXT = path.resolve(__dirname, '..');

// what /health/json `routes` reports with OPUS-MT (no en/zh/bn -> ko or pt model)
const ROUTES = {
  en: ['zh', 'bn', 'vi', 'ja', 'es', 'fr', 'de', 'ru', 'it'],
  zh: ['en', 'bn', 'vi', 'ja', 'es', 'fr', 'de', 'ru', 'it'],
  bn: ['en', 'zh', 'vi', 'ja', 'es', 'fr', 'de', 'ru', 'it'],
};
const TARGETS = ['en', 'zh', 'bn', 'vi', 'ja', 'ko', 'es', 'fr', 'de', 'ru', 'pt', 'it'];

test('a declared spoken language greys out the targets with no route, with the pair in the reason', () => {
  const a = R.availability(ROUTES, 'en', TARGETS);
  assert.deepEqual(a.ko, { available: false, reason: 'no local model for en->ko' });
  assert.deepEqual(a.pt, { available: false, reason: 'no local model for en->pt' });
  assert.equal(a.zh.available, true);
  assert.equal(a.bn.available, true);
  assert.equal(a.en.available, true, 'the spoken language itself is shown as is, not greyed');
});

test('Auto-detect greys out a target only when no spoken language reaches it', () => {
  const routes = { ...ROUTES, bn: ['en', 'zh'] };  // say vi is reachable from en and zh only
  const a = R.availability(routes, 'auto', TARGETS);
  assert.deepEqual(a.ko, { available: false, reason: 'no local model for en->ko, zh->ko, bn->ko' });
  assert.equal(a.vi.available, true);
  assert.equal(a.vi.reason, 'no local model for bn->vi');  // usable, with a note
});

test('without route information (backend unreachable) nothing is greyed out', () => {
  const a = R.availability(null, 'en', TARGETS);
  for (const t of TARGETS) assert.deepEqual(a[t], { available: true, reason: null });
});

test('the options page applies it to both target pickers when the spoken language or the backend changes', () => {
  const js = fs.readFileSync(path.join(EXT, 'options', 'options.js'), 'utf8');
  const html = fs.readFileSync(path.join(EXT, 'options', 'options.html'), 'utf8');
  assert.match(html, /<script src="\.\.\/lib\/target-routes\.js"><\/script>/);
  assert.match(js, /STTargetRoutes\.availability\(/);
  assert.match(js, /data\.routes/);
  assert.match(js, /asrSourceLang\??\.addEventListener\('change'/);
  for (const id of ['primaryLang', 'secondaryLang']) assert.ok(js.includes(id));
});
