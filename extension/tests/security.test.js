'use strict';
// Batch 3: E16 (frame messages), E17 (connect only on an enabled tab), W8 (socket in the worker).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { trustedFrameMessage } = require('../content-scripts/guards.js');
const { TabConnections } = require('../lib/tab-connections.js');
const { BackendPort } = require('../content-scripts/backend-port.js');

const PAGE = 'https://www.example-video.com';
const cueMsg = (origin, data) => ({ origin, data: data || { __subtrans: 'cue', cue: { cueId: 'c1', text: 'Hello' } } });

// ---------------------------------------------------------------- E16

test('postMessage cues are accepted only from the page\'s own origin', () => {
  assert.equal(trustedFrameMessage(cueMsg(PAGE), PAGE), true);
  assert.equal(trustedFrameMessage(cueMsg('https://ads.evil.example'), PAGE), false);
  assert.equal(trustedFrameMessage(cueMsg('null'), 'null'), false, 'opaque origins are never trusted');
  assert.equal(trustedFrameMessage(cueMsg(PAGE, { __subtrans: 'cue', cue: { text: 42 } }), PAGE), false);
  assert.equal(trustedFrameMessage(cueMsg(PAGE, { __subtrans: 'other', cue: { text: 'x' } }), PAGE), false);
  assert.equal(trustedFrameMessage(cueMsg(PAGE, 'string'), PAGE), false);
});

// ---------------------------------------------------------------- fakes

function fakePort(tabId, frameId = 0) {
  const port = {
    sender: tabId == null ? {} : { tab: { id: tabId }, frameId },
    posted: [], disconnected: false, msgListeners: [], discListeners: [],
    postMessage(m) { if (this.disconnected) throw new Error('disconnected'); this.posted.push(m); },
    disconnect() { this.disconnected = true; },
    onMessage: { addListener(fn) { port.msgListeners.push(fn); } },
    onDisconnect: { addListener(fn) { port.discListeners.push(fn); } },
    deliver(m) { return Promise.all(port.msgListeners.map(fn => fn(m))); },
    closeFromOtherSide() { port.discListeners.forEach(fn => fn()); },
  };
  return port;
}

function fakeSocketFactory() {
  const made = [];
  const create = () => {
    const s = {
      sessionId: null, url: null, connectedFlag: false, closed: false, calls: [],
      cb: {},
      onConnected(f) { this.cb.connected = f; }, onDisconnected(f) { this.cb.disconnected = f; },
      onPush(f) { this.cb.push = f; }, onError(f) { this.cb.error = f; },
      isConnected() { return this.connectedFlag; },
      async connect(url) { this.url = url; this.connectedFlag = true; this.cb.connected && this.cb.connected(); },
      disconnect() { this.closed = true; },
      async translateCue(cue, opts) { this.calls.push(['translateCue', cue, opts]); return { cue_id: cue.cueId, translations: {} }; },
      async updateConfig() { const e = new Error('config not applied'); e.errorType = 'input_invalid'; throw e; },
    };
    made.push(s);
    return s;
  };
  return { create, made };
}

const tick = () => new Promise(r => setImmediate(r));

// ---------------------------------------------------------------- E17 / W8 (worker side)

test('a tab that was not enabled gets no backend socket', async () => {
  const f = fakeSocketFactory();
  const tc = new TabConnections({ createSocket: f.create });
  const port = fakePort(7);
  await tc.attachPort(port);
  await port.deliver({ op: 'connect', sessionId: 'cs-1' });
  assert.equal(f.made.length, 0);
  assert.deepEqual(port.posted[0], { op: 'state', connected: false, reason: 'disabled' });
  assert.equal(port.disconnected, true);
});

test('sub-frames cannot open a backend socket even on an enabled tab', async () => {
  const f = fakeSocketFactory();
  const tc = new TabConnections({ createSocket: f.create });
  tc.setEnabled(7, true);
  const port = fakePort(7, 3);
  await tc.attachPort(port);
  assert.equal(f.made.length, 0);
  assert.equal(port.disconnected, true);
});

