/**
 * Subtitle Translator - Options Page Script
 * Handles loading, saving, and managing extension settings
 *
 * IMPORTANT: Uses chrome.storage.local for consistency with background.js
 * All keys must match background.js DEFAULT_SETTINGS exactly
 */

// Structured log (extension/obs.js): one session per options-page load; HTTP calls carry X-Session-Id.
const obs = window.STObs;
obs.init({ context: 'options', sessionId: obs.newSessionId('opt') });

// Schema version for migration
const SETTINGS_SCHEMA_VERSION = 4; // 4: cloud keys removed from the extension (D4)

// Language display names for UI
const LANGUAGE_NAMES = {
  'en': 'English',
  'zh': 'Chinese',
  'bn': 'Bengali',
  'vi': 'Vietnamese',
  'ja': 'Japanese',
  'ko': 'Korean',
  'es': 'Spanish',
  'fr': 'French',
  'de': 'German',
  'ru': 'Russian',
  'pt': 'Portuguese',
  'it': 'Italian',
  'none': 'None'
};

// Sample text for preview by language
const PREVIEW_TEXT = {
  'en': 'This is how your subtitles will look',
  'zh': '这是字幕的显示效果',
  'bn': 'আপনার সাবটাইটেল এভাবে দেখাবে',
  'vi': 'Đây là cách phụ đề của bạn sẽ trông như thế nào',
  'ja': 'これが字幕の表示例です',
  'ko': '이것이 자막이 표시되는 방식입니다',
  'es': 'Así es como se verán tus subtítulos',
  'fr': 'Voici comment vos sous-titres apparaîtront',
  'de': 'So werden Ihre Untertitel aussehen',
  'ru': 'Так будут выглядеть ваши субтитры',
  'pt': 'É assim que suas legendas aparecerão',
  'it': 'Ecco come appariranno i tuoi sottotitoli',
  'none': ''
};

// CJK languages (need fewer chars per line)
const CJK_LANGUAGES = ['zh', 'ja', 'ko'];

// Default settings - MUST match background.js DEFAULT_SETTINGS
const DEFAULT_SETTINGS = {
  schemaVersion: SETTINGS_SCHEMA_VERSION,
  enabled: true,
  serverHost: 'localhost',
  serverPort: 8765,
  serverUrl: 'ws://127.0.0.1:8765/ws',
  // Language configuration
  primaryLang: 'en',
  secondaryLang: 'zh',
  targetLanguages: ['en', 'zh'],
  languageOrder: 'primary-secondary',
  // Colors
  primaryColor: '#4facfe',
  secondaryColor: '#ffcc00',
  // Display settings
  fontSize: 20,
  fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif',
  primaryOnTop: true,
  overlayOpacity: 0.9,
  overlayPosition: 'above',
  // Translation settings
  usePostEditor: false,
  refinerEnabled: false,
  refinerProvider: 'ollama',
  fastMode: true,
  strictMeaningLock: true,  // IMPORTANT: Use strictMeaningLock, not strictMeaning
  // Live captions
  asrSourceLang: 'auto',
  asrEngine: 'auto',
  translationEngine: 'auto',
  showPartials: true,
  captionMode: false,         // D2: opt-in; turning it on requests the caption sites
  // Subtitle limits
  maxLines: 2,
  maxCharsLatin: 42,
  maxCharsCJK: 22,
  maxCharsEn: 42,
  maxCharsZh: 22,
  // Other settings
  showSpeakers: true,
  showMusic: true,
  showOriginal: true,
  saveCorrections: true,
  autoConnect: true
};

