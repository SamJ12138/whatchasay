'use strict';
// Phase 2b Batch 2 (D2): content scripts are injected on demand (activeTab) or registered
// for caption mode; the two must not both run in one page.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ensureInjected } = require('../lib/caption-mode.js');
const { loadScripts } = require('./load.js');

function page({ loadedAfterProbes = Infinity, completeAfterProbes = 0 } = {}) {
  const p = { probes: 0, injected: 0 };
  p.probe = async () => {
    p.probes++;
    return { loaded: p.injected > 0 || p.probes > loadedAfterProbes, state: p.probes > completeAfterProbes ? 'complete' : 'loading' };
  };
  p.inject = async () => { p.injected++; };
  return p;
}
const sleep = async () => {};

test('a page still loading gets its registered scripts, not a second copy', async () => {
  const p = page({ loadedAfterProbes: 2, completeAfterProbes: 3 });
  assert.equal(await ensureInjected({ probe: p.probe, inject: p.inject, sleep }), 'present');
  assert.equal(p.injected, 0);
});

test('a loaded page without the scripts gets them injected once', async () => {
  const p = page({ completeAfterProbes: 0 });
  assert.equal(await ensureInjected({ probe: p.probe, inject: p.inject, sleep }), 'injected');
  assert.equal(p.injected, 1);
});

test('scripts already running are left alone', async () => {
  const p = page({ loadedAfterProbes: 0 });
  assert.equal(await ensureInjected({ probe: p.probe, inject: p.inject, sleep }), 'present');
  assert.equal(p.injected, 0);
});

test('a page that keeps loading past the wait gets the scripts injected', async () => {
  const p = page({ completeAfterProbes: Infinity });
  assert.equal(await ensureInjected({ probe: p.probe, inject: p.inject, sleep, maxWaitMs: 1000, stepMs: 250 }), 'injected');
  assert.equal(p.injected, 1);
});

test('loading the content scripts twice keeps the first backend port', () => {
  const runtime = { connect() { throw new Error('not in this test'); } };
  const ctx = loadScripts(['content-scripts/backend-port.js'], { chrome: { runtime }, document: {} });
  const first = ctx.subtitleWS;
  assert.ok(first, 'no subtitleWS after the first load');
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'content-scripts', 'backend-port.js'), 'utf8'), ctx);
  assert.equal(ctx.subtitleWS, first, 'a second load replaced the port main.js holds');
});
