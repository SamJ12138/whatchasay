/**
 * overlay-rows.js: the rolling two-row layout of the subtitle block (docs/overlay/README.md).
 *
 * Row 1 holds the previous line's final translation, row 2 the current line's draft (or
 * its final, briefly). When the current line is finalised it moves to row 1 and row 2
 * clears for the next draft. A final stays visible at least max(minDisplayS, chars /
 * charsPerS) before it can be pushed out of row 1; if the next line finalises sooner,
 * row 1 is held and row 2 shows the newer final until the hold ends. A draft of a line
 * after that waits until row 2 is free.
 *
 * Fast speech: when finals arrive faster than the hold allows, the hold shrinks with the
 * backlog instead of the oldest line being dropped: it moves from the nominal hold toward
 * the floor (floorS, 1.0 s) in proportion to the lines waiting (half-way with one, the
 * floor with maxBacklog), never under the floor. A final that
 * arrives while row 1 has not yet had the floor waits unseen behind it (the newest is
 * always on screen in row 2) and takes row 1 in order; only when more than maxBacklog
 * lines wait is the oldest waiting line dropped (drops()). The time a final was visible
 * in row 2 counts towards its hold.
 *
 * Times are seconds on any clock the caller uses; nothing here schedules anything.
 * Pure: loaded by the content script and node tests.
 */
(function (root) {
  function createRows(opts = {}) {
    const minDisplayS = opts.minDisplayS == null ? 1.5 : opts.minDisplayS;
    const charsPerS = opts.charsPerS == null ? 15 : opts.charsPerS;
    const floorS = opts.floorS == null ? 1.0 : opts.floorS;
    const maxBacklog = opts.maxBacklog == null ? 2 : opts.maxBacklog;
    let row1 = null;      // {cueId, chars, visibleBefore, visibleSince}: the held final
    let row2 = null;      // {cueId, kind: 'draft'} | {cueId, kind: 'final', chars, visibleBefore, visibleSince}
    let waiting = [];     // finals between row 1 and row 2, oldest first, not on screen
    let pending = null;   // {cueId, hasText}: a line that has to wait for row 2
    let dropped = 0;

    const minDisplay = (chars) => Math.max(minDisplayS, (chars || 0) / charsPerS);
    /** The hold of a final with `backlog` finals waiting behind it: the nominal one, moved
     *  toward the floor in proportion to the backlog (half-way with one line waiting, the
     *  floor with maxBacklog), never under the floor. */
    const hold = (chars, backlog) => {
      const nominal = minDisplay(chars);
      const floor = Math.min(floorS, nominal);
      if (!backlog) return nominal;
      return nominal - (nominal - floor) * Math.min(1, backlog / Math.max(1, maxBacklog));
    };
    // the backlog that shortens row 1's hold: finals waiting unseen (a final in row 2 is on screen)
    const backlog = () => waiting.length;
    const queued = () => waiting.length + (row2 && row2.kind === 'final' ? 1 : 0);
    const visibleFor = (e, t) => (e.visibleBefore || 0) + (e.visibleSince == null ? 0 : t - e.visibleSince);
    const holdEnd = (e) => (e ? e.visibleSince - (e.visibleBefore || 0) + hold(e.chars, backlog()) : null);
    const entry = (cueId, chars, t) => ({ cueId, kind: 'final', chars, visibleBefore: 0, visibleSince: t });
    const park = (e, t) => { e.visibleBefore = visibleFor(e, t); e.visibleSince = null; return e; };
    const show = (e, t) => { if (e.visibleSince == null) e.visibleSince = t; return e; };

    /** Row 1's hold is over: the next final (the oldest waiting, else the one in row 2) moves up. */
    function advance(t) {
      if (!row1 || !queued() || t + 1e-9 < holdEnd(row1)) return false;   // 1 ns: float noise in the sums
      if (waiting.length) {
        row1 = show(waiting.shift(), t);
      } else {
        row1 = row2;
        row2 = pending ? { cueId: pending.cueId, kind: 'draft' } : null;
        pending = null;
      }
      return true;
    }

    function tick(t) {
      if (row1 && row1.visibleSince == null) row1.visibleSince = t;   // put in row 1 by remove(), which has no clock
      let moved = false;
      while (advance(t)) moved = true;
      return moved;
    }

    /** When tick(t) will next change something, or null. */
    function nextWake() {
      return row1 && queued() ? holdEnd(row1) : null;
    }

    /** A line being recognised (hasText: it has a draft translation to show). */
    function open(cueId, hasText, t) {
      tick(t);
      if ((row1 && row1.cueId === cueId) || (row2 && row2.cueId === cueId) || waiting.some((e) => e.cueId === cueId)) return;
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
      const w = waiting.find((e) => e.cueId === cueId);
      if (w) { w.chars = chars; return; }
      const e = entry(cueId, chars, t);
      if (!row1 || (!queued() && t + 1e-9 >= holdEnd(row1))) {
        row1 = e;
        if (row2 && row2.cueId === cueId) row2 = null;
        if (!row2 && pending) { row2 = { cueId: pending.cueId, kind: 'draft' }; pending = null; }
        return;
      }
      // row 1 is held: the newest final takes row 2; a final that was there waits behind row 1
      if (row2 && row2.kind === 'final') {
        waiting.push(park(row2, t));
        if (waiting.length > maxBacklog) { waiting.shift(); dropped++; }
      } else if (row2 && row2.kind === 'draft' && row2.cueId !== cueId && !pending) {
        pending = { cueId: row2.cueId, hasText: true };
      }
      row2 = e;
      tick(t);   // the larger backlog may have shortened row 1's hold to its end
    }

    /** The line is hidden. The newest line going means silence: everything goes. */
    function remove(cueId) {
      if (pending && pending.cueId === cueId) pending = null;
      waiting = waiting.filter((e) => e.cueId !== cueId);
      if (row2 && row2.cueId === cueId) {
        row2 = null;
        row1 = null;
        waiting = [];
        if (pending) { row2 = { cueId: pending.cueId, kind: 'draft' }; pending = null; }
        return;
      }
      if (row1 && row1.cueId === cueId) {
        row1 = null;
        if (waiting.length) row1 = waiting.shift();   // visible from the next tick
        else if (row2 && row2.kind === 'final') { row1 = row2; row2 = pending ? { cueId: pending.cueId, kind: 'draft' } : null; pending = null; }
      }
    }

    function clear() { row1 = null; row2 = null; waiting = []; pending = null; }

    function view() {
      return { row1: row1 ? { cueId: row1.cueId } : null, row2: row2 ? { cueId: row2.cueId, kind: row2.kind } : null,
               waiting: waiting.map((e) => e.cueId), pending: pending ? pending.cueId : null };
    }

    const drops = () => dropped;

    return { open, final, remove, clear, tick, nextWake, view, minDisplay, hold, drops };
  }

  const api = { createRows };
  root.STOverlayRows = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
