"""
Translation module for subtitle processing.

Provides:
- Text normalization
- Language detection
- Base neural machine translation
- AI post-editing for natural output
- Line breaking and formatting
"""

from .text_normalizer import TextNormalizer, normalize_subtitle, NormalizedText
from .language_detection import LanguageDetector, detect_language
from .base_translator import BaseTranslator, warmup_models
from .post_editor import PostEditor, warmup_ollama
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
    'PostEditor',
    'warmup_ollama',
    'SmartLineBreaker',
    'format_subtitle_lines',
    'TranslationPipeline',
    'get_pipeline',
    'warmup_pipeline',
]
