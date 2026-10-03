'use strict';
// A11: when a silent tab starts to sound, Chrome's tab capture misses the first few hundred
// milliseconds (the capture attaches to the tab's audio output once it is running). From the
// moment live captions start, the content script keeps an inaudible tone playing on the tab,
// so a video played afterwards is captured from its first sample (lib/capture-prime.js).
const { test } = require('node:test');
const assert = require('node:assert/strict');

const P = require('../lib/capture-prime.js');

/** A fake Web Audio: a context that starts running, or suspended (autoplay policy) until resume(). */
function fakeAudio(initial = 'running') {
  const made = [];
  class Gain { constructor() { this.gain = { value: 1 }; } connect(n) { this.to = n; return n; } }
  class Osc { constructor() { this.frequency = { value: 440 }; this.started = false; } connect(n) { this.to = n; return n; } start() { this.started = true; } stop() { this.stopped = true; } }
  class Ctx {
    constructor() { this.state = initial; this.destination = { name: 'destination' }; this.closed = false; this.listeners = {}; made.push(this); }
    createOscillator() { this.osc = new Osc(); return this.osc; }
    createGain() { this.gainNode = new Gain(); return this.gainNode; }
    async resume() { if (this.allowResume !== false) this.state = 'running'; }
    async close() { this.closed = true; this.state = 'closed'; }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  }
  return { AudioContext: Ctx, made };
}

test('start() plays an inaudible tone into the tab\'s output and reports it running', async () => {
  const audio = fakeAudio('running');
  const logs = [];
  const primer = P.createPrimer({ AudioContext: audio.AudioContext, log: (e, c) => logs.push([e, c]) });
  const r = await primer.start();
  assert.equal(r.state, 'running');
  const ctx = audio.made[0];
  assert.ok(ctx.osc.started);
  assert.ok(ctx.gainNode.gain.value > 0 && ctx.gainNode.gain.value <= 0.0001, 'inaudible, but not silent');
  assert.equal(ctx.gainNode.to, ctx.destination);
  assert.deepEqual(logs[0], ['success', { state: 'running' }]);
  assert.equal(primer.state(), 'running');
  primer.stop();
  assert.ok(ctx.closed);
  assert.equal(primer.state(), null);
});

test('a context the autoplay policy keeps suspended is resumed at the next user gesture, and said so', async () => {
  const audio = fakeAudio('suspended');
  const logs = [];
  const gestures = {};
  const doc = { addEventListener: (t, fn) => { gestures[t] = fn; }, removeEventListener: (t) => { delete gestures[t]; } };
  const primer = P.createPrimer({ AudioContext: audio.AudioContext, document: doc, log: (e, c) => logs.push([e, c]) });
  audio.AudioContext.prototype.resume = async function () { if (this.allow) this.state = 'running'; };
  const r = await primer.start();
  assert.equal(r.state, 'suspended');
  assert.equal(logs[0][0], 'skip');
  assert.equal(logs[0][1].error_type, 'input_invalid');
  assert.match(logs[0][1].error_message, /autoplay|interact/i);
  assert.ok(gestures.pointerdown && gestures.keydown, 'waits for a click or a key on the page');
  const ctx = audio.made[0];
  ctx.allow = true;
  await gestures.pointerdown();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(primer.state(), 'running');
  assert.deepEqual(logs[1], ['success', { state: 'running', after: 'gesture' }]);
  assert.ok(!gestures.pointerdown && !gestures.keydown, 'listened once');
});

test('without Web Audio, or when it throws, start() reports a skip and stop() is harmless', async () => {
  const logs = [];
  const none = P.createPrimer({ AudioContext: undefined, log: (e, c) => logs.push([e, c]) });
  assert.equal((await none.start()).state, null);
  assert.equal(logs[0][0], 'skip');
  none.stop();
  class Broken { constructor() { throw new Error('no audio device'); } }
  const broken = P.createPrimer({ AudioContext: Broken, log: (e, c) => logs.push([e, c]) });
  assert.equal((await broken.start()).state, null);
  assert.equal(logs[1][0], 'fail');
  assert.match(logs[1][1].error_message, /no audio device/);
});

test('start() twice keeps one tone; stop() then start() makes a fresh one', async () => {
  const audio = fakeAudio('running');
  const primer = P.createPrimer({ AudioContext: audio.AudioContext, log() {} });
  await primer.start();
  await primer.start();
  assert.equal(audio.made.length, 1);
  primer.stop();
  await primer.start();
  assert.equal(audio.made.length, 2);
  primer.stop();
});

test('the primer is injected with the overlay scripts, before main.js', () => {
  const { CONTENT_SCRIPT_FILES } = require('../lib/caption-mode.js');
  assert.ok(CONTENT_SCRIPT_FILES.indexOf('lib/capture-prime.js') >= 0, 'lib/capture-prime.js is injected');
  assert.ok(CONTENT_SCRIPT_FILES.indexOf('lib/capture-prime.js') < CONTENT_SCRIPT_FILES.indexOf('content-scripts/main.js'));
});