// DOM Elements
const elements = {
  serverHost: document.getElementById('serverHost'),
  serverPort: document.getElementById('serverPort'),
  connectionStatus: document.getElementById('connectionStatus'),
  refinerEnabled: document.getElementById('refinerEnabled'),
  refinerProvider: document.getElementById('refinerProvider'),
  asrSourceLang: document.getElementById('asrSourceLang'),
  asrEngine: document.getElementById('asrEngine'),
  translationEngine: document.getElementById('translationEngine'),
  showPartials: document.getElementById('showPartials'),
  captionMode: document.getElementById('captionMode'),
  captionModeExplanation: document.getElementById('captionModeExplanation'),
  captionModeSites: document.getElementById('captionModeSites'),
  cloudStatus: document.getElementById('cloudStatus'),
  keyNotice: document.getElementById('keyNotice'),
  keyNoticeText: document.getElementById('keyNoticeText'),
  dismissCloudNotice: document.getElementById('dismissCloudNotice'),
  strictMeaningLock: document.getElementById('strictMeaning'), // DOM ID is strictMeaning but we save as strictMeaningLock
  fontSize: document.getElementById('fontSize'),
  fontSizeValue: document.getElementById('fontSizeValue'),
  overlayPosition: document.getElementById('overlayPosition'),
  // Language elements
  primaryLang: document.getElementById('primaryLang'),
  secondaryLang: document.getElementById('secondaryLang'),
  languageOrder: document.getElementById('languageOrder'),
  // Character limits
  maxCharsLatin: document.getElementById('maxCharsLatin'),
  maxCharsCJK: document.getElementById('maxCharsCJK'),
  maxLines: document.getElementById('maxLines'),
  showSpeakers: document.getElementById('showSpeakers'),
  showMusic: document.getElementById('showMusic'),
  saveCorrections: document.getElementById('saveCorrections'),
  previewPrimary: document.getElementById('previewPrimary'),
  previewSecondary: document.getElementById('previewSecondary'),
  saveSettings: document.getElementById('saveSettings'),
  resetDefaults: document.getElementById('resetDefaults'),
  exportCorrections: document.getElementById('exportCorrections'),
  clearCache: document.getElementById('clearCache'),
  clearMemory: document.getElementById('clearMemory'),
  storageNote: document.getElementById('storageNote'),
  statusBar: document.getElementById('statusBar')
};

// Color pickers
const primaryColorPicker = document.getElementById('primaryColorPicker');
const secondaryColorPicker = document.getElementById('secondaryColorPicker');

/**
 * Show status message
 */
function showStatus(message, type = 'success') {
  elements.statusBar.textContent = message;
  elements.statusBar.className = `status-bar show ${type}`;

  setTimeout(() => {
    elements.statusBar.classList.remove('show');
  }, 3000);
}

/**
 * Migrate settings from old schema to new schema
 */
function migrateSettings(settings) {
  let migrated = { ...settings };
  let needsSave = false;

  // v4 (D4): keys an older version stored are deleted (the notice below says where they go now)
  const stripped = window.STSettingsMigration.stripCloudKeys(migrated);
  if (stripped.settings !== migrated) {
    migrated = stripped.settings;
    needsSave = true;
  }

  // Migration: strictMeaning -> strictMeaningLock
  if ('strictMeaning' in migrated && !('strictMeaningLock' in migrated)) {
    migrated.strictMeaningLock = migrated.strictMeaning;
    delete migrated.strictMeaning;
    needsSave = true;
    console.log('[Options] Migrated strictMeaning -> strictMeaningLock');
  }

  // Migration: Add schema version
  if (!migrated.schemaVersion || migrated.schemaVersion < SETTINGS_SCHEMA_VERSION) {
    migrated.schemaVersion = SETTINGS_SCHEMA_VERSION;
    needsSave = true;
    console.log('[Options] Updated schema version to', SETTINGS_SCHEMA_VERSION);
  }

  // Migration: Add primaryLang/secondaryLang if missing
  if (!migrated.primaryLang) {
    migrated.primaryLang = 'en';
    needsSave = true;
  }
  if (!migrated.secondaryLang) {
    migrated.secondaryLang = 'zh';
    needsSave = true;
  }

  // Ensure targetLanguages is derived from primary/secondary
  migrated.targetLanguages = [migrated.primaryLang];
  if (migrated.secondaryLang && migrated.secondaryLang !== 'none') {
    migrated.targetLanguages.push(migrated.secondaryLang);
  }

  return { settings: migrated, needsSave };
}

