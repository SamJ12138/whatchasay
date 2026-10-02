"""
Language detection for subtitle text.

Uses langdetect (based on Google's language-detection library) for fast,
local language identification. Falls back to simple heuristics for short texts.
"""

import logging
from typing import Optional, Tuple
from functools import lru_cache

from langdetect import detect, detect_langs, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

# Set seed for reproducibility
DetectorFactory.seed = 42

logger = logging.getLogger(__name__)


# Language code mappings (langdetect -> ISO 639-1)
LANGDETECT_TO_ISO = {
    'zh-cn': 'zh',
    'zh-tw': 'zh',
    'zh': 'zh',
}


class LanguageDetector:
    """
    Fast local language detection for subtitle text.
    
    Uses langdetect with fallbacks for short/ambiguous text.
    """
    
    # Minimum confidence threshold
    MIN_CONFIDENCE = 0.7
    
    # Minimum text length for reliable detection
    MIN_TEXT_LENGTH = 10
    
    # Scripts that indicate specific languages
    SCRIPT_INDICATORS = {
        # CJK
        'zh': (
            '\u4e00-\u9fff'  # CJK Unified Ideographs
            '\u3400-\u4dbf'  # CJK Extension A
        ),
        'ja': (
            '\u3040-\u309f'  # Hiragana
            '\u30a0-\u30ff'  # Katakana
        ),
        'ko': '\uac00-\ud7af',  # Hangul Syllables
        
        # Other scripts
        'ar': '\u0600-\u06ff',  # Arabic
        'he': '\u0590-\u05ff',  # Hebrew
        'ru': '\u0400-\u04ff',  # Cyrillic
        'th': '\u0e00-\u0e7f',  # Thai
        'hi': '\u0900-\u097f',  # Devanagari
    }
    
    def __init__(self, default_source_lang: str = "en"):
        """
        Initialize detector.
        
        Args:
            default_source_lang: Default language when detection fails
        """
        self.default_source_lang = default_source_lang
        self._compile_script_patterns()
    
    def _compile_script_patterns(self):
        """Compile regex patterns for script detection."""
        import re
        self.script_patterns = {}
        for lang, chars in self.SCRIPT_INDICATORS.items():
            self.script_patterns[lang] = re.compile(f'[{chars}]')
    
    def detect(self, text: str) -> Tuple[str, float]:
        """
        Detect language of text.
        
        Args:
            text: Text to analyze
            
        Returns:
            Tuple of (language_code, confidence)
        """
        text = text.strip()
        
        if not text:
            return self.default_source_lang, 0.0
        
        # Try script-based detection first for CJK and other distinctive scripts
        script_lang = self._detect_by_script(text)
        if script_lang:
            return script_lang, 0.95
        
        # If text is too short, use simple heuristics
        if len(text) < self.MIN_TEXT_LENGTH:
            return self._detect_short_text(text)
        
        # Use langdetect for longer text
        try:
            results = detect_langs(text)
            if results:
                top = results[0]
                lang = self._normalize_lang_code(top.lang)
                confidence = top.prob
                
                # If confidence is low, check for mixed content
                if confidence < self.MIN_CONFIDENCE:
                    # Try script detection as tiebreaker
                    script_lang = self._detect_by_script(text)
                    if script_lang:
                        return script_lang, max(confidence, 0.7)
                
                return lang, confidence
                
        except LangDetectException as e:
            logger.debug(f"Language detection failed: {e}")
        
        return self.default_source_lang, 0.5
    
    def _detect_by_script(self, text: str) -> Optional[str]:
        """
        Detect language based on character script.
        
        Useful for CJK and other distinctive scripts where
        character-based detection is very reliable.
        """
        # Count characters from each script
        script_counts = {}
        
        for lang, pattern in self.script_patterns.items():
            count = len(pattern.findall(text))
            if count > 0:
                script_counts[lang] = count
        
        if not script_counts:
            return None
        
        # Get dominant script
        dominant = max(script_counts, key=script_counts.get)
        dominant_count = script_counts[dominant]
        
        # Require at least 20% of characters to be from the script
        if dominant_count >= len(text) * 0.2:
            # Special handling for zh vs ja
            if dominant == 'zh' and 'ja' in script_counts:
                # If we have hiragana/katakana, it's likely Japanese
                if script_counts.get('ja', 0) > 2:
                    return 'ja'
            return dominant
        
        return None
    
    def _detect_short_text(self, text: str) -> Tuple[str, float]:
        """
        Detect language of short text using simple heuristics.
        """
        # Try script detection first
        script_lang = self._detect_by_script(text)
        if script_lang:
            return script_lang, 0.8
        
        # Try langdetect with lower confidence threshold
        try:
            lang = detect(text)
            return self._normalize_lang_code(lang), 0.6
        except LangDetectException:
            pass
        
        # Default to English for Latin script
        if text.isascii():
            return 'en', 0.5
        
        return self.default_source_lang, 0.3
    
    def _normalize_lang_code(self, code: str) -> str:
        """Normalize language code to ISO 639-1."""
        code = code.lower()
        return LANGDETECT_TO_ISO.get(code, code)
    
    def is_target_language(self, text: str, target_lang: str) -> bool:
        """
        Check if text is already in the target language.
        
        Useful for skipping translation when source == target.
        """
        detected, confidence = self.detect(text)
        
        # Normalize both for comparison
        detected = self._normalize_lang_code(detected)
        target_lang = self._normalize_lang_code(target_lang)
        
        # Require high confidence for match
        return detected == target_lang and confidence > 0.8


@lru_cache(maxsize=1000)
def detect_language(text: str) -> Tuple[str, float]:
    """
    Cached language detection.
    
    Args:
        text: Text to detect
        
    Returns:
        Tuple of (language_code, confidence)
    """
    detector = LanguageDetector()
    return detector.detect(text)


def get_language_name(code: str) -> str:
    """Get human-readable language name from code."""
    names = {
        'en': 'English',
        'zh': 'Chinese',
        'ru': 'Russian',
        'es': 'Spanish',
        'fr': 'French',
        'de': 'German',
        'ja': 'Japanese',
        'ko': 'Korean',
        'pt': 'Portuguese',
        'it': 'Italian',
        'ar': 'Arabic',
        'hi': 'Hindi',
        'tr': 'Turkish',
        'pl': 'Polish',
        'nl': 'Dutch',
        'vi': 'Vietnamese',
        'bn': 'Bengali',
        'th': 'Thai',
        'id': 'Indonesian',
        'uk': 'Ukrainian',
        'cs': 'Czech',
        'sv': 'Swedish',
        'da': 'Danish',
        'fi': 'Finnish',
        'no': 'Norwegian',
        'el': 'Greek',
        'he': 'Hebrew',
        'ro': 'Romanian',
        'hu': 'Hungarian',
        'bg': 'Bulgarian',
    }
    return names.get(code.lower(), code.upper())
