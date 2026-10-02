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
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/websocket-client.js']);
  const ws = new ctx.SubtitleWebSocket();
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

// ---- Batch 2: errors are surfaced, untranslated/error targets are not shown as translations

test('an error envelope with no waiting request is still an error (surfaced, not a push)', () => {
  const r = parseServerMessage(JSON.stringify({ type: 'error', correlation_id: 'unknown', payload: { error: 'Invalid JSON', error_type: 'parse' } }), pending('a'));
  assert.equal(r.kind, 'error');
  assert.equal(r.correlationId, null);
  assert.equal(r.errorType, 'parse');
});

test('a legacy result envelope carrying {status:"error"} is treated as an error', () => {
  const r = parseServerMessage(JSON.stringify({ type: 'result', correlation_id: 'a', payload: { status: 'error', error: 'database is locked' } }), pending('a'));
  assert.equal(r.kind, 'error');
  assert.equal(r.error, 'database is locked');
});

test('usableTranslation: ok and fallback are shown, untranslated and error are not', () => {
  const { usableTranslation, allTargetsOk } = require('../content-scripts/ws-protocol.js');
  assert.equal(usableTranslation({ single_line: 'x', status: 'ok' }), true);
  assert.equal(usableTranslation({ single_line: 'x', status: 'fallback' }), true);
  assert.equal(usableTranslation({ single_line: 'x' }), true, 'older servers send no status');
  assert.equal(usableTranslation({ single_line: 'Where?', status: 'untranslated' }), false);
  assert.equal(usableTranslation({ single_line: '', status: 'error' }), false);
  assert.equal(usableTranslation(undefined), false);
  assert.equal(allTargetsOk({ zh: { status: 'ok' }, bn: { status: 'ok' } }), true);
  assert.equal(allTargetsOk({ zh: { status: 'ok' }, bn: { status: 'fallback' } }), false);
});

test('the client reports an unsolicited error envelope through onError', () => {
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/websocket-client.js']);
  const ws = new ctx.SubtitleWebSocket();
  const errors = [];
  ws.onError((e) => errors.push(e));
  ws._handleMessage(JSON.stringify({ type: 'error', correlation_id: 'unknown', payload: { error: 'Invalid JSON', error_type: 'parse' } }));
  assert.equal(errors.length, 1);
  assert.equal(errors[0].message, 'Invalid JSON');
  assert.equal(errors[0].errorType, 'parse');
});
