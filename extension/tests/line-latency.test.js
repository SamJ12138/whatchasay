'use strict';
// Per-line latency as the viewer feels it (docs/latency.md), measured where the text is
// drawn: the backend says when the audio of a line's first and last word arrived
// (w_first / w_last, epoch seconds), the content script knows when it put text on screen.
//   first_display_ms      first word's audio -> first text of the line on screen
//   final_ms              last word's audio  -> final translation on screen
//   first_translation_ms  first word's audio -> first translated text on screen
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { EXT } = require('./load.js');
const { createTracker } = require('../content-scripts/line-latency.js');
const { CONTENT_SCRIPT_FILES } = require('../lib/caption-mode.js');

// one line ending, so a fixed-width slice of a source holds the same text on a CRLF checkout (Windows CI)
const read = (f) => fs.readFileSync(path.join(EXT, f), 'utf8').replace(/\r\n/g, '\n');

function tracker() {
  const clock = { ms: 0 };
  return { clock, t: createTracker(() => clock.ms) };
}

test('a line reports first_display from its first text and final from its last translation', () => {
  const { clock, t } = tracker();
  clock.ms = 1000500;  // first partial drawn 0.5 s after the first word's audio arrived
  assert.equal(t.text('asr_0', { w_first: 1000.0, w_last: 1000.3, lang: 'en', lang_status: 'manual' }), null);
  clock.ms = 1001000;
  assert.equal(t.text('asr_0', { w_first: 1000.0, w_last: 1000.8, lang: 'en', lang_status: 'manual' }), null);
  clock.ms = 1003100;  // the final caption
  assert.equal(t.text('asr_0', { w_first: 1000.0, w_last: 1002.4, lang: 'en', lang_status: 'manual' }, { final: true }), null);
  clock.ms = 1003200;  // its translation: the line is complete
  const rec = t.translation('asr_0', { w_first: 1000.0, w_last: 1002.4, lang_status: 'manual', targets_pending: 0, translations: { zh: {} } });
  assert.deepEqual(rec, {
    kind: 'line', cue_id: 'asr_0', lang: 'en', lang_status: 'manual',
    first_display_ms: 500, final_ms: 800, first_translation_ms: 3200, draft_shown: false, targets: ['zh'],
    drafts: 0, translation_revisions: 0, non_append_revisions: 0,
  });
  // one record per line
  assert.equal(t.translation('asr_0', { w_first: 1000.0, w_last: 1002.4, targets_pending: 0, translations: { zh: {} } }), null);
});

test('with two targets the line is complete when the last one is on screen', () => {
  const { clock, t } = tracker();
  clock.ms = 5000400;
  t.text('asr_3', { w_first: 5000.0, w_last: 5001.0, lang: 'bn', lang_status: 'confirmed' }, { final: true });
  clock.ms = 5001300;
  assert.equal(t.translation('asr_3', { w_first: 5000.0, w_last: 5001.0, targets_pending: 1, translations: { en: {} } }), null);
  clock.ms = 5001450;
  const rec = t.translation('asr_3', { w_first: 5000.0, w_last: 5001.0, targets_pending: 0, translations: { zh: {} } });
  assert.equal(rec.final_ms, 450);
  assert.equal(rec.first_translation_ms, 1300);  // the first target counted as the first translated text
  assert.deepEqual(rec.targets, ['en', 'zh']);
});

test('a draft translation counts as the first translated text, not as the final one', () => {
  const { clock, t } = tracker();
  clock.ms = 2000300;
  t.text('asr_1', { w_first: 2000.0, w_last: 2000.2, lang: 'en', lang_status: 'manual' });
  clock.ms = 2000900;
  assert.equal(t.translation('asr_1', { w_first: 2000.0, w_last: 2000.7, draft: true, translations: { zh: {} } }), null);
  clock.ms = 2003000;
  const rec = t.translation('asr_1', { w_first: 2000.0, w_last: 2002.5, targets_pending: 0, translations: { zh: {} } });
  assert.equal(rec.first_translation_ms, 900);
  assert.equal(rec.final_ms, 500);
  assert.equal(rec.draft_shown, true);
});

const shown = (lang, text, extra) => Object.assign({ translations: { [lang]: { display_text: text, single_line: text } } }, extra);

