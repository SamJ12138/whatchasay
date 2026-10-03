'use strict';
// Phase 2b Batch 2 (D2): a fresh install asks for no site access; the caption path is opt-in.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { CAPTION_ORIGINS, CONTENT_SCRIPT_FILES, registrationFor, EXPLANATION } = require('../lib/caption-mode.js');

const EXT = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(EXT, 'manifest.json'), 'utf8'));
// one line ending, so a fixed-width slice of a source holds the same text on a CRLF checkout (Windows CI)
const read = (f) => fs.readFileSync(path.join(EXT, f), 'utf8').replace(/\r\n/g, '\n');

// What the audio path needs: settings, the action-click grant, the offscreen audio
// document, and injecting the overlay into the clicked tab; tab audio capture
// itself is optional, asked for on first use (Chrome words tabCapture as "Read and
// change all your data on all websites", measured with getPermissionWarningsByManifest).
const AUDIO_PATH_PERMISSIONS = ['activeTab', 'offscreen', 'scripting', 'storage'];

test('default permissions are only what the audio path needs', () => {
  assert.deepEqual([...manifest.permissions].sort(), AUDIO_PATH_PERMISSIONS);
  assert.deepEqual(manifest.optional_permissions, ['tabCapture']);
});

test('the popup asks for tab audio capture inside the Start click', () => {
  const src = read('popup/popup.js');
  const handler = src.slice(src.indexOf("btnLive.addEventListener('click'"), src.indexOf("btnPrepareTranslator.addEventListener"));
  const req = handler.indexOf("chrome.permissions.request({ permissions: ['tabCapture'] })");
  assert.ok(req > 0, 'no tabCapture request in the Start handler');
  assert.ok(req < handler.indexOf('await'), 'the request must come before the first await (user gesture)');
});

test('a fresh install asks for no site access', () => {
  assert.equal(manifest.host_permissions, undefined, 'host_permissions are granted at install');
  assert.equal(manifest.content_scripts, undefined, 'declared content_scripts imply host access at install');
});

test('the caption sites are optional host permissions', () => {
  assert.deepEqual(manifest.optional_host_permissions, CAPTION_ORIGINS);
  assert.equal(CAPTION_ORIGINS.length, new Set(CAPTION_ORIGINS).size);
  assert.ok(!CAPTION_ORIGINS.includes('<all_urls>'));
});

test('every site adapter of the subtitle detector is covered by a caption origin', () => {
  const src = read('content-scripts/subtitle-detector.js');
  const block = src.slice(src.indexOf('this.siteAdapters = {'), src.indexOf("'default':"));
  const sites = [...block.matchAll(/'([a-z0-9.-]+\.[a-z]+)': \{/g)].map((m) => m[1]);
  assert.ok(sites.length >= 10, sites.join(','));
  for (const site of sites) {
    const host = site.startsWith('v.') ? site : 'www.' + site;
    const covered = CAPTION_ORIGINS.some((o) => {
      const h = o.replace(/^https:\/\//, '').replace(/\/\*$/, '');
      return h.startsWith('*.') ? (host === h.slice(2) || host.endsWith(h.slice(1))) : host === h;
    });
    assert.ok(covered, site + ' has no caption origin');
  }
});

test('content-script files exist and caption mode registers them only when on and granted', () => {
  for (const f of CONTENT_SCRIPT_FILES) assert.ok(fs.existsSync(path.join(EXT, f)), f);
  assert.equal(registrationFor(false, CAPTION_ORIGINS), null);
  assert.equal(registrationFor(true, []), null);
  const reg = registrationFor(true, ['https://*.youtube.com/*']);
  assert.deepEqual(reg.matches, ['https://*.youtube.com/*']);
  assert.deepEqual(reg.js, CONTENT_SCRIPT_FILES);
  assert.equal(reg.allFrames, true);
});

test('caption mode is off by default in both settings defaults', () => {
  assert.match(read('background.js'), /\n\s*captionMode: false,/);
  assert.match(read('options/options.js'), /\n\s*captionMode: false,/);
});

test('the options page explains what caption mode reads, next to its switch', () => {
  const html = read('options/options.html');
  assert.match(html, /id="captionMode"/);
  assert.match(html, /id="captionModeExplanation"/);
  assert.match(EXPLANATION, /reads the subtitle text/);
});
