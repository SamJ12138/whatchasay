'use strict';
const { test, mock } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./load');

function setup(extra = {}) {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  const sent = [];
  const relayed = [];
  const ctx = loadScripts(['obs.js'], {
    setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval,
    setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout,
    addEventListener() {},
    fetch: (url, opts) => { sent.push({ url, opts, body: JSON.parse(opts.body) }); return Promise.resolve({ ok: true }); },
    chrome: { runtime: { sendMessage: (m) => { relayed.push(m); return Promise.resolve(); } } },
    ...extra,
  });
  return { obs: ctx.STObs, sent, relayed };
}

test('records are batched and posted once per 2 s window', (t) => {
  t.after(() => mock.timers.reset());
  const { obs, sent } = setup();
  obs.init({ context: 'test', sessionId: 'sess-1' });
  obs.log('ext_ws', 'success', { duration_ms: 12.345, path: '/ws' });
  obs.log('ext_render', 'fail', { error_type: 'process', error_message: 'boom' });
  assert.equal(sent.length, 0, 'nothing is sent before the flush interval');
  mock.timers.tick(2000);
  assert.equal(sent.length, 1, 'one POST for both records');
  const { url, opts, body } = sent[0];
  assert.equal(url, 'http://127.0.0.1:8765/obs');
  assert.equal(opts.method, 'POST');
  assert.equal(opts.headers['X-Session-Id'], 'sess-1');
  assert.equal(body.events.length, 2);
  const [a, b] = body.events;
  assert.equal(a.stage, 'ext_ws');
  assert.equal(a.duration_ms, 12.3);
  assert.equal(a.session_id, 'sess-1');
  assert.equal(a.layer, 'extension');
  assert.equal(a.context.path, '/ws');
  assert.equal(b.error_type, 'process');
  assert.equal(b.error_message, 'boom');
  mock.timers.tick(2000);
  assert.equal(sent.length, 1, 'empty buffer: no further POST');
});

test('unknown error_type is normalised to unknown; endpoint follows the ws url', (t) => {
  t.after(() => mock.timers.reset());
  const { obs, sent } = setup();
  obs.init({ context: 'test' });
  obs.setEndpointFromWs('ws://localhost:9999/ws/asr?x=1');
  obs.log('ext_ws', 'fail', { error_type: 'weird' });
  obs.flush();
  assert.equal(sent[0].url, 'http://localhost:9999/obs');
  assert.equal(sent[0].body.events[0].error_type, 'unknown');
});

test('content scripts relay batches to the service worker instead of fetching', (t) => {
  t.after(() => mock.timers.reset());
  const { obs, sent, relayed } = setup();
  obs.init({ context: 'content', relay: true });
  obs.log('ext_caption_detect', 'success', {});
  mock.timers.tick(2000);
  assert.equal(sent.length, 0);
  assert.equal(relayed.length, 1);
  assert.equal(relayed[0].type, 'OBS_BATCH');
  assert.equal(relayed[0].events.length, 1);
});

test('buffer is bounded: oldest records dropped and the drop is reported', (t) => {
  t.after(() => mock.timers.reset());
  const { obs, sent } = setup();
  obs.init({ context: 'test' });
  for (let i = 0; i < 505; i++) obs.log('ext_ws_send', 'skip', { i });
  obs.flush();
  const events = sent[0].body.events;
  assert.equal(events.length, 501, '500 kept + one drop notice');
  assert.equal(events[0].context.i, 5, 'the five oldest were dropped');
  const notice = events[events.length - 1];
  assert.equal(notice.stage, 'ext_obs');
  assert.match(notice.error_message, /^5 record\(s\) dropped/);
});

test('a failing network never throws into the caller', (t) => {
  t.after(() => mock.timers.reset());
  const { obs } = setup({ fetch: () => { throw new Error('offline'); } });
  obs.init({ context: 'test' });
  obs.log('ext_ws', 'fail', {});
  assert.doesNotThrow(() => obs.flush());
});
