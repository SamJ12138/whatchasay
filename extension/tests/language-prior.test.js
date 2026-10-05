'use strict';
// The language prior (lib/language-prior.js, docs/page-prior.md): page signals and the
// channel memory combined into {en, zh, bn} before any audio; the memory of the language
// language ID confirmed per YouTube channel or site, weakened by every time it was wrong.
const { test } = require('node:test');
const assert = require('node:assert/strict');

const P = require('../lib/language-prior.js');

const yt = (o) => ({ origin: 'https://www.youtube.com', title: (o.title || '') + ' - YouTube', youtube: Object.assign(
  { videoId: 'v', channelId: 'UC1', title: null, author: null, description: null, captionAsr: null, audioTrack: null }, o) });

test('script: Han or Bengali text names zh / bn, Latin names en, numbers nothing', () => {
  assert.equal(P.scriptLanguage('10,000 রাজভোগের Order | Ora Char Jon | Movie Scene'), 'bn');
  assert.equal(P.scriptLanguage('File:台湾准备迎战强台风.webm - Wikimedia Commons'), 'zh');
  assert.equal(P.scriptLanguage('Streettalk Ep.1 高 - Mandarin from the Streets'), 'en');
  assert.equal(P.scriptLanguage('2026 | 12:30'), null);
  assert.equal(P.scriptLanguage(''), null);
});

test('language codes', () => {
  for (const [c, l] of [['en-US.4', 'en'], ['zh-Hans', 'zh'], ['bn', 'bn'], ['yue', null], ['ar', null], [null, null]]) {
    assert.equal(P.langFromCode(c), l, c);
  }
});

test('a Bengali title alone makes Bengali the favourite above the threshold', () => {
  const { prior, votes } = P.priorFrom(yt({ title: '10,000 রাজভোগের Order | Movie Scene', author: 'Bengali Movies' }));
  assert.deepEqual(votes.map((v) => [v.signal, v.lang]), [['pageScript', 'bn']]);
  assert.ok(prior.bn >= 0.6, JSON.stringify(prior));
});

test('the channel name or description counts when the title is Latin (one vote, not three)', () => {
  const { votes } = P.priorFrom(yt({ title: 'VOA Bangla Live Stream', author: 'VOA বাংলা', description: 'https://www.voabangla.com' }));
  assert.deepEqual(votes.map((v) => [v.signal, v.lang]), [['pageScript', 'bn']]);
});

test('a Latin title alone makes English the favourite, just over the threshold (6 of 9 right)', () => {
  const { prior } = P.priorFrom(yt({ title: 'Why is Trump threatening a US diesel export ban? | BBC News', author: 'BBC News' }));
  assert.equal(prior.en, 0.65);
  assert.ok(prior.en >= 0.6, JSON.stringify(prior));
});

test('a Chinese title against an English caption language stays under the threshold', () => {
  const { prior } = P.priorFrom(yt({ title: 'TED 中英雙語字幕: 如何讓壓力成為你的朋友', captionAsr: 'en' }));
  assert.ok(Math.max(prior.en, prior.zh, prior.bn) < 0.6, JSON.stringify(prior));
});

test("YouTube's caption language and a Latin title agree: English is the favourite", () => {
  const { prior } = P.priorFrom(yt({ title: 'Why is Trump threatening a US diesel export ban?', captionAsr: 'en' }));
  assert.ok(prior.en >= 0.6, JSON.stringify(prior));
});

test('a dubbed video: the default audio track outweighs nothing else', () => {
  const { votes } = P.priorFrom(yt({ title: '[ENG SUB] 7 Years Apart', audioTrack: 'en-US.4', captionAsr: 'ar' }));
  assert.deepEqual(votes.map((v) => [v.signal, v.lang]), [['audioTrack', 'en'], ['pageScript', 'en']]);
});

test('a page with nothing to go on has no prior', () => {
  assert.equal(P.priorFrom({ origin: 'https://example.org', title: '', youtube: null }).prior, null);
  assert.equal(P.priorFrom(null).prior, null);
});

test('the memory: confirmed languages are remembered per YouTube channel, else per site', () => {
  assert.equal(P.memoryKey(yt({ channelId: 'UCabc' })), 'yt:UCabc');
  assert.equal(P.memoryKey({ origin: 'https://commons.wikimedia.org', youtube: null }), 'site:https://commons.wikimedia.org');
  assert.equal(P.memoryKey({ youtube: null }), null);
});

test('a remembered channel language is a strong vote on its own', () => {
  let mem = P.remember({}, 'yt:UC1', 'zh', 1);
  const { prior } = P.priorFrom(yt({ title: 'What Do Chinese Think About Leftover MEN?' }), mem['yt:UC1']);
  assert.ok(prior.zh >= 0.6, JSON.stringify(prior));
});

