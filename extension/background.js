/**
 * Background Service Worker
 *
 * Handles:
 * - Settings storage + migration
 * - Keyboard commands
 * - Live captions: tab audio capture via tabCapture -> offscreen document,
 *   and relaying caption events from the offscreen document to the tab
 */

import './obs.js';

const obs = globalThis.STObs;
obs.init({ context: 'background' });

// Relay failures can repeat for every caption; log the first and then every 100th.
const relayFails = { count: 0 };
function logRelayFail(stage, err, extra) {
  relayFails.count++;
  if (relayFails.count === 1 || relayFails.count % 100 === 0) {
    obs.log(stage, 'fail', Object.assign({ error_type: 'process', error_message: (err && err.message) || String(err), failures_so_far: relayFails.count }, extra || {}));
  }
}

const SETTINGS_SCHEMA_VERSION = 3;

const DEFAULT_SETTINGS = {
  enabled: true,
  serverUrl: 'ws://127.0.0.1:8765/ws',
  primaryLang: 'en',
  secondaryLang: 'zh',
  targetLanguages: ['en', 'zh'],
  fontSize: 20,
  fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", "Noto Sans Bengali", sans-serif',
  primaryOnTop: true,
  maxLines: 2,
  maxCharsLatin: 42,
  maxCharsCJK: 22,
  maxCharsEn: 42,
  maxCharsZh: 22,
  maxCharsVi: 45,
  overlayOpacity: 0.9,
  overlayPosition: 'above',
  usePostEditor: false,       // legacy name; now means "async refiner"
  refinerEnabled: false,
  fastMode: true,
  strictMeaningLock: true,
  showOriginal: true,
  autoConnect: true,
  // Live captions
  asrSourceLang: 'auto',      // auto | en | zh | bn
  asrEngine: 'auto',          // auto | sherpa-zipformer | whisper | cloud
  translationEngine: 'auto',  // auto | chrome | backend
  showPartials: true,
  // Optional cloud keys (stored locally only, sent to the local backend)
  cloudKeys: {},
  schemaVersion: SETTINGS_SCHEMA_VERSION,
};

const ASR_SERVER_PATH = '/ws/asr';

function asrUrlFrom(serverUrl) {
  try {
    const u = new URL(serverUrl || DEFAULT_SETTINGS.serverUrl);
    u.pathname = ASR_SERVER_PATH;
    return u.toString();
  } catch (_) {
    return 'ws://127.0.0.1:8765' + ASR_SERVER_PATH;
  }
}

/**
 * Migrate settings from older schema versions.
 */
function migrateSettings(settings) {
  if (!settings) {
    return { settings: { ...DEFAULT_SETTINGS }, needsSave: true };
  }
  let migrated = { ...settings };
  let needsSave = false;

  if ('strictMeaning' in migrated && !('strictMeaningLock' in migrated)) {
    migrated.strictMeaningLock = migrated.strictMeaning;
    delete migrated.strictMeaning;
    needsSave = true;
  }

  // v2 wrote primaryLanguage/secondaryLanguage while the UI read primaryLang/secondaryLang.
  if ('primaryLanguage' in migrated) {
    if (!migrated.primaryLang) migrated.primaryLang = migrated.primaryLanguage;
    delete migrated.primaryLanguage;
    needsSave = true;
  }
  if ('secondaryLanguage' in migrated) {
    if (!migrated.secondaryLang) migrated.secondaryLang = migrated.secondaryLanguage;
    delete migrated.secondaryLanguage;
    needsSave = true;
  }
  if (!migrated.primaryLang) {
    const langs = migrated.targetLanguages || ['en', 'zh'];
    migrated.primaryLang = langs[0] || 'en';
    migrated.secondaryLang = langs[1] || 'zh';
    needsSave = true;
  }

  // v3: synchronous post-editor is gone; carry the preference into refinerEnabled
  if (!('refinerEnabled' in migrated)) {
    migrated.refinerEnabled = !!migrated.usePostEditor && migrated.fastMode === false;
    migrated.fastMode = true;
    needsSave = true;
  }

  for (const [key, value] of Object.entries(DEFAULT_SETTINGS)) {
    if (!(key in migrated)) {
      migrated[key] = value;
      needsSave = true;
    }
  }
  migrated.targetLanguages = [migrated.primaryLang];
  if (migrated.secondaryLang && migrated.secondaryLang !== 'none') {
    migrated.targetLanguages.push(migrated.secondaryLang);
  }
  if (migrated.schemaVersion !== SETTINGS_SCHEMA_VERSION) {
    migrated.schemaVersion = SETTINGS_SCHEMA_VERSION;
    needsSave = true;
  }
  return { settings: migrated, needsSave };
}

