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
import './content-scripts/ws-protocol.js';
import './lib/websocket-client.js';
import './lib/tab-connections.js';
import './lib/caption-mode.js';
import './lib/corrections.js';
import './lib/glossary.js';
import './lib/settings-migration.js';

const obs = globalThis.STObs;
obs.init({ context: 'background' });

// Per-tab backend connections (E17, W8): a tab talks to /ws only after the user
// enabled translation on it, and the socket lives here so its Origin is the
// extension. See lib/tab-connections.js.
const tabConnections = new globalThis.STTabConnections({
  createSocket: () => new globalThis.SubtitleWebSocket(),
  getServerUrl: async () => (await getSettings()).serverUrl,
  persist: (ids) => chrome.storage.session.set({ enabledTabs: ids }).catch((e) =>
    obs.log('ext_settings', 'fail', { error_type: 'unknown', error_message: 'enabledTabs save: ' + (e.message || e) })),
  log: (action, tabId, reason) => obs.log('ext_tab', action === 'refuse' ? 'skip' : 'success',
    { action, tab_id: tabId, reason: reason || undefined }),
});
tabConnections.restoreFrom(chrome.storage.session.get('enabledTabs').then((r) => r.enabledTabs || []));
chrome.runtime.onConnect.addListener((port) => {
  if (port.name === 'st-ws') tabConnections.attachPort(port);
});
chrome.tabs.onRemoved.addListener((tabId) => tabConnections.forget(tabId));

// Relay failures can repeat for every caption; log the first and then every 100th.
const relayFails = { count: 0 };
function logRelayFail(stage, err, extra) {
  relayFails.count++;
  if (relayFails.count === 1 || relayFails.count % 100 === 0) {
    obs.log(stage, 'fail', Object.assign({ error_type: 'process', error_message: (err && err.message) || String(err), failures_so_far: relayFails.count }, extra || {}));
  }
}

const SETTINGS_SCHEMA_VERSION = 5; // 4: cloud keys removed from the extension (D4); 5: translation-only overlay

