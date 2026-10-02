/**
 * Offscreen document: owns tab-audio capture, the ASR WebSocket and the
 * optional on-device Chrome Translator. Lives as long as capture runs (the
 * service worker may be suspended meanwhile; this page is not).
 *
 * Messages from the service worker (all have target: 'offscreen'):
 *   ASR_START  {streamId, tabId, sourceLang, targetLangs, serverUrl, engine, cloudKeys, translationEngine}
 *   ASR_STOP   {tabId}
 *   ASR_CONFIG {tabId, sourceLang?, targetLangs?}
 *   ASR_STATUS {}
 *   TRANSLATOR_PREPARE {pairs:[[src,tgt],...]}   (download language packs; needs to be relayed from a user gesture page)
 *
 * Messages to the service worker:
 *   ASR_EVENT  {tabId, event}   event = {type:'partial'|'final'|'translation'|'revision'|'lid'|'status'|'error', ...}
 */

const state = {
  tabId: null,
  stream: null,
  playbackCtx: null,
  captureCtx: null,
  workletNode: null,
  ws: null,
  serverUrl: 'ws://127.0.0.1:8765/ws/asr',
  sourceLang: 'auto',
  targetLangs: ['en'],
  translationEngine: 'auto', // auto | chrome | backend
  capturing: false,
  framesSent: 0,
  silentFrames: 0,
  silenceWarned: false,
  lastRms: 0,
  translators: new Map(), // "src>tgt" -> Translator
  chromeTranslatorOk: null,
  latencySamples: [],
};

const FRAME_MS = 40;
const SILENCE_RMS = 0.002;
const SILENCE_WARN_FRAMES = Math.round(10000 / FRAME_MS); // 10 s

function log(...args) {
  console.log('[Offscreen]', ...args);
}

function emit(event) {
  chrome.runtime.sendMessage({ type: 'ASR_EVENT', tabId: state.tabId, event }).catch(() => {});
}

// ---------------------------------------------------------------------------
// Capture
// ---------------------------------------------------------------------------

async function startCapture(msg) {
  if (state.capturing) {
    await stopCapture();
  }
  state.tabId = msg.tabId;
  state.serverUrl = msg.serverUrl || state.serverUrl;
  state.sourceLang = msg.sourceLang || 'auto';
  state.targetLangs = (msg.targetLangs && msg.targetLangs.length) ? msg.targetLangs : ['en'];
  state.translationEngine = msg.translationEngine || 'auto';
  state.framesSent = 0;
  state.silentFrames = 0;
  state.silenceWarned = false;
  state.latencySamples = [];

  // 1. tab audio stream
  state.stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: 'tab',
        chromeMediaSourceId: msg.streamId,
      },
    },
    video: false,
  });

  // 2. keep the user hearing the tab (capture mutes it otherwise), native rate
  state.playbackCtx = new AudioContext();
  state.playbackCtx.createMediaStreamSource(state.stream).connect(state.playbackCtx.destination);

  // 3. 16 kHz analysis graph -> worklet -> websocket
  state.captureCtx = new AudioContext({ sampleRate: 16000 });
  await state.captureCtx.audioWorklet.addModule(chrome.runtime.getURL('pcm-worklet.js'));
  state.workletNode = new AudioWorkletNode(state.captureCtx, 'pcm-frame-processor', {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    processorOptions: { frameMs: FRAME_MS },
  });
  state.workletNode.port.onmessage = onPcmFrame;
  state.captureCtx.createMediaStreamSource(state.stream).connect(state.workletNode);

  // 4. backend
  await connectWs(msg.cloudKeys);
  state.capturing = true;
  emit({ type: 'status', capturing: true, connected: true, sourceLang: state.sourceLang, targetLangs: state.targetLangs });
  log('capture started for tab', state.tabId);
}