async function getSettings() {
  const result = await chrome.storage.local.get('settings');
  const { settings, needsSave } = migrateSettings(result.settings);
  if (needsSave) await chrome.storage.local.set({ settings });
  return settings;
}

async function runSettingsMigration() {
  try {
    const s = await getSettings();
    obs.setEndpointFromWs(s.serverUrl);
  } catch (error) {
    console.error('Settings migration failed:', error);
    obs.log('ext_settings', 'fail', { error_type: 'unknown', error_message: error.message || String(error) });
  }
}

chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === 'install') {
    await chrome.storage.local.set({ settings: DEFAULT_SETTINGS });
    chrome.runtime.openOptionsPage();
  } else if (details.reason === 'update') {
    await runSettingsMigration();
  }
});
runSettingsMigration();

// ---------------------------------------------------------------------------
// Live captions (ASR) orchestration
// ---------------------------------------------------------------------------

const asrState = { tabId: null, capturing: false, sourceLang: 'auto', targetLangs: [], lastStatus: null };

async function loadAsrState() {
  try {
    const r = await chrome.storage.session.get('asrState');
    if (r.asrState) Object.assign(asrState, r.asrState);
  } catch (e) {
    obs.log('ext_settings', 'fail', { error_type: 'unknown', error_message: 'asrState load: ' + (e.message || e) });
  }
}
async function saveAsrState() {
  try { await chrome.storage.session.set({ asrState }); } catch (e) {
    obs.log('ext_settings', 'fail', { error_type: 'unknown', error_message: 'asrState save: ' + (e.message || e) });
  }
}
loadAsrState();

async function ensureOffscreen() {
  const has = chrome.offscreen.hasDocument ? await chrome.offscreen.hasDocument() : false;
  if (has) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['USER_MEDIA'],
    justification: 'Capture tab audio for live subtitles and translation',
  });
}

function sendToOffscreen(msg) {
  return chrome.runtime.sendMessage({ ...msg, target: 'offscreen' });
}