const DEFAULT_SETTINGS = {
  enabled: true,
  serverUrl: 'ws://127.0.0.1:8765/ws',
  primaryLang: 'en',
  secondaryLang: 'zh',
  targetLanguages: ['en', 'zh'],
  fontSize: 20,               // px, used only while no video element is known
  fontScalePct: 4.5,          // overlay font: percentage of the video's height
  fontMinPx: 14,              // ...never under this (small embeds)
  overlayBgOpacity: 0.6,      // the rounded box behind each subtitle line
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
  showOriginal: false,        // the source line (spoken words) under the translation: popup toggle / Alt+O
  autoConnect: true,
  // Live captions
  asrSourceLang: 'auto',      // auto | en | zh | bn
  asrEngine: 'auto',          // auto | sherpa-zipformer (the backend's only engine)
  translationEngine: 'auto',  // auto | chrome | backend
  showPartials: true,
  // Caption mode (D2): translate subtitles a page already shows. Off by default;
  // turning it on (Options) requests the caption sites as optional host permissions.
  captionMode: false,
  // No cloud keys here (D4): they live in the backend's config (backend/.env).
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

  // v4 (D4): keys an older version stored are deleted; the options page says where they go now
  const stripped = globalThis.STSettingsMigration.stripCloudKeys(migrated);
  if (stripped.settings !== migrated) {
    migrated = stripped.settings;
    needsSave = true;
  }

  // v5: the source line is off by default; applied once to settings from an older schema
  const fromVersion = typeof migrated.schemaVersion === 'number' ? migrated.schemaVersion : 0;
  const defaulted = globalThis.STSettingsMigration.applyOverlayDefaults(migrated, fromVersion);
  if (defaulted !== migrated) {
    migrated = defaulted;
    needsSave = true;
  }

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
// Site access (D2): no host permissions at install. The overlay scripts are
// injected into a tab the user acted on (activeTab: action click, Alt+L,
// popup) or registered for the sites the user granted to caption mode
// (lib/caption-mode.js).
// ---------------------------------------------------------------------------

const captionMode = globalThis.STCaptionMode;
let captionSync = Promise.resolve();

/** Register / update / unregister the caption-mode content scripts to match the
 *  setting and the granted origins. Serialized: two syncs never race to register. */
function syncCaptionScripts(reason) {
  captionSync = captionSync.then(() => doSyncCaptionScripts(reason), () => doSyncCaptionScripts(reason));
  return captionSync;
}

async function doSyncCaptionScripts(reason) {
  try {
    const settings = await getSettings();
    const granted = (await chrome.permissions.getAll()).origins || [];
    const want = captionMode.registrationFor(settings.captionMode === true, granted);
    const have = await chrome.scripting.getRegisteredContentScripts({ ids: [captionMode.REGISTRATION_ID] });
    if (!want) {
      if (have.length) await chrome.scripting.unregisterContentScripts({ ids: [captionMode.REGISTRATION_ID] });
    } else if (have.length) {
      await chrome.scripting.updateContentScripts([want]);
    } else {
      await chrome.scripting.registerContentScripts([want]);
    }
    obs.log('ext_caption_mode', 'success', { action: want ? 'registered' : 'off', reason, origins: want ? want.matches.length : 0 });
  } catch (e) {
    obs.log('ext_caption_mode', 'fail', { reason, error_type: 'process', error_message: e.message || String(e) });
  }
}

syncCaptionScripts('startup');
chrome.permissions.onAdded.addListener(() => syncCaptionScripts('permissions added'));
chrome.permissions.onRemoved.addListener(() => syncCaptionScripts('permissions removed'));
chrome.storage.onChanged.addListener((changes, area) => {
  const c = area === 'local' && changes.settings;
  if (c && (c.oldValue || {}).captionMode !== (c.newValue || {}).captionMode) syncCaptionScripts('setting changed');
});

/** Make sure the overlay scripts run in the tab's top frame: already there
 *  (registered caption mode, or injected before), or injected now, never twice
 *  (captionMode.ensureInjected). Needs site access to the tab: activeTab from
 *  the user's action, or a granted origin. */
function ensureContentScript(tabId) {
  const target = { tabId, frameIds: [0] };
  return captionMode.ensureInjected({
    probe: async () => {
      const [r] = await chrome.scripting.executeScript({
        target, func: () => ({ loaded: !!globalThis.__subtitleTranslatorInitialized, state: document.readyState }),
      });
      return (r && r.result) || { loaded: false, state: 'complete' };
    },
    inject: () => chrome.scripting.executeScript({ target, files: captionMode.CONTENT_SCRIPT_FILES }),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
  });
}

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
    // tabCapture is optional (D2: Chrome words it as access to all your data on all
    // websites, so it is asked for on first use from the popup, not at install)
    step = 'permission';
    if (!(await chrome.permissions.contains({ permissions: ['tabCapture'] }))) {
      throw new Error('Tab audio capture is not allowed yet: start live captions from the extension popup once and allow it');
    }
    // Must be called from a user gesture (popup click / command)
    step = 'getMediaStreamId';
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
    // the overlay draws the captions: inject it with the access the user's action gave (activeTab)
    step = 'inject';
    const injected = await ensureContentScript(tabId);
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
      sessionId,
    });
    if (!res || !res.ok) {
      throw new Error((res && res.error) || 'Failed to start capture');
    }
    Object.assign(asrState, { tabId, capturing: true, sourceLang, targetLangs, sessionId });
    await saveAsrState();
    // the content script answers once the tab's audio output is primed (A11: a video played
    // after this is captured from its first sample), so the start is reported after that;
    // a page without the script, or a slow one, does not hold the start up for long
    step = 'prime';
    const prime = await Promise.race([
      chrome.tabs.sendMessage(tabId, { type: 'ASR_STATE', capturing: true, sourceLang, targetLangs, sessionId }, { frameId: 0 })
        .then((r) => (r && 'prime' in r ? r.prime : null))
        .catch((e) => { logRelayFail('ext_relay', e, { session_id: sessionId, msg_type: 'ASR_STATE' }); return null; }),
      new Promise((resolve) => setTimeout(() => resolve('timeout'), 2000)),
    ]);
    obs.log('ext_capture', 'success', { session_id: sessionId, duration_ms: performance.now() - t0, tab_id: tabId, source_lang: sourceLang, target_langs: targetLangs, overlay: injected, prime });
    return { ok: true, prime };
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
  const optional = chrome.runtime.getManifest().optional_permissions || [];
  return { ...asrState, offscreen: off, supported: !!chrome.tabCapture || optional.includes('tabCapture'),
    captureAllowed: !!chrome.tabCapture };
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
      // not allowed yet: the popup is where the user can allow tab audio capture (D2)
      if (/not allowed yet/.test(e.message || '') && chrome.action.openPopup) chrome.action.openPopup().catch(() => {});
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
          sendToOffscreen({ type: 'ASR_CONFIG', targetLangs: merged.targetLanguages, sourceLang: merged.asrSourceLang, translationEngine: merged.translationEngine })
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
    // ---- per-tab translation (E17) ----
    case 'GET_TAB_STATE': {
      const tabId = message.tabId || sender.tab?.id;
      Promise.all([tabConnections.ready, getSettings()]).then(([, s]) =>
        sendResponse({ enabled: tabConnections.isEnabled(tabId), captionMode: s.captionMode === true }));
      return true;
    }
    case 'SET_TAB_ENABLED': {
      // only the extension's own pages (popup, options) may switch a tab on: a content
      // script (sender.url = the web page) cannot enable itself
      if (!(sender.url || '').startsWith(chrome.runtime.getURL(''))) {
        sendResponse({ ok: false, error: 'not allowed from a content script' });
        return false;
      }
      const tabId = message.tabId;
      const on = !!message.enabled;
      (async () => {
        await tabConnections.ready;
        if (on) {
          // caption mode is opt-in (D2); a tab outside the granted sites is reachable only
          // through the activeTab grant of the popup the user opened on it
          if ((await getSettings()).captionMode !== true) {
            sendResponse({ ok: false, reason: 'caption_mode_off', error: 'Caption mode is off. Turn it on in Settings.' });
            return;
          }
          try {
            await ensureContentScript(tabId);
          } catch (e) {
            obs.log('ext_tab', 'fail', { action: 'enable', tab_id: tabId, error_type: 'input_invalid', error_message: 'inject: ' + (e.message || e) });
            sendResponse({ ok: false, reason: 'no_access', error: 'Cannot read this page: ' + (e.message || e) });
            return;
          }
        }
        tabConnections.setEnabled(tabId, on);
        chrome.tabs.sendMessage(tabId, { type: 'TAB_ENABLED', enabled: on }).catch(() => {});
        sendResponse({ ok: true, enabled: on });
      })();
      return true;
    }
    case 'SUBMIT_CORRECTION': {
      // Alt+E on a tab without a caption socket (live captions only): post it to the
      // backend from here, with the extension's origin. Only from a tab's content script.
      if (!sender.tab || (sender.url || '').startsWith(chrome.runtime.getURL(''))) {
        sendResponse({ ok: false, error: 'not from a tab' });
        return false;
      }
      (async () => {
        const res = await STCorrections.submit(message.correction || {}, (await getSettings()).serverUrl);
        obs.log('ext_correction', res.ok ? 'success' : 'fail', {
          path: '/corrections', tab_id: sender.tab.id, cue_id: (message.correction || {}).cueId,
          ...(res.ok ? {} : { error_type: 'process', error_message: res.error }),
        });
        sendResponse(res);
      })();
      return true;
    }
    case 'SUBMIT_GLOSSARY_TERM': {
      // Alt+E with a word selected in the recognised line: the term is posted to the
      // backend's /glossary from here, with the extension's origin. Only from a tab's
      // content script. Running live sessions use it on their next line.
      if (!sender.tab || (sender.url || '').startsWith(chrome.runtime.getURL(''))) {
        sendResponse({ ok: false, error: 'not from a tab' });
        return false;
      }
      (async () => {
        const term = message.term || {};
        const res = await STGlossary.save(term, (await getSettings()).serverUrl);
        obs.log('ext_glossary', res.ok ? 'success' : 'fail', {
          path: '/glossary', tab_id: sender.tab.id, source_lang: term.source_lang,
          heard_as: (term.heard_as || []).length, renderings: Object.keys(term.renderings || {}),
          ...(res.ok ? {} : { error_type: 'process', error_message: res.error }),
        });
        sendResponse(res);
      })();
      return true;
    }
    case 'FRAME_CUE': {
      // a sub-frame's detector (embedded player) -> the tab's top frame, only for enabled tabs (E16)
      const tabId = sender.tab?.id;
      if (tabId != null && sender.frameId !== 0 && tabConnections.isEnabled(tabId)) {
        chrome.tabs.sendMessage(tabId, { type: 'FRAME_CUE', kind: message.kind, cue: message.cue }, { frameId: 0 })
          .catch((e) => logRelayFail('ext_relay', e, { msg_type: 'FRAME_CUE', hop: 'sw->tab' }));
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