function onPcmFrame(e) {
  const { pcm, rms } = e.data;
  state.lastRms = rms;
  if (rms < SILENCE_RMS) {
    state.silentFrames++;
    if (!state.silenceWarned && state.silentFrames >= SILENCE_WARN_FRAMES) {
      state.silenceWarned = true;
      emit({ type: 'status', warning: 'no-audio', message: 'No audio is reaching the capture. Is the video playing? DRM-protected sites (Netflix, Disney+) block tab audio capture.' });
    }
  } else {
    if (state.silenceWarned) emit({ type: 'status', warning: null });
    state.silentFrames = 0;
    state.silenceWarned = false;
  }
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(pcm);
    state.framesSent++;
  }
}

async function stopCapture() {
  state.capturing = false;
  try { if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify({ type: 'stop' })); } catch (_) {}
  await new Promise(r => setTimeout(r, 250));
  try { if (state.ws) state.ws.close(); } catch (_) {}
  state.ws = null;
  try { if (state.workletNode) state.workletNode.disconnect(); } catch (_) {}
  state.workletNode = null;
  try { if (state.captureCtx) await state.captureCtx.close(); } catch (_) {}
  try { if (state.playbackCtx) await state.playbackCtx.close(); } catch (_) {}
  state.captureCtx = null;
  state.playbackCtx = null;
  if (state.stream) state.stream.getTracks().forEach(t => t.stop());
  state.stream = null;
  emit({ type: 'status', capturing: false, connected: false });
  log('capture stopped');
}

// ---------------------------------------------------------------------------
// Backend WebSocket
// ---------------------------------------------------------------------------

function connectWs(cloudKeys) {
  return new Promise((resolve, reject) => {
    const url = `${state.serverUrl}?source_lang=${encodeURIComponent(state.sourceLang)}&target_langs=${encodeURIComponent(state.targetLangs.join(','))}`;
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    let opened = false;
    ws.onopen = () => {
      opened = true;
      if (cloudKeys && Object.keys(cloudKeys).length) {
        ws.send(JSON.stringify({ type: 'config', cloud_keys: cloudKeys }));
      }
      resolve();
    };
    ws.onerror = (err) => {
      if (!opened) reject(new Error('Cannot connect to the backend at ' + state.serverUrl));
    };
    ws.onclose = (ev) => {
      if (state.capturing) {
        emit({ type: 'status', connected: false, warning: 'disconnected', message: `Backend disconnected (${ev.code}).` });
        // retry once after a short delay
        setTimeout(() => { if (state.capturing) connectWs(cloudKeys).catch(() => {}); }, 1500);
      }
    };
    ws.onmessage = (ev) => handleServerMessage(ev.data);
    state.ws = ws;
    setTimeout(() => { if (!opened) { try { ws.close(); } catch (_) {} reject(new Error('Backend connection timeout')); } }, 8000);
  });
}

async function handleServerMessage(data) {
  let msg;
  try { msg = JSON.parse(data); } catch (_) { return; }
  switch (msg.type) {
    case 'partial':
    case 'lid':
    case 'reset':
    case 'pong':
    case 'config_updated':
    case 'stopped':
    case 'ready':
      emit({ ...msg });
      break;
    case 'final': {
      emit({ ...msg });
      // Tier 0: translate on-device if available; the backend also sends
      // its own 'translation' which we suppress when we already handled it.
      if (await translateOnDevice(msg)) msg._handled = true;
      break;
    }
    case 'translation':
    case 'revision':
      if (state.translationEngine === 'chrome' && state.chromeTranslatorOk) return; // on-device already rendered
      if (msg.type === 'translation' && msg.server_ts) {
        const e2e = Date.now() / 1000 - msg.server_ts; // network only; caption-to-glass measured in content script
        state.latencySamples.push(e2e);
      }
      emit({ ...msg });
      break;
    case 'error':
      emit({ type: 'error', error: msg.error });
      break;
    default:
      emit({ ...msg });
  }
}

// ---------------------------------------------------------------------------
// Tier 0: Chrome built-in Translator API (on-device, Chrome 138+)
// ---------------------------------------------------------------------------

