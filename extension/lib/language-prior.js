/**
 * language-prior.js: what the page and this extension's memory say about the spoken language
 * before any audio is heard (docs/page-prior.md). The backend starts its first recognizer in
 * the prior's favourite language when the favourite reaches its threshold (0.6); language ID
 * still decides.
 *
 * Signals (measured on 23 pages in docs/page-prior.md, the weights below are from there):
 *   captionAsr   YouTube: the language of the auto-generated caption track (YouTube's own
 *                speech recognition)
 *   audioTrack   YouTube: the default audio track of a video with several (a dubbed video)
 *   pageScript   title, channel name and description as one vote: the first of them written in
 *                Han or Bengali script names zh / bn; Latin only says little (see the weights)
 *   memory       the last language language ID confirmed on this channel (YouTube) or site
 *                (elsewhere); its weight halves with every time it turned out wrong
 * Not used: <html lang> and og:locale (the interface language on YouTube and Commons, never
 * the speech's in the evaluation), the uploaded caption tracks (rare, translations).
 *
 * The combination is naive Bayes over {en, zh, bn} from a flat start: a signal that names
 * language L with accuracy a multiplies L by a and each other language by (1 - a) / 2.
 *
 * collectPageSignals runs in the page's MAIN world (chrome.scripting.executeScript) so it can
 * read YouTube's player response; it must not use anything outside its own body. The rest is
 * pure: loaded by the service worker and node tests.
 */
