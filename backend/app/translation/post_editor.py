"""
Post-editor using Ollama for natural subtitle refinement.

Takes base MT output and rewrites it to be:
- Natural sounding (like human subtitles)
- Properly formatted (line breaks, length constraints)
- Consistent (preserves names, numbers, tone)

Now supports arbitrary target languages (not just EN/ZH).
"""

import json
import logging
import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import httpx
import ollama
from ollama import AsyncClient

from ..config import settings, get_max_chars_for_language, get_language_display_name
from .text_normalizer import SubtitleLineBreaker

logger = logging.getLogger(__name__)


@dataclass
class PostEditResult:
    """Result from post-editing."""

    translations: Dict[str, Dict[str, Any]]  # {lang: {lines, single_line}}
    notes: Dict[str, bool]
    success: bool
    error: Optional[str] = None


class PostEditor:
    """
    Ollama-based post-editor for subtitle refinement.

    Rewrites machine translation output to be natural, formatted,
    and consistent with subtitle conventions.

    Now supports arbitrary target languages dynamically.
    """

    # Base system prompt template - languages are inserted dynamically
    SYSTEM_PROMPT_TEMPLATE = """You are a professional subtitle translator and editor. Your job is to refine machine-translated subtitles to sound natural while preserving exact meaning.

CRITICAL RULES:
1. NEVER change the meaning, even slightly
2. NEVER add information not in the original
3. NEVER remove important information
4. Preserve ALL names, numbers, dates, and proper nouns exactly
5. Preserve the emotional tone and register (formal/informal)
6. Keep translations concise - subtitles must be readable quickly
7. Format with appropriate line breaks for readability

OUTPUT FORMAT: You must respond ONLY with valid JSON, no markdown, no explanation:
{output_schema}

{line_limits}

SUBTITLE CONVENTIONS:
- Use natural spoken language, not literal translations
- Contractions are preferred in English (don't, won't, can't)
- Keep sentences short and punchy
- Sound effects stay as [brackets]: [sighs], [music]
- Speaker labels format: "SPEAKER: dialogue"
- Numbers 1-10 written as words, larger as digits
"""

    USER_PROMPT_TEMPLATE = """Refine these subtitle translations:

ORIGINAL ({source_lang}): {source_text}
{base_translations}

{context_section}

Constraints:
{constraints}

Return ONLY the JSON with refined translations."""

    CONTEXT_TEMPLATE = """Previous subtitles for context:
{prev_cues}

Ensure consistency with these previous lines (same names, terms, tone)."""

    def __init__(self):
        """Initialize post-editor."""
        self.config = settings.ollama
        self.client = AsyncClient(host=self.config.base_url)
        self._model_available: Optional[bool] = None

        # Line breakers are created dynamically per language
        self._line_breakers: Dict[str, SubtitleLineBreaker] = {}

    def _get_line_breaker(self, lang: str) -> SubtitleLineBreaker:
        """Get or create a line breaker for a language."""
        if lang not in self._line_breakers:
            max_chars = get_max_chars_for_language(lang)
            self._line_breakers[lang] = SubtitleLineBreaker(
                max_chars=max_chars,
                max_lines=settings.translation.max_lines
            )
        return self._line_breakers[lang]

    async def check_model_available(self) -> bool:
        """Check if the configured Ollama model is available."""
        if self._model_available is not None:
            return self._model_available

        try:
            models = await self.client.list()
            available_models = [m['name'] for m in models.get('models', [])]

            # Check for primary model
            if any(self.config.model in m for m in available_models):
                self._model_available = True
                return True

            # Check for fallback
            if any(self.config.fallback_model in m for m in available_models):
                logger.warning(
                    f"Primary model {self.config.model} not found, "
                    f"using fallback {self.config.fallback_model}"
                )
                self._model_available = True
                return True

            logger.error(f"Neither {self.config.model} nor {self.config.fallback_model} available")
            self._model_available = False
            return False

        except Exception as e:
            logger.error(f"Failed to check Ollama models: {e}")
            self._model_available = False
            return False

    async def post_edit(
        self,
        source_text: str,
        source_lang: str,
        base_translations: Dict[str, str],
        target_languages: List[str],
        prev_cues: Optional[List[str]] = None,
        strict_meaning: bool = True
    ) -> PostEditResult:
        """
        Post-edit translations to make them natural and formatted.

        Args:
            source_text: Original source text
            source_lang: Source language code
            base_translations: Dict of {lang: base_translation_text}
            target_languages: List of target language codes
            prev_cues: Previous cues for context
            strict_meaning: Enforce strict meaning preservation

        Returns:
            PostEditResult with refined translations
        """
        if not await self.check_model_available():
            # Fall back to simple formatting
            return self._fallback_format(base_translations, target_languages)

        # Build prompt
        prompt = self._build_prompt(
            source_text=source_text,
            source_lang=source_lang,
            base_translations=base_translations,
            target_languages=target_languages,
            prev_cues=prev_cues
        )

        system_prompt = self._build_system_prompt(target_languages)

        # Call Ollama
        try:
            response = await self._call_ollama(system_prompt, prompt)
            result = self._parse_response(response, target_languages)

            if result.success:
                return result

            # Retry with stricter JSON instructions
            logger.warning(f"First attempt failed: {result.error}, retrying...")
            response = await self._call_ollama(
                system_prompt,
                prompt + "\n\nIMPORTANT: Return ONLY valid JSON, nothing else."
            )
            result = self._parse_response(response, target_languages)

            if result.success:
                return result

            # Final fallback
            logger.warning("Post-editor failed, using fallback formatting")
            return self._fallback_format(base_translations, target_languages)

        except Exception as e:
            logger.error(f"Post-editor error: {e}")
            return self._fallback_format(base_translations, target_languages)

    def _build_system_prompt(self, target_languages: List[str]) -> str:
        """Build system prompt with dynamic language support."""
        # Build output schema based on target languages
        schema_parts = []
        for lang in target_languages:
            lang_name = get_language_display_name(lang)
            schema_parts.append(f'  "{lang}": {{"lines": ["Line 1", "Line 2"], "single_line": "Complete {lang_name} text"}}')

        output_schema = "{\n" + ",\n".join(schema_parts) + ',\n  "notes": {"changed_meaning": false, "kept_names": true}\n}'

        # Build line limits
        limits = []
        for lang in target_languages:
            lang_name = get_language_display_name(lang)
            max_chars = get_max_chars_for_language(lang)
            limits.append(f"- {lang_name}: max {max_chars} characters per line, max {settings.translation.max_lines} lines")

        line_limits = "Line length limits:\n" + "\n".join(limits)

        return self.SYSTEM_PROMPT_TEMPLATE.format(
            output_schema=output_schema,
            line_limits=line_limits
        )

    def _build_prompt(
        self,
        source_text: str,
        source_lang: str,
        base_translations: Dict[str, str],
        target_languages: List[str],
        prev_cues: Optional[List[str]] = None
    ) -> str:
        """Build the user prompt for post-editing."""
        # Build context section
        context_section = ""
        if prev_cues:
            prev_text = "\n".join(f"- {cue}" for cue in prev_cues[-3:])
            context_section = self.CONTEXT_TEMPLATE.format(prev_cues=prev_text)

        # Get language name
        source_lang_name = get_language_display_name(source_lang)

        # Build base translations section
        trans_lines = []
        for lang in target_languages:
            lang_name = get_language_display_name(lang)
            trans_text = base_translations.get(lang, source_text)
            trans_lines.append(f"BASE {lang_name.upper()}: {trans_text}")

        base_trans_section = "\n".join(trans_lines)

        # Build constraints section
        constraints = []
        for lang in target_languages:
            lang_name = get_language_display_name(lang)
            max_chars = get_max_chars_for_language(lang)
            constraints.append(f"- {lang_name}: max {max_chars} chars/line, max {settings.translation.max_lines} lines")

        constraints_section = "\n".join(constraints)

        return self.USER_PROMPT_TEMPLATE.format(
            source_lang=source_lang_name,
            source_text=source_text,
            base_translations=base_trans_section,
            context_section=context_section,
            constraints=constraints_section
        )

    async def _call_ollama(self, system_prompt: str, user_prompt: str, fast: bool = False) -> str:
        """Call Ollama API."""
        try:
            # Try primary model first
            model = self.config.model

            # Reduce num_predict in fast mode for lower latency
            num_predict = 350 if fast else 600

            response = await self.client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                options={
                    "temperature": 0.0 if fast else self.config.temperature,  # Greedy in fast mode
                    "num_predict": num_predict,
                }
            )

            return response['message']['content']

        except ollama.ResponseError as e:
            if "model" in str(e).lower():
                # Try fallback model
                logger.warning(f"Primary model failed, trying fallback")
                response = await self.client.chat(
                    model=self.config.fallback_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    options={
                        "temperature": 0.0 if fast else self.config.temperature,
                        "num_predict": 350 if fast else 600,
                    }
                )
                return response['message']['content']
            raise

    def _parse_response(self, response: str, target_languages: List[str]) -> PostEditResult:
        """Parse Ollama response into structured result."""
        try:
            # Clean response - remove markdown code blocks if present
            response = response.strip()
            if response.startswith("```"):
                # Remove code block markers
                lines = response.split('\n')
                response = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])

            # Try to find JSON in response
            json_start = response.find('{')
            json_end = response.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                data = json.loads(json_str)

                translations = {}

                # Process each target language
                for lang in target_languages:
                    if lang in data:
                        if isinstance(data[lang], dict):
                            lines = data[lang].get('lines', [])
                            single = data[lang].get('single_line', ' '.join(lines))
                        elif isinstance(data[lang], str):
                            single = data[lang]
                            # Apply line breaking
                            breaker = self._get_line_breaker(lang)
                            lines = breaker.break_lines(single)
                        else:
                            continue

                        translations[lang] = {
                            'lines': lines if lines else [single],
                            'single_line': single
                        }

                # Check if we got at least one translation
                if translations:
                    notes = data.get('notes', {'changed_meaning': False, 'kept_names': True})

                    return PostEditResult(
                        translations=translations,
                        notes=notes,
                        success=True
                    )

            return PostEditResult(
                translations={},
                notes={},
                success=False,
                error="Invalid JSON structure or missing language keys"
            )

        except json.JSONDecodeError as e:
            return PostEditResult(
                translations={},
                notes={},
                success=False,
                error=f"JSON parse error: {e}"
            )

    def _fallback_format(
        self,
        base_translations: Dict[str, str],
        target_languages: List[str]
    ) -> PostEditResult:
        """
        Fallback formatting when Ollama is unavailable or fails.

        Applies basic line breaking to the base translations.
        """
        translations = {}

        for lang in target_languages:
            trans_text = base_translations.get(lang, '')
            if not trans_text:
                continue

            breaker = self._get_line_breaker(lang)
            lines, single = breaker.format_with_constraints(trans_text)

            translations[lang] = {
                'lines': lines,
                'single_line': single
            }

        return PostEditResult(
            translations=translations,
            notes={'changed_meaning': False, 'kept_names': True},
            success=True,
            error=None
        )


    async def post_edit_batch(
        self,
        items: List[Dict[str, Any]],
        target_languages: List[str],
        fast: bool = False
    ) -> List[PostEditResult]:
        """
        Post-edit multiple cues in a single Ollama call.

        This is MUCH faster than calling post_edit() for each cue separately.

        Args:
            items: List of dicts with {source_text, source_lang, base_translations}
            target_languages: Target language codes
            fast: Use fast settings (lower num_predict, temperature=0)

        Returns:
            List of PostEditResult, one per input item
        """
        if not items:
            return []

        if not await self.check_model_available():
            # Fallback formatting for all items
            return [
                self._fallback_format(item['base_translations'], target_languages)
                for item in items
            ]

        # Build a combined prompt for all cues
        combined_prompt = self._build_batch_prompt(items, target_languages)
        system_prompt = self._build_batch_system_prompt(len(items), target_languages)

        try:
            response = await self._call_ollama(system_prompt, combined_prompt, fast)
            results = self._parse_batch_response(response, items, target_languages)
            return results
        except Exception as e:
            logger.error(f"Batch post-edit error: {e}")
            return [
                self._fallback_format(item['base_translations'], target_languages)
                for item in items
            ]

    def _build_batch_system_prompt(self, count: int, target_languages: List[str]) -> str:
        """Build system prompt for batch post-editing."""
        # Build output schema
        schema_parts = []
        for lang in target_languages:
            lang_name = get_language_display_name(lang)
            schema_parts.append(f'"{lang}": "{lang_name} text"')

        item_schema = "{" + ", ".join(schema_parts) + "}"
        output_schema = f'[{item_schema}, {item_schema}, ...]  // Array of {count} items'

        limits = []
        for lang in target_languages:
            lang_name = get_language_display_name(lang)
            max_chars = get_max_chars_for_language(lang)
            limits.append(f"- {lang_name}: max {max_chars} chars/line")

        return f"""You are refining {count} subtitle translations. Return a JSON ARRAY with exactly {count} objects.

OUTPUT: Return ONLY a JSON array, no markdown:
{output_schema}

RULES:
1. NEVER change meaning
2. Keep concise for readability
3. Preserve names/numbers exactly
{chr(10).join(limits)}

Return ONLY the JSON array."""

    def _build_batch_prompt(
        self,
        items: List[Dict[str, Any]],
        target_languages: List[str]
    ) -> str:
        """Build combined prompt for batch post-editing."""
        lines = ["Refine these subtitle translations:\n"]

        for i, item in enumerate(items, 1):
            source_lang = get_language_display_name(item['source_lang'])
            lines.append(f"[{i}] {source_lang}: {item['source_text']}")

            for lang in target_languages:
                lang_name = get_language_display_name(lang)
                trans = item['base_translations'].get(lang, '')
                if trans:
                    lines.append(f"    {lang_name}: {trans}")

            lines.append("")

        lines.append("Return a JSON array with refined translations for each item.")
        return "\n".join(lines)

    def _parse_batch_response(
        self,
        response: str,
        items: List[Dict[str, Any]],
        target_languages: List[str]
    ) -> List[PostEditResult]:
        """Parse batch response into individual results."""
        results = []

        try:
            # Clean and parse response
            response = response.strip()
            if response.startswith("```"):
                lines = response.split('\n')
                response = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])

            json_start = response.find('[')
            json_end = response.rfind(']') + 1

            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])

                if isinstance(data, list):
                    for i, item_result in enumerate(data):
                        if i >= len(items):
                            break

                        translations = {}
                        for lang in target_languages:
                            if lang in item_result:
                                text = item_result[lang]
                                if isinstance(text, str):
                                    breaker = self._get_line_breaker(lang)
                                    lines_list = breaker.break_lines(text)
                                    translations[lang] = {
                                        'lines': lines_list,
                                        'single_line': text
                                    }

                        if translations:
                            results.append(PostEditResult(
                                translations=translations,
                                notes={'changed_meaning': False, 'kept_names': True},
                                success=True
                            ))
                        else:
                            results.append(self._fallback_format(
                                items[i]['base_translations'], target_languages
                            ))

        except Exception as e:
            logger.warning(f"Batch parse error: {e}")

        # Fill in any missing results with fallback
        while len(results) < len(items):
            idx = len(results)
            results.append(self._fallback_format(
                items[idx]['base_translations'], target_languages
            ))

        return results


# Legacy wrapper for backwards compatibility
async def post_edit_legacy(
    source_text: str,
    source_lang: str,
    en_translation: str,
    zh_translation: str,
    prev_cues: Optional[List[str]] = None,
    strict_meaning: bool = True
) -> PostEditResult:
    """
    Legacy post-edit function for EN/ZH only.
    Maintained for backwards compatibility.
    """
    editor = PostEditor()
    return await editor.post_edit(
        source_text=source_text,
        source_lang=source_lang,
        base_translations={'en': en_translation, 'zh': zh_translation},
        target_languages=['en', 'zh'],
        prev_cues=prev_cues,
        strict_meaning=strict_meaning
    )


async def warmup_ollama():
    """
    Warm up Ollama by sending a test request.
    """
    editor = PostEditor()

    if not await editor.check_model_available():
        logger.warning("Ollama model not available for warmup")
        return

    # Send a simple test request to warm up
    try:
        result = await editor.post_edit(
            source_text="Hello",
            source_lang="en",
            base_translations={'en': 'Hello', 'zh': '你好'},
            target_languages=['en', 'zh'],
            prev_cues=None
        )
        logger.info("Ollama warmup complete")
    except Exception as e:
        logger.warning(f"Ollama warmup failed: {e}")