test('flicker: a change of the displayed translation that is not a pure append is counted per line', () => {
  const { clock, t } = tracker();
  clock.ms = 1000300;
  t.text('asr_0', { w_first: 1000.0, w_last: 1000.2, lang: 'bn', lang_status: 'manual' });
  const draft = (text) => t.translation('asr_0', shown('en', text, { w_first: 1000.0, w_last: 1001.0, draft: true }));
  clock.ms = 1000900; draft('It is');                       // first text: not a change
  clock.ms = 1001500; draft('It is too');                   // append
  clock.ms = 1002100; draft('It is too expensive');         // append
  clock.ms = 1002700; draft('To raise it is too expensive'); // rewritten: the viewer has to read it again
  clock.ms = 1003300; draft('To raise it is too expensive'); // the same text again: no change
  clock.ms = 1004000;
  const rec = t.translation('asr_0', shown('en', 'It is too expensive to raise it.', { w_first: 1000.0, w_last: 1003.5, targets_pending: 0 }));
  assert.equal(rec.drafts, 5);
  assert.equal(rec.translation_revisions, 4);               // 3 drafts changed the text, then the final did
  assert.equal(rec.non_append_revisions, 2);                // the rewrite, and the final
  assert.equal(rec.draft_shown, true);
});

test('flicker: sentence-final punctuation of a draft does not make the next one a rewrite', () => {
  const { clock, t } = tracker();
  clock.ms = 1000300;
  t.text('asr_0', { w_first: 1000.0, w_last: 1000.2, lang: 'bn', lang_status: 'manual' });
  clock.ms = 1000900;
  t.translation('asr_0', shown('en', 'I need.', { w_first: 1000.0, w_last: 1000.8, draft: true }));
  clock.ms = 1001500;
  t.translation('asr_0', shown('zh', '我需要。', { w_first: 1000.0, w_last: 1000.8, draft: true }));
  clock.ms = 1002500;
  t.translation('asr_0', shown('en', 'I need ten thousand tomorrow.', { w_first: 1000.0, w_last: 1002.0, targets_pending: 1 }));
  const rec = t.translation('asr_0', shown('zh', '我需要一万个。', { w_first: 1000.0, w_last: 1002.0, targets_pending: 0 }));
  assert.equal(rec.translation_revisions, 2);               // one per target
  assert.equal(rec.non_append_revisions, 0);
});

test('a line with nothing to translate is complete at its final caption', () => {
  const { clock, t } = tracker();
  clock.ms = 3000350;
  t.text('asr_0', { w_first: 3000.0, w_last: 3000.1, lang: 'en', lang_status: 'manual' });
  clock.ms = 3002000;
  t.text('asr_0', { w_first: 3000.0, w_last: 3001.5, lang: 'en', lang_status: 'manual' }, { final: true });
  clock.ms = 3002010;  // the backend's empty translation message: no target differs from the speech
  const rec = t.translation('asr_0', { w_first: 3000.0, w_last: 3001.5, targets_pending: 0, translations: {} });
  assert.equal(rec.first_display_ms, 350);
  assert.equal(rec.final_ms, 500);            // measured at the final caption, the last text this line gets
  assert.equal(rec.first_translation_ms, null);
  assert.deepEqual(rec.targets, []);
});

test('lines without word times, and lines never shown, give no record', () => {
  const { clock, t } = tracker();
  clock.ms = 1000;
  t.text('asr_0', { lang: 'en' });  // an older backend: no w_first
  assert.equal(t.translation('asr_0', { targets_pending: 0, translations: { zh: {} } }), null);
  // a translation for a line that was never drawn (dropped as stale, or partials off and no final)
  assert.equal(t.translation('asr_9', { w_first: 1.0, w_last: 2.0, targets_pending: 0, translations: { zh: {} } }), null);
});

