/**
 * Popup Script
 *
 * Status, quick toggles, and the Live Captions controls. Live captions are
 * started from here because chrome.tabCapture requires a user gesture on an
 * extension page.
 */

const obs = window.STObs;
obs.init({ context: 'popup', sessionId: obs.newSessionId('popup') });

document.addEventListener('DOMContentLoaded', async () => {
  const $ = (id) => document.getElementById(id);
  const connectionStatus = $('connection-status');
  const detectionMethod = $('detection-method');
  const translationCount = $('translation-count');
  const latencyValue = $('latency-value');
  const toggleOverlay = $('toggle-overlay');
  const toggleRefiner = $('toggle-refiner');
  const btnSwap = $('btn-swap');
  const btnClear = $('btn-clear');
  const btnFontUp = $('btn-font-up');
  const btnFontDown = $('btn-font-down');
  const btnPicker = $('btn-picker');
  const btnLive = $('btn-live');
  const liveLang = $('live-lang');
  const liveStatus = $('live-status');
  const btnPrepareTranslator = $('btn-prepare-translator');
  const openOptions = $('open-options');

  const LANG_NAMES = { auto: 'Auto-detect', en: 'English', zh: 'Chinese', bn: 'Bengali' };

  let settings = await getSettings();
  toggleOverlay.checked = settings.enabled !== false;
  toggleRefiner.checked = settings.refinerEnabled === true;
  liveLang.value = settings.asrSourceLang || 'auto';

  let liveState = { capturing: false, tabId: null };
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });

  updateStatus();
  updateLiveStatus();
  const statusInterval = setInterval(() => { updateStatus(); updateLiveStatus(); }, 1500);
  window.addEventListener('unload', () => clearInterval(statusInterval));

  // ---- quick toggles ----
  toggleOverlay.addEventListener('change', async (e) => {
    await updateSettings({ enabled: e.target.checked });
    sendCommand('toggle-overlay');
  });

  toggleRefiner.addEventListener('change', async (e) => {
    await updateSettings({ refinerEnabled: e.target.checked, usePostEditor: e.target.checked, fastMode: !e.target.checked });
  });

  btnSwap.addEventListener('click', () => sendCommand('swap-order'));
  btnClear.addEventListener('click', () => { if (activeTab?.id) chrome.tabs.reload(activeTab.id); });
  btnFontUp.addEventListener('click', () => sendCommand('increase-font'));
  btnFontDown.addEventListener('click', () => sendCommand('decrease-font'));

  btnPicker.addEventListener('click', () => {
    if (!activeTab?.id) return;
    chrome.tabs.sendMessage(activeTab.id, { type: 'START_PICKER' }, (response) => {
      void chrome.runtime.lastError;
      if (response?.success) window.close();
    });
  });

  // ---- live captions ----
  liveLang.addEventListener('change', async () => {
    const sourceLang = liveLang.value;
    await updateSettings({ asrSourceLang: sourceLang });
    chrome.runtime.sendMessage({ type: 'ASR_SET_LANG', sourceLang }, () => void chrome.runtime.lastError);
  });

  btnLive.addEventListener('click', async () => {
    if (!activeTab?.id) return;
    btnLive.disabled = true;
    try {
      if (liveState.capturing) {
        await sendToBackground({ type: 'ASR_STOP' });
        setLiveUi(false);
      } else {
        liveStatus.textContent = 'Starting…';
        const res = await sendToBackground({ type: 'ASR_START', tabId: activeTab.id, sourceLang: liveLang.value });
        if (res && res.ok) {
          setLiveUi(true);
          liveStatus.textContent = 'Listening — captions appear on the video';
          setTimeout(() => window.close(), 600);
        } else {
          liveStatus.textContent = 'Failed: ' + ((res && res.error) || 'unknown error');
          liveStatus.className = 'live-status error';
        }
      }
    } finally {
      btnLive.disabled = false;
    }
  });

  btnPrepareTranslator.addEventListener('click', async () => {
    // Runs in the popup (a top-level extension page with user activation), which is
    // what the Translator API needs to download language packs.
    if (typeof Translator === 'undefined') {
      liveStatus.textContent = 'On-device translation needs Chrome 138+ on desktop.';
      liveStatus.className = 'live-status error';
      return;
    }
    const langs = ['en', 'zh', 'bn'];
    const targets = [settings.primaryLang, settings.secondaryLang].filter(l => l && l !== 'none');
    const pairs = [];
    for (const src of langs) for (const tgt of targets) if (src !== tgt) pairs.push([src, tgt]);
    liveStatus.className = 'live-status';
    let ready = 0, failed = [];
    for (const [src, tgt] of pairs) {
      try {
        liveStatus.textContent = `Preparing ${src}→${tgt}…`;
        const avail = await Translator.availability({ sourceLanguage: src, targetLanguage: tgt });
        if (avail === 'unavailable') { failed.push(`${src}→${tgt}`); continue; }
        await Translator.create({
          sourceLanguage: src, targetLanguage: tgt,
          monitor(m) { m.addEventListener('downloadprogress', (e) => { liveStatus.textContent = `Downloading ${src}→${tgt}: ${Math.round(e.loaded * 100)}%`; }); },
        });
        ready++;
      } catch (e) {
        failed.push(`${src}→${tgt}`);
        obs.log('ext_translate_ondevice', 'fail', { action: 'prepare', pair: `${src}>${tgt}`, error_type: 'process', error_message: e.message || String(e) });
      }
    }
    liveStatus.textContent = `On-device translation ready for ${ready}/${pairs.length} pairs` + (failed.length ? ` (unavailable: ${failed.join(', ')})` : '');
    if (ready > 0) await updateSettings({ translationEngine: settings.translationEngine === 'backend' ? 'backend' : 'auto' });
  });

  openOptions.addEventListener('click', (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });

  // ---- helpers ----
  function setLiveUi(active) {
    liveState.capturing = active;
    btnLive.querySelector('.btn-icon').textContent = active ? '⏹️' : '🎙️';
    btnLive.querySelector('.btn-label').textContent = active ? 'Stop Live Captions' : 'Start Live Captions';
    btnLive.classList.toggle('active', active);
  }

  async function updateLiveStatus() {
    const st = await sendToBackground({ type: 'ASR_STATUS' });
    if (!st) return;
    const mine = st.capturing && (!activeTab || st.tabId === activeTab.id);
    setLiveUi(!!mine);
    if (st.capturing && !mine) {
      liveStatus.textContent = 'Live captions are running in another tab';
      liveStatus.className = 'live-status';
      return;
    }
    if (mine) {
      const off = st.offscreen || {};
      const last = st.lastStatus || {};
      if (last.warning === 'no-audio') {
        liveStatus.textContent = 'No audio reaching capture (DRM site or paused video)';
        liveStatus.className = 'live-status error';
      } else {
        const lang = st.sourceLang && st.sourceLang !== 'auto' ? LANG_NAMES[st.sourceLang] || st.sourceLang : 'auto-detect';
        liveStatus.textContent = `Listening (${lang})${off.connected === false ? ' — backend disconnected' : ''}${off.chromeTranslator ? ' · on-device MT' : ''}`;
        liveStatus.className = 'live-status ok';
      }
    } else if (!st.supported) {
      liveStatus.textContent = 'Tab capture not available in this browser';
      liveStatus.className = 'live-status error';
    } else if (!liveStatus.textContent || liveStatus.textContent.startsWith('Listening')) {
      liveStatus.textContent = 'For videos without subtitles: captions + translation from the tab audio';
      liveStatus.className = 'live-status';
    }
  }

  function sendToBackground(msg) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(msg, (response) => {
        void chrome.runtime.lastError;
        resolve(response || null);
      });
    });
  }

  function getSettings() {
    return sendToBackground({ type: 'GET_SETTINGS' }).then(r => r || {});
  }

  async function updateSettings(newSettings) {
    const r = await sendToBackground({ type: 'UPDATE_SETTINGS', settings: newSettings });
    if (r && r.settings) settings = r.settings;
    return r;
  }

  function sendCommand(command) {
    if (activeTab?.id) chrome.tabs.sendMessage(activeTab.id, { type: 'COMMAND', command }, () => void chrome.runtime.lastError);
  }

  function updateStatus() {
    if (!activeTab?.id) return;
    chrome.tabs.sendMessage(activeTab.id, { type: 'GET_STATUS' }, (response) => {
      if (chrome.runtime.lastError || !response) {
        connectionStatus.textContent = 'Not active on this page';
        connectionStatus.className = 'status-value disconnected';
        detectionMethod.textContent = '-';
        translationCount.textContent = '-';
        latencyValue.textContent = '-';
        return;
      }
      connectionStatus.textContent = response.connected ? 'Connected' : 'Disconnected';
      connectionStatus.className = 'status-value ' + (response.connected ? 'connected' : 'disconnected');
      detectionMethod.textContent = response.live?.active ? 'Live captions' : (response.detectionMethod || 'None');
      translationCount.textContent = (response.stats && response.stats.requestsCompleted) || 0;
      const l = response.latency || {};
      const parts = [];
      if (l.cue_p50_ms != null) parts.push(`${l.cue_p50_ms} ms/cue`);
      if (l.mt_p50_ms != null) parts.push(`MT ${l.mt_p50_ms} ms`);
      latencyValue.textContent = parts.length ? parts.join(' · ') : '-';
    });
  }
});
