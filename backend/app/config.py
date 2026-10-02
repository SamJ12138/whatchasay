"""
Configuration settings for the subtitle translator backend.
All settings can be overridden via environment variables (prefix SUBTITLE_,
nested delimiter "__", e.g. SUBTITLE_ASR__ENGINE=cloud).
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from pydantic_settings import BaseSettings
from pydantic import Field


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


class MTConfig(BaseSettings):
    """Engine selection for the translation hot path."""

    # Preferred order. Engines that are unavailable (no GPU, no key) are skipped.
    #   hymt   - Tencent HY-MT1.5-1.8B via llama.cpp on the GPU (en/zh/bn direct)
    #   opus   - CTranslate2 int8 OPUS-MT on CPU (+ pivot through English)
    #   cloud  - Google Translate v3 / Azure Translator (needs key)
    engine_order: List[str] = Field(default=["hymt", "opus", "cloud"])

    hymt_gguf: Path = Field(default=Path("data/models/mt/HY-MT1.5-1.8B-Q4_K_M.gguf"))
    hymt_repo: str = Field(default="tencent/HY-MT1.5-1.8B-GGUF")
    hymt_file: str = Field(default="HY-MT1.5-1.8B-Q4_K_M.gguf", description="Q4_K_M: 25-40% faster Bengali output than Q8_0 with equivalent quality")
    hymt_gpu_layers: int = Field(default=-1, description="-1 = all layers on GPU")
    hymt_ctx: int = Field(default=1024)
    hymt_max_tokens: int = Field(default=96)
    hymt_parallel: int = Field(default=2, description="llama-server slots; lets two target languages decode together")
    # Languages HY-MT handles well enough to be preferred over OPUS.
    hymt_languages: List[str] = Field(default=["en", "zh", "bn", "vi", "ja", "ko", "es", "fr", "de", "ru", "pt", "it", "ar", "hi"])


class ASRConfig(BaseSettings):
    """Streaming speech recognition."""

    engine: str = Field(default="auto", description="auto | sherpa-zipformer | whisper | cloud")
    models_dir: Path = Field(default=Path("data/models/asr"))
    languages: List[str] = Field(default=["en", "zh", "bn"], description="Languages with streaming models")
    num_threads: int = Field(default=2)
    # Endpoint rules (seconds): trailing silence after speech / after any audio / max utterance
    rule1_min_trailing_silence: float = Field(default=1.2)
    rule2_min_trailing_silence: float = Field(default=0.6)
    rule3_min_utterance_length: float = Field(default=10.0)
    lid_window_s: float = Field(default=2.5)
    partial_interval_ms: int = Field(default=120)
    warmup_languages: List[str] = Field(default=["en", "zh", "bn"], description="Recognizers loaded at startup so auto-detect switches are instant")
    # Accuracy mode (faster-whisper, delayed)
    whisper_model: str = Field(default="large-v3-turbo")
    whisper_bn_model: str = Field(default="mozilla-ai/whisper-large-v3-bn")


class CloudConfig(BaseSettings):
    """Optional cloud tier. Keys come from env or the extension config message."""

    asr_provider: str = Field(default="gladia", description="gladia | elevenlabs")
    gladia_api_key: str = Field(default="")
    elevenlabs_api_key: str = Field(default="")
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
    """Ollama post-editor configuration (used by the refiner when provider=ollama)."""

    base_url: str = Field(default="http://localhost:11434")
    model: str = Field(default="qwen3:4b")
    fallback_model: str = Field(default="qwen2.5:7b")
    timeout: float = Field(default=30.0)
    max_retries: int = Field(default=1)
    temperature: float = Field(default=0.3)
    context_window_size: int = Field(default=3)


class CacheConfig(BaseSettings):
    memory_cache_size: int = Field(default=1000)
    memory_cache_ttl: int = Field(default=3600)
    tm_database_path: Path = Field(default=Path("data/translation_memory.db"))
    fuzzy_match_threshold: float = Field(default=0.95)


class ServerConfig(BaseSettings):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8765)
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

    # Kept for the /config/speed_mode endpoint; "fast" is the only mode that
    # meets the latency target. Batching no longer depends on it.
    speed_mode: str = Field(default="fast")

    # Micro-batching: collect cues for this many ms before one model call.
    batch_window_ms: int = Field(default=30)
    max_batch_size: int = Field(default=8)
    post_edit_batch_size: int = Field(default=3)


class Settings(BaseSettings):
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    lang_detect: LangDetectConfig = Field(default_factory=LangDetectConfig)
    mt: MTConfig = Field(default_factory=MTConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    refiner: RefinerConfig = Field(default_factory=RefinerConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    data_dir: Path = Field(default=Path("data"))
    corrections_file: Path = Field(default=Path("data/corrections.jsonl"))

    class Config:
        env_prefix = "SUBTITLE_"
        env_nested_delimiter = "__"


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
