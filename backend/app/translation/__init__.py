"""
Translation module for subtitle processing.

Provides:
- Text normalization
- Language detection
- Base neural machine translation (router + engines)
- Line breaking and formatting
(The v1 synchronous Ollama post-editor is gone; async refinement lives in refiner.py.)
"""

from .text_normalizer import TextNormalizer, normalize_subtitle, NormalizedText
from .language_detection import LanguageDetector, detect_language
from .base_translator import BaseTranslator, warmup_models
from .line_breaker import SmartLineBreaker, format_subtitle_lines
from .pipeline import TranslationPipeline, get_pipeline, warmup_pipeline

__all__ = [
    'TextNormalizer',
    'normalize_subtitle',
    'NormalizedText',
    'LanguageDetector',
    'detect_language',
    'BaseTranslator',
    'warmup_models',
    'SmartLineBreaker',
    'format_subtitle_lines',
    'TranslationPipeline',
    'get_pipeline',
    'warmup_pipeline',
]
