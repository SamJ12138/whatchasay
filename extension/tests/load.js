// Load extension scripts (classic, browser-style) into a fresh vm context.
// globals: extra names visible to the scripts (window, chrome, fetch, ...).
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const EXT = path.resolve(__dirname, '..');

const quietConsole = { log() {}, debug() {}, warn() {}, error() {}, info() {} };

function loadScripts(files, globals = {}) {
  const ctx = {
    console: quietConsole,
    setTimeout, clearTimeout, setInterval, clearInterval,
    crypto: globalThis.crypto, URL, URLSearchParams, Date, Math, JSON, Promise, Error, Map, Set, Array, Object,
    String, Number, Uint8Array, performance: globalThis.performance,
    ...globals,
  };
  ctx.globalThis = ctx;
  if (!('window' in globals)) ctx.window = ctx;
  if (!('self' in globals)) ctx.self = ctx;
  vm.createContext(ctx);
  for (const f of files) {
    vm.runInContext(fs.readFileSync(path.join(EXT, f), 'utf8'), ctx, { filename: f });
  }
  return ctx;
}

module.exports = { loadScripts, EXT, quietConsole };
