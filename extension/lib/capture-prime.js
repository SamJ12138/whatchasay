/**
 * capture-prime.js: keep the captured tab's audio output running (observations A11).
 *
 * When a silent tab starts to sound, Chrome's tab capture misses the first few hundred
 * milliseconds: the capture attaches to the tab's audio output once that is running, and a
 * video that opens with speech loses its first words. From the moment live captions start,
 * the content script plays an inaudible tone on the tab (Web Audio, gain 0.00002: -94 dBFS,
 * under the capture's own silence threshold), so the output is already running when the
 * viewer presses play. A page the viewer has not interacted with may keep the tone's
 * AudioContext suspended (Chrome's autoplay policy); it is then resumed at the next click
 * or key on the page, and the skip is logged.
 *
 * createPrimer({AudioContext, document, log}) -> {start(), stop(), state()}; log(event,
 * context) is the ext_prime stage. Pure: loaded by the content script and node tests.
 */
(function (root) {
  const GAIN = 0.00002;
  const GESTURES = ['pointerdown', 'keydown'];

  function createPrimer(deps = {}) {
    const Ctx = deps.AudioContext;
    const doc = deps.document;
    const log = typeof deps.log === 'function' ? deps.log : () => {};
    let ctx = null;
    let listening = null;   // the gesture handler while waiting for the policy

    function unlisten() {
      if (!listening || !doc) return;
      for (const t of GESTURES) { try { doc.removeEventListener(t, listening, true); } catch (_) {} }
      listening = null;
    }

    async function resumeAfterGesture() {
      const c = ctx;
      unlisten();
      if (!c) return;
      try { await c.resume(); } catch (_) {}
      if (c !== ctx) return;
      if (c.state === 'running') log('success', { state: 'running', after: 'gesture' });
      else log('skip', { state: c.state, after: 'gesture', error_type: 'input_invalid', error_message: 'tone still ' + c.state + ' after a gesture' });
    }

    /** Start the tone. Resolves {state}: 'running', 'suspended' (waiting for a gesture) or null. */
    async function start() {
      if (ctx) return { state: ctx.state };
      if (typeof Ctx !== 'function') {
        log('skip', { error_type: 'process', error_message: 'no AudioContext on this page' });
        return { state: null };
      }
      try {
        ctx = new Ctx();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        gain.gain.value = GAIN;
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        if (ctx.state !== 'running') { try { await ctx.resume(); } catch (_) {} }
      } catch (e) {
        log('fail', { error_type: 'process', error_message: 'tone: ' + (e && e.message ? e.message : String(e)) });
        stop();
        return { state: null };
      }
      if (ctx.state === 'running') {
        log('success', { state: 'running' });
      } else {
        // the autoplay policy: no user activation on this page yet
        log('skip', { state: ctx.state, error_type: 'input_invalid',
                      error_message: 'tone ' + ctx.state + ' by the autoplay policy: starts at the first click or key on the page (interact with the page)' });
        if (doc && typeof doc.addEventListener === 'function') {
          listening = () => { resumeAfterGesture(); };
          for (const t of GESTURES) doc.addEventListener(t, listening, true);
        }
      }
      return { state: ctx.state };
    }

    function stop() {
      unlisten();
      const c = ctx;
      ctx = null;
      if (!c) return;
      try { if (typeof c.close === 'function') c.close().catch(() => {}); } catch (_) {}
    }

    function state() { return ctx ? ctx.state : null; }

    return { start, stop, state };
  }

  const api = { createPrimer, GAIN };
  root.STCapturePrime = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
