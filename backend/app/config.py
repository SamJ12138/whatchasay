"""
Configuration settings for the subtitle translator backend.
All settings can be overridden via environment variables (prefix SUBTITLE_,
nested delimiter "__", e.g. SUBTITLE_ASR__ENGINE=cloud).
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator, model_validator


def _detect_cuda() -> bool:
    """True if CTranslate2 or torch can see an NVIDIA GPU."""
    from . import obs

    try:
        import ctranslate2  # noqa

        if ctranslate2.get_cuda_device_count() > 0:
            obs.log("startup", "success", component="cuda_detect", via="ctranslate2")
            return True
    except Exception as e:
        obs.log_exc("startup", e, event="skip", api="process", component="cuda_detect", via="ctranslate2")
        pass
    try:
        import torch  # noqa

        found = bool(torch.cuda.is_available())
        obs.log("startup", "success" if found else "skip", component="cuda_detect", via="torch",
                **({} if found else {"error_type": "process", "error_message": "no CUDA device; HY-MT will be skipped",
                                     "degraded": "device=cpu"}))
        return found
    except Exception as e:
        obs.log_exc("startup", e, event="skip", api="process", component="cuda_detect", via="torch",
                    degraded="device=cpu; HY-MT will be skipped")
        return False


# --------------------------------------------------------------------------
# Languages
# --------------------------------------------------------------------------

LANGUAGE_NAMES: Dict[str, str] = {
    "en": "English",
    "zh": "Chinese",
    "bn": "Bengali",
    "vi": "Vietnamese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
    "ar": "Arabic",
    "hi": "Hindi",
}


class TranslationConfig(BaseSettings):
    """Translation pipeline configuration."""

    # Target languages (ISO 639-1 codes) - default output languages
    target_languages: List[str] = Field(default=["en", "zh"])

    # Languages offered in the UI. en/zh/bn are first-class (streaming ASR +
    # direct MT); the rest are translation-only via OPUS-MT pivots.
    supported_languages: List[str] = Field(
        default=["en", "zh", "bn", "vi", "ja", "ko", "es", "fr", "de", "ru", "pt", "it"]
    )

    max_lines: int = Field(default=2, description="Maximum subtitle lines")

    # Per-language character limits per line. Note: len() counts combining
    # marks, so Bengali/Hindi limits are a little generous on purpose.
    max_chars_by_lang: dict = Field(default_factory=lambda: {
        "en": 42, "vi": 42, "es": 42, "fr": 42, "de": 40, "pt": 42, "it": 42,
        "ru": 38, "zh": 22, "ja": 22, "ko": 22, "ar": 35, "hi": 38, "bn": 38,
        "_default": 40,
    })

    # Legacy fields (still read by the extension config message)
    max_chars_en: int = Field(default=42)
    max_chars_zh: int = Field(default=22)
    max_chars_vi: int = Field(default=42)
    max_chars_default: int = Field(default=40)

    max_reading_speed_en: float = Field(default=25.0)
    max_reading_speed_zh: float = Field(default=15.0)
    max_reading_speed_vi: float = Field(default=22.0)

    # Device for neural models: "cuda" if an NVIDIA GPU is visible, else "cpu".
    device: str = Field(default_factory=lambda: "cuda" if _detect_cuda() else "cpu")

    # OPUS-MT direct models, verified to exist on Hugging Face (Sept 2026).
    # Only pairs whose *target* really is the listed language are here.
    # Anything missing is routed through English (see get_opus_route) or to
    # HY-MT / cloud by the MT router.
    opus_models: dict = Field(default_factory=lambda: {
        "en": {
            "zh": "Helsinki-NLP/opus-mt-en-zh",
            # No official Helsinki en->bn model; community fine-tune of opus-mt-en-inc (~30-45 ms, decent quality).
            "bn": "shhossain/opus-mt-en-to-bn",
            "vi": "Helsinki-NLP/opus-mt-en-vi",
            "ja": "Helsinki-NLP/opus-mt-en-jap",
            "es": "Helsinki-NLP/opus-mt-en-es",
            "fr": "Helsinki-NLP/opus-mt-en-fr",
            "de": "Helsinki-NLP/opus-mt-en-de",
            "ru": "Helsinki-NLP/opus-mt-en-ru",
            "it": "Helsinki-NLP/opus-mt-en-it",
            "ar": "Helsinki-NLP/opus-mt-en-ar",
            "hi": "Helsinki-NLP/opus-mt-en-hi",
        },
        "zh": {"en": "Helsinki-NLP/opus-mt-zh-en", "vi": "Helsinki-NLP/opus-mt-zh-vi"},
        "bn": {"en": "Helsinki-NLP/opus-mt-bn-en"},
        "vi": {"en": "Helsinki-NLP/opus-mt-vi-en"},
        "ja": {"en": "Helsinki-NLP/opus-mt-ja-en", "vi": "Helsinki-NLP/opus-mt-ja-vi"},
        "ko": {"en": "Helsinki-NLP/opus-mt-ko-en"},
        "ru": {"en": "Helsinki-NLP/opus-mt-ru-en", "vi": "Helsinki-NLP/opus-mt-ru-vi"},
        "es": {"en": "Helsinki-NLP/opus-mt-es-en", "vi": "Helsinki-NLP/opus-mt-es-vi"},
        "fr": {"en": "Helsinki-NLP/opus-mt-fr-en", "vi": "Helsinki-NLP/opus-mt-fr-vi"},
        "de": {"en": "Helsinki-NLP/opus-mt-de-en", "vi": "Helsinki-NLP/opus-mt-de-vi", "zh": "Helsinki-NLP/opus-mt-de-ZH"},
        "pt": {"en": "Helsinki-NLP/opus-mt-tc-big-pt-en"},
        "it": {"en": "Helsinki-NLP/opus-mt-it-en", "vi": "Helsinki-NLP/opus-mt-it-vi"},
        "ar": {"en": "Helsinki-NLP/opus-mt-ar-en"},
        "hi": {"en": "Helsinki-NLP/opus-mt-hi-en"},
    })

    # Allow X->en->Y two-hop translation when no direct OPUS model exists.
    allow_pivot: bool = Field(default=True)

    # Where CTranslate2-converted OPUS models are cached.
    ct2_dir: Path = Field(default=Path("data/models/ct2"))

    # OPUS-MT decoding (CTranslate2). D1 bar (tests/test_opus_quality.py): no repeated
    # 3-gram in an output, output length within 3x the source.
    opus_beam_size: int = Field(default=2)
    opus_repetition_penalty: float = Field(default=1.2)
    opus_no_repeat_ngram_size: int = Field(default=2, description="0 = off")
    opus_max_tokens: int = Field(default=128, description="hard cap on output tokens")
    opus_max_length_ratio: float = Field(default=3.0, description="output tokens <= ratio x source tokens + 4 (stops a runaway decode); 0 = only the hard cap")
    # Multi-target OPUS models need a target-language token first (as in their model cards).
    opus_target_tokens: dict = Field(default_factory=lambda: {
        "Helsinki-NLP/opus-mt-en-zh": ">>cmn_Hans<<",
        "Helsinki-NLP/opus-mt-de-ZH": ">>cmn_Hans<<",
    })


class LangDetectConfig(BaseSettings):
    """Text language detection for cues without a declared language (T12).

    A distinctive script decides first (CJK, kana, Hangul, Bengali, Devanagari,
    Arabic, ...). Latin-script text shorter than `min_chars` is not guessed by
    langdetect at all: it takes the session hint. Longer text takes langdetect's
    answer only if its probability is >= `confidence_floor` and the language is
    in `candidate_languages`; otherwise the session hint. The session hint comes
    from the cue (`lang_hint`), the /ws connection's `source_lang_hint`, or
    `default_hint`.
    """

    min_chars: int = Field(default=24, description="shorter Latin-script text uses the session hint")
    confidence_floor: float = Field(default=0.90)
    candidate_languages: List[str] = Field(
        default=["en", "zh", "bn", "vi", "ja", "ko", "es", "fr", "de", "ru", "pt", "it", "ar", "hi", "nl", "pl", "tr", "id"]
    )
    default_hint: str = Field(default="en")


MT_ENGINES = ("opus", "hymt", "cloud")


class MTConfig(BaseSettings):
    """Engine selection for the translation hot path (D1).

    engine (SUBTITLE_MT__ENGINE):
      opus   - OPUS-MT on CTranslate2 int8, CPU (+ pivot through English). Default.
      hymt   - Tencent HY-MT1.5-1.8B via llama.cpp on an NVIDIA GPU (en/zh/bn direct).
               Optional: needs `python scripts/download_models.py --hymt --accept-hymt-license`
               (Tencent HY Community License); the backend never downloads it.
      cloud  - Google / Azure (needs a key in the backend config).
    engine_order: the router's order; empty = [engine] with opus behind it as the fallback.
    """

    engine: str = Field(default="opus")
    engine_order: List[str] = Field(default_factory=list)

    @field_validator("engine")
    @classmethod
    def _known_engine(cls, v: str) -> str:
        if v not in MT_ENGINES:
            raise ValueError(f"unknown MT engine {v!r}; one of {MT_ENGINES}")
        return v

    @model_validator(mode="after")
    def _derive_order(self):
        if not self.engine_order:
            self.engine_order = [self.engine] + (["opus"] if self.engine != "opus" else [])
        return self

    hymt_gguf: Path = Field(default=Path("data/models/mt/HY-MT1.5-1.8B-Q4_K_M.gguf"))
    hymt_repo: str = Field(default="tencent/HY-MT1.5-1.8B-GGUF")
    hymt_file: str = Field(default="HY-MT1.5-1.8B-Q4_K_M.gguf", description="Q4_K_M: 25-40% faster Bengali output than Q8_0 with equivalent quality")
    hymt_gpu_layers: int = Field(default=-1, description="-1 = all layers on GPU")
    hymt_ctx: int = Field(default=1024)
    hymt_max_tokens: int = Field(default=96)
    hymt_parallel: int = Field(default=2, description="llama-server slots; lets two target languages decode together")
    # llama-server lifecycle (P2, P3)
    hymt_health_timeout_s: float = Field(default=120.0, description="first start: model load until /health is ok")
    hymt_restart_timeout_s: float = Field(default=20.0, description="a request that finds the child dead waits at most this long for the restarted one")
    hymt_warmup_attempts: int = Field(default=3)
    hymt_warmup_backoff_s: float = Field(default=1.0, description="pause between warmup attempts, doubled each time")
    hymt_retry_initial_s: float = Field(default=15.0, description="after a failed start, HY-MT is skipped this long, then tried again")
    hymt_retry_max_s: float = Field(default=300.0, description="cap for the doubling retry pause")
    # Languages HY-MT handles well enough to be preferred over OPUS.
    hymt_languages: List[str] = Field(default=["en", "zh", "bn", "vi", "ja", "ko", "es", "fr", "de", "ru", "pt", "it", "ar", "hi"])


class ASRConfig(BaseSettings):
    """Streaming speech recognition."""

    engine: str = Field(default="auto", description="auto | sherpa-zipformer (the only engine)")
    models_dir: Path = Field(default=Path("data/models/asr"))
    # Mandarin model: "bilingual" (Apache-2.0, Mandarin + English) or "large" (lower error
    # rate on read Mandarin, no declared license; scripts/download_models.py --mandarin-large).
    zh_model: str = Field(default="bilingual")
    # English casing + punctuation before translation (asr/punctuation.py; docs/asr-input-quality.md)
    punctuation: bool = Field(default=True)
    punct_dir: Path = Field(default=Path("data/models/punct"))
    languages: List[str] = Field(default=["en", "zh", "bn"], description="Languages with streaming models")
    num_threads: int = Field(default=2)
    # Endpoint rules (seconds): trailing silence after speech / after any audio / max utterance
    rule1_min_trailing_silence: float = Field(default=1.2)
    rule2_min_trailing_silence: float = Field(default=0.6)
    rule3_min_utterance_length: float = Field(default=10.0)
    lid_window_s: float = Field(default=2.5)
    partial_interval_ms: int = Field(default=120)
    warmup_languages: List[str] = Field(default=["en", "zh", "bn"], description="Recognizers loaded at startup so auto-detect switches are instant")

    @field_validator("zh_model")
    @classmethod
    def _known_zh_model(cls, v: str) -> str:
        if v not in ("bilingual", "large"):
            raise ValueError(f"unknown Mandarin model {v!r}; one of ('bilingual', 'large')")
        return v


class CloudConfig(BaseSettings):
    """Optional cloud tier (D4): off by default. Keys live only in the backend's
    config: environment variables or backend/.env (SUBTITLE_CLOUD__ENABLED=true,
    SUBTITLE_CLOUD__GOOGLE_API_KEY=...). The extension never stores or sends them;
    they travel to the provider in request headers, never in a URL."""

    enabled: bool = Field(default=False, description="master switch for every cloud provider (MT and refiner)")
    mt_provider: str = Field(default="google", description="google | azure")
    google_api_key: str = Field(default="")
    azure_translator_key: str = Field(default="")
    azure_translator_region: str = Field(default="")


class RefinerConfig(BaseSettings):
    """Asynchronous LLM refinement (never in the hot path)."""

    enabled: bool = Field(default=False)
    provider: str = Field(default="ollama", description="ollama | groq | gemini")
    deadline_s: float = Field(default=0.8)
    # Refinement is skipped for these targets (small LLMs degrade Bengali).
    skip_languages: List[str] = Field(default=["bn"])
    groq_api_key: str = Field(default="")
    groq_model: str = Field(default="llama-3.1-8b-instant")
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-flash-lite")


class OllamaConfig(BaseSettings):
    """Local Ollama server for the optional async refiner (refiner.provider=ollama)."""

    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="qwen3:4b")
    timeout: float = Field(default=30.0)
    temperature: float = Field(default=0.3)
    context_window_size: int = Field(default=3)


class TMConfig(BaseSettings):
    """What the persistent translation memory keeps (D3). SUBTITLE_TM__*.

    Audio (live-caption) sessions use an in-memory cache that dies with the
    session; their text reaches the TM only with persist_audio_sessions. Caption
    cues (page subtitles; they reach the backend only when the user turned caption
    mode on) persist with persist_captions. User corrections always persist.
    Machine rows unused for retention_days are deleted at startup (0 = keep).
    """

    persist_audio_sessions: bool = Field(default=False)
    persist_captions: bool = Field(default=True)
    retention_days: int = Field(default=30)


class CacheConfig(BaseSettings):
    memory_cache_size: int = Field(default=1000)
    memory_cache_ttl: int = Field(default=3600)
    tm_database_path: Path = Field(default=Path("data/translation_memory.db"))
    fuzzy_match_threshold: float = Field(default=0.95)


class ServerConfig(BaseSettings):
    host: str = Field(default="127.0.0.1", description="loopback only (security.check_bind_host)")
    port: int = Field(default=8765)
    # Origins allowed to call the backend (security.py): the unpacked extension next
    # to this backend (id derived from its path), these extension ids, and the
    # backend's own loopback origin. SUBTITLE_SERVER__EXTENSION_IDS='["<id>"]'
    allow_local_extension: bool = Field(default=True)
    extension_ids: List[str] = Field(default_factory=list)
    extra_origins: List[str] = Field(default_factory=list)
    debug: bool = Field(default=False)
    ws_ping_interval: float = Field(default=30.0)
    ws_ping_timeout: float = Field(default=10.0)
    max_concurrent_translations: int = Field(default=5)
    batch_timeout: float = Field(default=0.1)


class FeatureFlags(BaseSettings):
    # Synchronous post-editing is gone; use_post_editor now only gates the
    # async refiner (see RefinerConfig.enabled) for backwards compatibility.
    use_post_editor: bool = Field(default=False)
    fast_mode: bool = Field(default=True)
    strict_meaning_lock: bool = Field(default=True)
    enable_corrections: bool = Field(default=True)
    warmup_on_start: bool = Field(default=True)

    # Micro-batching: collect cues for this many ms before one model call.
    batch_window_ms: int = Field(default=30)
    max_batch_size: int = Field(default=8)


class Settings(BaseSettings):
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    lang_detect: LangDetectConfig = Field(default_factory=LangDetectConfig)
    mt: MTConfig = Field(default_factory=MTConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    refiner: RefinerConfig = Field(default_factory=RefinerConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    tm: TMConfig = Field(default_factory=TMConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    data_dir: Path = Field(default=Path("data"))
    corrections_file: Path = Field(default=Path("data/corrections.jsonl"))

    class Config:
        env_prefix = "SUBTITLE_"
        env_nested_delimiter = "__"
        # cloud keys and other secrets (D4); relative to the working directory (backend/ for run.py)
        env_file = os.environ.get("SUBTITLE_ENV_FILE", ".env")
        env_file_encoding = "utf-8"

    @model_validator(mode="after")
    def _cloud_in_router(self):
        """Cloud on (D4): the cloud engine joins the router order, last, unless placed already."""
        if self.cloud.enabled and "cloud" not in self.mt.engine_order:
            self.mt.engine_order.append("cloud")
        return self


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def get_max_chars_for_language(lang: str) -> int:
    max_chars = settings.translation.max_chars_by_lang.get(lang)
    if max_chars is not None:
        return max_chars
    if lang == "en":
        return settings.translation.max_chars_en
    if lang == "vi":
        return settings.translation.max_chars_vi
    if lang in ("zh", "ja", "ko"):
        return settings.translation.max_chars_zh
    return settings.translation.max_chars_by_lang.get("_default", settings.translation.max_chars_default)


def get_language_display_name(lang: str) -> str:
    return LANGUAGE_NAMES.get(lang, lang.upper())


def get_opus_model(source_lang: str, target_lang: str) -> Optional[str]:
    """Direct OPUS-MT model for a pair, or None if none exists."""
    models = settings.translation.opus_models
    return models.get(source_lang, {}).get(target_lang)


def get_opus_route(source_lang: str, target_lang: str) -> Optional[List[Tuple[str, str, str]]]:
    """
    Sequence of (src, tgt, model) hops to translate source->target with OPUS-MT.
    Direct model if available, else X->en->Y pivot when allowed. None if impossible.
    """
    if source_lang == target_lang:
        return []
    direct = get_opus_model(source_lang, target_lang)
    if direct:
        return [(source_lang, target_lang, direct)]
    if not settings.translation.allow_pivot or "en" in (source_lang, target_lang):
        return None
    first = get_opus_model(source_lang, "en")
    second = get_opus_model("en", target_lang)
    if first and second:
        return [(source_lang, "en", first), ("en", target_lang, second)]
    return None
