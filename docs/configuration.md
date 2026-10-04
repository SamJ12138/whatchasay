# Configuration

**In the extension:** the popup's **Settings** link opens the options page: languages, line layout and colours,
the overlay's font (a percentage of the video's height, its minimum in px) and the box behind the text,
live-caption language and engine, caption mode, the optional refinement, and data management.

**In the backend:** environment variables, or the same names in `backend/.env` (copy `backend/.env.example`, which
lists all of them with their defaults; `.env` is ignored by git). Restart the backend after a change. Lists and
objects are JSON; relative paths are relative to `backend/`. Every setting:

**Translation engine**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_MT__ENGINE` | `opus` | Translation engine: opus (OPUS-MT on CPU), hymt (HY-MT on an NVIDIA GPU, after `download_models.py --hymt --accept-hymt-license`) or cloud (Google/Azure) |
| `SUBTITLE_MT__ENGINE_ORDER` | `[]` | Router order; empty = [engine] with opus behind it as the fallback (cloud is appended when enabled) |
| `SUBTITLE_MT__HYMT_GGUF` | `data/models/mt/HY-MT1.5-1.8B-Q4_K_M.gguf` | HY-MT model file (Q4_K_M: 25-40% faster Bengali output than Q8_0, equivalent quality) |
| `SUBTITLE_MT__HYMT_REPO` | `tencent/HY-MT1.5-1.8B-GGUF` | Hugging Face repo the download script fetches the GGUF from |
| `SUBTITLE_MT__HYMT_GPU_LAYERS` | `-1` | Layers offloaded to the GPU (-1 = all) |
| `SUBTITLE_MT__HYMT_CTX` | `1024` | llama-server context size per slot |
| `SUBTITLE_MT__HYMT_MAX_TOKENS` | `96` | Maximum output tokens per HY-MT translation |
| `SUBTITLE_MT__HYMT_PARALLEL` | `2` | llama-server slots (two target languages decode together) |
| `SUBTITLE_MT__HYMT_HEALTH_TIMEOUT_S` | `120.0` | First start: seconds to wait for llama-server to load the model |
| `SUBTITLE_MT__HYMT_RESTART_TIMEOUT_S` | `20.0` | A request that finds llama-server dead waits at most this long for the restarted one |
| `SUBTITLE_MT__HYMT_WARMUP_ATTEMPTS` | `3` | Warmup attempts before HY-MT is paused |
| `SUBTITLE_MT__HYMT_WARMUP_BACKOFF_S` | `1.0` | Pause between warmup attempts (doubles each time) |
| `SUBTITLE_MT__HYMT_RETRY_INITIAL_S` | `15.0` | After a failed start HY-MT is skipped this long, then tried again |
| `SUBTITLE_MT__HYMT_RETRY_MAX_S` | `300.0` | Cap for that doubling pause |
| `SUBTITLE_MT__HYMT_LANGUAGES` | `["en", "zh", "bn", "vi", "ja", "ko", "es", "fr", "de", "r...]` | Target languages HY-MT is preferred for (others go to OPUS-MT) |

**Translation: languages, line layout, OPUS-MT**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_TRANSLATION__TARGET_LANGUAGES` | `["en", "zh"]` | Default target languages (the extension usually sends its own per session) |
| `SUBTITLE_TRANSLATION__SUPPORTED_LANGUAGES` | `["en", "zh", "bn", "vi", "ja", "ko", "es", "fr", "de", "r...]` | Languages offered to the extension |
| `SUBTITLE_TRANSLATION__MAX_LINES` | `2` | Maximum lines per subtitle |
| `SUBTITLE_TRANSLATION__MAX_CHARS_BY_LANG` | `{"en": 42, "vi": 42, "es": 42, "fr": 42, "de": 40, "pt": ...` | Characters per line by language ("_default" for the rest) |
| `SUBTITLE_TRANSLATION__MAX_CHARS_EN` | `42` | Older English line limit, used when MAX_CHARS_BY_LANG has no entry |
| `SUBTITLE_TRANSLATION__MAX_CHARS_ZH` | `22` | Older Chinese/Japanese/Korean line limit, used when MAX_CHARS_BY_LANG has no entry |
| `SUBTITLE_TRANSLATION__MAX_CHARS_VI` | `42` | Older Vietnamese line limit, used when MAX_CHARS_BY_LANG has no entry |
| `SUBTITLE_TRANSLATION__MAX_CHARS_DEFAULT` | `40` | Line limit for other languages when MAX_CHARS_BY_LANG has no "_default" |
| `SUBTITLE_TRANSLATION__MAX_READING_SPEED_EN` | `25.0` | English reading-speed limit (characters per second) used by the line breaker |
| `SUBTITLE_TRANSLATION__MAX_READING_SPEED_ZH` | `15.0` | Chinese reading-speed limit (characters per second) used by the line breaker |
| `SUBTITLE_TRANSLATION__DEVICE` | `cpu` | OPUS-MT device: cuda or cpu; default cuda when an NVIDIA GPU is visible, else cpu |
| `SUBTITLE_TRANSLATION__OPUS_MODELS` | `{...}` | OPUS-MT model per direction: {"<src>": {"<tgt>": "<Hugging Face model>"}}; default: see backend/app/config.py |
| `SUBTITLE_TRANSLATION__ALLOW_PIVOT` | `true` | Translate X->en->Y when no direct OPUS-MT model exists |
| `SUBTITLE_TRANSLATION__CT2_DIR` | `data/models/ct2` | Where converted (CTranslate2) OPUS-MT models live |
| `SUBTITLE_TRANSLATION__OPUS_BEAM_SIZE` | `2` | OPUS-MT beam size |
| `SUBTITLE_TRANSLATION__OPUS_REPETITION_PENALTY` | `1.2` | OPUS-MT repetition penalty |
| `SUBTITLE_TRANSLATION__OPUS_NO_REPEAT_NGRAM_SIZE` | `2` | Never repeat an n-gram of this size (0 = off) |
| `SUBTITLE_TRANSLATION__OPUS_MAX_TOKENS` | `128` | Hard cap on output tokens |
| `SUBTITLE_TRANSLATION__OPUS_MAX_LENGTH_RATIO` | `3.0` | Output tokens <= ratio x source tokens + 4 (stops a runaway decode; 0 = only the hard cap) |
| `SUBTITLE_TRANSLATION__OPUS_TARGET_TOKENS` | `{"Helsinki-NLP/opus-mt-en-zh": ">>cmn_Hans<<", "Helsinki-...` | Target-language token for multi-target models |
| `SUBTITLE_TRANSLATION__GLOSSARY_MAX_ATTEMPTS` | `3` | Glossary: a term travels through MT as a placeholder; a sentence whose placeholder the engine lost is re-translated with the next placeholder of the direction, this many attempts in all (1-3), then unprotected |

**Language detection for page subtitles without a declared language**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_LANG_DETECT__MIN_CHARS` | `24` | Shorter Latin-script text uses the session's language hint instead of a guess |
| `SUBTITLE_LANG_DETECT__CONFIDENCE_FLOOR` | `0.9` | Longer text takes the detector's answer only at or above this probability |
| `SUBTITLE_LANG_DETECT__CANDIDATE_LANGUAGES` | `["en", "zh", "bn", "vi", "ja", "ko", "es", "fr", "de", "r...]` | Languages the detector may answer with (others fall back to the hint) |
| `SUBTITLE_LANG_DETECT__DEFAULT_HINT` | `en` | Hint when neither the cue nor the tab declares a language |

**Speech recognition (live captions)**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_ASR__ENGINE` | `auto` | Speech engine: auto or sherpa-zipformer (the only engine) |
| `SUBTITLE_ASR__MODELS_DIR` | `data/models/asr` | Where the speech models live |
| `SUBTITLE_ASR__ZH_MODEL` | `bilingual` | Mandarin model: bilingual (Apache-2.0, default) or large (no declared license, lower error rate on read Mandarin; `download_models.py --mandarin-large --accept-mandarin-large-terms`) |
| `SUBTITLE_ASR__PUNCTUATION` | `true` | Restore casing and punctuation of English speech before translation |
| `SUBTITLE_ASR__PUNCT_DIR` | `data/models/punct` | Where the punctuation model lives |
| `SUBTITLE_ASR__LANGUAGES` | `["en", "zh", "bn"]` | Spoken languages with streaming models (auto-detect chooses among these) |
| `SUBTITLE_ASR__NUM_THREADS` | `1` | CPU threads per recognizer and for language ID. 1: onnxruntime's thread pools spin between 40 ms frames, so 2 cost 1.5 cores per recognizer at real time against 0.06 with 1, for 3-4 ms per decode chunk |
| `SUBTITLE_ASR__RULE1_MIN_TRAILING_SILENCE` | `1.2` | Endpointing (seconds): reset after this much silence when no word was recognised yet |
| `SUBTITLE_ASR__RULE2_MIN_TRAILING_SILENCE` | `0.6` | The silence threshold: end a line after this much silence following speech (seconds) |
| `SUBTITLE_ASR__RULE3_MIN_UTTERANCE_LENGTH` | `30.0` | The recognizer's own hard reset after this many seconds without an endpoint; it can cut inside a word and is only a backstop behind MAX_SEGMENT_S |
| `SUBTITLE_ASR__SPLIT_GAP_S` | `0.5` | Also end a line before a word that starts this long after the previous one (seconds; pauses the silence rule misses; 0 = off) |
| `SUBTITLE_ASR__SPLIT_GAP_RATIO` | `2.5` | ...only if that pause is at least this many times the line's median gap between tokens (slow speech is not chopped; 0 = no test) |
| `SUBTITLE_ASR__SPLIT_MIN_PIECE_S` | `2.0` | ...and only once the line is at least this long (seconds; a hesitation does not become a line) |
| `SUBTITLE_ASR__MAX_SEGMENT_S` | `6.0` | Longest line in seconds of speech: past it the line is cut at its widest gap between words (0 = no limit) |
| `SUBTITLE_ASR__MAX_SEGMENT_TOKENS` | `48` | Longest line in recognizer tokens, cut the same way (0 = no limit) |
| `SUBTITLE_ASR__LID_WINDOW_S` | `2.5` | Spoken-language ID, full window: seconds of voiced audio at which any answer above the floor is accepted (and the first-guess language confirmed) |
| `SUBTITLE_ASR__LID_FIRST_WINDOW_S` | `1.0` | First, early attempt after this many seconds of voiced audio; early, only another language than the first guess is accepted, twice in a row |
| `SUBTITLE_ASR__LID_RETRY_STEP_S` | `0.5` | Audio between early attempts (seconds; voiced or not) |
| `SUBTITLE_ASR__LID_MIN_CONFIDENCE` | `0.6` | Spoken-language ID answers only at or above this confidence among the spoken languages |
| `SUBTITLE_ASR__LID_PRIOR_THRESHOLD` | `0.6` | The page's language prior (docs/page-prior.md): its favourite is the first recognizer at or above this, else English |
| `SUBTITLE_ASR__LID_PRIOR_FLOOR_EN` | `0.97` | With a prior favouring English, one language-ID answer confirms it early only at or above this (whisper-tiny says English up to 0.97 on the first second of Mandarin and Bengali) |
| `SUBTITLE_ASR__PARALLEL_WINDOW_S` | `5.0` | Until the language is confirmed, every spoken language's recognizer hears the audio for at most this many seconds; the confirmed one's text is ready at once, nothing replayed (0 = off) |
| `SUBTITLE_ASR__PARTIAL_INTERVAL_MS` | `120` | Minimum gap between partial captions (ms) |
| `SUBTITLE_ASR__DRAFT_STABLE_PARTIALS` | `3` | Draft translation of a line that is still open: a word counts as stable after this many partial results in a row (0 = no drafts) |
| `SUBTITLE_ASR__DRAFT_DEBOUNCE_MS` | `1500` | The stable part of an open line is re-translated at most once per this many ms (lower: earlier drafts, more rewriting on screen) |
| `SUBTITLE_ASR__DRAFT_AGREE_K` | `2` | Local agreement: of a draft translation only the start that the line's last K drafts agree on is shown (1 = every draft as it is); halves the rewrites on screen (`docs/latency.md`) |
| `SUBTITLE_ASR__DRAFT_MIN_WORDS` | `3` | A draft only once the stable part of the line has this many words (or the share below, whichever comes first): no one-word first drafts |
| `SUBTITLE_ASR__DRAFT_MIN_CJK_CHARS` | `4` | The same minimum in characters for a line in Chinese, Japanese or Korean |
| `SUBTITLE_ASR__DRAFT_MIN_FRACTION` | `0.4` | Or this share of the words heard so far in the line |
| `SUBTITLE_ASR__WARMUP_LANGUAGES` | `["en", "zh", "bn"]` | Recognizers loaded at startup so a language switch is instant |

**Cloud providers (off by default; text leaves this computer when on)**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_CLOUD__ENABLED` | `false` | Master switch for cloud translation and the Groq/Gemini refiners |
| `SUBTITLE_CLOUD__MT_PROVIDER` | `google` | Cloud translation provider: google or azure |
| `SUBTITLE_CLOUD__GOOGLE_API_KEY` | `(empty)` | Google Cloud Translation API key |
| `SUBTITLE_CLOUD__AZURE_TRANSLATOR_KEY` | `(empty)` | Azure Translator key |
| `SUBTITLE_CLOUD__AZURE_TRANSLATOR_REGION` | `(empty)` | Azure Translator region (e.g. westeurope) |

**Optional LLM refinement (a second, improved translation a moment later)**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_REFINER__ENABLED` | `false` | Off by default; a tab can still ask for it in the extension's settings |
| `SUBTITLE_REFINER__PROVIDER` | `ollama` | Refiner: ollama (local), groq or gemini (cloud: also needs SUBTITLE_CLOUD__ENABLED=true) |
| `SUBTITLE_REFINER__DEADLINE_S` | `0.8` | A refinement later than this (seconds) is dropped |
| `SUBTITLE_REFINER__SKIP_LANGUAGES` | `["bn"]` | Target languages never refined (small LLMs degrade Bengali) |
| `SUBTITLE_REFINER__GROQ_API_KEY` | `(empty)` | Groq API key (refiner) |
| `SUBTITLE_REFINER__GROQ_MODEL` | `llama-3.1-8b-instant` | Groq model |
| `SUBTITLE_REFINER__GEMINI_API_KEY` | `(empty)` | Gemini API key (refiner) |
| `SUBTITLE_REFINER__GEMINI_MODEL` | `gemini-2.5-flash-lite` | Gemini model |
| `SUBTITLE_OLLAMA__BASE_URL` | `http://localhost:11434` | Local Ollama server for provider=ollama |
| `SUBTITLE_OLLAMA__MODEL` | `qwen3:4b` | Ollama model |
| `SUBTITLE_OLLAMA__TIMEOUT` | `30.0` | Seconds per Ollama request |
| `SUBTITLE_OLLAMA__CONTEXT_WINDOW_SIZE` | `3` | Previous subtitles given to the refiner as context |

**What is remembered**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_TM__PERSIST_AUDIO_SESSIONS` | `false` | Live-caption (audio) sessions write to the translation memory (default: never) |
| `SUBTITLE_TM__PERSIST_CAPTIONS` | `true` | Page subtitles (caption mode) are kept in the translation memory |
| `SUBTITLE_TM__RETENTION_DAYS` | `30` | Machine translations unused this many days are deleted at startup (0 = keep; corrections are kept) |
| `SUBTITLE_CACHE__MEMORY_CACHE_SIZE` | `1000` | In-memory translation cache: entries |
| `SUBTITLE_CACHE__MEMORY_CACHE_TTL` | `3600` | In-memory translation cache: seconds an entry lives |
| `SUBTITLE_CACHE__TM_DATABASE_PATH` | `data/translation_memory.db` | The translation memory (SQLite) |
| `SUBTITLE_DATA_DIR` | `data` | Folder for runtime data |

**Server**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_SERVER__HOST` | `127.0.0.1` | Bind address; loopback only, anything else is refused |
| `SUBTITLE_SERVER__PORT` | `8765` | Port (also run.py --port; the extension's server URL must match) |
| `SUBTITLE_SERVER__ALLOW_LOCAL_EXTENSION` | `true` | Accept the unpacked extension next to this backend (its id is derived from its folder) |
| `SUBTITLE_SERVER__EXTENSION_IDS` | `[]` | More extension ids allowed to call the backend |
| `SUBTITLE_SERVER__EXTRA_ORIGINS` | `[]` | More origins allowed to call the backend |
| `SUBTITLE_SERVER__DEBUG` | `false` | Debug logging (also run.py --debug) |
| `SUBTITLE_SERVER__WS_PING_INTERVAL` | `30.0` | WebSocket keepalive ping interval (seconds) |
| `SUBTITLE_SERVER__WS_PING_TIMEOUT` | `10.0` | WebSocket keepalive ping timeout (seconds) |
| `SUBTITLE_SERVER__MAX_CONCURRENT_TRANSLATIONS` | `5` | Translations running at once |

**Behaviour flags**

| Variable | Default | |
|---|---|---|
| `SUBTITLE_FEATURES__USE_POST_EDITOR` | `false` | Older name for SUBTITLE_REFINER__ENABLED (either one turns refinement on) |
| `SUBTITLE_FEATURES__FAST_MODE` | `true` | Session default for the extension's fast mode |
| `SUBTITLE_FEATURES__STRICT_MEANING_LOCK` | `true` | Session default for the extension's strict meaning lock |
| `SUBTITLE_FEATURES__WARMUP_ON_START` | `true` | Load models at startup (off: they load on first use) |
| `SUBTITLE_FEATURES__BATCH_WINDOW_MS` | `30` | Page-subtitle cues arriving within this many ms are translated in one batch |
| `SUBTITLE_FEATURES__MAX_BATCH_SIZE` | `8` | Most cues in one such batch |
