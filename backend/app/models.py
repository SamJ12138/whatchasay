"""
Pydantic models for the subtitle translator API.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
import xxhash


class MessageType(str, Enum):
    """WebSocket message types."""
    CUE = "cue"
    BATCH = "batch"
    CORRECTION = "correction"
    CONFIG = "config"
    RESULT = "result"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"
    REVISION = "revision"  # server push: refined translation for an earlier cue


class SubtitleCue(BaseModel):
    """A single subtitle cue from the browser."""
    
    text: str = Field(..., description="Raw subtitle text")
    start_time: float = Field(..., description="Start time in seconds")
    end_time: float = Field(..., description="End time in seconds")
    cue_id: Optional[str] = Field(None, description="Unique cue identifier")
    source_lang: Optional[str] = Field(None, description="Declared source language (skips detection)")
    lang_hint: Optional[str] = Field(None, description="Session language hint used by detection (T12)")
    
    # Context for coherence
    prev_cues: Optional[List[str]] = Field(
        default=None,
        description="Previous cue texts for context"
    )
    
    @field_validator('text')
    @classmethod
    def validate_text(cls, v: str) -> str:
        """Ensure text is not empty after stripping."""
        v = v.strip()
        if not v:
            raise ValueError("Cue text cannot be empty")
        return v
    
    def generate_cue_id(self) -> str:
        """Generate a stable cue ID based on content and timing."""
        if self.cue_id:
            return self.cue_id
        
        # Create hash from text + timing
        content = f"{self.text}|{self.start_time:.2f}|{self.end_time:.2f}"
        return xxhash.xxh64(content.encode()).hexdigest()[:16]
    
    @property
    def duration(self) -> float:
        """Get cue duration in seconds."""
        return self.end_time - self.start_time


TARGET_STATUSES = ("ok", "fallback", "untranslated", "error")


class TranslatedLine(BaseModel):
    """One target language of a result.

    status:
        ok            engine output (or the text itself when target == source)
        fallback      output of a fallback engine after the preferred one failed
        untranslated  nothing translated it; single_line is the source text as a placeholder
        error         translation failed; single_line is "" and error_type says why
    """

    lines: List[str] = Field(..., description="Formatted lines (1-2); empty on error")
    single_line: str = Field(..., description="Single line version; empty on error")
    language: str = Field(..., description="Language code")
    status: str = Field(default="ok", description="ok | fallback | untranslated | error")
    engine: Optional[str] = Field(default=None, description="engine that produced the line")
    error_type: Optional[str] = Field(default=None, description="obs error_type when status is error/untranslated")
    error: Optional[str] = Field(default=None, description="why it is not a translation")
    
    @property
    def display_text(self) -> str:
        """Get display text with line breaks."""
        return "\n".join(self.lines)


class TranslationResult(BaseModel):
    """Complete translation result for a cue."""
    
    cue_id: str = Field(..., description="Original cue ID")
    source_text: str = Field(..., description="Original text")
    source_lang: str = Field(..., description="Detected source language")
    
    translations: Dict[str, TranslatedLine] = Field(
        ...,
        description="Translations keyed by language code"
    )
    
    # Metadata
    from_cache: bool = Field(default=False, description="Whether result was cached")
    post_edited: bool = Field(default=False, description="Whether post-editor was used")
    revision: int = Field(default=1, description="1 = fast MT, 2 = refined")
    processing_time_ms: float = Field(default=0, description="Processing time in ms")
    
    # Quality notes from post-editor
    notes: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Post-editor quality notes"
    )


class TranslationCorrection(BaseModel):
    """User correction for a translation."""
    
    cue_id: str = Field(..., description="Cue ID being corrected")
    source_text: str = Field(..., description="Original source text")
    source_lang: Optional[str] = Field(default=None, description="Source language (detected or declared)")
    original_translation: Dict[str, str] = Field(
        ...,
        description="Original translations {lang: text}"
    )
    corrected_translation: Dict[str, str] = Field(
        ...,
        description="Corrected translations {lang: text}"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Correction timestamp"
    )


class WebSocketMessage(BaseModel):
    """Generic WebSocket message wrapper."""
    
    type: MessageType = Field(..., description="Message type")
    correlation_id: str = Field(..., description="Request correlation ID")
    payload: Any = Field(..., description="Message payload")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Message timestamp"
    )


class CueRequest(BaseModel):
    """Request to translate a single cue."""
    
    cue: SubtitleCue = Field(..., description="Cue to translate")
    target_languages: Optional[List[str]] = Field(
        default=None,
        description="Override target languages"
    )
    skip_post_edit: bool = Field(
        default=False,
        description="Skip post-editor for speed"
    )


class BatchRequest(BaseModel):
    """Request to translate multiple cues (e.g., on seek)."""
    
    cues: List[SubtitleCue] = Field(..., description="Cues to translate")
    target_languages: Optional[List[str]] = Field(
        default=None,
        description="Override target languages"
    )
    skip_post_edit: bool = Field(
        default=False,
        description="Skip post-editor for speed"
    )


class ConfigUpdate(BaseModel):
    """Configuration update from extension."""

    target_languages: Optional[List[str]] = None
    use_post_editor: Optional[bool] = None
    fast_mode: Optional[bool] = None
    strict_meaning_lock: Optional[bool] = None
    max_lines: Optional[int] = None
    max_chars_en: Optional[int] = None
    max_chars_zh: Optional[int] = None
    max_chars_vi: Optional[int] = None  # Vietnamese support
    max_chars_by_lang: Optional[Dict[str, int]] = None  # Generic per-language limits
    refiner_enabled: Optional[bool] = None
    source_lang_hint: Optional[str] = None  # language of this tab's page/track; see settings.lang_detect
    cloud_keys: Optional[Dict[str, str]] = None  # {google_api_key, azure_translator_key, azure_translator_region, gladia_api_key, elevenlabs_api_key, groq_api_key, gemini_api_key}


class HealthResponse(BaseModel):
    """Health check response."""
    
    status: str = Field(default="ok")
    models_loaded: bool = Field(default=False)
    ollama_available: bool = Field(default=False)
    cache_entries: int = Field(default=0)
    uptime_seconds: float = Field(default=0)
    device: str = Field(default="cpu")
    mt_engines: Dict[str, Any] = Field(default_factory=dict)
    asr: Dict[str, Any] = Field(default_factory=dict)
    refiner_enabled: bool = Field(default=False)


class PostEditorOutput(BaseModel):
    """Structured output from the post-editor. Now supports arbitrary languages."""

    # Dynamic translations keyed by language code
    translations: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Translations keyed by language code {lang: {lines, single_line}}"
    )
    notes: Optional[Dict[str, bool]] = Field(
        default=None,
        description="Quality/safety notes"
    )

    # Legacy fields for backwards compatibility
    en: Optional[Dict[str, Any]] = Field(
        default=None,
        description="English translation with lines (legacy)"
    )
    zh: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Chinese translation with lines (legacy)"
    )

    @classmethod
    def from_raw(cls, data: dict, target_languages: Optional[List[str]] = None) -> "PostEditorOutput":
        """Parse from raw dict, handling missing fields."""
        translations = {}

        # Try to extract translations for each known language
        all_langs = target_languages or ['en', 'zh', 'vi', 'ja', 'ko', 'es', 'fr', 'de', 'ru']
        for lang in all_langs:
            if lang in data and data[lang]:
                translations[lang] = data[lang]

        return cls(
            translations=translations,
            en=data.get("en"),
            zh=data.get("zh"),
            notes=data.get("notes", {})
        )


class TranslationMemoryEntry(BaseModel):
    """Entry in the translation memory database."""
    
    source_text: str = Field(..., description="Normalized source text")
    source_lang: str = Field(..., description="Source language code")
    target_lang: str = Field(..., description="Target language code")
    translation: str = Field(..., description="Translated text")
    formatted_lines: List[str] = Field(..., description="Formatted lines")
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    use_count: int = Field(default=1)
    is_user_corrected: bool = Field(default=False)
    
    # Hash for quick lookup
    content_hash: str = Field(default="")
    
    def compute_hash(self) -> str:
        """Compute content hash for deduplication."""
        content = f"{self.source_text}|{self.source_lang}|{self.target_lang}"
        return xxhash.xxh64(content.encode()).hexdigest()