async function startAsr(tabId, overrides = {}) {
  // One session id per capture of a tab; the offscreen document sends it in the /ws/asr handshake.
  const sessionId = obs.newSessionId('asr-' + tabId);
  const t0 = performance.now();
  let step = 'settings';
  obs.log('ext_capture', 'start', { session_id: sessionId, tab_id: tabId });
  try {
    const settings = await getSettings();
    obs.setEndpointFromWs(settings.serverUrl);
    if (asrState.capturing && asrState.tabId && asrState.tabId !== tabId) {
      step = 'stop_previous';
      await stopAsr();
    }
    // Must be called from a user gesture (popup click / command)
    step = 'getMediaStreamId';
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
    step = 'ensureOffscreen';
    await ensureOffscreen();
    const sourceLang = overrides.sourceLang || settings.asrSourceLang || 'auto';
    const targetLangs = overrides.targetLangs || settings.targetLanguages || ['en'];
    step = 'ASR_START';
    const res = await sendToOffscreen({
      type: 'ASR_START',
      streamId,
      tabId,
      sourceLang,
      targetLangs,
      engine: settings.asrEngine || 'auto',
      translationEngine: settings.translationEngine || 'auto',
      serverUrl: asrUrlFrom(settings.serverUrl),
      cloudKeys: settings.cloudKeys || {},
      sessionId,
    });
    if (!res || !res.ok) {
      throw new Error((res && res.error) || 'Failed to start capture');
    }
    Object.assign(asrState, { tabId, capturing: true, sourceLang, targetLangs, sessionId });
    await saveAsrState();
    chrome.tabs.sendMessage(tabId, { type: 'ASR_STATE', capturing: true, sourceLang, targetLangs, sessionId }, { frameId: 0 })
      .catch((e) => logRelayFail('ext_relay', e, { session_id: sessionId, msg_type: 'ASR_STATE' }));
    obs.log('ext_capture', 'success', { session_id: sessionId, duration_ms: performance.now() - t0, tab_id: tabId, source_lang: sourceLang, target_langs: targetLangs });
    return { ok: true };
  } catch (e) {
    const msg = e.message || String(e);
    obs.log('ext_capture', 'fail', {
      session_id: sessionId, duration_ms: performance.now() - t0, tab_id: tabId, step,
      error_type: /timeout/i.test(msg) ? 'timeout' : (step === 'getMediaStreamId' ? 'input_invalid' : 'process'),
      error_message: msg,
    });
    throw e;
  }
}

async function stopAsr() {
  const sessionId = asrState.sessionId || null;
  try { await sendToOffscreen({ type: 'ASR_STOP' }); } catch (e) {
    obs.log('ext_capture', 'skip', { session_id: sessionId, error_type: 'process', error_message: 'ASR_STOP not delivered: ' + (e.message || e) });
  }
  const tabId = asrState.tabId;
  Object.assign(asrState, { tabId: null, capturing: false, sessionId: null });
  await saveAsrState();
  if (tabId) chrome.tabs.sendMessage(tabId, { type: 'ASR_STATE', capturing: false }, { frameId: 0 })
    .catch((e) => logRelayFail('ext_relay', e, { session_id: sessionId, msg_type: 'ASR_STATE' }));
  try {
    if (chrome.offscreen.hasDocument && await chrome.offscreen.hasDocument()) await chrome.offscreen.closeDocument();
  } catch (e) {
    obs.log('ext_capture', 'skip', { session_id: sessionId, error_type: 'process', error_message: 'closeDocument: ' + (e.message || e) });
  }
  obs.log('ext_capture', 'success', { session_id: sessionId, action: 'stop', tab_id: tabId });
  return { ok: true };
}

async function getAsrStatus() {
  let off = null;
  try {
    if (chrome.offscreen.hasDocument && await chrome.offscreen.hasDocument()) off = await sendToOffscreen({ type: 'ASR_STATUS' });
  } catch (_) {}
  return { ...asrState, offscreen: off, supported: !!chrome.tabCapture };
}

chrome.tabs.onRemoved.addListener((tabId) => {
  if (asrState.capturing && asrState.tabId === tabId) stopAsr();
});

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

