/**
 * overlay-rows.js: the rolling two-row layout of the subtitle block (docs/overlay/README.md).
 *
 * Row 1 holds the previous line's final translation, row 2 the current line's draft (or
 * its final, briefly). When the current line is finalised it moves to row 1 and row 2
 * clears for the next draft. A final stays visible at least max(minDisplayS, chars /
 * charsPerS) before it can be pushed out of row 1; if the next line finalises sooner,
 * row 1 is held and row 2 shows the newer final until the hold ends. A draft of a line
 * after that waits until row 2 is free. Fast speech (a third final inside one hold)
 * pushes the oldest line out early: the newest line is always on screen.
 *
 * Times are seconds on any clock the caller uses; nothing here schedules anything.
 * Pure: loaded by the content script and node tests.
 */
(function (root) {
  function createRows(opts = {}) {
    const minDisplayS = opts.minDisplayS == null ? 1.5 : opts.minDisplayS;
    const charsPerS = opts.charsPerS == null ? 15 : opts.charsPerS;
    let row1 = null;      // {cueId, chars, finalAt}
    let row2 = null;      // {cueId, kind: 'draft' | 'final', chars, finalAt}
    let pending = null;   // {cueId, hasText}: a line that has to wait for row 2

    const minDisplay = (chars) => Math.max(minDisplayS, (chars || 0) / charsPerS);
    const holdEnd = (e) => (e && e.finalAt != null ? e.finalAt + minDisplay(e.chars) : null);

    /** Row 1's hold is over: the held final in row 2 moves up, a waiting draft takes row 2. */
    function tick(t) {
      if (!row1 || !row2 || row2.kind !== 'final' || t < holdEnd(row1)) return false;
      row1 = row2;
      row2 = pending ? { cueId: pending.cueId, kind: 'draft' } : null;
      pending = null;
      return true;
    }

    /** When tick(t) will next change something, or null. */
    function nextWake() {
      return row1 && row2 && row2.kind === 'final' ? holdEnd(row1) : null;
    }

    /** A line being recognised (hasText: it has a draft translation to show). */
    function open(cueId, hasText, t) {
      tick(t);
      if ((row1 && row1.cueId === cueId) || (row2 && row2.cueId === cueId)) return;
      if (!row2) { row2 = { cueId, kind: 'draft' }; pending = null; return; }
      if (row2.kind === 'draft' && hasText) { row2 = { cueId, kind: 'draft' }; pending = null; return; }
      // row 2 is a held final, or an older draft still waiting for its final: wait
      if (!pending || pending.cueId !== cueId) pending = { cueId, hasText: !!hasText };
      else pending.hasText = pending.hasText || !!hasText;
    }

    /** The line's final translation is on screen (chars: its length, for the hold). */
    function final(cueId, chars, t) {
      tick(t);
      if (pending && pending.cueId === cueId) pending = null;
      if (row1 && row1.cueId === cueId) { row1.chars = chars; return; }
      if (row2 && row2.cueId === cueId && row2.kind === 'final') { row2.chars = chars; return; }
      const entry = { cueId, kind: 'final', chars, finalAt: t };
      if (!row1 || t >= holdEnd(row1)) {
        row1 = entry;
        if (row2 && row2.cueId === cueId) row2 = null;
        if (!row2 && pending) { row2 = { cueId: pending.cueId, kind: 'draft' }; pending = null; }
        return;
      }
      // row 1 is held
      if (row2 && row2.kind === 'final') row1 = row2;       // fast speech: the oldest goes early
      else if (row2 && row2.kind === 'draft' && row2.cueId !== cueId && !pending) pending = { cueId: row2.cueId, hasText: true };
      row2 = entry;
    }

    /** The line is hidden. The newest line going means silence: everything goes. */
    function remove(cueId) {
      if (pending && pending.cueId === cueId) pending = null;
      if (row2 && row2.cueId === cueId) {
        row2 = null;
        row1 = null;
        if (pending) { row2 = { cueId: pending.cueId, kind: 'draft' }; pending = null; }
        return;
      }
      if (row1 && row1.cueId === cueId) {
        row1 = null;
        if (row2 && row2.kind === 'final') { row1 = row2; row2 = pending ? { cueId: pending.cueId, kind: 'draft' } : null; pending = null; }
      }
    }

    function clear() { row1 = null; row2 = null; pending = null; }

    function view() {
      return { row1: row1 ? { cueId: row1.cueId } : null, row2: row2 ? { cueId: row2.cueId, kind: row2.kind } : null,
               pending: pending ? pending.cueId : null };
    }

    return { open, final, remove, clear, tick, nextWake, view, minDisplay };
  }

  const api = { createRows };
  root.STOverlayRows = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
