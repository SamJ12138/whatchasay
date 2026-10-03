'use strict';
// Overlay batch 2 (docs/overlay/README.md): where the subtitle block goes. Below the video,
// in the page's space, when the video does not fill the viewport; over its bottom, with a
// safe margin, when it is fullscreen or fills the viewport. Above the site's control bar
// when that is showing. Draggable, the position remembered per site origin.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./load.js');
const { fakeDom, FakeElement } = require('./fake-dom.js');

const L = require('../lib/overlay-layout.js');

const VP = { w: 1280, h: 720 };
const box = (x, y, w, h) => ({ x, y, w, h });

// ---- placement: pure ----

test('a video that does not fill the viewport gets the block below its bottom edge, centred on it', () => {
  const p = L.placement({ video: box(320, 100, 640, 360), viewport: VP, fullscreen: false, blockHeight: 60 });
  assert.equal(p.mode, 'page');
  assert.equal(p.top, 460 + p.gap);
  assert.equal(p.left + p.width / 2, 640);       // centred on the video
  assert.ok(p.width <= 640 && p.width >= 0.8 * 640);
  assert.equal(p.bottom, undefined);
});

test('a fullscreen video, or one that fills the viewport, gets the block over its bottom with a margin, inside the bottom 20%', () => {
  for (const input of [
    { video: box(0, 0, 1280, 720), viewport: VP, fullscreen: true, blockHeight: 80 },
    { video: box(0, 20, 1280, 690), viewport: VP, fullscreen: false, blockHeight: 80 },   // fills it without the API
  ]) {
    const p = L.placement(input);
    assert.equal(p.mode, 'overlay');
    const v = input.video;
    assert.ok(p.bottom <= v.y + v.h - 8, 'a safe margin above the bottom edge');
    assert.ok(p.bottom - input.blockHeight >= v.y + v.h * 0.80, 'the block stays within the bottom 20%');
    assert.equal(p.top, undefined);
  }
});

test('no room below the video (it ends at the bottom of the viewport): overlay it instead', () => {
  const p = L.placement({ video: box(0, 300, 1280, 420), viewport: VP, fullscreen: false, blockHeight: 60 });
  assert.equal(p.mode, 'overlay');
  const q = L.placement({ video: box(320, 100, 640, 360), viewport: { w: 1280, h: 480 }, fullscreen: false, blockHeight: 60 });
  assert.equal(q.mode, 'overlay');
});

test('a visible control bar at the bottom of the video lifts the block above it', () => {
  const video = box(0, 0, 1280, 720);
  const free = L.placement({ video, viewport: VP, fullscreen: true, blockHeight: 60 });
  const lifted = L.placement({ video, viewport: VP, fullscreen: true, blockHeight: 60, controls: box(0, 660, 1280, 60) });
  assert.ok(lifted.bottom <= 660 - lifted.gap, `${lifted.bottom} above the controls`);
  assert.ok(lifted.bottom < free.bottom);
  // a bar that does not reach up to the block changes nothing
  const low = L.placement({ video, viewport: VP, fullscreen: true, blockHeight: 60, controls: box(0, 716, 1280, 4) });
  assert.equal(low.bottom, free.bottom);
});

test('the dragged offset moves the block and is clamped to the viewport', () => {
  const base = L.placement({ video: box(320, 100, 640, 360), viewport: VP, fullscreen: false, blockHeight: 60 });
  const moved = L.placement({ video: box(320, 100, 640, 360), viewport: VP, fullscreen: false, blockHeight: 60, offset: { dx: 50, dy: -30 } });
  assert.equal(moved.left, base.left + 50);
  assert.equal(moved.top, base.top - 30);
  const far = L.placement({ video: box(320, 100, 640, 360), viewport: VP, fullscreen: false, blockHeight: 60, offset: { dx: 5000, dy: 5000 } });
  assert.ok(far.left + far.width <= VP.w);
  assert.ok(far.top + 60 <= VP.h);
});

test('without a video the block sits 15% up the viewport, as before', () => {
  const p = L.placement({ video: null, viewport: VP, blockHeight: 60 });
  assert.equal(p.mode, 'viewport');
  assert.equal(p.bottom, 720 * 0.85);
});

// ---- controls: which element over the video's bottom edge is a control bar ----

test('a bottom-anchored, wide, visible element over the video is the control bar; the video and tall panels are not', () => {
  const video = box(0, 0, 1280, 720);
  const picked = L.pickControls([
    { rect: box(0, 0, 1280, 720), opacity: 1, isVideo: true },
    { rect: box(0, 660, 1280, 60), opacity: 1 },           // the bar
    { rect: box(0, 0, 1280, 720), opacity: 1 },            // a full-size gradient / click layer
    { rect: box(1200, 680, 40, 40), opacity: 1 },          // a button
    { rect: box(0, 672, 1280, 48), opacity: 0 },           // a hidden bar
  ], video);
  assert.deepEqual(picked, box(0, 660, 1280, 60));
  assert.equal(L.pickControls([{ rect: box(0, 0, 1280, 720), opacity: 1, isVideo: true }], video), null);
});

// ---- the overlay with these rules ----

