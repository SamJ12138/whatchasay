/**
 * guards.js: what a content script accepts from the page (E16). Pure; node-tested.
 *
 * trustedFrameMessage(event, pageOrigin): a window 'message' event may carry a
 * subtitle cue only if it comes from the page's own origin and has the cue
 * shape. Cross-origin frames (ads, embedded players on other sites) cannot
 * inject text into the overlay this way; the extension's own frame scripts
 * relay their cues through the service worker instead (FRAME_CUE).
 */
(function (root) {
  function trustedFrameMessage(event, pageOrigin) {
    if (!event || !pageOrigin || pageOrigin === 'null') return false;
    if (event.origin !== pageOrigin) return false;
    const d = event.data;
    if (!d || typeof d !== 'object') return false;
    if (d.__subtrans !== 'cue' && d.__subtrans !== 'cueEnd') return false;
    const cue = d.cue;
    return !!cue && typeof cue === 'object' && typeof cue.text === 'string' && cue.text.length <= 2000;
  }

  const api = { trustedFrameMessage };
  root.STGuards = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