/**
 * Get target languages array from current settings
 */
function getTargetLanguages() {
  const primary = elements.primaryLang?.value || 'en';
  const secondary = elements.secondaryLang?.value || 'zh';
  const languages = [primary];
  if (secondary && secondary !== 'none') {
    languages.push(secondary);
  }
  return languages;
}

/**
 * Load settings from storage
 * USES chrome.storage.local for consistency with background.js
 */
async function loadSettings() {
  try {
    const result = await chrome.storage.local.get('settings');
    let settings = { ...DEFAULT_SETTINGS, ...result.settings };

    // Run migration
    const { settings: migratedSettings, needsSave } = migrateSettings(settings);
    settings = migratedSettings;

    // Save migrated settings if needed
    if (needsSave) {
      await chrome.storage.local.set({ settings });
      console.log('[Options] Saved migrated settings');
    }

    // Apply settings to form
    if (elements.serverHost) elements.serverHost.value = settings.serverHost || 'localhost';
    if (elements.serverPort) elements.serverPort.value = settings.serverPort || 8765;
    if (elements.refinerEnabled) elements.refinerEnabled.checked = settings.refinerEnabled === true;
    if (elements.refinerProvider) elements.refinerProvider.value = settings.refinerProvider || 'ollama';
    if (elements.asrSourceLang) elements.asrSourceLang.value = settings.asrSourceLang || 'auto';
    if (elements.asrEngine) elements.asrEngine.value = settings.asrEngine || 'auto';
    if (elements.translationEngine) elements.translationEngine.value = settings.translationEngine || 'auto';
    if (elements.showPartials) elements.showPartials.checked = settings.showPartials !== false;
    if (elements.captionMode) elements.captionMode.checked = settings.captionMode === true;
    showKeyNotice(settings.keysRemovedNotice);
    if (elements.strictMeaningLock) elements.strictMeaningLock.checked = settings.strictMeaningLock !== false;
    if (elements.fontSize) {
      elements.fontSize.value = settings.fontSize || 20;
      if (elements.fontSizeValue) elements.fontSizeValue.textContent = `${settings.fontSize || 20}px`;
    }
    if (elements.overlayPosition) elements.overlayPosition.value = settings.overlayPosition || 'above';

    // Language settings
    if (elements.primaryLang) elements.primaryLang.value = settings.primaryLang || 'en';
    if (elements.secondaryLang) elements.secondaryLang.value = settings.secondaryLang || 'zh';
    if (elements.languageOrder) elements.languageOrder.value = settings.languageOrder || 'primary-secondary';

    // Character limits
    if (elements.maxCharsLatin) elements.maxCharsLatin.value = settings.maxCharsLatin || settings.maxCharsEn || 42;
    if (elements.maxCharsCJK) elements.maxCharsCJK.value = settings.maxCharsCJK || settings.maxCharsZh || 22;
    if (elements.maxLines) elements.maxLines.value = settings.maxLines || 2;
    if (elements.showSpeakers) elements.showSpeakers.checked = settings.showSpeakers !== false;
    if (elements.showMusic) elements.showMusic.checked = settings.showMusic !== false;
    if (elements.saveCorrections) elements.saveCorrections.checked = settings.saveCorrections !== false;

    // Set color pickers
    setColorPicker(primaryColorPicker, settings.primaryColor || '#4facfe');
    setColorPicker(secondaryColorPicker, settings.secondaryColor || '#ffcc00');

    // Update preview
    updatePreview(settings);

    // Check connection status
    const host = settings.serverHost || 'localhost';
    const port = settings.serverPort || 8765;
    checkConnection(host, port);

    console.log('[Options] Settings loaded:', settings);

  } catch (error) {
    console.error('[Options] Failed to load settings:', error);
    obs.log('ext_settings', 'fail', { error_type: 'unknown', error_message: 'load: ' + (error.message || error) });
    showStatus('Failed to load settings', 'error');
  }
}

