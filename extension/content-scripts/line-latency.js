/**
 * line-latency.js: per-line latency of live captions as the viewer feels it
 * (docs/latency.md). Pure bookkeeping, no DOM and no chrome.*: the content script
 * calls it where it draws live text and logs what it returns (stage `line_latency`).
 *
 * The backend says when the audio of a line's first and last recognised word reached
 * it (w_first / w_last, epoch seconds, on every partial, final and translation) and
 * when the session's first audio frame did (w_session); the content script runs on the
 * same machine, so "now" minus those is the wait the viewer had.
 *
 *   first_display_ms      first word's audio -> first text of the line on screen
 *                         (source text or a draft translation)
 *   final_ms              last word's audio  -> final translation on screen (all
 *                         targets; a line with nothing to translate: its final caption)
 *   first_translation_ms  first word's audio -> first translated text on screen
 *
 * Flicker, per line: how often the translation on screen changed (translation_revisions:
 * a draft or the final replacing other text) and how many of those changes were not a
 * pure append (non_append_revisions: the new text does not start with the old one, its
 * closing punctuation aside), i.e. the viewer had to read it again.
 *
 * createTracker(now) -> {
 *   start()                        a live session begins
 *   reset()                        the recognizer was replaced (utterance ids restart)
 *   text(cueId, ev, {final})       source text of the line was drawn; null
 *   translation(cueId, ev)         a translation was drawn; the line's record once its
 *                                  final translation is complete, else null
 *   confirmed(ev)                  text in a confirmed language was drawn; the session's
 *                                  first_confirmed record the first time, else null
 * }
 */
(function (root) {
  const certain = (status) => status === 'confirmed' || status === 'manual';
  const num = (v) => typeof v === 'number' && isFinite(v);
  // what a translation engine puts at the end of a sentence, and a prefix's translation gets too
  const CLOSING = /[\s.,!?;:\u3002\uff01\uff1f\uff0c\u3001\u2026\u0964\u0965'"\u201d\u2019)\]]+$/;

  function createTracker(now) {
    const clock = now || (() => Date.now());
    let lines = new Map();
    let firstConfirmedDone = false;
    // source text drawn so far: [{w: audio arrival of the last word shown, at: when}], w increasing
    let drawn = [];

    // When the word whose audio arrived at w was first on screen: now, unless an earlier
    // line's partial already showed it (a line cut off a longer one by the length rule).
    function firstDrawn(w, t) {
      const earlier = drawn.find((d) => d.w >= w);
      return earlier ? Math.min(earlier.at, t) : t;
    }

    function line(cueId, ev, create) {
      const t = clock();
      let ln = lines.get(cueId);
      if (!ln && create && num(ev.w_first)) {
        ln = { wFirst: ev.w_first, wLast: null, lang: ev.lang || null, langStatus: null, shownAt: firstDrawn(ev.w_first, t),
               finalTextAt: null, firstTranslationAt: null, draftShown: false, targets: [], done: false,
               shown: {}, drafts: 0, revisions: 0, nonAppend: 0 };
        lines.set(cueId, ln);
        if (lines.size > 50) lines.delete(lines.keys().next().value);
      }
      if (ln) {
        if (num(ev.w_last)) ln.wLast = ev.w_last;
        if (ev.lang_status) ln.langStatus = ev.lang_status;
      }
      return ln;
    }

    return {
      start() { lines = new Map(); drawn = []; firstConfirmedDone = false; },

      reset() { lines = new Map(); drawn = []; },

      text(cueId, ev, opts) {
        ev = ev || {};
        const ln = line(cueId, ev, true);
        if (ln && opts && opts.final) ln.finalTextAt = clock();
        if (num(ev.w_last) && (!drawn.length || drawn[drawn.length - 1].w < ev.w_last)) {
          drawn.push({ w: ev.w_last, at: clock() });
          if (drawn.length > 200) drawn.shift();
        }
        return null;
      },

      translation(cueId, ev) {
        ev = ev || {};
        const ln = line(cueId, ev, !!ev.draft);  // a draft can be the line's first text
        if (!ln || ln.done) return null;
        const t = clock();
        const targets = Object.keys(ev.translations || {});
        if (targets.length && ln.firstTranslationAt === null) ln.firstTranslationAt = t;
        for (const tgt of targets) {
          const tr = ev.translations[tgt] || {};
          const text = tr.display_text || tr.single_line || '';
          const before = ln.shown[tgt];
          if (before !== undefined && text !== before) {
            ln.revisions++;
            if (!text.startsWith(before.replace(CLOSING, ''))) ln.nonAppend++;
          }
          ln.shown[tgt] = text;
        }
        if (ev.draft) { ln.draftShown = true; ln.drafts++; return null; }
        for (const tgt of targets) if (!ln.targets.includes(tgt)) ln.targets.push(tgt);
        if (ev.targets_pending > 0 || !num(ln.wLast)) return null;
        ln.done = true;
        // nothing to translate: the final caption was the last text this line got
        const completeAt = ln.targets.length ? t : (ln.finalTextAt !== null ? ln.finalTextAt : t);
        return {
          kind: 'line', cue_id: cueId, lang: ln.lang, lang_status: ln.langStatus,
          first_display_ms: Math.round(ln.shownAt - ln.wFirst * 1000),
          final_ms: Math.round(completeAt - ln.wLast * 1000),
          first_translation_ms: ln.firstTranslationAt === null ? null : Math.round(ln.firstTranslationAt - ln.wFirst * 1000),
          draft_shown: ln.draftShown, targets: ln.targets.slice(),
          drafts: ln.drafts, translation_revisions: ln.revisions, non_append_revisions: ln.nonAppend,
        };
      },

      confirmed(ev) {
        if (firstConfirmedDone || !ev || !certain(ev.lang_status) || !num(ev.w_session)) return null;
        firstConfirmedDone = true;
        const t = clock();
        return { kind: 'first_confirmed', lang: ev.lang || null, lang_status: ev.lang_status,
                 since_session_ms: Math.round(t - ev.w_session * 1000), at_ms: t };
      },
    };
  }

  const api = { createTracker };
  root.STLineLatency = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
