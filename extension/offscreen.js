/**
 * Offscreen document: owns tab-audio capture, the ASR WebSocket and the
 * optional on-device Chrome Translator. Lives as long as capture runs (the
 * service worker may be suspended meanwhile; this page is not).
 *
 * Messages from the service worker (all have target: 'offscreen'):
 *   ASR_START  {streamId, tabId, sourceLang, targetLangs, serverUrl, engine, translationEngine}
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

const obs = globalThis.STObs;
obs.init({ context: 'offscreen' });
// Per-frame stage: log one summary line per 100 frames sent, and per 100 dropped.
const frameStats = { sent: 0, sentBytes: 0, dropped: 0, relayFails: 0 };

function log(...args) {
  console.log('[Offscreen]', ...args);
}

function emit(event) {
  chrome.runtime.sendMessage({ type: 'ASR_EVENT', tabId: state.tabId, event }).catch((e) => {
    frameStats.relayFails++;
    if (frameStats.relayFails === 1 || frameStats.relayFails % 100 === 0) {
      obs.log('ext_relay', 'fail', { error_type: 'process', error_message: e.message || String(e), hop: 'offscreen->sw', msg_type: event && event.type, failures_so_far: frameStats.relayFails });
    }
  });
}

function wsErrorType(msg) {
  return /timeout/i.test(msg) ? 'timeout' : 'process';
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
  obs.setSession(msg.sessionId || obs.newSessionId('asr-' + msg.tabId));
  obs.setEndpointFromWs(state.serverUrl);
  Object.assign(frameStats, { sent: 0, sentBytes: 0, dropped: 0, relayFails: 0 });
  const t0 = performance.now();
  let step = 'getUserMedia';
  try {
    await startCaptureSteps(msg, (s) => { step = s; });
  } catch (e) {
    obs.log('ext_capture', 'fail', { duration_ms: performance.now() - t0, step, error_type: step === 'connectWs' ? wsErrorType(e.message || '') : 'process', error_message: e.message || String(e) });
    throw e;
  }
  obs.log('ext_capture', 'success', { duration_ms: performance.now() - t0, step: 'offscreen', tab_id: state.tabId });
}

async function startCaptureSteps(msg, setStep) {
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
  setStep('audioGraph');
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
  setStep('connectWs');
  await connectWs();
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
      obs.log('ext_capture', 'fail', { error_type: 'input_invalid', error_message: 'no audio above RMS ' + SILENCE_RMS + ' for 10 s', warning: 'no-audio' });
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
    frameStats.sent++;
    frameStats.sentBytes += pcm.byteLength || 0;
    if (frameStats.sent % 100 === 0) {
      obs.log('ext_ws_send', 'success', { summary: true, count: 100, total: frameStats.sent, bytes: frameStats.sentBytes, buffered_amount: state.ws.bufferedAmount });
      frameStats.sentBytes = 0;
    }
  } else {
    // Frame dropped: socket not open (connecting / reconnecting / closed).
    frameStats.dropped++;
    if (frameStats.dropped === 1 || frameStats.dropped % 100 === 0) {
      obs.log('ext_ws_send', 'skip', { error_type: 'process', error_message: 'socket not open; audio frame dropped', dropped_so_far: frameStats.dropped, ws_state: state.ws ? state.ws.readyState : null });
    }
  }
}

async function stopCapture() {
  state.capturing = false;
  const errs = [];
  try { if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify({ type: 'stop' })); } catch (e) { errs.push('stop msg: ' + e.message); }
  await new Promise(r => setTimeout(r, 250));
  try { if (state.ws) state.ws.close(); } catch (e) { errs.push('ws close: ' + e.message); }
  state.ws = null;
  try { if (state.workletNode) state.workletNode.disconnect(); } catch (e) { errs.push('worklet: ' + e.message); }
  state.workletNode = null;
  try { if (state.captureCtx) await state.captureCtx.close(); } catch (e) { errs.push('captureCtx: ' + e.message); }
  try { if (state.playbackCtx) await state.playbackCtx.close(); } catch (e) { errs.push('playbackCtx: ' + e.message); }
  state.captureCtx = null;
  state.playbackCtx = null;
  if (state.stream) state.stream.getTracks().forEach(t => t.stop());
  state.stream = null;
  emit({ type: 'status', capturing: false, connected: false });
  log('capture stopped');
  obs.log('ext_capture', errs.length ? 'fail' : 'success', {
    action: 'stop', frames_sent: frameStats.sent, frames_dropped: frameStats.dropped,
    error_type: errs.length ? 'unknown' : null, error_message: errs.length ? errs.join('; ') : null,
  });
  obs.flush();
}

// ---------------------------------------------------------------------------
// Backend WebSocket
// ---------------------------------------------------------------------------

function connectWs() {
  return new Promise((resolve, reject) => {
    const sid = obs.getSession();
    const url = `${state.serverUrl}?source_lang=${encodeURIComponent(state.sourceLang)}&target_langs=${encodeURIComponent(state.targetLangs.join(','))}` +
      (sid ? `&session_id=${encodeURIComponent(sid)}` : '');
    const t0 = performance.now();
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    let opened = false;
    let settled = false;
    ws.onopen = () => {
      opened = true;
      settled = true;
      obs.log('ext_ws', 'success', { duration_ms: performance.now() - t0, path: '/ws/asr' });
      resolve();
    };
    ws.onerror = (err) => {
      if (!opened && !settled) {
        settled = true;
        obs.log('ext_ws', 'fail', { duration_ms: performance.now() - t0, path: '/ws/asr', error_type: 'process', error_message: 'Cannot connect to the backend at ' + state.serverUrl });
      }
      if (!opened) reject(new Error('Cannot connect to the backend at ' + state.serverUrl));
    };
    ws.onclose = (ev) => {
      if (opened) {
        const normal = ev.code === 1000 || ev.code === 1001 || ev.code === 1005;
        obs.log('ext_ws', normal ? 'success' : 'fail', { action: 'close', path: '/ws/asr', close_code: ev.code, capturing: state.capturing, error_type: normal ? null : 'process', error_message: normal ? null : 'socket closed with code ' + ev.code });
      }
      if (state.capturing) {
        emit({ type: 'status', connected: false, warning: 'disconnected', message: `Backend disconnected (${ev.code}).` });
        // retry once after a short delay
        obs.log('ext_ws', 'start', { action: 'reconnect', path: '/ws/asr', delay_ms: 1500, after_close_code: ev.code });
        setTimeout(() => { if (state.capturing) connectWs().catch(() => {}); }, 1500);  // connect failure logged above
      }
    };
    ws.onmessage = (ev) => handleServerMessage(ev.data);
    state.ws = ws;
    setTimeout(() => {
      if (!opened) {
        if (!settled) {
          settled = true;
          obs.log('ext_ws', 'fail', { duration_ms: performance.now() - t0, path: '/ws/asr', error_type: 'timeout', error_message: 'Backend connection timeout (8 s)' });
        }
        try { ws.close(); } catch (_) {}
        reject(new Error('Backend connection timeout'));
      }
    }, 8000);
  });
}

async function handleServerMessage(data) {
  let msg;
  try { msg = JSON.parse(data); } catch (e) {
    obs.log('ext_ws_receive', 'fail', { error_type: 'parse', error_message: e.message, bytes: data && data.length });
    return;
  }
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
    case 'draft':
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
      obs.log('ext_ws_receive', 'fail', { error_type: 'process', error_message: 'backend error: ' + msg.error, path: '/ws/asr' });
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
      obs.log('ext_translate_ondevice', 'skip', { error_type: 'process', error_message: `Translator ${key} is ${avail}`, pair: key });
      state.translators.set(key, null);
      return null;
    }
    const t = await Translator.create({ sourceLanguage: src, targetLanguage: tgt });
    state.translators.set(key, t);
    return t;
  } catch (e) {
    log('Translator create failed', key, e);
    obs.log('ext_translate_ondevice', 'fail', { error_type: 'process', error_message: 'Translator create: ' + (e.message || e), pair: key });
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
      obs.log('ext_translate_ondevice', 'fail', { duration_ms: performance.now() - t0, error_type: 'process', error_message: e.message || String(e), pair: `${src}>${tgt}`, utterance_id: finalMsg.utterance_id });
      state.chromeTranslatorOk = false;
      return false;
    }
  }
  state.chromeTranslatorOk = true;
  obs.log('ext_translate_ondevice', 'success', { duration_ms: performance.now() - t0, targets, utterance_id: finalMsg.utterance_id,
    duplicate_with_backend: state.translationEngine !== 'chrome' });
  emit({
    type: 'translation',
    engine: 'chrome',
    utterance_id: finalMsg.utterance_id,
    cue_id: 'asr_' + finalMsg.utterance_id,
    revision: 1,
    source_text: finalMsg.text,
    source_lang: src,
    lang_status: finalMsg.lang_status,
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
            state.ws.send(JSON.stringify({ type: 'config', source_lang: msg.sourceLang, target_langs: msg.targetLangs }));
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
      if (msg.type !== 'ASR_START') {  // startCapture logs its own failure
        obs.log('ext_capture', 'fail', { error_type: 'unknown', error_message: e.message || String(e), msg_type: msg.type, where: 'offscreen router' });
      }
      emit({ type: 'error', error: e.message || String(e) });
      sendResponse({ ok: false, error: e.message || String(e) });
    }
  })();
  return true; // async response
});

log('offscreen document ready');