/**
 * Save settings to storage
 * USES chrome.storage.local for consistency with background.js
 */
async function saveSettings() {
  try {
    const primaryColor = getSelectedColor(primaryColorPicker);
    const secondaryColor = getSelectedColor(secondaryColorPicker);
    const targetLanguages = getTargetLanguages();

    const settings = {
      schemaVersion: SETTINGS_SCHEMA_VERSION,
      enabled: true,
      serverHost: elements.serverHost?.value || 'localhost',
      serverPort: parseInt(elements.serverPort?.value) || 8765,
      serverUrl: `ws://${elements.serverHost?.value || 'localhost'}:${elements.serverPort?.value || 8765}/ws`,
      // Language settings
      primaryLang: elements.primaryLang?.value || 'en',
      secondaryLang: elements.secondaryLang?.value || 'zh',
      targetLanguages: targetLanguages,
      languageOrder: elements.languageOrder?.value || 'primary-secondary',
      // Colors
      primaryColor: primaryColor,
      secondaryColor: secondaryColor,
      // Display settings
      fontSize: parseInt(elements.fontSize?.value) || 20,
      fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", "Noto Sans Bengali", sans-serif',
      primaryOnTop: (elements.languageOrder?.value || 'primary-secondary') === 'primary-secondary',
      overlayOpacity: 0.9,
      overlayPosition: elements.overlayPosition?.value || 'above',
      // Translation settings
      refinerEnabled: elements.refinerEnabled?.checked === true,
      refinerProvider: elements.refinerProvider?.value || 'ollama',
      usePostEditor: elements.refinerEnabled?.checked === true,
      fastMode: elements.refinerEnabled?.checked !== true,
      strictMeaningLock: elements.strictMeaningLock?.checked !== false,  // IMPORTANT: strictMeaningLock
      // Live captions
      asrSourceLang: elements.asrSourceLang?.value || 'auto',
      asrEngine: elements.asrEngine?.value || 'auto',
      translationEngine: elements.translationEngine?.value || 'auto',
      showPartials: elements.showPartials?.checked !== false,
      // Subtitle limits
      maxLines: parseInt(elements.maxLines?.value) || 2,
      maxCharsLatin: parseInt(elements.maxCharsLatin?.value) || 42,
      maxCharsCJK: parseInt(elements.maxCharsCJK?.value) || 22,
      maxCharsEn: parseInt(elements.maxCharsLatin?.value) || 42,
      maxCharsZh: parseInt(elements.maxCharsCJK?.value) || 22,
      // Other settings
      showSpeakers: elements.showSpeakers?.checked !== false,
      showMusic: elements.showMusic?.checked !== false,
      showOriginal: true,
      saveCorrections: elements.saveCorrections?.checked !== false,
      autoConnect: true
    };

    // Save through the background worker so migration runs and live-caption config is pushed
    await new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: 'UPDATE_SETTINGS', settings }, (r) => { void chrome.runtime.lastError; resolve(r); });
    });

    console.log('[Options] Settings saved:', settings);
    showStatus('Settings saved successfully!', 'success');

  } catch (error) {
    console.error('[Options] Failed to save settings:', error);
    obs.log('ext_settings', 'fail', { error_type: 'unknown', error_message: 'save: ' + (error.message || error) });
    showStatus('Failed to save settings', 'error');
  }
}

/**
 * Caption mode (D2): switching it on asks Chrome for the caption sites (the
 * request must run inside the click); it is saved as on only once granted.
 * Switching it off saves it off and gives the site access back.
 */
const CAPTION = window.STCaptionMode;

function saveCaptionMode(on) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: 'UPDATE_SETTINGS', settings: { captionMode: on } }, (r) => { void chrome.runtime.lastError; resolve(r); });
  });
}

