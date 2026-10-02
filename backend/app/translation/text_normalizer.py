"""
Text normalization for subtitle processing.

Handles:
- HTML/formatting tag removal
- Speaker label extraction
- Whitespace normalization
- Special character handling
- Music/sound effect markers
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
import ftfy


@dataclass
class NormalizedText:
    """Result of text normalization."""
    
    text: str  # Cleaned text for translation
    speaker: Optional[str]  # Extracted speaker label
    is_music: bool  # Whether this is a music/song cue
    is_sound_effect: bool  # Whether this is a sound effect
    has_italics: bool  # Whether original had italics
    original: str  # Original text
    
    @property
    def should_translate(self) -> bool:
        """Whether this cue should be translated."""
        # Strip music/sound markers and punctuation; if nothing is left there
        # are no words to translate (e.g. a bare "♪" or "[music]").
        content = re.sub(r"[\s♪♫🎵🎶\[\]\(\)\*\.\-–—…:,;!?\"'|]+", "", self.text)
        content = re.sub(r"(?i)music|singing", "", content) if (self.is_music or self.is_sound_effect) else content
        if (self.is_music or self.is_sound_effect) and not content:
            return False
        if not self.text.strip() or not content:
            return False
        return True


class TextNormalizer:
    """Normalizes subtitle text for translation."""
    
    # Patterns for HTML/formatting tags
    HTML_TAG_PATTERN = re.compile(r'<[^>]+>')
    WEBVTT_TAG_PATTERN = re.compile(r'</?(?:c|v|b|i|u|ruby|rt|lang)[^>]*>')
    
    # Speaker label patterns
    # Matches: "JOHN:", "John:", "[John]", "(John)", "- John:"
    SPEAKER_PATTERNS = [
        re.compile(r'^[\-–—]\s*([A-Z][A-Za-z\s]+):\s*'),  # - JOHN:
        re.compile(r'^\[([^\]]+)\]\s*'),  # [John]
        re.compile(r'^\(([^\)]+)\)\s*'),  # (John)
        re.compile(r'^([A-Z][A-Z\s]+):\s*'),  # JOHN:
        re.compile(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?):\s*'),  # John:
    ]
    
    # Music/sound markers
    MUSIC_MARKERS = ['♪', '♫', '🎵', '🎶', '[music]', '[singing]', '(music)', '(singing)']
    SOUND_EFFECT_PATTERNS = [
        re.compile(r'^\[([^\]]+)\]$'),  # [sighs]
        re.compile(r'^\(([^\)]+)\)$'),  # (sighs)
        re.compile(r'^\*([^\*]+)\*$'),  # *sighs*
    ]
    
    # Censorship/bleep patterns
    BLEEP_PATTERN = re.compile(r'\[(?:bleep|beep|censored)\]', re.IGNORECASE)
    
    # Line break markers
    LINE_BREAK_MARKERS = ['<br>', '<br/>', '<br />', '\n', '\\n', '|']
    
    # Unicode normalization
    UNICODE_REPLACEMENTS = {
        '\u2019': "'",  # Right single quote
        '\u2018': "'",  # Left single quote
        '\u201c': '"',  # Left double quote
        '\u201d': '"',  # Right double quote
        '\u2014': '-',  # Em dash
        '\u2013': '-',  # En dash
        '\u2026': '...',  # Ellipsis
        '\u00a0': ' ',  # Non-breaking space
    }
    
    def __init__(
        self,
        preserve_speaker: bool = True,
        preserve_music_markers: bool = True,
        strip_html: bool = True,
    ):
        """
        Initialize normalizer.
        
        Args:
            preserve_speaker: Keep speaker labels separate
            preserve_music_markers: Keep music/sound indicators
            strip_html: Remove HTML tags
        """
        self.preserve_speaker = preserve_speaker
        self.preserve_music_markers = preserve_music_markers
        self.strip_html = strip_html
    
    def normalize(self, text: str) -> NormalizedText:
        """
        Normalize subtitle text.
        
        Args:
            text: Raw subtitle text
            
        Returns:
            NormalizedText with cleaned text and metadata
        """
        original = text
        
        # Fix Unicode issues
        text = ftfy.fix_text(text)
        
        # Apply Unicode replacements
        for old, new in self.UNICODE_REPLACEMENTS.items():
            text = text.replace(old, new)
        
        # Check for italics before stripping tags
        has_italics = bool(re.search(r'</?i>', text, re.IGNORECASE))
        
        # Strip HTML tags
        if self.strip_html:
            text = self.HTML_TAG_PATTERN.sub('', text)
            text = self.WEBVTT_TAG_PATTERN.sub('', text)
        
        # Normalize line breaks
        for marker in self.LINE_BREAK_MARKERS:
            text = text.replace(marker, ' ')
        
        # Check for music
        is_music = any(marker.lower() in text.lower() for marker in self.MUSIC_MARKERS)
        
        # Check for sound effect (entire cue is a sound description)
        is_sound_effect = False
        text_stripped = text.strip()
        for pattern in self.SOUND_EFFECT_PATTERNS:
            if pattern.match(text_stripped):
                is_sound_effect = True
                break
        
        # Extract speaker label
        speaker = None
        if self.preserve_speaker:
            speaker, text = self._extract_speaker(text)
        
        # Handle bleeps
        text = self.BLEEP_PATTERN.sub('[***]', text)
        
        # Normalize whitespace
        text = ' '.join(text.split())
        text = text.strip()
        
        # Handle music markers
        if is_music and self.preserve_music_markers:
            # Keep one music symbol at start if present
            if not text.startswith('♪'):
                for marker in ['♪', '♫']:
                    if marker in original:
                        text = f'♪ {text}'
                        break
        
        return NormalizedText(
            text=text,
            speaker=speaker,
            is_music=is_music,
            is_sound_effect=is_sound_effect,
            has_italics=has_italics,
            original=original,
        )
    
    def _extract_speaker(self, text: str) -> Tuple[Optional[str], str]:
        """Extract speaker label from text."""
        text = text.strip()
        
        for pattern in self.SPEAKER_PATTERNS:
            match = pattern.match(text)
            if match:
                speaker = match.group(1).strip()
                remaining = text[match.end():].strip()
                return speaker, remaining
        
        return None, text
    
    def normalize_for_cache(self, text: str) -> str:
        """
        Normalize text for cache key generation.
        Creates a consistent key regardless of minor formatting differences.
        """
        # Full normalization
        normalized = self.normalize(text)
        
        # Further simplification for cache key
        cache_text = normalized.text.lower()
        
        # Remove punctuation variations
        cache_text = re.sub(r'[.,!?;:]+', '', cache_text)
        
        # Normalize whitespace
        cache_text = ' '.join(cache_text.split())
        
        return cache_text


class SubtitleLineBreaker:
    """
    Handles breaking subtitles into appropriate lines.
    """
    
    # Preferred break points (in order of preference)
    BREAK_AFTER = ['. ', '? ', '! ', ', ', '; ', ': ', ' - ', '– ', '— ']
    BREAK_BEFORE = ['"', "'", '(', '[']
    
    def __init__(self, max_chars: int = 42, max_lines: int = 2):
        """
        Initialize line breaker.
        
        Args:
            max_chars: Maximum characters per line
            max_lines: Maximum number of lines
        """
        self.max_chars = max_chars
        self.max_lines = max_lines
    
    def break_lines(self, text: str) -> List[str]:
        """
        Break text into subtitle-appropriate lines.
        
        Args:
            text: Text to break
            
        Returns:
            List of lines
        """
        text = text.strip()
        
        # If it fits in one line, return as-is
        if len(text) <= self.max_chars:
            return [text]
        
        # Try to find a good break point
        lines = []
        remaining = text
        
        while remaining and len(lines) < self.max_lines:
            if len(remaining) <= self.max_chars:
                lines.append(remaining)
                break
            
            # Find best break point within max_chars
            break_pos = self._find_break_point(remaining)
            
            if break_pos > 0:
                lines.append(remaining[:break_pos].strip())
                remaining = remaining[break_pos:].strip()
            else:
                # No good break point, force break at max_chars
                lines.append(remaining[:self.max_chars])
                remaining = remaining[self.max_chars:].strip()
        
        # If we still have text, append to last line (over limit)
        if remaining and lines:
            lines[-1] = f"{lines[-1]} {remaining}"
        elif remaining:
            lines.append(remaining)
        
        return lines
    
    def _find_break_point(self, text: str) -> int:
        """Find the best position to break the text."""
        # Don't break too early
        min_break = min(self.max_chars // 3, 15)
        search_end = min(len(text), self.max_chars)
        
        best_pos = -1
        best_score = -1
        
        # Try preferred break points
        for i, break_after in enumerate(self.BREAK_AFTER):
            pos = text.rfind(break_after, min_break, search_end)
            if pos > 0:
                # Higher index = lower priority break point
                score = 100 - i
                # Prefer breaking closer to middle
                middle = self.max_chars // 2
                score += 10 - abs(pos - middle) // 5
                
                if score > best_score:
                    best_score = score
                    best_pos = pos + len(break_after)
        
        # If no good break point, try space
        if best_pos < 0:
            pos = text.rfind(' ', min_break, search_end)
            if pos > 0:
                best_pos = pos + 1
        
        return best_pos
    
    def format_with_constraints(
        self,
        text: str,
        target_chars: Optional[int] = None
    ) -> Tuple[List[str], str]:
        """
        Format text with line constraints.
        
        Returns:
            Tuple of (lines list, single line version)
        """
        if target_chars:
            old_max = self.max_chars
            self.max_chars = target_chars
        
        lines = self.break_lines(text)
        single = ' '.join(lines)
        
        if target_chars:
            self.max_chars = old_max
        
        return lines, single


# Convenience function for quick normalization
def normalize_subtitle(text: str) -> NormalizedText:
    """Quick normalize a subtitle text."""
    normalizer = TextNormalizer()
    return normalizer.normalize(text)
