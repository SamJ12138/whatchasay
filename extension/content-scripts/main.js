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
  let enabled = true;        // overlay shown (Alt+T)
  let tabEnabled = false;    // user enabled translation on this tab (popup); only then is /ws used (E17)
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
    sessionId: null,  // obs session of the offscreen /ws/asr connection
  };

  // Components (loaded from other scripts)
  const ws = window.subtitleWS;
  const detector = window.subtitleDetector;
  const overlay = window.subtitleOverlay;

  // Structured log. Only the top frame owns a session (one per tab page load);
  // records are relayed to the service worker, which posts them to /obs.
  const obs = window.STObs || { log() {}, init() {}, setSession() {}, newSessionId() { return null; } };
  obs.init({ context: IS_TOP ? 'content' : 'content-frame', relay: true });
  if (IS_TOP) obs.setSession(obs.newSessionId('cs'));

  function statusesOf(translations) {
    const out = {};
    for (const [lang, t] of Object.entries(translations || {})) out[lang] = (t && t.status) || 'ok';
    return out;
  }

  function reqErrorType(error) {
    const m = (error && error.message) || '';
    if (/timeout/i.test(m)) return 'timeout';
    if (/Connection closed/i.test(m)) return 'process';
    return 'unknown';
  }

  async function init() {
    if (!IS_TOP) {
      // Frames run detection so embedded players work and hand their cues to the top
      // frame through the service worker (not window.postMessage, E16), only while
      // translation is enabled on this tab.
      if (detector) {
        const relay = (kind) => (cue) => {
          try { chrome.runtime.sendMessage({ type: 'FRAME_CUE', kind, cue }).catch(() => {}); } catch (_) {}
        };
        detector.onCue(relay('cue'));
        detector.onCueEnd(relay('cueEnd'));
        chrome.runtime.onMessage.addListener((m) => {
          if (m && m.type === 'TAB_ENABLED') { if (m.enabled) detector.start(); else detector.stop(); }
        });
        chrome.runtime.sendMessage({ type: 'GET_TAB_STATE' }, (st) => {
          void chrome.runtime.lastError;
          if (st && st.enabled) detector.start();
        });
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

      if (detector) {
        detector.onCue(handleNewCue);
        detector.onCueEnd(handleCueEnd);
      }

      // Cues posted by same-origin frames only (E16); cross-origin frames use FRAME_CUE.
      window.addEventListener('message', (e) => {
        if (!window.STGuards || !window.STGuards.trustedFrameMessage(e, window.location.origin)) return;
        const d = e.data;
        if (d.__subtrans === 'cue') handleNewCue(d.cue);
        else if (d.__subtrans === 'cueEnd') handleCueEnd(d.cue);
      });

      chrome.runtime.onMessage.addListener(handleMessage);

      // Nothing is detected and no backend connection exists until the user
      // enables translation on this tab (popup); the worker remembers the choice.
      chrome.runtime.sendMessage({ type: 'GET_TAB_STATE' }, (st) => {
        void chrome.runtime.lastError;
        if (st && st.enabled) setTabEnabled(true);
      });
      setupKeyboardShortcuts();

      // Ask background whether live captions are already running for this tab
      chrome.runtime.sendMessage({ type: 'ASR_STATUS' }, (st) => {
        void chrome.runtime.lastError;
        if (st && st.capturing) { live.sessionId = st.sessionId || null; setLiveState(true, st.sourceLang); }
      });

      console.log('[SubTrans] Initialized, connected:', connected);
    } catch (error) {
      console.error('[SubTrans] Initialization error:', error);
      obs.log('ext_init', 'fail', { error_type: 'unknown', error_message: error.message || String(error) });
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

  function pageLanguage() {
    const track = detector && detector.videoElement && Array.from(detector.videoElement.textTracks || [])
      .find(t => t.mode !== 'disabled' && t.language);
    const raw = (track && track.language) || document.documentElement.lang || '';
    return raw.toLowerCase().split(/[-_]/)[0] || null;
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
      // language to assume for short / ambiguous cues (backend settings.lang_detect)
      source_lang_hint: pageLanguage() || undefined,
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
      ws.updateConfig(backendConfig()).catch(err => {
        console.error('[SubTrans] Config update failed:', err);
        obs.log('ext_ws_request', 'fail', { msg_type: 'config', error_type: reqErrorType(err), error_message: err.message || String(err) });
      });
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

  function setTabEnabled(on) {
    if (on === tabEnabled) return;
    tabEnabled = on;
    obs.log('ext_tab', 'success', { action: on ? 'enabled' : 'disabled' });
    if (on) {
      if (detector && enabled) detector.start();
      connectToBackend();
    } else {
      if (detector) detector.stop();
      if (ws) ws.disconnect();
      connected = false;
    }
  }

  async function handleNewCue(cue) {
    if (!enabled || !tabEnabled) return;
    if (live.active) {
      obs.log('ext_caption_detect', 'skip', { cue_id: cue.cueId, error_message: 'live captions own the overlay; cue ignored' });
      return; // live captions own the overlay while running
    }
    obs.log('ext_caption_detect', 'success', { cue_id: cue.cueId, text_len: (cue.text || '').length, method: detector ? detector.getMethod() : null });

    const cached = translationCache.get(cue.text);
    if (cached) {
      overlay.showTranslation(cue.cueId, cue.text, cached);
      obs.log('ext_render', 'success', { cue_id: cue.cueId, from: 'client_cache', targets: Object.keys(cached || {}) });
      return;
    }
    if (pendingTranslations.has(cue.cueId)) return;

    if (!connected) {
      overlay.showTranslation(cue.cueId, cue.text, {});
      obs.log('ext_cue_request', 'skip', { cue_id: cue.cueId, error_type: 'process', error_message: 'backend not connected; source text shown untranslated' });
      return;
    }

    pendingTranslations.set(cue.cueId, true);
    cueTextById.set(cue.cueId, cue.text);
    if (cueTextById.size > 500) cueTextById.delete(cueTextById.keys().next().value);

    const reqT0 = performance.now();
    try {
      const sentAt = performance.now();
      const result = await ws.translateCue(cue, {
        targetLanguages: targetLanguages(),
        skipPostEdit: !settings.refinerEnabled,
      });
      obs.log('ext_cue_request', 'success', { duration_ms: performance.now() - reqT0, cue_id: cue.cueId, server_cue_id: result.cue_id,
        server_ms: result.processing_time_ms, from_cache: result.from_cache, server_error: result.notes && result.notes.error ? String(result.notes.error).slice(0, 200) : undefined });
      if (result.cue_id && result.cue_id !== cue.cueId) cueTextById.set(result.cue_id, cue.text);
      // cache only complete, primary-engine answers; fallback / untranslated / error are asked again next time
      if (globalThis.STWsProtocol.allTargetsOk(result.translations)) {
        translationCache.set(cue.text, result.translations);
        if (translationCache.size > 500) translationCache.delete(translationCache.keys().next().value);
      }
      overlay.showTranslation(cue.cueId, cue.text, result.translations, { sourceLang: result.source_lang });
      if (result.cue_id && result.cue_id !== cue.cueId) {
        // server generated its own id; keep both keys pointing at the same entry for revisions
        overlay.currentCues.set(result.cue_id, overlay.currentCues.get(cue.cueId));
      }
      recordLatency(performance.now() - sentAt);
      obs.log('ext_render', 'success', { duration_ms: performance.now() - reqT0, cue_id: cue.cueId, from: 'backend', targets: Object.keys(result.translations || {}),
        statuses: statusesOf(result.translations), degraded: !!result.degraded });
    } catch (error) {
      console.error('[SubTrans] Translation failed:', error);
      obs.log('ext_cue_request', 'fail', { duration_ms: performance.now() - reqT0, cue_id: cue.cueId, error_type: reqErrorType(error), error_message: error.message || String(error), degraded: 'source text shown untranslated' });
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
      obs.log('ext_ws_request', 'success', { msg_type: 'correction', cue_id: correction.cueId });
    } catch (error) {
      console.error('[SubTrans] Correction submission failed:', error);
      obs.log('ext_ws_request', 'fail', { msg_type: 'correction', cue_id: correction.cueId, error_type: reqErrorType(error), error_message: error.message || String(error) });
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
        obs.log('ext_render', 'success', { kind: 'final', utterance_id: ev.utterance_id, lang: ev.lang, text_len: (ev.text || '').length,
          session_id: live.sessionId || undefined, server_to_glass_ms: ev.server_ts ? Math.round(Date.now() - ev.server_ts * 1000) : null });
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
        obs.log('ext_render', 'success', { kind: ev.type, utterance_id: ev.utterance_id, revision: ev.revision, engine: ev.engine || 'backend',
          targets: Object.keys(ev.translations || {}), statuses: statusesOf(ev.translations), mt_ms: ev.mt_ms, session_id: live.sessionId || undefined,
          server_to_glass_ms: ev.server_ts ? Math.round(Date.now() - ev.server_ts * 1000) : null, cue_on_screen: !!existing });
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
        obs.log('ext_render', 'fail', { kind: 'error_notice', session_id: live.sessionId || undefined, error_type: 'process', error_message: String(ev.error || 'unknown').slice(0, 300) });
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
          tabEnabled,
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
        if (detector && tabEnabled) { detector.stop(); detector.start(); }
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
        live.sessionId = message.capturing ? (message.sessionId || null) : null;
        setLiveState(!!message.capturing, message.sourceLang);
        sendResponse({ success: true });
        break;

      case 'ASR_CAPTION':
        handleLiveEvent(message.event);
        sendResponse({ success: true });
        break;

      // ---- per-tab translation (E17) and sub-frame cues (E16), from the service worker ----
      case 'TAB_ENABLED':
        setTabEnabled(!!message.enabled);
        sendResponse({ success: true });
        break;

      case 'FRAME_CUE':
        if (message.kind === 'cueEnd') handleCueEnd(message.cue);
        else if (message.cue && typeof message.cue.text === 'string') handleNewCue(message.cue);
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
        if (enabled) { overlay.show(); if (tabEnabled) detector.start(); } else { overlay.hide(); detector.stop(); }
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
      ws.updateConfig(backendConfig()).catch(err => {
        console.error('[SubTrans] Config update failed:', err);
        obs.log('ext_ws_request', 'fail', { msg_type: 'config', error_type: reqErrorType(err), error_message: err.message || String(err) });
      });
    }
    if (tabEnabled && settings.serverUrl && settings.serverUrl !== ws.serverUrl) {
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