test('reset forgets the open lines: the next recognizer numbers its utterances from 0 again', () => {
  const { clock, t } = tracker();
  clock.ms = 1000300;
  t.text('asr_0', { w_first: 1000.0, w_last: 1000.2, lang: 'en', lang_status: 'provisional' });
  t.reset();
  clock.ms = 1005400;
  t.text('asr_0', { w_first: 1005.0, w_last: 1005.2, lang: 'bn', lang_status: 'confirmed' }, { final: true });
  clock.ms = 1005500;
  const rec = t.translation('asr_0', { w_first: 1005.0, w_last: 1005.2, targets_pending: 0, translations: { en: {} } });
  assert.equal(rec.first_display_ms, 400);
  assert.equal(rec.lang, 'bn');
});

test('a line cut off a longer one counts its first display from when its first word was first drawn', () => {
  // The length rule cuts an open line at its widest pause: the words after the cut were
  // already on screen, inside the longer line's partial, before they became a line of their own.
  const { clock, t } = tracker();
  clock.ms = 1000500;
  t.text('asr_0', { w_first: 1000.0, w_last: 1000.3, lang: 'en', lang_status: 'manual' });
  clock.ms = 1003000;  // the partial now shows words up to the one whose audio arrived at 1002.6
  t.text('asr_0', { w_first: 1000.0, w_last: 1002.6, lang: 'en', lang_status: 'manual' });
  clock.ms = 1006500;  // cut: asr_0 ends with the word at 1002.0, asr_1 starts with the one at 1002.4
  t.text('asr_0', { w_first: 1000.0, w_last: 1002.0, lang: 'en', lang_status: 'manual' }, { final: true });
  t.text('asr_1', { w_first: 1002.4, w_last: 1006.2, lang: 'en', lang_status: 'manual' });
  clock.ms = 1009000;
  t.text('asr_1', { w_first: 1002.4, w_last: 1008.5, lang: 'en', lang_status: 'manual' }, { final: true });
  const rec = t.translation('asr_1', { w_first: 1002.4, w_last: 1008.5, targets_pending: 0, translations: { zh: {} } });
  assert.equal(rec.first_display_ms, 600);   // drawn at 1003.0, not at the cut (1006.5)

  // a line that starts after everything drawn so far (a pause split, an endpoint) counts from now
  clock.ms = 1010000;
  t.text('asr_2', { w_first: 1009.6, w_last: 1009.8, lang: 'en', lang_status: 'manual' }, { final: true });
  const next = t.translation('asr_2', { w_first: 1009.6, w_last: 1009.8, targets_pending: 0, translations: { zh: {} } });
  assert.equal(next.first_display_ms, 400);
});

test('the first confirmed-language text of a session is reported once, from the session\'s first audio', () => {
  const { clock, t } = tracker();
  clock.ms = 7001000;
  assert.equal(t.confirmed({ w_session: 7000.0, lang: 'en', lang_status: 'provisional' }), null);
  clock.ms = 7005300;
  assert.deepEqual(t.confirmed({ w_session: 7000.0, lang: 'bn', lang_status: 'confirmed' }),
    { kind: 'first_confirmed', lang: 'bn', lang_status: 'confirmed', since_session_ms: 5300, at_ms: 7005300 });
  clock.ms = 7006000;
  assert.equal(t.confirmed({ w_session: 7000.0, lang: 'bn', lang_status: 'confirmed' }), null);
  t.start();  // a new live session
  clock.ms = 8000900;
  assert.equal(t.confirmed({ w_session: 8000.0, lang: 'en', lang_status: 'manual' }).since_session_ms, 900);
});

test('the content script loads the tracker and logs line_latency where it draws live text', () => {
  const i = CONTENT_SCRIPT_FILES.indexOf('content-scripts/line-latency.js');
  assert.ok(i >= 0 && i < CONTENT_SCRIPT_FILES.indexOf('content-scripts/main.js'));
  const main = read('content-scripts/main.js');
  const live = main.slice(main.indexOf('function handleLiveEvent'), main.indexOf('// Latency bookkeeping'));
  const arm = (name) => live.slice(live.indexOf(`case '${name}'`), live.indexOf('break;', live.indexOf(`case '${name}'`)));
  assert.match(arm('partial'), /lineLatency\.text\(/);
  assert.match(arm('final'), /lineLatency\.text\([^;]*final: true/);
  assert.match(arm('revision'), /lineLatency\.translation\(/);
  assert.match(arm('reset'), /lineLatency\.reset\(\)/);
  assert.match(main, /obs\.log\('line_latency', 'success'/);
});
