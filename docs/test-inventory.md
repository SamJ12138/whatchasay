# Test inventory

Starting point: the 54 tests at `ab26b47` (`backend/tests`, 49 pass / 5 skip with the server down). Columns:
**Model** = loads a real model or binary (Zipformer, HY-MT/llama-server, OPUS, whisper-tiny); **Live** = needs the
backend running on `127.0.0.1:8765`. **Now** = where the test lives after Batch 1.

Run: `cd backend && venv\Scripts\python -m pytest` (fast suite, `-m "not slow"` is the default in `pytest.ini`);
`pytest -m slow` for the slow ones; `cd extension && node --test` for the extension.

## The original 54

| File :: test | What it actually asserts | Model | Live | Now |
|---|---|---|---|---|
| `test_asr_session` :: `test_explicit_language_starts_immediately_and_skips_lid` | explicit `en` session is confirmed at once, the LID callback is never called, partials carry `lang=en` (fake engine) | – | – | fast |
| `test_asr_session` :: `test_auto_mode_starts_provisionally_then_switches_and_replays` | `auto` starts provisional `en`; partials flow before LID; LID=`bn` → `reset`, confirmed `lid` with `switched`, old recognizer closed, buffered audio replayed into the new one | – | – | fast |
| `test_asr_session` :: `test_auto_mode_confirms_without_reset_when_guess_is_right` | LID agrees with the provisional language → no `reset`, one recognizer | – | – | fast |
| `test_asr_session` :: `test_manual_override_restarts_recognizer` | `set_language('zh')` returns a manual confirmed `lid` and starts a second recognizer | – | – | fast |
| `test_asr_session` :: `test_real_zipformer_streams_partials_before_end` | real English Zipformer on `test_wavs/0.wav`: first partial < 2 s of audio, final text contains NIGHTFALL/LAMPS, median 40 ms frame < 40 ms CPU | **Zipformer** | – | **slow** |
| `test_cache` :: `test_lru_cache_evicts_oldest` | LRU capacity 2 evicts the oldest key | – | – | fast |
| `test_cache` :: `test_translation_cache_key_is_order_independent` | memory-cache key ignores target order, differs for a different target set | – | – | fast |
| `test_cache` :: `test_translation_memory_roundtrip_and_correction_guard` | TM set/get round trip; a user correction wins and a later machine write does not overwrite it (tmp_path DB) | – | – | fast |
| `test_hymt` :: `test_prompt_uses_chinese_template_when_zh_involved` | zh source or target → Chinese prompt template with the Chinese language name | – | – | fast |
| `test_hymt` :: `test_prompt_uses_english_template_otherwise` | otherwise the English template | – | – | fast |
| `test_hymt` :: `test_hymt_engine_translates_under_budget` | real llama-server + Q4 GGUF on CUDA: warmup, en→zh output contains CJK in < 600 ms, en→bn output contains Bengali | **HY-MT, llama-server, GPU** | – | **slow** |
| `test_integration_live` :: `test_health_reports_engines` | `/health/json` status ok, MT engines non-empty, ASR available | (server's) | **yes** | fast, in-process (`test_app_inprocess`) |
| `test_integration_live` :: `test_http_translate_all_directions_under_budget` ×3 (en→zh,bn; zh→en,bn; bn→en,zh) | `/translate` 200, every target non-empty and ≠ source, zh output has CJK, bn output has Bengali, < 1.5 s | (server's) | **yes** | fast, in-process; fake engine writes a target-script marker so the script checks stay meaningful; also checks the repeat comes from the cache |
| `test_integration_live` :: `test_ws_asr_streams_partials_finals_and_translations` | real-time WAV over `/ws/asr`: partial < 2 s, a final, a zh translation < 1 s after the final | (server's) | **yes** | fast, in-process with the fake ASR engine (order final → translation, same utterance id); the latency part is covered by the slow audio harness |
| `test_line_breaker` :: `test_short_english_stays_single_line` | short English stays one line | – | – | fast |
| `test_line_breaker` :: `test_long_english_breaks_within_limit` | long English → 2 lines ≤ 42 chars, words preserved | – | – | fast |
| `test_line_breaker` :: `test_chinese_breaks_on_punctuation` | Chinese ≤ 2 lines, ≤ 24 chars | – | – | fast |
| `test_line_breaker` :: `test_bengali_danda_is_a_break_point` | `।` is a break point; 2 lines, first ends with `।`, ≤ 38 chars | – | – | fast |
| `test_normalizer` :: `test_strips_html_and_normalizes_quotes` | HTML stripped, curly quote normalised, translatable | – | – | fast |
| `test_normalizer` :: `test_music_marker_only_is_not_translated` | `♪♪` → music, not translated | – | – | fast |
| `test_normalizer` :: `test_sound_effect_only_is_not_translated` | `[door slams]` → sound effect, not translated | – | – | fast |
| `test_normalizer` :: `test_speaker_label_extracted` | `JOHN:` label extracted, text starts with the line | – | – | fast |
| `test_normalizer` :: `test_bengali_script_detected` | Bengali sentence detected as `bn`, confidence ≥ 0.8 | – (langdetect profiles) | – | fast |
| `test_normalizer` :: `test_chinese_and_english_detected` | zh and en sentences detected correctly | – | – | fast |
| `test_pipeline` :: `test_fast_path_translates_all_targets` | fake engine output for zh and bn, revision 1, not post-edited | – | – | fast |
| `test_pipeline` :: `test_source_equals_target_is_passthrough` | bn→bn returns the source; bn→en goes through the engine | – | – | fast |
| `test_pipeline` :: `test_memory_cache_hit_second_time` | second identical cue is `from_cache` and makes no engine call | – | – | fast |
| `test_pipeline` :: `test_passthrough_uses_request_targets` | `[music]` passthrough has exactly the requested targets and a music/SFX note | – | – | fast |
| `test_pipeline` :: `test_refine_batch_returns_revision_2` | fake refiner → revision 2, zh refined, bn (skip list) untouched | – | – | fast |
| `test_pipeline` :: `test_refiner_disabled_for_bengali_only` | `refiner_enabled` false for bn-only targets and when disabled | – | – | fast |
| `test_routing` :: `test_every_direct_model_targets_its_language` ×14 (one per source language) | every configured OPUS model name ends in its target language | – | – | fast |
| `test_routing` :: `test_no_x_to_zh_points_at_english_model` | no X→zh route uses an `-en` model | – | – | fast |
| `test_routing` :: `test_direct_route` | en→zh and bn→en direct | – | – | fast |
| `test_routing` :: `test_pivot_route_through_english` | fr→zh pivots fr→en→zh | – | – | fast |
| `test_routing` :: `test_bengali_routes` | en→bn community model, zh→bn via en, bn→zh ends in zh | – | – | fast |
| `test_routing` :: `test_missing_route_is_none_not_english` | vi→ko has no route (None) | – | – | fast |
| `test_routing` :: `test_same_language_is_empty_route` | en→en is an empty route | – | – | fast |
| `test_routing` :: `test_bengali_is_first_class` | bn in supported/ASR/HY-MT languages, 38-char limit, display name | – | – | fast |
| `test_routing` :: `test_defaults_are_fast_mode` | fast_mode on, refiner off, batch window > 0 (Phase 3: speed_mode removed) | – | – | fast |

Totals: 2 loaded models (now slow), 5 needed the live server (now fast, in-process), 47 were already pure.

Two things the old tests did not do: nothing pointed the translation memory away from `backend/data/` for the
in-process code paths (only the two tests that built their own `TranslationMemory(tmp_path)` were safe), and nothing
ran without the server's real engines except `test_pipeline`.

## Added in Batch 0–1

| File :: test | Asserts | Suite |
|---|---|---|
| `test_startup` :: `test_lifespan_survives_pipeline_init_failure` | startup survives a pipeline that fails to build (phase-1 regression) | fast |
| `test_fake_llama` :: 3 tests | `HyMTEngine` drives `tests/fake_llama_server.py` like the real binary; exit-on-start reports its exit code; the stub exits with a chosen code after N completions | fast |
| `test_e2e_extension` :: `test_tab_audio_reaches_content_script_as_translated_cue`, `test_page_subtitles_are_translated_over_ws` | real extension in Chromium (`scripts/e2e_extension.py`): tab audio → offscreen → `/ws/asr` → content script renders a translation; WebVTT track → detector → `/ws` → overlay | **slow** (models + browser; needs a Python with playwright) |
| `extension/tests/obs.test.js` (5) | `obs.js`: 2 s batching into one POST, session header, endpoint from the ws URL, error_type normalisation, content-script relay, bounded buffer + drop notice, never throws | node |
| `extension/tests/ws-protocol.test.js` (5) | `/ws` frame parser: reply / error / push / invalid; the content-script client resolves and rejects pending requests through it | node |

## Fixtures (`backend/tests/conftest.py`, `backend/tests/fakes.py`)

- The whole session runs with `SUBTITLE_DATA_DIR` and `SUBTITLE_CACHE__TM_DATABASE_PATH`
  pointing at a scratch dir, so no code path can open `backend/data/translation_memory.db`.
- `fake_mt` → `FakeMTEngine(name, mode='ok'|'fail'|'timeout'|'source'|'empty', pairs=None, delay_s=0)`: deterministic
  output with a target-script marker, records every call.
- `fake_asr` → `FakeASREngine`: a partial per frame, a final every `final_every` frames.
- `scratch_tm` → an initialised `TranslationMemory` in `tmp_path`.
- `app_env` → the real FastAPI app wired to fake MT engines, the fake ASR engine and a scratch TM;
  `app_env.client(session_id)` (a `TestClient`; use it as a context manager so the lifespan runs on one loop) sends
  `X-Session-Id`; `app_env.ws_url(path, session_id, **query)`; `app_env.set_engines(primary=…, fallback=…)`.
- `fake_llama(*stub_args)` → a `HyMTEngine` whose child is `tests/fake_llama_server.py` (flags: `--exit-on-start`,
  `--exit-after N`, `--exit-code N`, `--health-delay S`, `--never-healthy`, `--stderr-line TEXT`); shut down after the
  test.

## Added in Phase 2b

| File :: test | Asserts | Suite |
|---|---|---|
| `test_tm_migration` (6) | v1 database -> `<name>.bak-1` written before the migration (old schema, same rows, WAL rows included), logged; no backup for a new or current database; unstamped 2a databases are schema 2; an existing backup is never overwritten; a failed backup stops the migration | fast |
| `test_e2e_extension` :: `test_caption_session_survives_service_worker_idle_timeout` | `--path sw-idle`: real idle timeout over raw CDP (keepalive kept the worker; idle stop -> restart -> next cue translated; forced stop -> same) | **slow** |
| `extension/tests/sw-lifetime.test.js` (4) | a woken worker keeps the first message of a port that connects during the enabled-tab restore; refused / closed ports open no socket; the content side posts a keepalive every 20 s and reopens its port when the worker goes away (not when refused) | node |
| `test_opus_quality` (7) | OPUS-MT, six en/zh/bn directions over `tests/opus_samples.py`: no repeated 3-gram, output <= 3x the source; en->zh output is simplified Chinese | **slow** (CTranslate2 models) |
| `test_engine_selection` (7) | default engine opus; `SUBTITLE_MT__ENGINE=hymt` -> [hymt, opus]; unknown engine refused; HY-MT configured but absent (or no GPU) -> one log line naming `scripts/download_models.py`, OPUS serves, nothing downloaded; the runtime never downloads the GGUF or llama-server | fast |
| `test_download_models` (5) | `scripts/download_models.py` with recorded fetchers: default = ASR + LID + 4 OPUS models under `--data-dir`; `--hymt` shows the license summary and exits 2 without `--accept-hymt-license`; with it, GGUF + llama-server; skip flags | fast |
| `extension/tests/manifest.test.js` (8) | default permissions = storage/activeTab/offscreen/scripting, `tabCapture` optional and requested inside the Start click before any await; no host_permissions or content_scripts; optional hosts = the 14 caption origins and cover every detector site adapter; registration only when on and granted; caption mode off in both defaults; Options explains it | node |
| `extension/tests/injection.test.js` (5) | `ensureInjected`: waits for a loading page's registered scripts, injects once into a loaded page without them, leaves running scripts alone, injects after the wait; loading `backend-port.js` twice keeps the first port | node |
| `test_e2e_extension` :: `test_fresh_install_asks_for_no_site_access_and_caption_mode_is_opt_in` | `--path permissions` on the real extension, fresh profile: no origins, no install warnings, no content scripts; explanation shown; caption-mode toggle requests the 14 sites and stays off without the grant; Start requests `tabCapture` | **slow** |
| `test_memory_privacy` (13) | D3 defaults; an audio session writes no TM row and leaves nothing in the process-wide cache; its cache serves a repeated final and dies with the session; `persist_audio_sessions` opt-in; caption cues persist (and not with `persist_captions` off); retention sweep removes old machine rows only (0 = keep) and runs at startup; `/tm/clear` empties the TM and the deleted text is not in the database files; a web page cannot call it; `/health/json` reports the policy; concurrent first use initializes once | fast |
| `test_e2e_extension` :: `test_options_clear_translation_memory_button` | `--path clear-memory`: storage sentence; dismissed confirmation keeps the TM, accepted one empties it | **slow** |
| `test_cloud` (17) | cloud off by default (no engine even with a key); factory and router order; Google/Azure keys in headers, never the URL; HTTP / network / malformed errors carry no key (also through the router, logs and obs); Gemini key in a header; Groq/Gemini refiners need cloud on; `/ws`, `/ws/asr`, `POST /config` cannot set keys; startup line + `/health/json` name the receivers | fast |
| `extension/tests/cloud-keys.test.js` (4) | migration deletes stored keys and leaves a notice (count, where to put them); no extension source stores, reads or sends keys; defaults carry no key object | node |
| `test_e2e_extension` :: `test_options_page_holds_no_keys_and_names_the_cloud_receivers[off/on]` | `--path cloud-keys`: legacy keys deleted from storage with a notice; no key fields; the cloud line matches `/health/json` (off: nothing leaves; on: Google Cloud Translation) | **slow** |

## Added in Phase 3

| File :: test | Asserts | Suite |
|---|---|---|
| `test_punctuation` (8 fast + 1 slow) | `/ws/asr` restores English finals and partials through the session's `restore_text` hook (final keeps `raw_text`); unchanged text has no `raw_text`; a failing restorer keeps the caption and logs `punctuate` fail; `create_session` uses the package restorer; on by default, English only; a missing model passes text through with one WARNING and no download; `asr.punctuation=false` means no restorer; the MT engine receives the restored text. Slow: the real model restores two sentences exactly, p50 < 50 ms, Mandarin unchanged | fast / **slow** |
| `test_mandarin_model` (6) | default Mandarin model is the Apache-2.0 bilingual one; unknown `asr.zh_model` refused; `large` configured but absent -> bilingual + one WARNING naming the flags; `large` used when present; the backend never downloads the gated model; unused fp32 files dropped after extraction | fast |
| `test_download_models` (+4) | default set includes the punctuation model and the bilingual Mandarin model and prints no license warning; `--mandarin-large` shows the notice and exits 2 without `--accept-mandarin-large-terms`; with it, the large model is added; the accept flag alone downloads nothing extra | fast |
| `test_dead_code` (13) | `/config/speed_mode` is gone (404/405); nine unused settings are gone; no reference to the never-implemented cloud / Whisper ASR engines; the v1 Modelfile and TRAINING.md are gone; startup no longer probes `ollama` / `llama_cpp` | fast |
| `test_e2e_extension` :: `test_mandarin_tab_audio_with_the_licensed_model` | `--path audio` with a Mandarin WAV, source zh, target en: translated cue in the page, served by the bilingual model (`/health/json` models), TM unchanged | **slow** |
| `extension/tests/options-shortcuts.test.js` (3) | the Options page lists every manifest command with its suggested key, nothing else but the Alt+E page key (handled in `main.js`), no Alt+[ / Alt+]; it reads the bound keys with `chrome.commands.getAll()` | node |
| `test_repo_hygiene` (3) | the patterns catch what they should and pass no-reply addresses, decorators, placeholders; no tracked file holds a local username, school domain, gmail, home path, phone number or non-no-reply e-mail; with a local TM (copied), no TM text outside the sample files (skipped in CI) | fast |
| `test_env_example` (4) | every setting documented once in `backend/.env.example` and nothing else; the fully uncommented file loads to the defaults; key fields empty, cloud off; `backend/.env` ignored, the example not | fast |
| `test_security` :: `test_harness_derives_extension_ids_like_the_backend` | the harness's copy of the extension-id derivation equals `app.security`'s | fast |
| `test_dead_code` (+7) | seven settings nothing read are gone | fast |
| `test_env_example` (+2) | every setting has its own description line (no pipe character), and the configuration table (`docs/configuration.md`) lists every setting | fast |
| `test_app_inprocess` :: `test_health_reports_engines` (extended) | `/health/json` names this process's `run_id` | fast |
| `extension/tests/options-asr.test.js` (2) | the ASR engine select offers only auto / sherpa-zipformer; no extension source mentions Whisper | node |
| `extension/tests/options-shortcuts.test.js` (+1) | the popup's shortcut list shows exactly the bound keys | node |

## Added with provisional subtitles and the correction check (2026-10-02)

| File :: test | Asserts | Suite |
|---|---|---|
| `extension/tests/provisional.test.js` (10) | the overlay's real render path on a fake DOM (`tests/fake-dom.js`): live text is dimmed (`provisional`) above *Detecting language…* until the language is confirmed; a switch discards it and drops its late translations, so the first undimmed line is the first confirmed one; confirmation of the same language undims in place; after a failed detection the text stays dimmed under *Language not detected* until the user picks a language; page subtitles and a stopped session are never dimmed; the label does not re-animate on redraw; `main.js` passes `lang_status` through; Alt+E skips provisional lines; on-device translations carry the status | node |
| `test_app_inprocess` :: `test_ws_asr_translation_carries_the_language_status_of_its_sentence[auto/en]` | a `/ws/asr` translation repeats its final's `lang_status` (`provisional` with auto-detect before LID, `manual` with a declared language) | fast |
| `test_harness_url` (+7) | the harness reads `dimmed` per overlay line and the language label; `script_of` names a line's writing system; `DimmingLog` passes a correct run and names each violation (undimmed before confirmation, dimmed without label, dimmed or labelled after confirmation), keeping scripts and times, never text | fast |
| `test_e2e_extension` :: `test_first_confirmed_subtitle_is_the_first_undimmed_one[en-zh/bn-en]` | Chromium, Auto-detect, the en and bn sample WAVs played from their first word: the overlay read through CDP every 250 ms has dimmed, labelled text before the content script renders the confirmed language and none after; for bn the dimmed text is Latin script and the first undimmed line Bengali | **slow** |
| `extension/tests/corrections.test.js` (+1) | keys typed into a correction (`m`, `j`, space, Enter; keydown / keypress / keyup) are stopped at the edited line and do not reach the page | node |
| `test_harness_url` (+8) | `correction_target` = the translated, undimmed cue on screen with its original; `--tm` refuses any path under `backend/data`; `page_untouched` tells a muted, paused or rewound video from an undisturbed one (a looping clip's clock is not compared) | fast |
| `test_e2e_extension` :: `test_alt_e_correction_on_a_live_captions_only_tab_is_saved` (extended) | the test page has player shortcuts on the document (m, space, j); typing "hm, just a moment" into the correction leaves the video as it was | **slow** |
| `test_docs_media` :: `test_using_it_has_the_real_correction_example_and_says_what_a_correction_covers` | the README's Corrections paragraph carries the live test's real example (রাজভোগ heard as রাজবুক, "A Royal Book Shower", what was typed), says a correction is used again only letter for letter and was not used on the rerun, points at T13, and no longer promises "in live captions and in caption mode alike" | fast |
