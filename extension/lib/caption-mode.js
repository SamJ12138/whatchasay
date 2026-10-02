/**
 * caption-mode.js: the page-caption path as an opt-in feature (D2).
 *
 * A fresh install has no site access. The live-caption (audio) path needs none:
 * the user's click on the action (or Alt+L) grants activeTab for that tab, and
 * the overlay script is injected with chrome.scripting.executeScript.
 *
 * Caption mode (reading the subtitles a page already shows) is off by default.
 * Turning it on in Options requests CAPTION_ORIGINS as optional host
 * permissions; the content scripts are then registered for the origins the user
 * granted (registrationFor), and a tab is still translated only after the user
 * enables it in the popup (E17). Turning it off unregisters them and gives the
 * permissions back.
 *
 * Pure: loaded by the service worker, the options page and node tests.
 */
(function (root) {
  // One pattern per site adapter in content-scripts/subtitle-detector.js.
  const CAPTION_ORIGINS = [
    'https://*.youtube.com/*',
    'https://*.netflix.com/*',
    'https://*.primevideo.com/*',
    'https://*.disneyplus.com/*',
    'https://*.hbomax.com/*',
    'https://*.max.com/*',
    'https://*.twitch.tv/*',
    'https://*.yfsp.tv/*',
    'https://*.bilibili.com/*',
    'https://*.iqiyi.com/*',
    'https://*.youku.com/*',
    'https://v.qq.com/*',
    'https://*.viu.com/*',
    'https://*.abema.tv/*',
  ];

  // Same files, same order, as the old manifest content_scripts entry.
  const CONTENT_SCRIPT_FILES = [
    'obs.js',
    'content-scripts/ws-protocol.js',
    'content-scripts/guards.js',
    'content-scripts/backend-port.js',
    'content-scripts/subtitle-detector.js',
    'content-scripts/overlay.js',
    'content-scripts/main.js',
  ];

  const REGISTRATION_ID = 'st-caption-mode';

  const EXPLANATION = 'Caption mode reads the subtitle text that supported video sites already show on the page ' +
    'and sends it to your local backend for translation; turning it on asks Chrome for access to those sites, ' +
    'and a tab is read only after you enable it in the popup.';

  /** The content-script registration caption mode needs, or null (off, or no site granted). */
  function registrationFor(captionMode, grantedOrigins) {
    const matches = captionMode ? (grantedOrigins || []).filter((o) => typeof o === 'string' && o) : [];
    if (!matches.length) return null;
    return {
      id: REGISTRATION_ID,
      matches,
      js: CONTENT_SCRIPT_FILES.slice(),
      allFrames: true,
      runAt: 'document_idle',
      persistAcrossSessions: true,
    };
  }

  /**
   * Inject the content scripts into a tab's top frame unless they run there
   * already, without racing caption mode's registered scripts: those run at
   * document_idle, so on a page still loading we wait for them (up to maxWaitMs)
   * before injecting; a page that finished loading without them (it loaded
   * before caption mode was switched on, or the tab is reached through activeTab)
   * gets them injected. Running them twice would leave two backend ports.
   *   probe()  -> {loaded, state} of the top frame (state = document.readyState)
   *   inject() -> runs CONTENT_SCRIPT_FILES in the top frame
   */
  async function ensureInjected({ probe, inject, sleep, maxWaitMs = 5000, stepMs = 250, settleMs = 300 }) {
    let p = await probe();
    let waited = 0;
    while (!p.loaded && p.state !== 'complete' && waited < maxWaitMs) {
      await sleep(stepMs);
      waited += stepMs;
      p = await probe();
    }
    if (!p.loaded && waited > 0) {
      await sleep(settleMs); // document_idle may fire just after load completes
      p = await probe();
    }
    if (p.loaded) return 'present';
    await inject();
    return 'injected';
  }

  const api = { CAPTION_ORIGINS, CONTENT_SCRIPT_FILES, REGISTRATION_ID, EXPLANATION, registrationFor, ensureInjected };
  root.STCaptionMode = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