chrome.commands.onCommand.addListener(async (command) => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  if (command === 'toggle-live-captions') {
    try {
      if (asrState.capturing) await stopAsr(); else await startAsr(tab.id);
    } catch (e) {
      console.error('Live captions toggle failed:', e);  // startAsr already logged ext_capture fail
    }
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { type: 'COMMAND', command }, { frameId: 0 });
  } catch (error) {
    console.error('Error sending command:', error);
    obs.log('ext_relay', 'fail', { error_type: 'process', error_message: error.message || String(error), msg_type: 'COMMAND', command });
  }
});

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.target === 'offscreen') return false;

  switch (message.type) {
    case 'GET_SETTINGS':
      getSettings().then(sendResponse);
      return true;

    case 'UPDATE_SETTINGS':
      (async () => {
        const current = await getSettings();
        const { settings: merged } = migrateSettings({ ...current, ...message.settings });
        await chrome.storage.local.set({ settings: merged });
        broadcastToTabs({ type: 'SETTINGS_UPDATED', settings: merged });
        obs.setEndpointFromWs(merged.serverUrl);
        if (asrState.capturing) {
          sendToOffscreen({ type: 'ASR_CONFIG', targetLangs: merged.targetLanguages, sourceLang: merged.asrSourceLang, translationEngine: merged.translationEngine, cloudKeys: merged.cloudKeys })
            .catch((e) => obs.log('ext_relay', 'fail', { session_id: asrState.sessionId, error_type: 'process', error_message: e.message || String(e), msg_type: 'ASR_CONFIG' }));
        }
        sendResponse({ success: true, settings: merged });
      })();
      return true;

    case 'GET_CONNECTION_STATUS':
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]?.id) {
          chrome.tabs.sendMessage(tabs[0].id, { type: 'GET_STATUS' }, { frameId: 0 }, (response) => {
            void chrome.runtime.lastError;
            sendResponse(response || { connected: false });
          });
        } else {
          sendResponse({ connected: false });
        }
      });
      return true;

    case 'TOGGLE_OVERLAY':
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs[0]?.id) chrome.tabs.sendMessage(tabs[0].id, { type: 'COMMAND', command: 'toggle-overlay' }, { frameId: 0 }).catch(() => {});
      });
      sendResponse({ success: true });
      return false;

    // ---- live captions ----
    case 'ASR_START': {
      const tabId = message.tabId || sender.tab?.id;
      startAsr(tabId, message).then(sendResponse).catch(e => sendResponse({ ok: false, error: e.message || String(e) }));  // failure logged in startAsr
      return true;
    }
    case 'ASR_STOP':
      stopAsr().then(sendResponse);
      return true;
    case 'ASR_STATUS':
    case 'GET_ASR_STATUS':
      getAsrStatus().then(sendResponse);
      return true;
    case 'ASR_SET_LANG':
      (async () => {
        asrState.sourceLang = message.sourceLang;
        await saveAsrState();
        if (asrState.capturing) await sendToOffscreen({ type: 'ASR_CONFIG', sourceLang: message.sourceLang })
          .catch((e) => obs.log('ext_relay', 'fail', { session_id: asrState.sessionId, error_type: 'process', error_message: e.message || String(e), msg_type: 'ASR_CONFIG' }));
        sendResponse({ ok: true });
      })();
      return true;
    case 'ASR_EVENT': {
      // from the offscreen document -> the captured tab's top frame
      const tabId = message.tabId || asrState.tabId;
      if (message.event && message.event.type === 'status') asrState.lastStatus = message.event;
      if (tabId) {
        chrome.tabs.sendMessage(tabId, { type: 'ASR_CAPTION', event: message.event }, { frameId: 0 })
          .catch((e) => logRelayFail('ext_relay', e, { session_id: asrState.sessionId, msg_type: message.event && message.event.type, hop: 'sw->tab' }));
      } else {
        logRelayFail('ext_relay', new Error('no captured tab id; caption event dropped'), { session_id: asrState.sessionId, hop: 'sw->tab' });
      }
      return false;
    }
    case 'OBS_BATCH':
      // log records from content scripts; posted to the backend with ours
      obs.ingest(message.events);
      return false;
    case 'TRANSLATOR_PREPARE':
      (async () => {
        await ensureOffscreen();
        sendResponse(await sendToOffscreen({ type: 'TRANSLATOR_PREPARE', pairs: message.pairs }));
      })();
      return true;

    case 'LOG':
      console.log('[Content]', message.message);
      return false;

    default:
      return false;
  }
});

async function broadcastToTabs(message) {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id) chrome.tabs.sendMessage(tab.id, message).catch(() => {});
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === 'complete') {
    chrome.tabs.sendMessage(tabId, { type: 'PAGE_LOADED' }, { frameId: 0 }).catch(() => {});
  }
});

console.log('Background service worker initialized');
