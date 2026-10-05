# Status and limitations

- Tested end to end on Windows 11 only. Linux runs the fast and extension tests in CI; macOS is untested.
- Speech recognition covers English, Mandarin and Bengali only. Bengali recognition is the weakest of the three (the
  Bengali model's word error rate is about 18-21 %), and Bengali names come out misspelled.
- Translation quality per direction (OPUS-MT, from `docs/asr-input-quality.md` and the Phase 2b checks): English to
  Chinese and Mandarin to English keep the meaning of ordinary sentences but read stiffly; English to Bengali is
  usable; literary or long sentences lose clauses; Mandarin to Bengali and Bengali to Chinese go through English and
  can drop a clause. HY-MT (GPU) is better for Bengali.
- Without the English punctuation model (if it is missing) English speech is translated in upper case, which OPUS-MT
  handles badly. The bilingual Mandarin model writes English words inside Mandarin speech in upper case, and they
  are not restored.
- A line ends at a pause (0.5-0.6 s) and is at most 6 seconds of speech: a longer sentence is cut at its widest
  pause and its parts are translated separately, which can split a clause from its verb.
- With *Auto-detect*, the first second or two (1.3-2.3 s on the benchmark pages when the page's title or YouTube's
  caption language points to the spoken language; 1.8-4.5 s when it points elsewhere, e.g. an English title on
  Bengali speech) are recognised in the page's language, or in English, until detection has heard enough. That text
  is dimmed under *Detecting language…* and replaced if the language turns out to be another one; choosing the
  spoken language avoids the wait.
- While live captions are on, the extension plays an inaudible tone on the tab (-94 dB, Web Audio) so that a video
  started afterwards is captured from its first sample (`docs/observations.md`, A11). On a page you have not
  clicked or typed on yet, Chrome's autoplay policy holds the tone back until your first click or key there: start
  the video by clicking it and the first words may still be lost that once.
- Spoken-language detection (whisper-tiny) is unreliable on Bengali: on its own it often takes Bengali for Hindi or
  Nepali. The backend now picks the likeliest of English, Mandarin and Bengali, and stays on its first guess when
  that is not clear enough: the captions then stay dimmed under *Language not detected, assuming English* (or the
  language the page pointed to, `docs/page-prior.md`). On a Bengali drama whisper-tiny's own top three were
  Telugu, Malayalam and Hindi, and the choice among the three came out English at 0.93-0.96. On a Bengali vlog
  channel (talk over music, English words in Bengali sentences) it confirmed English or Mandarin on 9 of 10
  stretches, and the channel memory then remembers the wrong language for one visit (`docs/page-prior.md`). Set the
  spoken language under *Live Captions* in the popup instead of *Auto-detect*.
- The glossary replaces a term's heard-as spellings on the recognised line and carries the term through
  translation as a placeholder the engine copies. OPUS-MT keeps no placeholder in every direction, so the result is
  checked and the sentence re-translated with the next placeholder (up to three; measured, the three cover 48 of 48
  test sentences over the six en/zh/bn directions, `docs/observations.md` T13); a sentence where none survives is
  translated with the correct spelling and no rendering. The term is matched on the recognised words, so a spelling
  the recognizer has never produced before, and is more than a letter or two from the ones taught, is not caught
  until it is added. An Alt+E correction of a whole translation still covers only the exact sentence it was made on.
- A draft translation is the translation of the part of a sentence heard so far. Only the start that the last two
  drafts agree on is shown, so it grows rather than changes under your eyes, but it is short (about two words on
  the live clip) and the final translation still replaces it, which rewrites the line 0.8 times per line on the
  live clip and twice on a long English sentence. `SUBTITLE_ASR__DRAFT_STABLE_PARTIALS=0` turns drafts off,
  `SUBTITLE_ASR__DRAFT_AGREE_K=1` shows every draft whole.
- A very short line that was misrecognised can get an invented translation: OPUS-MT turned three misheard Bengali
  words into "We were married in July 1955." (`docs/latency.md`).
- Bengali lines end a little later than English or Mandarin ones, and fast dialogue with pauses under half a
  second still runs two sentences into one line (up to the 6-second cut). The Bengali model decodes in 0.64 s
  chunks (`decode_chunk_len` 64 in its encoder; the English and Mandarin models 0.32 s), so it sees a pause and
  ends a line only on a chunk boundary: its final translation comes 1.2-1.4 s after the last word against 0.8-0.9 s
  (`docs/latency.md`), and its first words 0.7-0.8 s after they were spoken against 0.3-0.4 s. It is a model limit:
  sherpa-onnx has one Bengali streaming model and it comes in this one export, with no checkpoint to re-export
  from, so there is no smaller-chunk export to switch to (`docs/observations.md`, A9).
- Below a video on a normal page the block sits over whatever the page has there (on YouTube, the title); drag it
  elsewhere if that is in the way. A line is at most two rows of about 42 characters: a longer translation keeps
  its end, with "…" at the start (none of the live clip's 12 lines needed it, `docs/overlay/README.md`). In
  fullscreen the two lines can take up to a quarter of the picture's height when both wrap; lower the font in
  *Settings → Display* if that is too much.
- Korean and Portuguese targets stay untranslated with OPUS-MT (no model from English).
- Sites that protect their audio (DRM) give silence; the popup then says no audio is reaching the capture.
- Chrome's built-in on-device translator (Chrome 138+, *Prepare on-device translation* in the popup) is wired in but
  has not been measured.
- HY-MT's `llama-server` is downloaded for Windows only.