function onCaptionModeChange(e) {
  const on = e.target.checked;
  if (on) {
    chrome.permissions.request({ origins: CAPTION.CAPTION_ORIGINS }).then(async (granted) => {
      if (!granted) {
        e.target.checked = false;
        obs.log('ext_caption_mode', 'skip', { action: 'request', error_type: 'input_invalid', error_message: 'site access not granted' });
        showStatus('Chrome did not grant access to the caption sites; caption mode stays off', 'error');
        return;
      }
      await saveCaptionMode(true);
      obs.log('ext_caption_mode', 'success', { action: 'enabled', origins: CAPTION.CAPTION_ORIGINS.length });
      showStatus('Caption mode is on. Enable a tab from the popup to translate its subtitles.', 'success');
    }).catch((err) => {
      e.target.checked = false;
      obs.log('ext_caption_mode', 'fail', { action: 'request', error_type: 'process', error_message: err.message || String(err) });
      showStatus('Could not request site access: ' + (err.message || err), 'error');
    });
  } else {
    saveCaptionMode(false).then(() => chrome.permissions.remove({ origins: CAPTION.CAPTION_ORIGINS }).catch(() => {}));
    obs.log('ext_caption_mode', 'success', { action: 'disabled' });
    showStatus('Caption mode is off; site access returned to Chrome.', 'success');
  }
}

function initCaptionMode() {
  if (elements.captionModeExplanation) elements.captionModeExplanation.textContent = CAPTION.EXPLANATION;
  if (elements.captionModeSites) {
    const hosts = CAPTION.CAPTION_ORIGINS.map((o) => o.replace(/^https:\/\/(\*\.)?/, '').replace(/\/\*$/, ''));
    elements.captionModeSites.textContent = 'Sites: ' + hosts.join(', ');
  }
  if (elements.captionMode) elements.captionMode.addEventListener('change', onCaptionModeChange);
}

/**
 * Reset to default settings
 */
async function resetDefaults() {
  if (confirm('Are you sure you want to reset all settings to defaults?')) {
    await chrome.storage.local.set({ settings: DEFAULT_SETTINGS });
    chrome.permissions.remove({ origins: CAPTION.CAPTION_ORIGINS }).catch(() => {});
    loadSettings();
    showStatus('Settings reset to defaults', 'success');
  }
}

/**
 * Set color picker selection
 */
function setColorPicker(picker, color) {
  if (!picker) return;
  const options = picker.querySelectorAll('.color-option');
  let foundMatch = false;
  options.forEach(option => {
    const input = option.querySelector('input');
    if (input && input.value === color) {
      option.classList.add('selected');
      input.checked = true;
      foundMatch = true;
    } else {
      option.classList.remove('selected');
      if (input) input.checked = false;
    }
  });
  // If no match found, select the first option
  if (!foundMatch && options.length > 0) {
    options[0].classList.add('selected');
    const firstInput = options[0].querySelector('input');
    if (firstInput) firstInput.checked = true;
  }
}

/**
 * Get selected color from picker
 */
function getSelectedColor(picker) {
  if (!picker) return '#4facfe';
  const selected = picker.querySelector('.color-option.selected input');
  return selected ? selected.value : '#4facfe';
}

/**
 * Update preview with current settings
 */
