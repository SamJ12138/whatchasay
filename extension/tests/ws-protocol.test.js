'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseServerMessage } = require('../content-scripts/ws-protocol.js');
const { loadScripts } = require('./load');

const pending = (...ids) => (cid) => ids.includes(cid);

test('result envelope for a waiting request is a reply', () => {
  const r = parseServerMessage(JSON.stringify({ type: 'result', correlation_id: 'a', payload: { cue_id: 'c1' } }), pending('a'));
  assert.equal(r.kind, 'reply');
  assert.equal(r.correlationId, 'a');
  assert.deepEqual(r.payload, { cue_id: 'c1' });
});

test('error envelope for a waiting request is an error with its message', () => {
  const r = parseServerMessage(JSON.stringify({ type: 'error', correlation_id: 'a', payload: { error: 'bad', error_type: 'input_invalid' } }), pending('a'));
  assert.equal(r.kind, 'error');
  assert.equal(r.error, 'bad');
  assert.equal(r.errorType, 'input_invalid');
});

test('revision for a finished request is a push', () => {
  const r = parseServerMessage(JSON.stringify({ type: 'revision', correlation_id: 'old', payload: {} }), pending('a'));
  assert.equal(r.kind, 'push');
  assert.equal(r.message.type, 'revision');
});

test('garbage is invalid, never thrown', () => {
  assert.equal(parseServerMessage('{nope', pending()).kind, 'invalid');
  assert.equal(parseServerMessage('42', pending()).kind, 'invalid');
  assert.equal(parseServerMessage('[1]', pending()).kind, 'invalid');
});

test('the content-script client resolves and rejects pending requests through the parser', async () => {
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'content-scripts/websocket-client.js']);
  const ws = ctx.subtitleWS;
  ws.connected = true;
  ws.ws = { send() {}, readyState: 1 };
  const ok = ws._sendRequest('r1', { type: 'cue' }, 1000);
  const bad = ws._sendRequest('r2', { type: 'config' }, 1000);
  ws._handleMessage(JSON.stringify({ type: 'result', correlation_id: 'r1', payload: { cue_id: 'x' } }));
  ws._handleMessage(JSON.stringify({ type: 'error', correlation_id: 'r2', payload: { error: 'nope', error_type: 'process' } }));
  assert.deepEqual(await ok, { cue_id: 'x' });
  await assert.rejects(bad, (e) => e.message === 'nope' && e.errorType === 'process');
  const pushes = [];
  ws.onPush((m) => pushes.push(m));
  ws._handleMessage(JSON.stringify({ type: 'revision', correlation_id: 'r1', payload: {} }));
  assert.equal(pushes.length, 1);
});
