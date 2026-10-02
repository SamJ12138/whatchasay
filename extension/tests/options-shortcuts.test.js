'use strict';
// Phase 3 Batch A: the Options page's shortcut list matches manifest.json (it showed
// Alt+] / Alt+[ and a decrease-font command the manifest never had).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const EXT = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(EXT, 'manifest.json'), 'utf8'));
const html = fs.readFileSync(path.join(EXT, 'options', 'options.html'), 'utf8');

// Page keys handled by the content script itself (not chrome.commands).
const PAGE_KEYS = { 'Alt+E': /e\.altKey && e\.key === 'e'/ };

function shortcutItems() {
  const list = html.slice(html.indexOf('class="keyboard-shortcuts"'), html.indexOf('chrome://extensions/shortcuts'));
  const items = [];
  const re = /<div class="shortcut-item"([^>]*)>\s*<span>([^<]*)<\/span>\s*<div class="shortcut-key">(.*?)<\/div>/gs;
  for (const m of list.matchAll(re)) {
    const command = (m[1].match(/data-command="([^"]+)"/) || [])[1];
    const pageKey = (m[1].match(/data-page-key="([^"]+)"/) || [])[1];
    const keys = [...m[3].matchAll(/<kbd>([^<]*)<\/kbd>/g)].map((k) => k[1]).join('+');
    items.push({ command, pageKey, label: m[2].trim(), keys });
  }
  return items;
}

const asShown = (suggested) => suggested.split('+').map((k) => (k === 'Period' ? '.' : k)).join('+');

test('every manifest command is listed with its suggested key', () => {
  const items = shortcutItems();
  for (const [name, cmd] of Object.entries(manifest.commands)) {
    const item = items.find((i) => i.command === name);
    assert.ok(item, `${name} missing from the options page`);
    assert.equal(item.keys, asShown(cmd.suggested_key.default), name);
  }
});

test('nothing else is listed except page keys the content script handles', () => {
  const main = fs.readFileSync(path.join(EXT, 'content-scripts', 'main.js'), 'utf8');
  for (const item of shortcutItems()) {
    if (item.command) {
      assert.ok(manifest.commands[item.command], `${item.command} is not a manifest command`);
    } else {
      assert.ok(PAGE_KEYS[item.pageKey], `unknown shortcut row: ${JSON.stringify(item)}`);
      assert.equal(item.keys, item.pageKey);
      assert.match(main, PAGE_KEYS[item.pageKey], `${item.pageKey} is not handled by the content script`);
    }
  }
  assert.ok(!/<kbd>\[<\/kbd>|<kbd>\]<\/kbd>/.test(html), 'stale Alt+[ / Alt+] keys');
});

test('the page shows the keys actually bound when chrome.commands is available', () => {
  const js = fs.readFileSync(path.join(EXT, 'options', 'options.js'), 'utf8');
  assert.match(js, /chrome\.commands\.getAll\(\)/);
});
