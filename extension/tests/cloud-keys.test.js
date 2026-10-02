'use strict';
// Phase 2b Batch 4 (D4): API keys never live in the extension and never travel over ws/HTTP.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { stripCloudKeys, WHERE } = require('../lib/settings-migration.js');

const EXT = path.resolve(__dirname, '..');

function sources(dir = EXT, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (!['tests', 'node_modules', 'icons'].includes(e.name)) sources(p, out);
    } else if (/\.(js|html)$/.test(e.name)) {
      out.push(p);
    }
  }
  return out;
}

test('migration deletes stored keys and leaves a notice saying where they go now', () => {
  const old = {
    primaryLang: 'en',
    cloudKeys: { google_api_key: 'g-secret', groq_api_key: 'q-secret', azure_translator_key: '', azure_translator_region: 'eastus', refiner_provider: 'groq' },
  };
  const { settings, removed } = stripCloudKeys(old);
  assert.equal(removed, 2);
  assert.equal('cloudKeys' in settings, false);
  assert.equal(settings.keysRemovedNotice.count, 2);
  assert.equal(settings.primaryLang, 'en');
  assert.ok(!JSON.stringify(settings).includes('secret'));
  assert.match(WHERE, /backend\/\.env/);
  assert.equal('cloudKeys' in old, true, 'the input object is not mutated');
});

test('settings without keys are left alone; an empty key set leaves no notice', () => {
  const s = { primaryLang: 'zh' };
  assert.deepEqual(stripCloudKeys(s), { settings: s, removed: 0 });
  const { settings, removed } = stripCloudKeys({ cloudKeys: { google_api_key: '' } });
  assert.equal(removed, 0);
  assert.equal('keysRemovedNotice' in settings, false);
  assert.equal('cloudKeys' in settings, false);
});

test('no extension code stores, reads or sends API keys', () => {
  const offenders = [];
  for (const f of sources()) {
    const rel = path.relative(EXT, f).replace(/\\/g, '/');
    if (rel === 'lib/settings-migration.js') continue; // the one place that deletes them
    const src = fs.readFileSync(f, 'utf8');
    for (const needle of ['cloud_keys', 'cloudKeys', '_api_key', 'type="password"', 'gladiaKey', 'googleKey', 'groqKey', 'geminiKey']) {
      if (src.includes(needle)) offenders.push(`${rel}: ${needle}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test('the settings defaults no longer carry a key object', () => {
  for (const f of ['background.js', 'options/options.js']) {
    assert.doesNotMatch(fs.readFileSync(path.join(EXT, f), 'utf8'), /cloudKeys:\s*\{\}/, f);
  }
});