const tr = (lang, text) => ({ [lang]: { lines: [text], single_line: text, display_text: text, status: 'ok' } });

function fakeVideo(x, y, w, h) {
  const v = new FakeElement('video');
  v.getBoundingClientRect = () => ({ left: x, top: y, right: x + w, bottom: y + h, width: w, height: h, x, y });
  return v;
}

function newOverlay(stored = {}) {
  const dom = fakeDom();
  const storage = { data: { ...stored }, writes: [] };
  const chrome = { storage: { local: {
    get: (key, cb) => { const out = { [key]: storage.data[key] }; if (cb) cb(out); return Promise.resolve(out); },
    set: (obj, cb) => { Object.assign(storage.data, obj); storage.writes.push(obj); if (cb) cb(); return Promise.resolve(); },
  } } };
  const win = { innerWidth: VP.w, innerHeight: VP.h, addEventListener() {}, location: { origin: 'https://www.youtube.com' },
    requestAnimationFrame: (f) => { f(); return 1; }, cancelAnimationFrame() {} };
  const ctx = loadScripts(['content-scripts/ws-protocol.js', 'lib/overlay-layout.js', 'content-scripts/overlay.js'],
    { window: win, document: Object.assign(dom.document, { fullscreenElement: null, elementsFromPoint: () => [] }),
      ResizeObserver: dom.ResizeObserver, chrome });
  const overlay = ctx.window.subtitleOverlay;
  overlay.primaryLang = 'en';
  overlay.secondaryLang = 'none';
  overlay.init();
  overlay.setLanguageStatus('manual', { lang: 'bn', name: 'Bengali' });
  return { overlay, storage, win, document: dom.document };
}

test('the container is put below a page video and over a fullscreen one', () => {
  const { overlay, document } = newOverlay();
  overlay.showTranslation('asr_0', 'src', tr('en', 'A royal book.'), { sourceLang: 'bn', langStatus: 'manual' });
  overlay.setVideoElement(fakeVideo(320, 100, 640, 360));
  assert.equal(overlay.placementMode, 'page');
  assert.equal(parseFloat(overlay.container.style.top) >= 460, true, overlay.container.style.top);
  assert.equal(overlay.container.style.bottom, 'auto');
  const fs = fakeVideo(0, 0, 1280, 720);
  document.fullscreenElement = fs;
  overlay.setVideoElement(fs);
  assert.equal(overlay.placementMode, 'overlay');
  assert.equal(overlay.container.style.top, 'auto');
  const cssBottom = parseFloat(overlay.container.style.bottom);
  assert.ok(cssBottom >= 8 && cssBottom < 0.15 * 720, overlay.container.style.bottom);
});

test('a drag moves the block and the offset is remembered for the site origin', () => {
  const { overlay, storage } = newOverlay();
  overlay.showTranslation('asr_0', 'src', tr('en', 'A royal book.'), { sourceLang: 'bn', langStatus: 'manual' });
  overlay.setVideoElement(fakeVideo(320, 100, 640, 360));
  const before = { left: parseFloat(overlay.container.style.left), top: parseFloat(overlay.container.style.top) };
  const block = overlay.block;
  const ev = (x, y) => ({ clientX: x, clientY: y, pointerId: 1, button: 0, preventDefault() {}, stopPropagation() {} });
  block.dispatch('pointerdown', ev(600, 480));
  block.dispatch('pointermove', ev(602, 481));   // under the drag threshold: a click, not a drag
  assert.equal(overlay.dragging, false);
  block.dispatch('pointermove', ev(640, 420));
  assert.equal(overlay.dragging, true);
  block.dispatch('pointerup', ev(640, 420));
  assert.equal(parseFloat(overlay.container.style.left), before.left + 40);
  assert.equal(parseFloat(overlay.container.style.top), before.top - 60);
  const saved = JSON.parse(JSON.stringify(storage.data.overlayOffsets));  // objects from the vm context
  assert.deepEqual(saved['https://www.youtube.com'].page, { dx: 40, dy: -60 });
});

test('a remembered offset is applied when the overlay starts on that origin', () => {
  const { overlay } = newOverlay({ overlayOffsets: { 'https://www.youtube.com': { page: { dx: 10, dy: 20 } } } });
  overlay.showTranslation('asr_0', 'src', tr('en', 'A royal book.'), { sourceLang: 'bn', langStatus: 'manual' });
  overlay.setVideoElement(fakeVideo(320, 100, 640, 360));
  const p = L.placement({ video: box(320, 100, 640, 360), viewport: VP, fullscreen: false, blockHeight: overlay.blockHeight() });
  assert.equal(parseFloat(overlay.container.style.left), Math.round(p.left + 10));
  assert.equal(parseFloat(overlay.container.style.top), Math.round(p.top + 20));
});

test('the per-site override names the control bar on YouTube and on the harness pages', () => {
  assert.equal(L.controlsOverride('https://www.youtube.com').selector, '.ytp-chrome-bottom');
  assert.equal(L.controlsOverride('http://127.0.0.1:8123').selector, '#controls');
  assert.equal(L.controlsOverride('https://example.com'), null);
});