async function translatorFor(src, tgt) {
  const key = `${src}>${tgt}`;
  if (state.translators.has(key)) return state.translators.get(key);
  if (typeof Translator === 'undefined') return null;
  try {
    const avail = await Translator.availability({ sourceLanguage: src, targetLanguage: tgt });
    if (avail !== 'available') {
      log(`Translator ${key} is ${avail}; not usable here (needs a user gesture to download)`);
      state.translators.set(key, null);
      return null;
    }
    const t = await Translator.create({ sourceLanguage: src, targetLanguage: tgt });
    state.translators.set(key, t);
    return t;
  } catch (e) {
    log('Translator create failed', key, e);
    state.translators.set(key, null);
    return null;
  }
}

async function translateOnDevice(finalMsg) {
  if (state.translationEngine === 'backend') return false;
  const src = finalMsg.lang;
  const targets = state.targetLangs.filter(l => l !== src);
  if (!targets.length) return false;
  const t0 = performance.now();
  const translations = {};
  for (const tgt of targets) {
    const tr = await translatorFor(src, tgt);
    if (!tr) { state.chromeTranslatorOk = false; return false; }
    try {
      const text = (await tr.translate(finalMsg.text)).trim();
      translations[tgt] = { lines: [text], single_line: text, display_text: text };
    } catch (e) {
      log('on-device translate failed', e);
      state.chromeTranslatorOk = false;
      return false;
    }
  }
  state.chromeTranslatorOk = true;
  emit({
    type: 'translation',
    engine: 'chrome',
    utterance_id: finalMsg.utterance_id,
    cue_id: 'asr_' + finalMsg.utterance_id,
    revision: 1,
    source_text: finalMsg.text,
    source_lang: src,
    translations,
    mt_ms: Math.round(performance.now() - t0),
  });
  return true;
}

async function prepareTranslators(pairs) {
  // Called from a user-gesture context via the popup; here we just report.
  const report = {};
  if (typeof Translator === 'undefined') return { supported: false };
  for (const [src, tgt] of pairs) {
    try {
      report[`${src}>${tgt}`] = await Translator.availability({ sourceLanguage: src, targetLanguage: tgt });
    } catch (e) {
      report[`${src}>${tgt}`] = 'error';
    }
  }
  return { supported: true, report };
}

// ---------------------------------------------------------------------------
// Message router
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.target !== 'offscreen') return false;
  (async () => {
    try {
      switch (msg.type) {
        case 'ASR_START':
          await startCapture(msg);
          sendResponse({ ok: true });
          break;
        case 'ASR_STOP':
          await stopCapture();
          sendResponse({ ok: true });
          break;
        case 'ASR_CONFIG': {
          if (msg.targetLangs) state.targetLangs = msg.targetLangs;
          if (msg.sourceLang) state.sourceLang = msg.sourceLang;
          if (msg.translationEngine) state.translationEngine = msg.translationEngine;
          if (state.ws && state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(JSON.stringify({ type: 'config', source_lang: msg.sourceLang, target_langs: msg.targetLangs, cloud_keys: msg.cloudKeys }));
          }
          sendResponse({ ok: true });
          break;
        }
        case 'ASR_STATUS':
          sendResponse({
            capturing: state.capturing,
            connected: !!(state.ws && state.ws.readyState === WebSocket.OPEN),
            tabId: state.tabId,
            sourceLang: state.sourceLang,
            targetLangs: state.targetLangs,
            framesSent: state.framesSent,
            lastRms: state.lastRms,
            chromeTranslator: state.chromeTranslatorOk,
          });
          break;
        case 'TRANSLATOR_PREPARE':
          sendResponse(await prepareTranslators(msg.pairs || []));
          break;
        default:
          sendResponse({ ok: false, error: 'unknown message ' + msg.type });
      }
    } catch (e) {
      log('error', e);
      emit({ type: 'error', error: e.message || String(e) });
      sendResponse({ ok: false, error: e.message || String(e) });
    }
  })();
  return true; // async response
});

log('offscreen document ready');
