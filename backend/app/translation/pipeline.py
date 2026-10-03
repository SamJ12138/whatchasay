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
import weakref
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config import settings
from ..models import SubtitleCue, TranslationResult, TranslatedLine
from .text_normalizer import TextNormalizer, NormalizedText
from .language_detection import detect_language
from .base_translator import (
    BaseTranslator, MTItem, get_base_translator, warmup_models,
    STATUS_OK, STATUS_FALLBACK, STATUS_UNTRANSLATED, STATUS_ERROR,
)
from .line_breaker import format_subtitle_lines
from .glossary import ProtectRun, store as glossary_store
from ..cache.memory_cache import get_translation_cache, TranslationCache
from ..cache.translation_memory import get_translation_memory, TranslationMemory
from .. import obs

logger = logging.getLogger(__name__)

# TM quality by origin: a fallback engine's row is replaced by a later primary
# result and never overwrites one (translation_memory.set keeps the better row).
QUALITY_FALLBACK = 0.6
QUALITY_BASE = 0.8
QUALITY_REFINED = 0.9


@dataclass
class MemoryPolicy:
    """Where one session's translations may be remembered (D3).

    cache        in-memory cache to use; None = the process-wide one
    tm_write     store results in the persistent TM
    tm_touch     a TM hit counts as a use (updates use_count / updated_at)
    context      texts join the refiner's context window
    The TM is always read (user corrections and stored caption rows apply)."""

    cache: Optional[TranslationCache] = None
    tm_write: bool = True
    tm_touch: bool = True
    context: bool = True


def caption_policy() -> MemoryPolicy:
    """Page subtitles (/ws) and /translate: kept unless settings.tm.persist_captions is off."""
    return MemoryPolicy(tm_write=settings.tm.persist_captions)


# The running live sessions' own caches (weak: a cache goes when its session does), so a
# user correction can reach them too.
_live_caches: "weakref.WeakSet[TranslationCache]" = weakref.WeakSet()


def audio_session_policy() -> MemoryPolicy:
    """One live-caption (/ws/asr) session: its own cache, gone with the session; the
    persistent TM is not written or touched unless settings.tm.persist_audio_sessions."""
    keep = settings.tm.persist_audio_sessions
    cache = TranslationCache()
    _live_caches.add(cache)
    return MemoryPolicy(cache=cache, tm_write=keep, tm_touch=keep, context=keep)