test('an enabled tab gets one socket in the worker, carrying its session id', async () => {
  const f = fakeSocketFactory();
  const persisted = [];
  const tc = new TabConnections({ createSocket: f.create, getServerUrl: async () => 'ws://127.0.0.1:8765/ws', persist: (ids) => persisted.push(ids) });
  tc.setEnabled(7, true);
  assert.deepEqual(persisted.at(-1), [7]);
  const port = fakePort(7);
  await tc.attachPort(port);
  await port.deliver({ op: 'connect', sessionId: 'cs-1' });
  assert.equal(f.made.length, 1);
  assert.equal(f.made[0].sessionId, 'cs-1');
  assert.equal(f.made[0].url, 'ws://127.0.0.1:8765/ws');
  assert.deepEqual(port.posted.at(-1), { op: 'state', connected: true });

  await port.deliver({ op: 'request', id: 1, method: 'translateCue', args: [{ cueId: 'c1', text: 'Hi' }, { targetLanguages: ['zh'] }] });
  assert.deepEqual(port.posted.at(-1), { op: 'reply', id: 1, ok: true, payload: { cue_id: 'c1', translations: {} } });
  await port.deliver({ op: 'request', id: 2, method: 'updateConfig', args: [{}] });
  assert.deepEqual(port.posted.at(-1), { op: 'reply', id: 2, ok: false, error: 'config not applied', errorType: 'input_invalid' });
  await port.deliver({ op: 'request', id: 3, method: 'constructor', args: [] });
  assert.equal(port.posted.at(-1).ok, false, 'only the request methods are callable');

  f.made[0].cb.push({ type: 'revision', payload: {} });
  assert.deepEqual(port.posted.at(-1), { op: 'push', message: { type: 'revision', payload: {} } });
});

test('disabling a tab or closing its page closes the socket', async () => {
  const f = fakeSocketFactory();
  const tc = new TabConnections({ createSocket: f.create });
  tc.setEnabled(7, true);
  const port = fakePort(7);
  await tc.attachPort(port);
  await port.deliver({ op: 'connect' });
  tc.setEnabled(7, false);
  assert.equal(f.made[0].closed, true);
  assert.equal(port.disconnected, true);

  tc.setEnabled(8, true);
  const port2 = fakePort(8);
  await tc.attachPort(port2);
  await port2.deliver({ op: 'connect' });
  port2.closeFromOtherSide(); // navigation / tab closed
  assert.equal(f.made[1].closed, true);
  tc.forget(8);
  assert.equal(tc.isEnabled(8), false);
});

test('a restarted worker restores enabled tabs before answering ports', async () => {
  const f = fakeSocketFactory();
  const tc = new TabConnections({ createSocket: f.create });
  let release;
  tc.restoreFrom(new Promise(r => { release = r; }));
  const port = fakePort(9);
  const attached = tc.attachPort(port);
  release([9]);
  await attached;
  await port.deliver({ op: 'connect' });
  assert.equal(f.made.length, 1);
});

// ---------------------------------------------------------------- content side

function fakeRuntime() {
  const rt = { ports: [], connect() { const p = fakePort(1); p.sentByClient = []; p.postMessage = (m) => p.sentByClient.push(m); rt.ports.push(p); return p; } };
  return rt;
}

test('BackendPort opens nothing until connect(), then correlates replies', async () => {
  const rt = fakeRuntime();
  const bp = new BackendPort(rt);
  await assert.rejects(bp.translateCue({ text: 'x' }), /Not connected/);
  assert.equal(rt.ports.length, 0, 'no port (and so no socket) before connect()');
  const states = [];
  bp.onConnected(() => states.push('up'));
  bp.onDisconnected(() => states.push('down'));
  await bp.connect('ws://127.0.0.1:8765/ws');
  const port = rt.ports[0];
  assert.equal(port.sentByClient[0].op, 'connect');
  port.msgListeners.forEach(fn => fn({ op: 'state', connected: true }));
  const p = bp.translateCue({ cueId: 'c1', text: 'Hello' }, { targetLanguages: ['zh'] });
  const req = port.sentByClient.at(-1);
  assert.equal(req.method, 'translateCue');
  port.msgListeners.forEach(fn => fn({ op: 'reply', id: req.id, ok: true, payload: { cue_id: 'c1' } }));
  assert.deepEqual(await p, { cue_id: 'c1' });
  const q = bp.updateConfig({});
  const req2 = port.sentByClient.at(-1);
  port.msgListeners.forEach(fn => fn({ op: 'reply', id: req2.id, ok: false, error: 'bad', errorType: 'input_invalid' }));
  await assert.rejects(q, (e) => e.message === 'bad' && e.errorType === 'input_invalid');
  bp.disconnect();
  assert.deepEqual(states, ['up', 'down']);
});

test('BackendPort stops retrying when the worker says the tab is disabled', async () => {
  const rt = fakeRuntime();
  const bp = new BackendPort(rt);
  await bp.connect();
  const port = rt.ports[0];
  port.msgListeners.forEach(fn => fn({ op: 'state', connected: false, reason: 'disabled' }));
  port.discListeners.forEach(fn => fn());
  await tick();
  assert.equal(bp.getStats().refused, 'disabled');
  assert.equal(bp.retryTimer === null || bp.port === null, true);
  bp.disconnect();
});
