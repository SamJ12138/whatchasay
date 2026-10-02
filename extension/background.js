/**
 * Background Service Worker
 *
 * Handles:
 * - Settings storage + migration
 * - Keyboard commands
 * - Live captions: tab audio capture via tabCapture -> offscreen document,
 *   and relaying caption events from the offscreen document to the tab
 */

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
    await getSettings();
  } catch (error) {
    console.error('Settings migration failed:', error);
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
  } catch (_) {}
}
async function saveAsrState() {
  try { await chrome.storage.session.set({ asrState }); } catch (_) {}
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
  const settings = await getSettings();
  if (asrState.capturing && asrState.tabId && asrState.tabId !== tabId) {
    await stopAsr();
  }
  // Must be called from a user gesture (popup click / command)
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await ensureOffscreen();
  const sourceLang = overrides.sourceLang || settings.asrSourceLang || 'auto';
  const targetLangs = overrides.targetLangs || settings.targetLanguages || ['en'];
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
  });
  if (!res || !res.ok) {
    throw new Error((res && res.error) || 'Failed to start capture');
  }
  Object.assign(asrState, { tabId, capturing: true, sourceLang, targetLangs });
  await saveAsrState();
  chrome.tabs.sendMessage(tabId, { type: 'ASR_STATE', capturing: true, sourceLang, targetLangs }, { frameId: 0 }).catch(() => {});
  return { ok: true };
}

async function stopAsr() {
  try { await sendToOffscreen({ type: 'ASR_STOP' }); } catch (_) {}
  const tabId = asrState.tabId;
  Object.assign(asrState, { tabId: null, capturing: false });
  await saveAsrState();
  if (tabId) chrome.tabs.sendMessage(tabId, { type: 'ASR_STATE', capturing: false }, { frameId: 0 }).catch(() => {});
  try {
    if (chrome.offscreen.hasDocument && await chrome.offscreen.hasDocument()) await chrome.offscreen.closeDocument();
  } catch (_) {}
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
      console.error('Live captions toggle failed:', e);
    }
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { type: 'COMMAND', command }, { frameId: 0 });
  } catch (error) {
    console.error('Error sending command:', error);
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
        if (asrState.capturing) {
          sendToOffscreen({ type: 'ASR_CONFIG', targetLangs: merged.targetLanguages, sourceLang: merged.asrSourceLang, translationEngine: merged.translationEngine, cloudKeys: merged.cloudKeys }).catch(() => {});
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
      startAsr(tabId, message).then(sendResponse).catch(e => sendResponse({ ok: false, error: e.message || String(e) }));
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
        if (asrState.capturing) await sendToOffscreen({ type: 'ASR_CONFIG', sourceLang: message.sourceLang }).catch(() => {});
        sendResponse({ ok: true });
      })();
      return true;
    case 'ASR_EVENT': {
      // from the offscreen document -> the captured tab's top frame
      const tabId = message.tabId || asrState.tabId;
      if (message.event && message.event.type === 'status') asrState.lastStatus = message.event;
      if (tabId) chrome.tabs.sendMessage(tabId, { type: 'ASR_CAPTION', event: message.event }, { frameId: 0 }).catch(() => {});
      return false;
    }
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