def clear_memory_caches(*extra: Optional[TranslationCache]) -> int:
    """Empty the process-wide cache, every running live session's cache and `extra`, so
    a user correction wins at once everywhere. Returns how many caches were cleared."""
    caches = {id(c): c for c in (get_translation_cache(), *_live_caches, *extra) if c is not None}
    for c in caches.values():
        c.clear()
    return len(caches)


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
        policy: Optional[MemoryPolicy] = None,
    ) -> TranslationResult:
        results = await self.translate_batch([cue], target_languages, skip_post_edit, policy=policy)
        return results[0]

    async def translate_batch(
        self,
        cues: List[SubtitleCue],
        target_languages: Optional[List[str]] = None,
        skip_post_edit: bool = False,
        policy: Optional[MemoryPolicy] = None,
    ) -> List[TranslationResult]:
        if not cues:
            return []
        policy = policy or caption_policy()
        target_languages = list(target_languages or settings.translation.target_languages)
        start = time.time()
        self.stats.total_requests += len(cues)
        tally = {"cache_hits": 0, "tm_hits": 0, "mt_items": 0, "passthrough": 0}
        failed: Optional[Exception] = None
        async with self._semaphore:
            try:
                results = await self._process_batch(cues, target_languages, tally, policy)
            except Exception as e:
                logger.exception("Pipeline error: %s", e)
                self.stats.errors += len(cues)
                failed = e
                results = [self._create_error_result(c, e, target_languages) for c in cues]
        elapsed = (time.time() - start) * 1000
        per = elapsed / max(len(results), 1)
        for r in results:
            r.processing_time_ms = per
            self.stats.record(per)
        logger.info("Translate: %d cue(s) in %.0f ms (%.0f ms/cue) langs=%s", len(cues), elapsed, per, ",".join(target_languages))
        ctx = dict(
            n_cues=len(cues), targets=target_languages,
            source_langs=sorted({r.source_lang for r in results}),
            cue_ids=[r.cue_id for r in results][:20],
            text_len=sum(len(c.text or "") for c in cues),
            **tally,
        )
        if failed is not None:
            obs.log_exc("translate", failed, api="process", duration_ms=elapsed,
                        degraded="every target returned as an error (no text, nothing stored)", **ctx)
        else:
            obs.log("translate", "success", duration_ms=elapsed, **ctx)
        return results

    # -------------------------------------------------------------- internals

    async def _process_batch(self, cues: List[SubtitleCue], target_languages: List[str],
                             tally: Optional[Dict[str, int]] = None,
                             policy: Optional[MemoryPolicy] = None) -> List[TranslationResult]:
        """Per target the result carries a status (models.TranslatedLine):
        ok / fallback are stored in the TM (fallback at a lower quality) when the
        session's policy allows it; untranslated / error are never stored or
        cached and never pass the source text off as a translation."""
        results: List[Optional[TranslationResult]] = [None] * len(cues)
        cache_key = tuple(sorted(target_languages))
        tally = tally if tally is not None else {}
        policy = policy or caption_policy()
        memory = policy.cache if policy.cache is not None else self._memory_cache
        stores: List[Any] = []

        # 1. normalize + language + memory cache
        work: List[Dict[str, Any]] = []
        for i, cue in enumerate(cues):
            normalized = self.normalizer.normalize(cue.text)
            if not normalized.should_translate:
                results[i] = self._create_passthrough_result(cue.generate_cue_id(), cue.text, normalized, target_languages)
                tally["passthrough"] = tally.get("passthrough", 0) + 1
                obs.log("normalize", "skip", error_message="not translated (music / sound effect / empty)",
                        is_music=normalized.is_music, is_sound_effect=normalized.is_sound_effect, text_len=len(cue.text or ""))
                continue
            text = normalized.text
            if cue.source_lang:
                source_lang = cue.source_lang
            else:
                with obs.span("lang_detect", text_len=len(text), hint=cue.lang_hint) as sp:
                    source_lang, conf = detect_language(text, cue.lang_hint)
                    sp.set(lang=source_lang, confidence=round(float(conf), 2))
            cached = memory.get(text, source_lang, cache_key)
            if cached:
                self.stats.cache_hits += 1
                tally["cache_hits"] = tally.get("cache_hits", 0) + 1
                results[i] = self._create_result(cue.generate_cue_id(), text, source_lang, cached, from_cache=True)
                continue
            work.append({"i": i, "cue": cue, "text": text, "lang": source_lang, "trans": {}, "tm_fallback": {}})

        if not work:
            return [r for r in results if r is not None]

        # 2. translation memory (persistent) - per item, per target
        for w in work:
            for tgt in target_languages:
                if tgt == w["lang"]:
                    continue
                with obs.span("tm_lookup", api="process", src=w["lang"], tgt=tgt, text_len=len(w["text"])) as sp:
                    tm = await self._translation_memory.get(w["text"], w["lang"], tgt, touch=policy.tm_touch)
                    sp.set(hit=bool(tm))
                    if tm:
                        sp.set(user_corrected=tm.get("is_user_corrected"), quality=tm.get("quality_score"),
                               engine=tm.get("engine"), equals_source=tm["translation"].strip() == w["text"].strip())
                if not tm:
                    continue
                corrected = bool(tm.get("is_user_corrected"))
                if not corrected and tm["translation"].strip() == w["text"].strip():
                    continue  # T6 legacy row: the source stored as its own translation; never served
                entry = {"translation": tm["translation"], "lines": tm.get("lines") or tm.get("formatted_lines") or [],
                         "from_tm": True, "status": STATUS_OK, "engine": tm.get("engine") or ("user" if corrected else "tm")}
                if not corrected and (tm.get("quality_score") or 0.0) < QUALITY_BASE:
                    entry["status"] = STATUS_FALLBACK
                    primary = self.base_translator.pick(w["lang"], tgt)
                    if primary is not None and primary.name != tm.get("engine"):
                        # a fallback row while the primary engine is available: ask it again and
                        # keep this row as the best available answer if it fails
                        w["tm_fallback"][tgt] = entry
                        continue
                self.stats.tm_hits += 1
                tally["tm_hits"] = tally.get("tm_hits", 0) + 1
                w["trans"][tgt] = entry

        # 3. group remaining by (src, tgt) and batch-translate
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for w in work:
            for tgt in target_languages:
                if tgt != w["lang"] and tgt not in w["trans"]:
                    groups.setdefault((w["lang"], tgt), []).append(w)

        async def _run_group(src: str, tgt: str, items: List[Dict[str, Any]]):
            texts = [it["text"] for it in items]
            self.stats.translations += len(texts)
            tally["mt_items"] = tally.get("mt_items", 0) + len(texts)
            try:
                outs = await self._translate_with_glossary(texts, src, tgt)
            except Exception as e:
                logger.error("MT failed %s->%s: %s", src, tgt, e)
                obs.log_exc("translate", e, src=src, tgt=tgt, n=len(texts), phase="mt_group",
                            degraded="returned as an error (no text, nothing stored)")
                et = obs.classify(e, "process")
                outs = [MTItem("", STATUS_ERROR, None, et, str(e) or type(e).__name__) for _ in texts]
            for it, out in zip(items, outs):
                if out.status in (STATUS_ERROR, STATUS_UNTRANSLATED) and tgt in it["tm_fallback"]:
                    it["trans"][tgt] = it["tm_fallback"][tgt]  # best available: the stored fallback row
                    self.stats.tm_hits += 1
                    tally["tm_hits"] = tally.get("tm_hits", 0) + 1
                    continue
                it["trans"][tgt] = {"translation": out.text, "lines": None, "from_tm": False, "status": out.status,
                                    "engine": out.engine, "error_type": out.error_type, "error": out.error}

        await asyncio.gather(*(_run_group(s, t, items) for (s, t), items in groups.items()))

        # 4. format, cache (only when every target is ok), store (ok / fallback only)
        for w in work:
            i, cue, text, src = w["i"], w["cue"], w["text"], w["lang"]
            translations: Dict[str, Dict[str, Any]] = {}
            all_ok = True
            for tgt in target_languages:
                if tgt == src:
                    lines, single = format_subtitle_lines(text, tgt)
                    translations[tgt] = {"lines": lines, "single_line": single, "translation": text,
                                         "status": STATUS_OK, "engine": "identity"}
                    continue
                entry = w["trans"].get(tgt) or {"translation": "", "status": STATUS_ERROR, "engine": None,
                                                "error_type": "unknown", "error": "no translation produced"}
                status = entry["status"]
                if status == STATUS_ERROR:
                    lines, single = [], ""
                elif entry.get("lines"):
                    lines, single = entry["lines"], entry["translation"]
                else:
                    lines, single = format_subtitle_lines(entry["translation"], tgt)
                translations[tgt] = {"lines": lines, "single_line": single, "translation": single, "status": status,
                                     "engine": entry.get("engine"), "error_type": entry.get("error_type"),
                                     "error": entry.get("error")}
                all_ok = all_ok and status == STATUS_OK
                if status in (STATUS_OK, STATUS_FALLBACK) and not entry.get("from_tm") and policy.tm_write:
                    quality = QUALITY_BASE if status == STATUS_OK else QUALITY_FALLBACK
                    stores.append(obs.traced("tm_store", self._translation_memory.set(
                        source_text=text, source_lang=src, target_lang=tgt,
                        translation=single, formatted_lines=lines, quality_score=quality, engine=entry.get("engine"),
                    ), api="process", kind="machine" if status == STATUS_OK else "fallback", src=src, tgt=tgt,
                        quality=quality, engine=entry.get("engine"), equals_source=False))
            if all_ok:
                memory.set(text, src, cache_key, translations)
            results[i] = self._create_result(cue.generate_cue_id(), text, src, translations, from_cache=False)
            if policy.context:
                self._context_window.append(text)
                if len(self._context_window) > settings.ollama.context_window_size:
                    self._context_window.pop(0)

        await self._await_stores(stores)
        return [r for r in results if r is not None]

    async def _translate_with_glossary(self, texts: List[str], src: str, tgt: str) -> List[MTItem]:
        """The engine call of one (src, tgt) group, with the glossary's terms protected
        through it (translation/glossary.py): a placeholder per term, checked on the way
        back, the next placeholder of the direction when one was lost. Texts without a
        term go through in one call as before."""
        glossary = glossary_store.for_lang(src)
        if glossary is None:
            return await self.base_translator.translate_batch_detailed(texts, src, tgt)
        run = ProtectRun(texts, src, tgt, glossary, settings.translation.glossary_max_attempts)
        last: Dict[int, MTItem] = {}
        while (batch := run.next_batch()) is not None:
            who = list(run.who)
            items = await self.base_translator.translate_batch_detailed(batch, src, tgt)
            for i, it in zip(who, items):
                last[i] = it
            run.accept([it.text if it.status in (STATUS_OK, STATUS_FALLBACK) else None for it in items])
        out: List[MTItem] = []
        for i, text in enumerate(run.outs):
            it = last.get(i) or MTItem("", STATUS_ERROR, None, "unknown", "no translation produced")
            out.append(MTItem(text, it.status, it.engine, it.error_type, it.error) if text is not None else it)
        return out

    async def _await_stores(self, stores: List[Any]) -> None:
        """TM writes are awaited before the result is returned (T7): a failure is
        logged by obs.traced (stage tm_store, event fail, error_type) and here;
        it never fails the translation and never becomes an unretrieved task."""
        if not stores:
            return
        outcomes = await asyncio.gather(*stores, return_exceptions=True)
        failed = [o for o in outcomes if isinstance(o, BaseException)]
        if failed:
            logger.warning("TM store failed for %d of %d write(s): %s", len(failed), len(outcomes), failed[0])

    # ---------------------------------------------------------------- refiner

    def refiner_enabled(self, target_languages: List[str], enabled: Optional[bool] = None) -> bool:
        """`enabled` is a session's own preference (W7); None = the server default."""
        on = (settings.refiner.enabled or settings.features.use_post_editor) if enabled is None else enabled
        if not on:
            return False
        return any(t not in settings.refiner.skip_languages for t in target_languages)

    async def refine_batch(
        self,
        results: List[TranslationResult],
        target_languages: List[str],
        deadline_s: Optional[float] = None,
        enabled: Optional[bool] = None,
        policy: Optional[MemoryPolicy] = None,
    ) -> List[TranslationResult]:
        """
        Improve already-delivered fast translations with an LLM, under a hard
        deadline. Returns only the results that changed, as revision 2.
        `enabled`: the requesting session's preference (None = server default).
        `policy`: where the refined text may be remembered (D3).
        """
        if not results or not self.refiner_enabled(target_languages, enabled):
            return []
        policy = policy or caption_policy()
        memory = policy.cache if policy.cache is not None else self._memory_cache
        from .refiner import get_refiner

        refiner = get_refiner()
        if refiner is None:
            obs.log("refine", "skip", error_type="input_invalid", provider=settings.refiner.provider,
                    error_message="refiner enabled but no provider could be built (missing key?)")
            return []
        langs = [t for t in target_languages if t not in settings.refiner.skip_languages]
        pairs = []
        for r in results:
            if r.notes and r.notes.get("error"):
                continue
            base = {l: r.translations[l].single_line for l in langs
                    if l in r.translations and l != r.source_lang
                    and r.translations[l].status in (STATUS_OK, STATUS_FALLBACK)}
            if base:
                pairs.append((r, {"source_text": r.source_text, "source_lang": r.source_lang, "base_translations": base}))
        items = [it for _, it in pairs]
        if not items:
            return []
        r0 = time.time()
        rctx = dict(provider=settings.refiner.provider, n=len(items), langs=langs)
        try:
            refined = await asyncio.wait_for(
                refiner.refine(items, langs, prev_cues=self._context_window[-3:]),
                timeout=deadline_s or settings.refiner.deadline_s,
            )
        except asyncio.TimeoutError:
            logger.info("Refiner missed the %.1fs deadline; keeping fast output", deadline_s or settings.refiner.deadline_s)
            obs.log("refine", "fail", duration_ms=(time.time() - r0) * 1000, error_type="timeout",
                    error_message=f"missed the {deadline_s or settings.refiner.deadline_s}s deadline", degraded="fast output kept", **rctx)
            return []
        except Exception as e:
            logger.warning("Refiner failed: %s", e)
            obs.log_exc("refine", e, duration_ms=(time.time() - r0) * 1000, degraded="fast output kept", **rctx)
            return []

        changed: List[TranslationResult] = []
        cache_key = tuple(sorted(target_languages))
        engine_name = f"refiner:{settings.refiner.provider}"
        stores: List[Any] = []
        for (r, item), new_texts in zip(pairs, refined):
            if not new_texts:
                continue
            updated = False
            translations = {l: {"lines": tl.lines, "single_line": tl.single_line, "translation": tl.single_line,
                                "status": tl.status, "engine": tl.engine, "error_type": tl.error_type, "error": tl.error}
                            for l, tl in r.translations.items()}
            for lang, text in new_texts.items():
                if not text or lang not in item["base_translations"] or text.strip() == translations[lang]["single_line"].strip():
                    continue
                lines, single = format_subtitle_lines(text, lang)
                translations[lang] = {"lines": lines, "single_line": single, "translation": single,
                                      "status": STATUS_OK, "engine": engine_name}
                updated = True
                if not policy.tm_write:
                    continue
                stores.append(obs.traced("tm_store", self._translation_memory.set(
                    source_text=r.source_text, source_lang=r.source_lang, target_lang=lang,
                    translation=single, formatted_lines=lines, quality_score=QUALITY_REFINED, engine=engine_name,
                ), api="process", kind="refined", src=r.source_lang, tgt=lang, quality=QUALITY_REFINED))
            if updated and all(t.get("status") == STATUS_OK for t in translations.values()):
                memory.set(r.source_text, r.source_lang, cache_key, translations)
                new_r = self._create_result(r.cue_id, r.source_text, r.source_lang, translations, from_cache=False, post_edited=True)
                new_r.revision = 2
                changed.append(new_r)
        await self._await_stores(stores)
        self.stats.refinements += len(changed)
        obs.log("refine", "success", duration_ms=(time.time() - r0) * 1000, changed=len(changed), **rctx)
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
            if not data:
                continue
            status = data.get("status") or STATUS_OK
            single = data.get("single_line")
            if single is None:
                single = data.get("translation", "")
            lines = list(data.get("lines") or []) if status == STATUS_ERROR else (data.get("lines") or [single])
            trans_lines[lang] = TranslatedLine(
                lines=lines, single_line=single, language=lang, status=status, engine=data.get("engine"),
                error_type=data.get("error_type"), error=data.get("error"),
            )
        return TranslationResult(
            cue_id=cue_id, source_text=source_text, source_lang=source_lang,
            translations=trans_lines, from_cache=from_cache, post_edited=post_edited,
        )

    def _create_passthrough_result(self, cue_id: str, text: str, normalized: NormalizedText, target_languages: List[str]) -> TranslationResult:
        reason = "not translatable (music / sound effect)"
        trans_lines = {lang: TranslatedLine(lines=[text], single_line=text, language=lang, status=STATUS_UNTRANSLATED,
                                            error=reason) for lang in target_languages}
        return TranslationResult(
            cue_id=cue_id, source_text=text, source_lang="unknown", translations=trans_lines,
            from_cache=False, post_edited=False,
            notes={"is_music": normalized.is_music, "is_sound_effect": normalized.is_sound_effect},
        )

    def _create_error_result(self, cue: SubtitleCue, error: BaseException, target_languages: List[str]) -> TranslationResult:
        """Whole-batch failure (T5): every target is an error with no text."""
        error_type = obs.classify(error, "process")
        message = str(error) or type(error).__name__
        trans_lines = {lang: TranslatedLine(lines=[], single_line="", language=lang, status=STATUS_ERROR,
                                            error_type=error_type, error=message) for lang in target_languages}
        return TranslationResult(
            cue_id=cue.generate_cue_id(), source_text=cue.text, source_lang=cue.source_lang or "unknown",
            translations=trans_lines, from_cache=False, post_edited=False,
            notes={"error": message, "error_type": error_type},
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