test('on a second visit the remembered language beats a wrong page (romanised title, English caption language)', () => {
  // the cold case of docs/latency.md: a Bengali vlog whose title is romanised and whose YouTube
  // caption language is English; language ID confirmed Bengali on the channel's previous video
  const mem = P.remember({}, 'yt:UC1', 'bn', 1);
  const page = yt({ title: 'Panchmi te Kolkata e thakur dekhte gelam 🪷✨|| Durga Pujo Vlog', author: 'Bidisha Das',
    description: 'Hey everyone , I hope you will enjoy this vlog.', captionAsr: 'en' });
  assert.ok(P.priorFrom(page).prior.en >= 0.6);  // the page alone: English
  const { prior, votes } = P.priorFrom(page, mem['yt:UC1']);
  assert.deepEqual(votes.map((v) => v.signal + ':' + v.lang), ['captionAsr:en', 'pageScript:en', 'memory:bn']);
  assert.ok(prior.bn >= 0.6 && prior.bn > prior.en, JSON.stringify(prior));
});

test('a memory wrong once alone stays under the threshold', () => {
  let mem = P.remember({}, 'yt:UC1', 'en', 1);
  mem = P.remember(mem, 'yt:UC1', 'bn', 2);  // it said en, language ID confirmed bn
  const { prior } = P.priorFrom({ origin: 'https://www.youtube.com', youtube: { channelId: 'UC1' } }, mem['yt:UC1']);
  assert.ok(prior.bn < 0.6, JSON.stringify(prior));
});

test('the memory weakens with every time it was wrong, and never recovers', () => {
  let mem = P.remember({}, 'k', 'zh', 1);
  assert.equal(P.memoryConfidence(mem.k), 0.97);
  mem = P.remember(mem, 'k', 'zh', 2);
  assert.deepEqual([mem.k.lang, mem.k.misses, mem.k.hits], ['zh', 0, 2]);
  mem = P.remember(mem, 'k', 'en', 3);            // it said zh, the speech was English
  assert.deepEqual([mem.k.lang, mem.k.misses], ['en', 1]);
  assert.equal(P.memoryConfidence(mem.k), 0.485);
  mem = P.remember(mem, 'k', 'en', 4);
  assert.equal(P.memoryConfidence(mem.k), 0.485);  // right again: still once wrong
  mem = P.remember(mem, 'k', 'zh', 5);
  assert.equal(P.memoryConfidence(mem.k), 0.2425);
  const { prior } = P.priorFrom(yt({ title: 'x y z' }), mem.k);  // a weak memory alone stays under the threshold
  assert.ok(prior.zh < 0.6, JSON.stringify(prior));
});

test('the memory keeps the newest keys only', () => {
  let mem = {};
  for (let i = 0; i < P.MEMORY_MAX_KEYS + 5; i++) mem = P.remember(mem, 'k' + i, 'en', i);
  assert.equal(Object.keys(mem).length, P.MEMORY_MAX_KEYS);
  assert.ok(!('k0' in mem) && ('k' + (P.MEMORY_MAX_KEYS + 4)) in mem);
});

test('unknown languages are not remembered', () => {
  assert.deepEqual(P.remember({}, 'k', 'ja', 1), {});
  assert.deepEqual(P.remember({}, null, 'en', 1), {});
});

test('the prior travels as en:p,zh:p,bn:p', () => {
  assert.equal(P.encodePrior({ en: 0.1, zh: 0.8, bn: 0.1 }), 'en:0.1,zh:0.8,bn:0.1');
  assert.equal(P.encodePrior(null), '');
});

test('collectPageSignals reads YouTube\'s player response and the page head', () => {
  const pr = { videoDetails: { videoId: 'abc', channelId: 'UCx', title: 'T', author: 'A', shortDescription: 'D' },
    captions: { playerCaptionsTracklistRenderer: { captionTracks: [{ languageCode: 'bn', kind: 'asr' }, { languageCode: 'en' }] } },
    streamingData: { adaptiveFormats: [{ audioTrack: { id: 'en.3', audioIsDefault: false } }, { audioTrack: { id: 'bn.4', audioIsDefault: true } }] } };
  const metas = { 'og:title': 'OG title', 'og:locale': 'en_US' };
  const document = {
    title: 'T - YouTube',
    documentElement: { getAttribute: (k) => (k === 'lang' ? 'en' : null) },
    querySelector: (sel) => {
      const m = /meta\[property="([^"]+)"\]/.exec(sel);
      return m && metas[m[1]] ? { getAttribute: () => metas[m[1]] } : null;
    },
    getElementById: (id) => (id === 'movie_player' ? { getPlayerResponse: () => pr } : null),
  };
  const location = { href: 'https://www.youtube.com/watch?v=abc', origin: 'https://www.youtube.com', hostname: 'www.youtube.com' };
  const fn = new Function('document', 'location', 'window', 'URL', `return (${P.collectPageSignals.toString()})();`);
  const s = fn(document, location, {}, URL);
  assert.equal(s.htmlLang, 'en');
  assert.equal(s.ogLocale, 'en_US');
  assert.equal(s.title, 'OG title');
  assert.deepEqual(s.youtube, { videoId: 'abc', channelId: 'UCx', title: 'T', author: 'A', description: 'D',
    captionAsr: 'bn', audioTrack: 'bn.4' });
});
