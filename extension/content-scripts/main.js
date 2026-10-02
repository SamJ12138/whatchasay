/**
 * Main Content Script
 *
 * Orchestrates:
 * - Subtitle detection (existing captions on the page)
 * - WebSocket communication with the local backend
 * - Live captions delivered by the background/offscreen pipeline (tab audio)
 * - Overlay display, revisions, user corrections
 * - Message handling from background/popup
 */

(function() {
  'use strict';

  if (window.__subtitleTranslatorInitialized) {
    return;
  }
  window.__subtitleTranslatorInitialized = true;

  // Only the top frame renders the overlay and talks to the backend.
  const IS_TOP = window === window.top;

  // State
  let settings = null;
  let enabled = true;
  let connected = false;
  const translationCache = new Map();   // raw cue text -> translations
  const cueTextById = new Map();        // cueId -> raw cue text (for revisions)
  const pendingTranslations = new Map();

  // Live captions state
  const live = {
    active: false,
    sourceLang: null,
    lastUtteranceId: null,
    hideTimer: null,
    latencySamples: [],
  };

  // Components (loaded from other scripts)
  const ws = window.subtitleWS;
  const detector = window.subtitleDetector;
  const overlay = window.subtitleOverlay;

  async function init() {
    if (!IS_TOP) {
      // Frames still run detection so embedded players work, but forward cues to the top frame.
      if (detector) {
        detector.onCue((cue) => window.top.postMessage({ __subtrans: 'cue', cue }, '*'));
        detector.onCueEnd((cue) => window.top.postMessage({ __subtrans: 'cueEnd', cue }, '*'));
        detector.start();
      }
      return;
    }

    try {
      settings = await loadSettings();
      if (overlay) {
        overlay.init();
        overlay.onCorrection(handleCorrection);
        applyOverlaySettings();
      }

      if (settings.autoConnect !== false) {
        await connectToBackend();
      }

      if (detector) {
        detector.onCue(handleNewCue);
        detector.onCueEnd(handleCueEnd);
        if (enabled) detector.start();
      }

      window.addEventListener('message', (e) => {
        const d = e.data;
        if (!d || !d.__subtrans) return;
        if (d.__subtrans === 'cue') handleNewCue(d.cue);
        else if (d.__subtrans === 'cueEnd') handleCueEnd(d.cue);
      });

      chrome.runtime.onMessage.addListener(handleMessage);
      setupKeyboardShortcuts();

      // Ask background whether live captions are already running for this tab
      chrome.runtime.sendMessage({ type: 'ASR_STATUS' }, (st) => {
        void chrome.runtime.lastError;
        if (st && st.capturing) setLiveState(true, st.sourceLang);
      });

      console.log('[SubTrans] Initialized, connected:', connected);
    } catch (error) {
      console.error('[SubTrans] Initialization error:', error);
    }
  }

  function loadSettings() {
    return new Promise((resolve) => {
      let done = false;
      try {
        chrome.runtime.sendMessage({ type: 'GET_SETTINGS' }, (response) => {
          if (done) return;
          done = true;
          if (chrome.runtime.lastError) { resolve(getDefaultSettings()); return; }
          resolve(response || getDefaultSettings());
        });
      } catch (error) {
        done = true;
        resolve(getDefaultSettings());
      }
      setTimeout(() => { if (!done) { done = true; resolve(getDefaultSettings()); } }, 2000);
    });
  }

  function getDefaultSettings() {
    return {
      enabled: true,
      serverUrl: 'ws://127.0.0.1:8765/ws',
      primaryLang: 'en',
      secondaryLang: 'zh',
      targetLanguages: ['en', 'zh'],
      fontSize: 20,
      fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", "Noto Sans Bengali", sans-serif',
      primaryOnTop: true,
      primaryColor: '#ffffff',
      secondaryColor: '#ffeb3b',
      maxLines: 2,
      maxCharsLatin: 42,
      maxCharsCJK: 22,
      overlayOpacity: 0.9,
      overlayPosition: 'above',
      refinerEnabled: false,
      fastMode: true,
      strictMeaningLock: true,
      showOriginal: true,
      showPartials: true,
      autoConnect: true,
      asrSourceLang: 'auto',
    };
  }

  function targetLanguages() {
    const langs = [settings.primaryLang || 'en'];
    if (settings.secondaryLang && settings.secondaryLang !== 'none' && settings.secondaryLang !== langs[0]) {
      langs.push(settings.secondaryLang);
    }
    return langs;
  }

  function maxCharsByLang(langs) {
    const out = {};
    for (const lang of langs) {
      if (['zh', 'ja', 'ko'].includes(lang)) out[lang] = settings.maxCharsCJK || settings.maxCharsZh || 22;
      else if (lang === 'bn' || lang === 'hi') out[lang] = 38;
      else out[lang] = settings.maxCharsLatin || settings.maxCharsEn || 42;
    }
    return out;
  }

  function backendConfig() {
    const langs = targetLanguages();
    return {
      target_languages: langs,
      use_post_editor: !!settings.refinerEnabled,
      refiner_enabled: !!settings.refinerEnabled,
      fast_mode: settings.fastMode !== false,
      strict_meaning_lock: settings.strictMeaningLock !== false,
      max_lines: settings.maxLines || 2,
      max_chars_by_lang: maxCharsByLang(langs),
      cloud_keys: settings.cloudKeys || undefined,
    };
  }

  // ---------------------------------------------------------------------------
  // Backend connection
  // ---------------------------------------------------------------------------

  async function connectToBackend() {
    const serverUrl = settings.serverUrl || 'ws://127.0.0.1:8765/ws';
    if (!ws) return;

    ws.onConnected(() => {
      connected = true;
      ws.updateConfig(backendConfig()).catch(err => console.error('[SubTrans] Config update failed:', err));
    });
    ws.onDisconnected(() => { connected = false; });
    ws.onError((error) => console.error('[SubTrans] WebSocket error:', error));
    ws.onPush(handlePush);

    try {
      await ws.connect(serverUrl);
    } catch (error) {
      setTimeout(() => {
        if (!connected) ws.connect(serverUrl).catch(e => console.error('[SubTrans] Retry failed:', e));
      }, 3000);
    }
  }

  /** Server-push messages: refined translations arriving after the fast one. */
  function handlePush(message) {
    if (!message || message.type !== 'revision' || !message.payload) return;
    applyRevision(message.payload);
  }

  function applyRevision(payload) {
    const cueId = payload.cue_id;
    if (!cueId) return;
    const rawText = cueTextById.get(cueId);
    if (rawText) translationCache.set(rawText, payload.translations);
    if (overlay.currentCues.has(cueId)) {
      const existing = overlay.currentCues.get(cueId);
      overlay.showTranslation(cueId, existing.original, payload.translations, { revised: true, sourceLang: payload.source_lang });
    }
  }

  // ---------------------------------------------------------------------------
  // Existing-subtitle path
  // ---------------------------------------------------------------------------

  async function handleNewCue(cue) {
    if (!enabled) return;
    if (live.active) return; // live captions own the overlay while running

    const cached = translationCache.get(cue.text);
    if (cached) {
      overlay.showTranslation(cue.cueId, cue.text, cached);
      return;
    }
    if (pendingTranslations.has(cue.cueId)) return;

    if (!connected) {
      overlay.showTranslation(cue.cueId, cue.text, {});
      return;
    }

    pendingTranslations.set(cue.cueId, true);
    cueTextById.set(cue.cueId, cue.text);
    if (cueTextById.size > 500) cueTextById.delete(cueTextById.keys().next().value);

    try {
      const sentAt = performance.now();
      const result = await ws.translateCue(cue, {
        targetLanguages: targetLanguages(),
        skipPostEdit: !settings.refinerEnabled,
      });
      if (result.cue_id && result.cue_id !== cue.cueId) cueTextById.set(result.cue_id, cue.text);
      translationCache.set(cue.text, result.translations);
      if (translationCache.size > 500) translationCache.delete(translationCache.keys().next().value);
      overlay.showTranslation(cue.cueId, cue.text, result.translations, { sourceLang: result.source_lang });
      if (result.cue_id && result.cue_id !== cue.cueId) {
        // server generated its own id; keep both keys pointing at the same entry for revisions
        overlay.currentCues.set(result.cue_id, overlay.currentCues.get(cue.cueId));
      }
      recordLatency(performance.now() - sentAt);
    } catch (error) {
      console.error('[SubTrans] Translation failed:', error);
      overlay.showTranslation(cue.cueId, cue.text, {});
    } finally {
      pendingTranslations.delete(cue.cueId);
    }
  }

  function handleCueEnd(cue) {
    if (live.active) return;
    overlay.hideTranslation(cue.cueId);
  }

  async function handleCorrection(correction) {
    if (!connected) return;
    try {
      await ws.submitCorrection(
        correction.cueId,
        correction.sourceText,
        correction.originalTranslation,
        correction.correctedTranslation,
        correction.sourceLang || null
      );
      const updated = {};
      for (const [lang, text] of Object.entries(correction.correctedTranslation)) {
        updated[lang] = { lines: [text], single_line: text, display_text: text };
      }
      translationCache.set(correction.sourceText, updated);
    } catch (error) {
      console.error('[SubTrans] Correction submission failed:', error);
    }
  }

  // ---------------------------------------------------------------------------
  // Live captions (tab audio -> ASR -> MT), events relayed by background.js
  // ---------------------------------------------------------------------------

  const LANG_NAMES = { en: 'English', zh: 'Chinese', bn: 'Bengali', vi: 'Vietnamese', ja: 'Japanese', ko: 'Korean' };

  function setLiveState(active, sourceLang) {
    live.active = active;
    live.sourceLang = sourceLang || null;
    if (active) {
      overlay.clear();
      overlay.showNotice(sourceLang && sourceLang !== 'auto' ? `Listening (${LANG_NAMES[sourceLang] || sourceLang})` : 'Listening… detecting language', 'info', 5000);
    } else {
      overlay.clearPartial();
      overlay.showNotice('Live captions stopped', 'info', 2500);
      if (live.hideTimer) clearTimeout(live.hideTimer);
    }
  }

  function liveCueId(ev) {
    return ev.cue_id || ('asr_' + ev.utterance_id);
  }

  function scheduleLiveHide(cueId, ms) {
    if (live.hideTimer) clearTimeout(live.hideTimer);
    live.hideTimer = setTimeout(() => {
      // hide only if nothing newer arrived
      const last = Array.from(overlay.currentCues.keys()).pop();
      if (last === cueId) overlay.hideTranslation(cueId);
    }, ms);
  }

  function handleLiveEvent(ev) {
    if (!ev || !ev.type) return;
    switch (ev.type) {
      case 'partial':
        if (settings.showPartials !== false) overlay.showPartial('asr_' + ev.utterance_id, ev.text, ev.lang);
        break;
      case 'final': {
        const cueId = 'asr_' + ev.utterance_id;
        live.lastUtteranceId = ev.utterance_id;
        // drop older live cues so the stack shows one utterance at a time
        for (const id of Array.from(overlay.currentCues.keys())) {
          if (id !== cueId) overlay.currentCues.delete(id);
        }
        overlay.showTranslation(cueId, ev.text, {}, { sourceLang: ev.lang });
        if (ev.server_ts) recordLatency((Date.now() / 1000 - ev.server_ts) * 1000, 'final');
        scheduleLiveHide(cueId, 8000);
        break;
      }
      case 'translation':
      case 'revision': {
        const cueId = 'asr_' + ev.utterance_id;
        const existing = overlay.currentCues.get(cueId);
        // targets arrive one at a time; merge into what is already shown for this cue
        const merged = Object.assign({}, existing ? existing.translations : {}, ev.translations || {});
        overlay.showTranslation(cueId, existing ? existing.original : ev.source_text, merged, { revised: ev.revision > 1 || !!existing, sourceLang: ev.source_lang });
        if (ev.mt_ms !== undefined && !(ev.targets_pending > 0)) recordLatency(ev.mt_ms, 'mt');
        scheduleLiveHide(cueId, 8000);
        break;
      }
      case 'reset':
        // language switched after auto-detect: provisional captions are discarded
        overlay.clear();
        break;
      case 'lid':
        live.sourceLang = ev.lang;
        if (ev.source === 'provisional') overlay.showNotice(`Listening… assuming ${LANG_NAMES[ev.lang] || ev.lang} until detected`, 'info', 3000);
        else overlay.showNotice(`${ev.source === 'auto' ? 'Detected' : 'Language'}: ${LANG_NAMES[ev.lang] || ev.lang}`, 'info', 3000);
        break;
      case 'status':
        if (ev.warning === 'no-audio') overlay.showNotice(ev.message || 'No audio reaching capture', 'warn', 0);
        else if (ev.warning === 'disconnected') overlay.showNotice(ev.message || 'Backend disconnected', 'warn', 6000);
        else if (ev.warning === null && overlay.notice && overlay.notice.kind === 'warn') overlay.showNotice(null);
        if (typeof ev.capturing === 'boolean' && ev.capturing !== live.active) setLiveState(ev.capturing, ev.sourceLang);
        break;
      case 'error':
        overlay.showNotice('Live captions error: ' + (ev.error || 'unknown'), 'warn', 8000);
        break;
      case 'ready':
      case 'pong':
      case 'config_updated':
      case 'stopped':
        break;
      default:
        break;
    }
  }

  // ---------------------------------------------------------------------------
  // Latency bookkeeping (visible in the popup)
  // ---------------------------------------------------------------------------

  const latency = { cue: [], final: [], mt: [] };
  function recordLatency(ms, kind = 'cue') {
    const arr = latency[kind] || latency.cue;
    arr.push(ms);
    if (arr.length > 100) arr.shift();
  }
  function p50(arr) {
    if (!arr.length) return null;
    const s = [...arr].sort((a, b) => a - b);
    return Math.round(s[Math.floor(s.length / 2)]);
  }

  // ---------------------------------------------------------------------------
  // Messages from background / popup
  // ---------------------------------------------------------------------------

  function handleMessage(message, sender, sendResponse) {
    switch (message.type) {
      case 'COMMAND':
        handleCommand(message.command);
        sendResponse({ success: true });
        break;

      case 'GET_STATUS':
        sendResponse({
          connected,
          enabled,
          detectionMethod: detector ? detector.getMethod() : null,
          cacheSize: translationCache.size,
          stats: ws ? ws.getStats() : {},
          live: { active: live.active, sourceLang: live.sourceLang },
          latency: { cue_p50_ms: p50(latency.cue), final_p50_ms: p50(latency.final), mt_p50_ms: p50(latency.mt) },
        });
        break;

      case 'SETTINGS_UPDATED':
        settings = message.settings;
        applySettings();
        sendResponse({ success: true });
        break;

      case 'PAGE_LOADED':
        if (detector) { detector.stop(); detector.start(); }
        sendResponse({ success: true });
        break;

      case 'START_PICKER':
        detector.startElementPicker((result) => {
          if (result) {
            detector.setManualSelector(result.selector);
            chrome.runtime.sendMessage({ type: 'PICKER_RESULT', selector: result.selector, text: result.text });
          }
        });
        sendResponse({ success: true });
        break;

      case 'SET_MANUAL_SELECTOR':
        detector.setManualSelector(message.selector, message.container);
        sendResponse({ success: true });
        break;

      case 'CLEAR_MANUAL_SELECTOR':
        detector.clearManualSelector();
        sendResponse({ success: true });
        break;

      // ---- live captions ----
      case 'ASR_STATE':
        setLiveState(!!message.capturing, message.sourceLang);
        sendResponse({ success: true });
        break;

      case 'ASR_CAPTION':
        handleLiveEvent(message.event);
        sendResponse({ success: true });
        break;

      default:
        sendResponse({ error: 'Unknown message type' });
    }
    return true;
  }

  function handleCommand(command) {
    switch (command) {
      case 'toggle-overlay':
        enabled = !enabled;
        if (enabled) { overlay.show(); detector.start(); } else { overlay.hide(); detector.stop(); }
        break;
      case 'swap-order':
        overlay.swapOrder();
        break;
      case 'increase-font':
        overlay.increaseFontSize();
        break;
      case 'decrease-font':
        overlay.decreaseFontSize();
        break;
      case 'toggle-edit': {
        const lastCue = Array.from(overlay.currentCues.keys()).pop();
        if (lastCue) overlay.enableEditMode(lastCue);
        break;
      }
    }
  }

  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (e.altKey && e.key === 'e') {
        e.preventDefault();
        handleCommand('toggle-edit');
      }
    });
  }

  function applyOverlaySettings() {
    const langs = targetLanguages();
    settings.targetLanguages = langs;
    overlay.updateSettings({
      fontSize: settings.fontSize,
      fontFamily: settings.fontFamily,
      opacity: settings.overlayOpacity,
      primaryColor: settings.primaryColor || settings.enColor,
      secondaryColor: settings.secondaryColor || settings.zhColor,
      primaryLang: settings.primaryLang,
      secondaryLang: settings.secondaryLang,
      targetLanguages: langs,
    });
    overlay.primaryOnTop = settings.primaryOnTop !== false;
    overlay.showOriginal = settings.showOriginal !== false;
  }

  function applySettings() {
    applyOverlaySettings();
    if (connected) {
      ws.updateConfig(backendConfig()).catch(err => console.error('[SubTrans] Config update failed:', err));
    }
    if (settings.serverUrl && settings.serverUrl !== ws.serverUrl) {
      ws.disconnect();
      connectToBackend();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
