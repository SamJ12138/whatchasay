'use strict';
// Phase 2b Batch 0: the caption path across service-worker restarts (docs/architecture-notes.md).
// The browser test is backend/scripts/e2e_extension.py --path sw-idle (real idle timeout).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { TabConnections } = require('../lib/tab-connections.js');
const { BackendPort } = require('../content-scripts/backend-port.js');

// Like a chrome.runtime.Port: a message is handed to the listeners present when it
// is dispatched; with none, it is gone.
function fakePort(tabId, frameId = 0) {
  const port = {
    sender: { tab: { id: tabId }, frameId },
    posted: [], disconnected: false, msgListeners: [], discListeners: [],
    postMessage(m) { if (this.disconnected) throw new Error('disconnected'); this.posted.push(m); },
    disconnect() { this.disconnected = true; },
    onMessage: { addListener(fn) { port.msgListeners.push(fn); } },
    onDisconnect: { addListener(fn) { port.discListeners.push(fn); } },
    deliver(m) { return Promise.all(port.msgListeners.map(fn => fn(m))); },
    closeFromOtherSide() { port.disconnected = true; port.discListeners.forEach(fn => fn()); },
  };
  return port;
}

function fakeSockets() {
  const made = [];
  const create = () => {
    const s = {
      cb: {}, connectedFlag: false,
      onConnected(f) { this.cb.connected = f; }, onDisconnected(f) { this.cb.disconnected = f; },
      onPush(f) { this.cb.push = f; }, onError(f) { this.cb.error = f; },
      isConnected() { return this.connectedFlag; },
      async connect(url) { this.url = url; this.connectedFlag = true; this.cb.connected && this.cb.connected(); },
      disconnect() { this.closed = true; },
      async translateCue(cue) { return { cue_id: cue.cueId, translations: {} }; },
    };
    made.push(s);
    return s;
  };
  return { create, made };
}

const tick = () => new Promise(r => setImmediate(r));

test('a restarted worker keeps the first message of a port that connects while it restores enabled tabs', async () => {
  // The worker was stopped (idle timeout); the tab's port reopens and wakes it. Its
  // first message ({op:'connect'}) is dispatched right after onConnect, while the
  // worker is still reading the enabled tab ids from chrome.storage.session.
  const sockets = fakeSockets();
  const tc = new TabConnections({ createSocket: sockets.create, getServerUrl: async () => 'ws://127.0.0.1:8765/ws' });
  let restored;
  tc.restoreFrom(new Promise((r) => { restored = r; }));
  const port = fakePort(7);
  const attached = tc.attachPort(port);
  await port.deliver({ op: 'connect', sessionId: 'cs-7' });
  restored([7]);
  await attached;
  await tick();
  assert.equal(sockets.made.length, 1, 'the connect message was dropped: no socket for the tab');
  assert.deepEqual(port.posted.at(-1), { op: 'state', connected: true });
  // and requests that follow are answered over the new socket
  await port.deliver({ op: 'request', id: 1, method: 'translateCue', args: [{ cueId: 'c1', text: 'Hi' }, {}] });
  await tick();
  assert.deepEqual(port.posted.at(-1), { op: 'reply', id: 1, ok: true, payload: { cue_id: 'c1', translations: {} } });
});

test('a port refused after the restore drops its early messages and opens no socket', async () => {
  const sockets = fakeSockets();
  const tc = new TabConnections({ createSocket: sockets.create });
  let restored;
  tc.restoreFrom(new Promise((r) => { restored = r; }));
  const port = fakePort(8);
  const attached = tc.attachPort(port);
  await port.deliver({ op: 'connect' });
  restored([]); // tab 8 is not enabled
  await attached;
  await tick();
  assert.equal(sockets.made.length, 0);
  assert.equal(port.disconnected, true);
});

test('a port that closes before the restore finishes is not attached', async () => {
  const sockets = fakeSockets();
  const tc = new TabConnections({ createSocket: sockets.create });
  let restored;
  tc.restoreFrom(new Promise((r) => { restored = r; }));
  const port = fakePort(9);
  const attached = tc.attachPort(port);
  await port.deliver({ op: 'connect' });
  port.closeFromOtherSide();
  restored([9]);
  await attached;
  await tick();
  assert.equal(sockets.made.length, 0);
  assert.equal(tc.conns.size, 0);
});

test('the content side keeps the worker awake and reopens its port when the worker goes away', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const ports = [];
  const runtime = { connect() { const p = fakePort(1); ports.push(p); return p; }, lastError: null };
  const bp = new BackendPort(runtime);
  bp.connect();
  assert.equal(ports.length, 1);
  assert.deepEqual(ports[0].posted[0].op, 'connect');
  // keepalive: a port message resets Chrome's 30 s idle timer; one goes out every 20 s
  t.mock.timers.tick(20000);
  assert.equal(ports[0].posted.filter(m => m.op === 'keepalive').length, 1);
  t.mock.timers.tick(20000);
  assert.equal(ports[0].posted.filter(m => m.op === 'keepalive').length, 2);
  // the worker is stopped anyway (Chrome update, crash, throttled timers): the port closes
  ports[0].closeFromOtherSide();
  assert.equal(bp.isConnected(), false);
  t.mock.timers.tick(1000);
  assert.equal(ports.length, 2, 'port not reopened after the worker went away');
  assert.equal(ports[1].posted[0].op, 'connect');
  // a refused port (tab disabled) is not reopened
  ports[1].msgListeners.forEach(fn => fn({ op: 'state', connected: false, reason: 'disabled' }));
  ports[1].closeFromOtherSide();
  t.mock.timers.tick(60000);
  assert.equal(ports.length, 2);
});