(function (root) {
  const LANGS = ['en', 'zh', 'bn'];
  // a signal's accuracy when present (docs/page-prior.md, "Weights")
  const ACCURACY = { captionAsr: 0.8, audioTrack: 0.8, scriptNonLatin: 0.85, scriptLatin: 0.65 };
  const MEMORY_CONFIDENCE = 0.9;   // a remembered language never wrong so far
  const MEMORY_DECAY = 0.5;        // ...times this for every time it was wrong
  const MEMORY_MAX_KEYS = 500;

  /** Runs in the page (MAIN world): everything the prior may use, as plain data. */
  function collectPageSignals() {
    const meta = (key) => {
      const el = document.querySelector(`meta[property="${key}"],meta[name="${key}"]`);
      return el ? el.getAttribute('content') : null;
    };
    const out = {
      url: location.href, origin: location.origin, htmlLang: document.documentElement.getAttribute('lang'),
      ogLocale: meta('og:locale'), title: meta('og:title') || document.title || null,
      description: meta('og:description') || meta('description'), youtube: null,
    };
    if (/(^|\.)youtube\.com$/.test(location.hostname)) {
      let pr = null;
      try {
        const player = document.getElementById('movie_player');
        pr = player && typeof player.getPlayerResponse === 'function' ? player.getPlayerResponse() : null;
      } catch (_) { pr = null; }
      const vid = new URL(location.href).searchParams.get('v');
      if (!pr && window.ytInitialPlayerResponse && (window.ytInitialPlayerResponse.videoDetails || {}).videoId === vid) {
        pr = window.ytInitialPlayerResponse;  // the first page load; stale after in-app navigation
      }
      const vd = (pr && pr.videoDetails) || {};
      const tracks = (((pr && pr.captions) || {}).playerCaptionsTracklistRenderer || {}).captionTracks || [];
      const asr = tracks.find((t) => t.kind === 'asr');
      const formats = ((pr && pr.streamingData) || {}).adaptiveFormats || [];
      const audio = formats.map((f) => f.audioTrack).find((a) => a && a.audioIsDefault);
      out.youtube = pr ? {
        videoId: vd.videoId || vid, channelId: vd.channelId || null, title: vd.title || null,
        author: vd.author || null, description: vd.shortDescription || null,
        captionAsr: asr ? asr.languageCode : null, audioTrack: audio ? audio.id : null,
      } : null;
    }
    return out;
  }

  const HAN = /[㐀-䶿一-鿿豈-﫿]/g;
  const BENGALI = /[ঀ-৿]/g;
  const LATIN = /[A-Za-zÀ-ɏ]/g;

  /** zh / bn when the text has 2+ Han or Bengali characters (the more of the two), else en
   *  for 3+ Latin letters, else null. */
  function scriptLanguage(text) {
    if (!text) return null;
    const han = (text.match(HAN) || []).length;
    const bn = (text.match(BENGALI) || []).length;
    const latin = (text.match(LATIN) || []).length;
    if (Math.max(han, bn) >= 2) return han >= bn ? 'zh' : 'bn';
    return latin >= 3 ? 'en' : null;
  }

  function langFromCode(code) {
    if (!code) return null;
    const base = String(code).trim().toLowerCase().split(/[-_.]/)[0];
    return { en: 'en', eng: 'en', zh: 'zh', cmn: 'zh', zho: 'zh', chi: 'zh', bn: 'bn', ben: 'bn' }[base] || null;
  }

  /** Where the memory of this page's language lives: the YouTube channel, else the site. */
  function memoryKey(signals) {
    const yt = signals && signals.youtube;
    if (yt && yt.channelId) return 'yt:' + yt.channelId;
    return signals && signals.origin ? 'site:' + signals.origin : null;
  }

  function memoryConfidence(entry) {
    if (!entry || !LANGS.includes(entry.lang)) return 0;
    return MEMORY_CONFIDENCE * Math.pow(MEMORY_DECAY, entry.misses || 0);
  }

  /** The votes: [{signal, lang, accuracy}] */
  function votes(signals, memoryEntry) {
    const out = [];
    const yt = (signals && signals.youtube) || null;
    if (yt) {
      const asr = langFromCode(yt.captionAsr);
      if (asr) out.push({ signal: 'captionAsr', lang: asr, accuracy: ACCURACY.captionAsr });
      const audio = langFromCode(yt.audioTrack);
      if (audio) out.push({ signal: 'audioTrack', lang: audio, accuracy: ACCURACY.audioTrack });
    }
    const texts = yt ? [yt.title || (signals && signals.title), yt.author, yt.description]
      : [signals && signals.title, null, signals && signals.description];
    const nonLatin = texts.map(scriptLanguage).find((l) => l === 'zh' || l === 'bn');
    const titleLang = scriptLanguage(texts[0]);
    if (nonLatin) out.push({ signal: 'pageScript', lang: nonLatin, accuracy: ACCURACY.scriptNonLatin });
    else if (titleLang) out.push({ signal: 'pageScript', lang: titleLang, accuracy: ACCURACY.scriptLatin });
    const mem = memoryConfidence(memoryEntry);
    if (mem > 0) out.push({ signal: 'memory', lang: memoryEntry.lang, accuracy: mem });
    return out;
  }

  /** {prior: {en, zh, bn} summing to 1 (null without any vote), votes} */
  function priorFrom(signals, memoryEntry) {
    const v = votes(signals, memoryEntry);
    if (!v.length) return { prior: null, votes: v };
    const score = { en: 1, zh: 1, bn: 1 };
    for (const { lang, accuracy } of v) {
      const a = Math.min(0.99, Math.max(1 / 3, accuracy));
      for (const l of LANGS) score[l] *= l === lang ? a : (1 - a) / 2;
    }
    const total = LANGS.reduce((s, l) => s + score[l], 0);
    const prior = {};
    for (const l of LANGS) prior[l] = Math.round((score[l] / total) * 1000) / 1000;
    return { prior, votes: v };
  }

  /** "en:0.1,zh:0.8,bn:0.1" for the /ws/asr query */
  function encodePrior(prior) {
    if (!prior) return '';
    return LANGS.filter((l) => prior[l] != null).map((l) => `${l}:${prior[l]}`).join(',');
  }

  /** The memory after language ID confirmed `lang` on `key`: a language that differs from
   *  the remembered one replaces it, and the entry keeps count of how often it was wrong. */
  function remember(memory, key, lang, nowMs) {
    if (!key || !LANGS.includes(lang)) return memory || {};
    const next = Object.assign({}, memory || {});
    const old = next[key];
    next[key] = old && old.lang === lang
      ? { lang, misses: old.misses || 0, hits: (old.hits || 0) + 1, ts: nowMs }
      : { lang, misses: old ? (old.misses || 0) + 1 : 0, hits: 1, ts: nowMs };
    const keys = Object.keys(next);
    if (keys.length > MEMORY_MAX_KEYS) {
      keys.sort((a, b) => (next[a].ts || 0) - (next[b].ts || 0));
      for (const k of keys.slice(0, keys.length - MEMORY_MAX_KEYS)) delete next[k];
    }
    return next;
  }

  const api = { LANGS, ACCURACY, MEMORY_CONFIDENCE, MEMORY_DECAY, MEMORY_MAX_KEYS, collectPageSignals, scriptLanguage,
    langFromCode, memoryKey, memoryConfidence, votes, priorFrom, encodePrior, remember };
  root.STLanguagePrior = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : self);