function updatePreview(settings) {
  const fontSize = settings?.fontSize || parseInt(elements.fontSize?.value) || 20;
  const primaryColor = settings?.primaryColor || getSelectedColor(primaryColorPicker);
  const secondaryColor = settings?.secondaryColor || getSelectedColor(secondaryColorPicker);
  const order = settings?.languageOrder || elements.languageOrder?.value || 'primary-secondary';
  const primaryLang = settings?.primaryLang || elements.primaryLang?.value || 'en';
  const secondaryLang = settings?.secondaryLang || elements.secondaryLang?.value || 'zh';

  // Update preview text based on selected languages
  if (elements.previewPrimary) {
    elements.previewPrimary.textContent = PREVIEW_TEXT[primaryLang] || PREVIEW_TEXT['en'];
    elements.previewPrimary.style.fontSize = `${fontSize}px`;
    elements.previewPrimary.style.color = primaryColor;
  }

  if (elements.previewSecondary) {
    elements.previewSecondary.textContent = PREVIEW_TEXT[secondaryLang] || '';
    elements.previewSecondary.style.fontSize = `${fontSize}px`;
    elements.previewSecondary.style.color = secondaryColor;
  }

  // Reorder based on language order
  const previewBox = elements.previewPrimary?.parentElement;
  if (previewBox && elements.previewPrimary && elements.previewSecondary) {
    if (order === 'secondary-primary') {
      previewBox.insertBefore(elements.previewSecondary, elements.previewPrimary);
    } else {
      previewBox.insertBefore(elements.previewPrimary, elements.previewSecondary);
    }

    // Hide based on order
    elements.previewPrimary.style.display = order === 'secondary-only' ? 'none' : 'block';
    elements.previewSecondary.style.display = (order === 'primary-only' || secondaryLang === 'none') ? 'none' : 'block';
  }
}

/**
 * Check backend connection status
 */
async function checkConnection(host, port) {
  const t0 = performance.now();
  obs.setEndpointFromWs(`ws://${host}:${port}/ws`);
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);

    const response = await fetch(`http://${host}:${port}/health/json`, {
      method: 'GET',
      headers: obs.headers(),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    if (response.ok) {
      const data = await response.json();
      showStorageNote(data.privacy && data.privacy.tm);
      showCloudStatus(data.privacy && data.privacy.cloud);
      if (elements.connectionStatus) {
        const mt = Object.keys(data.mt_engines || {}).join(', ') || 'none';
        const asrLangs = (data.asr && data.asr.languages) ? data.asr.languages.join('/') : 'n/a';
        elements.connectionStatus.innerHTML = `
          <span style="color: #4caf50;">Connected</span> -
          Device: ${data.device} | MT: ${mt} | Live captions: ${asrLangs} |
          Cache entries: ${data.cache_entries || 0}
        `;
        elements.connectionStatus.style.background = 'rgba(76, 175, 80, 0.2)';
        elements.connectionStatus.style.color = '#4caf50';
      }
    } else {
      throw new Error('Server returned error');
    }
    obs.log('ext_http', 'success', { duration_ms: performance.now() - t0, path: '/health/json', status: response.status });
  } catch (error) {
    obs.log('ext_http', 'fail', { duration_ms: performance.now() - t0, path: '/health/json',
      error_type: error.name === 'AbortError' ? 'timeout' : 'process', error_message: error.message || String(error) });
    if (elements.connectionStatus) {
      elements.connectionStatus.innerHTML = `
        <span style="color: #ff6b6b;">Disconnected</span> -
        Start the backend: <code>python run.py</code>
      `;
      elements.connectionStatus.style.background = 'rgba(255, 77, 77, 0.2)';
      elements.connectionStatus.style.color = '#ff6b6b';
    }
  }
}

/**
 * Export corrections as JSON
 */
