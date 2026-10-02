/**
 * target-routes.js: which target languages can be translated to from the spoken
 * language, from the backend's /health/json `routes` ({spoken: [targets]}, computed from
 * the engines actually loaded). The options page greys out the rest, with the reason as a
 * tooltip ("no local model for en->ko"). With Auto-detect a target is greyed out only if
 * no spoken language reaches it; one reachable from some has a note naming the others.
 * No route information (backend unreachable): nothing is greyed out.
 *
 * Pure: loaded by the options page and node tests.
 */
(function (root) {
  const SPOKEN = ['en', 'zh', 'bn'];

  function availability(routes, source, targets) {
    const out = {};
    for (const t of targets) {
      if (!routes) { out[t] = { available: true, reason: null }; continue; }
      const sources = source && source !== 'auto' ? [source] : Object.keys(routes).length ? Object.keys(routes) : SPOKEN;
      const missing = sources.filter((s) => s !== t && !(routes[s] || []).includes(t));
      const reachable = sources.some((s) => s === t || (routes[s] || []).includes(t));
      out[t] = {
        available: reachable,
        reason: missing.length ? 'no local model for ' + missing.map((s) => `${s}->${t}`).join(', ') : null,
      };
    }
    return out;
  }

  const api = { availability };
  root.STTargetRoutes = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
