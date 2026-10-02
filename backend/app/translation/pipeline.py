"""
Translation pipeline.

Hot path (must stay well under ~150 ms per cue):
    normalize -> language (declared or detected) -> memory cache -> TM ->
    fast MT (HY-MT / OPUS-CT2 / cloud) -> line breaking -> cache -> result

There is deliberately NO synchronous LLM post-editing any more. If a refiner
is enabled, `refine_batch()` runs *after* the fast result has been delivered
and produces a `revision=2` result the websocket layer pushes separately.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config import settings
from ..models import SubtitleCue, TranslationResult, TranslatedLine
from .text_normalizer import TextNormalizer, NormalizedText
from .language_detection import detect_language
from .base_translator import BaseTranslator, get_base_translator, warmup_models
from .line_breaker import format_subtitle_lines
from ..cache.memory_cache import get_translation_cache, TranslationCache
from ..cache.translation_memory import get_translation_memory, TranslationMemory

logger = logging.getLogger(__name__)

QUALITY_BASE = 0.8
QUALITY_REFINED = 0.9


@dataclass
class PipelineStats:
    total_requests: int = 0
    cache_hits: int = 0
    tm_hits: int = 0
    translations: int = 0
    post_edits: int = 0
    refinements: int = 0
    errors: int = 0
    total_time_ms: float = 0
    latencies_ms: List[float] = None  # type: ignore

    def __post_init__(self):
        self.latencies_ms = []

    def record(self, ms: float) -> None:
        self.total_time_ms += ms
        self.latencies_ms.append(ms)
        if len(self.latencies_ms) > 2000:
            del self.latencies_ms[:1000]

    @property
    def avg_time_ms(self) -> float:
        return self.total_time_ms / self.total_requests if self.total_requests else 0

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        xs = sorted(self.latencies_ms)
        k = min(len(xs) - 1, max(0, int(round((p / 100) * (len(xs) - 1)))))
        return xs[k]


class TranslationPipeline:
    def __init__(self):
        self.normalizer = TextNormalizer()
        self.base_translator: BaseTranslator = get_base_translator()
        self._memory_cache: Optional[TranslationCache] = None
        self._translation_memory: Optional[TranslationMemory] = None
        self._semaphore = asyncio.Semaphore(settings.server.max_concurrent_translations)
        self.stats = PipelineStats()
        self._context_window: List[str] = []
        self._refiner = None  # lazily created

    async def initialize(self) -> None:
        self._memory_cache = get_translation_cache()
        self._translation_memory = await get_translation_memory()
        logger.info("Translation pipeline initialized (engines: %s)", list(self.base_translator.engines.keys()))

    # ------------------------------------------------------------------ API

    async def translate_cue(
        self,
        cue: SubtitleCue,
        target_languages: Optional[List[str]] = None,
        skip_post_edit: bool = False,
    ) -> TranslationResult:
        results = await self.translate_batch([cue], target_languages, skip_post_edit)
        return results[0]

    async def translate_batch(
        self,
        cues: List[SubtitleCue],
        target_languages: Optional[List[str]] = None,
        skip_post_edit: bool = False,
    ) -> List[TranslationResult]:
        if not cues:
            return []
        target_languages = list(target_languages or settings.translation.target_languages)
        start = time.time()
        self.stats.total_requests += len(cues)
        async with self._semaphore:
            try:
                results = await self._process_batch(cues, target_languages)
            except Exception as e:
                logger.exception("Pipeline error: %s", e)
                self.stats.errors += len(cues)
                results = [self._create_error_result(c, str(e), target_languages) for c in cues]
        elapsed = (time.time() - start) * 1000
        per = elapsed / max(len(results), 1)
        for r in results:
            r.processing_time_ms = per
            self.stats.record(per)
        logger.info("Translate: %d cue(s) in %.0f ms (%.0f ms/cue) langs=%s", len(cues), elapsed, per, ",".join(target_languages))
        return results

    # -------------------------------------------------------------- internals

    async def _process_batch(self, cues: List[SubtitleCue], target_languages: List[str]) -> List[TranslationResult]:
        results: List[Optional[TranslationResult]] = [None] * len(cues)
        cache_key = tuple(sorted(target_languages))

        # 1. normalize + language + memory cache
        work: List[Dict[str, Any]] = []
        for i, cue in enumerate(cues):
            normalized = self.normalizer.normalize(cue.text)
            if not normalized.should_translate:
                results[i] = self._create_passthrough_result(cue.generate_cue_id(), cue.text, normalized, target_languages)
                continue
            text = normalized.text
            source_lang = cue.source_lang or detect_language(text)[0]
            cached = self._memory_cache.get(text, source_lang, cache_key)
            if cached:
                self.stats.cache_hits += 1
                results[i] = self._create_result(cue.generate_cue_id(), text, source_lang, cached, from_cache=True)
                continue
            work.append({"i": i, "cue": cue, "text": text, "lang": source_lang, "trans": {}})

        if not work:
            return [r for r in results if r is not None]

        # 2. translation memory (persistent) - per item, per target
        for w in work:
            for tgt in target_languages:
                if tgt == w["lang"]:
                    continue
                tm = await self._translation_memory.get(w["text"], w["lang"], tgt)
                if tm:
                    self.stats.tm_hits += 1
                    w["trans"][tgt] = {"translation": tm["translation"], "lines": tm.get("lines") or tm.get("formatted_lines") or [], "from_tm": True}

        # 3. group remaining by (src, tgt) and batch-translate
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for w in work:
            for tgt in target_languages:
                if tgt != w["lang"] and tgt not in w["trans"]:
                    groups.setdefault((w["lang"], tgt), []).append(w)

        async def _run_group(src: str, tgt: str, items: List[Dict[str, Any]]):
            texts = [it["text"] for it in items]
            self.stats.translations += len(texts)
            try:
                outs = await self.base_translator.translate_batch(texts, src, tgt, fast=True)
            except Exception as e:
                logger.error("MT failed %s->%s: %s", src, tgt, e)
                outs = texts
            for it, out in zip(items, outs):
                it["trans"][tgt] = {"translation": out, "lines": None, "from_tm": False}

        await asyncio.gather(*(_run_group(s, t, items) for (s, t), items in groups.items()))

        # 4. format, cache, store
        for w in work:
            i, cue, text, src = w["i"], w["cue"], w["text"], w["lang"]
            translations: Dict[str, Dict[str, Any]] = {}
            for tgt in target_languages:
                if tgt == src:
                    lines, single = format_subtitle_lines(text, tgt)
                    translations[tgt] = {"lines": lines, "single_line": single, "translation": text}
                    continue
                entry = w["trans"].get(tgt)
                trans_text = entry["translation"] if entry else text
                if entry and entry.get("lines"):
                    lines, single = entry["lines"], trans_text
                else:
                    lines, single = format_subtitle_lines(trans_text, tgt)
                translations[tgt] = {"lines": lines, "single_line": single, "translation": trans_text}
                if entry and not entry.get("from_tm"):
                    asyncio.create_task(self._translation_memory.set(
                        source_text=text, source_lang=src, target_lang=tgt,
                        translation=trans_text, formatted_lines=lines, quality_score=QUALITY_BASE,
                    ))
            self._memory_cache.set(text, src, cache_key, translations)
            results[i] = self._create_result(cue.generate_cue_id(), text, src, translations, from_cache=False)
            self._context_window.append(text)
            if len(self._context_window) > settings.ollama.context_window_size:
                self._context_window.pop(0)

        return [r for r in results if r is not None]

    # ---------------------------------------------------------------- refiner

    def refiner_enabled(self, target_languages: List[str]) -> bool:
        if not (settings.refiner.enabled or settings.features.use_post_editor):
            return False
        return any(t not in settings.refiner.skip_languages for t in target_languages)

    async def refine_batch(
        self,
        results: List[TranslationResult],
        target_languages: List[str],
        deadline_s: Optional[float] = None,
    ) -> List[TranslationResult]:
        """
        Improve already-delivered fast translations with an LLM, under a hard
        deadline. Returns only the results that changed, as revision 2.
        """
        if not results or not self.refiner_enabled(target_languages):
            return []
        from .refiner import get_refiner

        refiner = get_refiner()
        if refiner is None:
            return []
        langs = [t for t in target_languages if t not in settings.refiner.skip_languages]
        items = [
            {
                "source_text": r.source_text,
                "source_lang": r.source_lang,
                "base_translations": {l: r.translations[l].single_line for l in langs if l in r.translations and l != r.source_lang},
            }
            for r in results
            if not r.notes or not r.notes.get("error")
        ]
        items = [it for it in items if it["base_translations"]]
        if not items:
            return []
        try:
            refined = await asyncio.wait_for(
                refiner.refine(items, langs, prev_cues=self._context_window[-3:]),
                timeout=deadline_s or settings.refiner.deadline_s,
            )
        except asyncio.TimeoutError:
            logger.info("Refiner missed the %.1fs deadline; keeping fast output", deadline_s or settings.refiner.deadline_s)
            return []
        except Exception as e:
            logger.warning("Refiner failed: %s", e)
            return []

        changed: List[TranslationResult] = []
        cache_key = tuple(sorted(target_languages))
        for r, new_texts in zip([r for r in results if not r.notes or not r.notes.get("error")], refined):
            if not new_texts:
                continue
            updated = False
            translations = {l: {"lines": tl.lines, "single_line": tl.single_line, "translation": tl.single_line} for l, tl in r.translations.items()}
            for lang, text in new_texts.items():
                if not text or lang not in translations or text.strip() == translations[lang]["single_line"].strip():
                    continue
                lines, single = format_subtitle_lines(text, lang)
                translations[lang] = {"lines": lines, "single_line": single, "translation": single}
                updated = True
                asyncio.create_task(self._translation_memory.set(
                    source_text=r.source_text, source_lang=r.source_lang, target_lang=lang,
                    translation=single, formatted_lines=lines, quality_score=QUALITY_REFINED,
                ))
            if updated:
                self._memory_cache.set(r.source_text, r.source_lang, cache_key, translations)
                new_r = self._create_result(r.cue_id, r.source_text, r.source_lang, translations, from_cache=False, post_edited=True)
                new_r.revision = 2
                changed.append(new_r)
        self.stats.refinements += len(changed)
        return changed

    # ---------------------------------------------------------------- helpers

    def _create_result(
        self,
        cue_id: str,
        source_text: str,
        source_lang: str,
        translations: Dict[str, Dict],
        from_cache: bool,
        post_edited: bool = False,
    ) -> TranslationResult:
        trans_lines = {}
        for lang, data in translations.items():
            if data:
                single = data.get("single_line") or data.get("translation", "")
                trans_lines[lang] = TranslatedLine(lines=data.get("lines") or [single], single_line=single, language=lang)
        return TranslationResult(
            cue_id=cue_id, source_text=source_text, source_lang=source_lang,
            translations=trans_lines, from_cache=from_cache, post_edited=post_edited,
        )

    def _create_passthrough_result(self, cue_id: str, text: str, normalized: NormalizedText, target_languages: List[str]) -> TranslationResult:
        trans_lines = {lang: TranslatedLine(lines=[text], single_line=text, language=lang) for lang in target_languages}
        return TranslationResult(
            cue_id=cue_id, source_text=text, source_lang="unknown", translations=trans_lines,
            from_cache=False, post_edited=False,
            notes={"is_music": normalized.is_music, "is_sound_effect": normalized.is_sound_effect},
        )

    def _create_error_result(self, cue: SubtitleCue, error: str, target_languages: List[str]) -> TranslationResult:
        trans_lines = {lang: TranslatedLine(lines=[cue.text], single_line=cue.text, language=lang) for lang in target_languages}
        return TranslationResult(
            cue_id=cue.generate_cue_id(), source_text=cue.text, source_lang=cue.source_lang or "unknown",
            translations=trans_lines, from_cache=False, post_edited=False, notes={"error": error},
        )

    def clear_context(self) -> None:
        self._context_window.clear()


_pipeline: Optional[TranslationPipeline] = None


async def get_pipeline() -> TranslationPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = TranslationPipeline()
        await _pipeline.initialize()
    return _pipeline


async def warmup_pipeline():
    logger.info("Starting pipeline warmup...")
    await get_pipeline()
    if settings.features.warmup_on_start:
        await warmup_models()
    logger.info("Pipeline warmup complete")
