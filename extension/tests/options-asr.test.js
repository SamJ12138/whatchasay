'use strict';
// Phase 3: the speech-engine choice offers only engines the backend has. The
// accuracy-mode Whisper engine was never implemented (docs/observations.md A5; the
// backend's registry stub is gone), so choosing it silently ran the Zipformer anyway.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const EXT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(EXT, 'options', 'options.html'), 'utf8');
const BACKEND_ENGINES = ['auto', 'sherpa-zipformer'];

test('the ASR engine select lists only engines the backend has', () => {
  const select = html.slice(html.indexOf('<select id="asrEngine">'), html.indexOf('</select>', html.indexOf('<select id="asrEngine">')));
  const values = [...select.matchAll(/<option value="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(values, BACKEND_ENGINES);
});

test('no extension source mentions the Whisper or cloud ASR engines', () => {
  for (const f of ['background.js', 'options/options.js', 'options/options.html', 'popup/popup.html', 'popup/popup.js']) {
    const src = fs.readFileSync(path.join(EXT, f), 'utf8');
    assert.ok(!/whisper/i.test(src), `${f} mentions Whisper`);
  }
});
