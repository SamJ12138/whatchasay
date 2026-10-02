"""
Enhanced line breaking for subtitle formatting.

Handles:
- Intelligent line breaks at natural points
- Language-specific character limits
- Reading speed considerations
- CJK and Latin text differences
"""

import re
from typing import List, Tuple, Optional
from dataclasses import dataclass

from ..config import settings, get_max_chars_for_language


@dataclass
class LineBreakResult:
    """Result of line breaking."""
    
    lines: List[str]  # Formatted lines
    single_line: str  # Single line version
    total_chars: int  # Total character count
    exceeds_limit: bool  # Whether any line exceeds limit


class SmartLineBreaker:
    """
    Intelligent line breaker for subtitle text.
    
    Considers:
    - Punctuation and natural pause points
    - Word boundaries (for Latin scripts)
    - Character boundaries (for CJK)
    - Reading speed constraints
    """
    
    # Break point preferences (higher score = better break point)
    BREAK_SCORES = {
        '. ': 100,   # End of sentence
        '? ': 100,
        '! ': 100,
        '。': 100,   # Chinese period
        '？': 100,   # Chinese question
        '！': 100,   # Chinese exclamation
        '। ': 100,   # Bengali/Hindi danda (sentence end)
        '।': 95,
        ', ': 80,    # Clause boundary
        '; ': 80,
        ': ': 75,
        '，': 80,    # Chinese comma
        '、': 75,    # Chinese enumeration
        '；': 80,    # Chinese semicolon
        ' - ': 70,   # Dash
        ' – ': 70,
        ' — ': 70,
        '...': 65,
        '……': 65,   # Chinese ellipsis
        '」': 60,    # Japanese closing quote
        '"': 60,     # Closing quote
        "' ": 55,
        ' ': 50,     # Space (last resort for Latin)
    }
    
    # CJK character ranges
    CJK_RANGES = (
        '\u4e00-\u9fff'   # CJK Unified
        '\u3400-\u4dbf'   # CJK Extension A
        '\u3040-\u309f'   # Hiragana
        '\u30a0-\u30ff'   # Katakana
        '\uac00-\ud7af'   # Hangul
    )
    CJK_PATTERN = re.compile(f'[{CJK_RANGES}]')
    
    # Characters that should not start a line
    NO_LINE_START = set('，。？！、；：」』）】》〉,.?!;:)]\'"')
    
    # Characters that should not end a line
    NO_LINE_END = set('「『（【《〈([')
    
    # Latin-script languages (can break at spaces)
    LATIN_SCRIPT_LANGUAGES = {'en', 'vi', 'es', 'fr', 'de', 'pt', 'it', 'nl', 'pl', 'cs', 'tr', 'id', 'ms', 'tl'}

    def __init__(
        self,
        max_chars: int = 42,
        max_lines: int = 2,
        language: str = 'en'
    ):
        """
        Initialize line breaker.

        Args:
            max_chars: Maximum characters per line
            max_lines: Maximum number of lines
            language: Target language code
        """
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.language = language
        # CJK languages need character-based breaking, not word-based
        self.is_cjk = language in ('zh', 'ja', 'ko')
        # Latin-script languages can break at word boundaries
        self.is_latin = language in self.LATIN_SCRIPT_LANGUAGES
    
    def break_lines(self, text: str) -> LineBreakResult:
        """
        Break text into subtitle-appropriate lines.
        
        Args:
            text: Text to break
            
        Returns:
            LineBreakResult with formatted lines
        """
        text = self._normalize_whitespace(text)
        
        # If it fits in one line, return as-is
        if len(text) <= self.max_chars:
            return LineBreakResult(
                lines=[text],
                single_line=text,
                total_chars=len(text),
                exceeds_limit=False
            )
        
        # Break into lines
        lines = self._break_recursive(text, self.max_lines)
        
        # Check if any line exceeds limit
        exceeds = any(len(line) > self.max_chars for line in lines)
        
        return LineBreakResult(
            lines=lines,
            single_line=' '.join(lines),
            total_chars=sum(len(line) for line in lines),
            exceeds_limit=exceeds
        )
    
    def _break_recursive(self, text: str, remaining_lines: int) -> List[str]:
        """Recursively break text into lines."""
        if remaining_lines <= 1:
            return [text]
        
        if len(text) <= self.max_chars:
            return [text]
        
        # Find best break point. The remainder must still fit in the lines we
        # have left, so never break so early that the tail overflows.
        needed = len(text) - (remaining_lines - 1) * self.max_chars
        break_pos = self._find_best_break(text, min_pos=needed if needed > 0 else None)
        
        if break_pos <= 0:
            # No good break point - force break
            break_pos = self.max_chars
        
        first_line = text[:break_pos].strip()
        remaining = text[break_pos:].strip()
        
        if not remaining:
            return [first_line]
        
        # Recurse for remaining lines
        remaining_broken = self._break_recursive(remaining, remaining_lines - 1)
        
        return [first_line] + remaining_broken
    
    def _find_best_break(self, text: str, min_pos: Optional[int] = None) -> int:
        """Find the best position to break the text."""
        # Search range: not too early, not past max_chars
        min_break = max(self.max_chars // 4, 10)
        max_break = min(len(text), self.max_chars)
        if min_pos is not None and min_pos < max_break:
            min_break = max(min_break, min_pos)
        
        # For CJK, we can break almost anywhere
        if self.is_cjk:
            return self._find_cjk_break(text, min_break, max_break)
        
        # For Latin scripts, find best punctuation/word break
        return self._find_latin_break(text, min_break, max_break)
    
    def _find_cjk_break(self, text: str, min_pos: int, max_pos: int) -> int:
        """Find break point for CJK text."""
        best_pos = -1
        best_score = -1
        
        # Try to break at punctuation first
        for punct, score in self.BREAK_SCORES.items():
            # Look for punctuation in valid range
            pos = text.rfind(punct, min_pos, max_pos)
            if pos > 0:
                # Adjust position to after punctuation
                actual_pos = pos + len(punct)
                
                # Check line end/start rules
                if actual_pos < len(text):
                    next_char = text[actual_pos]
                    if next_char in self.NO_LINE_START:
                        continue
                
                # Score based on position balance
                balance_score = 10 - abs((max_pos // 2) - actual_pos) // 5
                total_score = score + balance_score
                
                if total_score > best_score:
                    best_score = total_score
                    best_pos = actual_pos
        
        # If no punctuation break, break at target position
        if best_pos < 0:
            target = max_pos - 1
            
            # Avoid breaking in the middle of punctuation
            while target > min_pos:
                char = text[target]
                if char in self.NO_LINE_START:
                    target -= 1
                elif target > 0 and text[target - 1] in self.NO_LINE_END:
                    target -= 1
                else:
                    break
            
            best_pos = target
        
        return best_pos
    
    def _find_latin_break(self, text: str, min_pos: int, max_pos: int) -> int:
        """Find break point for Latin script text."""
        best_pos = -1
        best_score = -1
        
        # Try punctuation breaks first
        for punct, score in self.BREAK_SCORES.items():
            pos = text.rfind(punct, min_pos, max_pos)
            if pos > 0:
                actual_pos = pos + len(punct)
                
                # Prefer breaks closer to middle
                balance_score = 10 - abs((max_pos // 2) - actual_pos) // 5
                total_score = score + balance_score
                
                if total_score > best_score:
                    best_score = total_score
                    best_pos = actual_pos
        
        # If no punctuation, find last space
        if best_pos < 0:
            pos = text.rfind(' ', min_pos, max_pos)
            if pos > 0:
                best_pos = pos + 1
        
        return best_pos
    
    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace in text."""
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


def format_subtitle_lines(
    text: str,
    language: str,
    max_chars: Optional[int] = None,
    max_lines: int = 2
) -> Tuple[List[str], str]:
    """
    Format text for subtitle display.
    
    Args:
        text: Text to format
        language: Language code
        max_chars: Max characters per line (default: language-specific)
        max_lines: Max number of lines
        
    Returns:
        Tuple of (lines list, single line version)
    """
    if max_chars is None:
        max_chars = get_max_chars_for_language(language)
    
    breaker = SmartLineBreaker(
        max_chars=max_chars,
        max_lines=max_lines,
        language=language
    )
    
    result = breaker.break_lines(text)
    return result.lines, result.single_line


def estimate_reading_time(text: str, language: str) -> float:
    """
    Estimate reading time for subtitle text.

    Args:
        text: Subtitle text
        language: Language code

    Returns:
        Estimated reading time in seconds
    """
    # Characters per second by language
    cps_rates = {
        'en': 15.0,  # ~180 WPM for English
        'vi': 14.0,  # Vietnamese (Latin script with diacritics)
        'zh': 10.0,  # Slower for Chinese characters
        'bn': 12.0,  # Bengali (combining marks inflate len())
        'ja': 10.0,
        'ko': 10.0,
        'ru': 14.0,
        'es': 14.0,
        'fr': 14.0,
        'de': 13.0,
        'pt': 14.0,
        'it': 14.0,
        'ar': 12.0,
        'hi': 12.0,
    }

    cps = cps_rates.get(language, 14.0)
    return len(text) / cps


def check_reading_speed(
    text: str,
    duration: float,
    language: str
) -> Tuple[bool, float]:
    """
    Check if text can be read within the given duration.
    
    Args:
        text: Subtitle text
        duration: Available time in seconds
        language: Language code
        
    Returns:
        Tuple of (is_readable, actual_cps)
    """
    if duration <= 0:
        return False, float('inf')
    
    actual_cps = len(text) / duration
    
    # Maximum CPS limits
    max_cps = {
        'en': settings.translation.max_reading_speed_en,
        'zh': settings.translation.max_reading_speed_zh,
    }
    
    limit = max_cps.get(language, 20.0)
    return actual_cps <= limit, actual_cps