async function exportCorrections() {
  try {
    const host = elements.serverHost?.value || 'localhost';
    const port = elements.serverPort?.value || 8765;

    const response = await fetch(`http://${host}:${port}/stats`, { headers: obs.headers() });
    if (!response.ok) throw new Error('Failed to fetch stats');

    const stats = await response.json();

    // Create download
    const blob = new Blob([JSON.stringify(stats, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `subtitle-corrections-${new Date().toISOString().split('T')[0]}.json`;
    a.click();
    URL.revokeObjectURL(url);

    showStatus('Corrections exported successfully!', 'success');
  } catch (error) {
    console.error('Export failed:', error);
    obs.log('ext_http', 'fail', { path: '/stats', error_type: 'process', error_message: error.message || String(error) });
    showStatus('Failed to export - is the backend running?', 'error');
  }
}

/**
 * Clear translation cache
 */
async function clearCache() {
  if (!confirm('Are you sure you want to clear the translation cache? This cannot be undone.')) {
    return;
  }

  try {
    const host = elements.serverHost?.value || 'localhost';
    const port = elements.serverPort?.value || 8765;

    const response = await fetch(`http://${host}:${port}/cache/clear`, {
      method: 'POST',
      headers: obs.headers()
    });

    if (response.ok) {
      showStatus('Cache cleared successfully!', 'success');
      checkConnection(host, port);
    } else {
      throw new Error('Failed to clear cache');
    }
  } catch (error) {
    console.error('Clear cache failed:', error);
    obs.log('ext_http', 'fail', { path: '/cache/clear', error_type: 'process', error_message: error.message || String(error) });
    showStatus('Failed to clear cache - is the backend running?', 'error');
  }
}

/**
 * What is stored where (D3), in one sentence, from the backend's /health/json
 * `privacy.tm` (the defaults when the backend is not reachable).
 */
const DEFAULT_TM_POLICY = { persist_audio_sessions: false, persist_captions: true, retention_days: 30 };

function storageSentence(tm) {
  const p = Object.assign({}, DEFAULT_TM_POLICY, tm || {});
  const kept = ['your corrections'];
  if (p.persist_captions) kept.push('translated page subtitles from caption mode');
  if (p.persist_audio_sessions) kept.push('live-caption transcripts and their translations');
  const age = p.retention_days > 0 ? `, machine translations for ${p.retention_days} days after their last use` : '';
  const live = p.persist_audio_sessions ? '' : ', while live-caption audio and transcripts are only held in memory until the session ends';
  return 'Everything stays on this computer: Chrome keeps your settings, and the backend\'s translation memory ' +
    `(backend/data/translation_memory.db) keeps ${kept.join(' and ')}${age}${live}.`;
}

function showStorageNote(tm) {
  if (elements.storageNote) elements.storageNote.textContent = storageSentence(tm);
}

/**
 * Cloud providers (D4): which provider, if any, receives subtitle text, as the
 * backend reports it in /health/json `privacy.cloud` (the same list its startup log line names).
 */
function showCloudStatus(cloud) {
  if (!elements.cloudStatus) return;
  const receivers = (cloud && cloud.receivers) || [];
  elements.cloudStatus.textContent = receivers.length
    ? 'Subtitle text is sent to: ' + receivers.map((r) => `${r.provider} (${r.host}) for ${r.use}`).join('; ')
    : 'Cloud is off: no subtitle text leaves this computer.';
}

/** Older versions kept API keys here; the migration deleted them and left this notice. */
function showKeyNotice(removed) {
  if (!elements.keyNotice) return;
  elements.keyNotice.hidden = !(removed && removed.count);
  if (removed && removed.count && elements.keyNoticeText) {
    elements.keyNoticeText.textContent = `${removed.count} cloud API key(s) were removed from the extension. ` +
      window.STSettingsMigration.WHERE;
  }
}

async function dismissCloudNotice() {
  const result = await chrome.storage.local.get('settings');
  const settings = { ...(result.settings || {}) };
  delete settings.keysRemovedNotice;
  await chrome.storage.local.set({ settings });
  showKeyNotice(null);
}

/**
 * Clear the backend's translation memory (POST /tm/clear), after a confirmation.
 */
async function clearMemory() {
  if (!confirm('Delete everything in the translation memory (translations, your corrections, glossary)? This cannot be undone.')) {
    return;
  }
  const host = elements.serverHost?.value || 'localhost';
  const port = elements.serverPort?.value || 8765;
  try {
    const response = await fetch(`http://${host}:${port}/tm/clear`, { method: 'POST', headers: obs.headers() });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    const n = (data.removed && data.removed.translations) || 0;
    const c = (data.removed && data.removed.corrections) || 0;
    obs.log('ext_http', 'success', { path: '/tm/clear', removed_translations: n, removed_corrections: c });
    showStatus(`Translation memory cleared (${n} translations, ${c} corrections)`, 'success');
    checkConnection(host, port);
  } catch (error) {
    obs.log('ext_http', 'fail', { path: '/tm/clear', error_type: 'process', error_message: error.message || String(error) });
    showStatus('Failed to clear the translation memory - is the backend running?', 'error');
  }
}

/**
 * Initialize color picker events
 */
function initColorPickers() {
  [primaryColorPicker, secondaryColorPicker].forEach(picker => {
    if (!picker) return;
    picker.querySelectorAll('.color-option').forEach(option => {
      option.addEventListener('click', () => {
        picker.querySelectorAll('.color-option').forEach(o => o.classList.remove('selected'));
        option.classList.add('selected');
        const input = option.querySelector('input');
        if (input) input.checked = true;
        updatePreview();
      });
    });
  });
}

/**
 * Initialize all event listeners
 */
function initEventListeners() {
  // Save button
  if (elements.saveSettings) {
    elements.saveSettings.addEventListener('click', saveSettings);
  }

  // Reset button
  if (elements.resetDefaults) {
    elements.resetDefaults.addEventListener('click', resetDefaults);
  }

  // Export corrections
  if (elements.exportCorrections) {
    elements.exportCorrections.addEventListener('click', exportCorrections);
  }

  // Clear cache
  if (elements.clearCache) {
    elements.clearCache.addEventListener('click', clearCache);
  }

  if (elements.dismissCloudNotice) {
    elements.dismissCloudNotice.addEventListener('click', dismissCloudNotice);
  }

  // Clear the persistent translation memory (D3)
  if (elements.clearMemory) {
    elements.clearMemory.addEventListener('click', clearMemory);
  }

  // Font size slider
  if (elements.fontSize) {
    elements.fontSize.addEventListener('input', () => {
      if (elements.fontSizeValue) {
        elements.fontSizeValue.textContent = `${elements.fontSize.value}px`;
      }
      updatePreview();
    });
  }

  // Language selection changes
  if (elements.primaryLang) {
    elements.primaryLang.addEventListener('change', () => updatePreview());
  }
  if (elements.secondaryLang) {
    elements.secondaryLang.addEventListener('change', () => updatePreview());
  }
  if (elements.languageOrder) {
    elements.languageOrder.addEventListener('change', () => updatePreview());
  }

  // Connection check on server settings change
  if (elements.serverHost) {
    elements.serverHost.addEventListener('blur', () => {
      checkConnection(elements.serverHost.value, elements.serverPort?.value || 8765);
    });
  }
  if (elements.serverPort) {
    elements.serverPort.addEventListener('blur', () => {
      checkConnection(elements.serverHost?.value || 'localhost', elements.serverPort.value);
    });
  }

  // Initialize color pickers
  initColorPickers();
}

// Keyboard shortcuts: the list in options.html mirrors the manifest's suggested keys
// (extension/tests/options-shortcuts.test.js); this shows the keys actually bound,
// which the user may have changed in chrome://extensions/shortcuts.
function keyLabel(shortcut) {
  return shortcut.split('+').map((k) => (k === 'Period' ? '.' : k === 'Comma' ? ',' : k));
}

async function showBoundShortcuts() {
  if (!chrome.commands?.getAll) return;
  let commands = [];
  try {
    commands = await chrome.commands.getAll();
  } catch (_) {
    return;
  }
  for (const cmd of commands) {
    const item = document.querySelector(`.shortcut-item[data-command="${cmd.name}"] .shortcut-key`);
    if (!item) continue;
    item.textContent = '';
    if (!cmd.shortcut) {
      item.textContent = 'not set';
      continue;
    }
    for (const k of keyLabel(cmd.shortcut)) {
      const kbd = document.createElement('kbd');
      kbd.textContent = k;
      item.appendChild(kbd);
    }
  }
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', () => {
  console.log('[Options] Initializing...');
  showStorageNote(null);
  loadSettings();
  initEventListeners();
  initCaptionMode();
  showBoundShortcuts();

  // Periodic connection check
  setInterval(() => {
    const host = elements.serverHost?.value || 'localhost';
    const port = elements.serverPort?.value || 8765;
    checkConnection(host, port);
  }, 30000);
});
