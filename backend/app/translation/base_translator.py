"""
Fast neural machine translation for the subtitle hot path.

Two local engines plus an optional cloud engine, all behind one interface:

    OpusCT2Engine   - Helsinki-NLP OPUS-MT (MarianMT) converted to CTranslate2
                      int8 and run on CPU. ~40-120 ms per short line, greedy.
                      Pairs without a direct model pivot through English.
    HyMTEngine      - Tencent HY-MT1.5-1.8B (GGUF, llama.cpp) on the GPU.
                      Covers en/zh/bn directly. See hymt_translator.py.
    CloudMTEngine   - Google Translate v3 / Azure Translator. See cloud_translator.py.

`BaseTranslator` is the router used by pipeline.py. Its public methods keep
the historical signatures (translate / translate_batch) so the pipeline and
the websocket layer did not have to change.

No PyTorch / transformers are imported here; the old HF generation path is gone.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Tuple

from ..config import settings, get_opus_route

logger = logging.getLogger(__name__)

try:
    import ctranslate2  # type: ignore

    CT2_AVAILABLE = True
except ImportError:  # pragma: no cover
    ctranslate2 = None
    CT2_AVAILABLE = False


class MTEngine(Protocol):
    name: str

    def supports(self, source_lang: str, target_lang: str) -> bool: ...

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]: ...

    def status(self) -> dict: ...


# ---------------------------------------------------------------------------
# OPUS-MT on CTranslate2
# ---------------------------------------------------------------------------


class _Ct2Marian:
    """One loaded OPUS-MT model (translator + sentencepiece tokenizers)."""

    def __init__(self, model_dir: Path, device: str, compute_type: str, intra_threads: int):
        import sentencepiece as spm  # local import: optional dep for cloud-only installs

        self.translator = ctranslate2.Translator(
            str(model_dir),
            device=device,
            compute_type=compute_type,
            inter_threads=1,
            intra_threads=intra_threads,
        )
        self.sp_source = spm.SentencePieceProcessor(model_file=str(model_dir / "source.spm"))
        self.sp_target = spm.SentencePieceProcessor(model_file=str(model_dir / "target.spm"))

    def translate(self, texts: List[str], beam_size: int = 2, max_tokens: int = 128) -> List[str]:
        tokens = [self.sp_source.encode(t, out_type=str) for t in texts]
        results = self.translator.translate_batch(
            tokens,
            beam_size=beam_size,
            max_decoding_length=max_tokens,
            repetition_penalty=1.2,
            no_repeat_ngram_size=2,
        )
        out = []
        for r in results:
            hyp = r.hypotheses[0] if r.hypotheses else []
            out.append(self.sp_target.decode(hyp).strip())
        return out


class OpusCT2Engine:
    name = "opus-ct2"

    def __init__(self, ct2_dir: Path, device: str = "cpu", max_models: int = 8):
        self.ct2_dir = Path(ct2_dir)
        # OPUS models are tiny; CPU int8 is fast and leaves the GPU for HY-MT/ASR.
        self.device = "cpu"
        self.compute_type = "int8"
        self.intra_threads = max(1, min(8, (os.cpu_count() or 4) // 2))
        self.max_models = max_models
        self._models: Dict[str, _Ct2Marian] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    # -- model management --------------------------------------------------

    def _convert(self, hf_name: str, out_dir: Path) -> None:
        """Convert a Hugging Face MarianMT checkpoint to CTranslate2 int8."""
        from ctranslate2.converters import TransformersConverter  # needs transformers + torch

        logger.info("Converting %s to CTranslate2 int8 (one-time)...", hf_name)
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        converter = TransformersConverter(hf_name, copy_files=["source.spm", "target.spm"], load_as_float16=False)
        converter.convert(str(out_dir), quantization="int8", force=True)

    def _ensure(self, hf_name: str) -> _Ct2Marian:
        with self._lock:
            if hf_name in self._models:
                self._order.remove(hf_name)
                self._order.append(hf_name)
                return self._models[hf_name]
        out_dir = self.ct2_dir / hf_name.replace("/", "__")
        if not (out_dir / "model.bin").exists():
            self._convert(hf_name, out_dir)
        t0 = time.time()
        model = _Ct2Marian(out_dir, self.device, self.compute_type, self.intra_threads)
        logger.info("Loaded OPUS-MT %s in %.1fs", hf_name, time.time() - t0)
        with self._lock:
            self._models[hf_name] = model
            self._order.append(hf_name)
            while len(self._order) > self.max_models:
                old = self._order.pop(0)
                self._models.pop(old, None)
                logger.info("Evicted OPUS-MT model %s", old)
        return model

    # -- MTEngine ------------------------------------------------------------

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return CT2_AVAILABLE and get_opus_route(source_lang, target_lang) is not None

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        route = get_opus_route(source_lang, target_lang)
        if route is None:
            raise ValueError(f"No OPUS-MT route for {source_lang}->{target_lang}")
        current = list(texts)
        for src, tgt, hf_name in route:
            model = self._ensure(hf_name)
            current = model.translate(current)
        return current

    def warmup(self, pairs: List[Tuple[str, str]]) -> None:
        for src, tgt in pairs:
            route = get_opus_route(src, tgt)
            if not route:
                continue
            for _, _, hf_name in route:
                try:
                    self._ensure(hf_name)
                except Exception as e:
                    logger.warning("OPUS warmup failed for %s: %s", hf_name, e)

    def status(self) -> dict:
        return {"engine": self.name, "available": CT2_AVAILABLE, "loaded": list(self._models.keys()), "device": self.device}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class BaseTranslator:
    """
    Routes each (source, target) pair to the first engine in
    settings.mt.engine_order that supports it. All engine calls run in a
    thread so the event loop stays responsive.
    """

    def __init__(self):
        self.device = settings.translation.device
        self.engines: Dict[str, MTEngine] = {}
        self._build_engines()
        # Per-engine concurrency limits (engines declare max_concurrency; default 1).
        self._engine_locks: Dict[str, threading.Semaphore] = {n: self._make_gate(e) for n, e in self.engines.items()}

    @staticmethod
    def _make_gate(engine) -> threading.Semaphore:
        return threading.Semaphore(max(1, int(getattr(engine, "max_concurrency", 1))))

    def _build_engines(self) -> None:
        order = settings.mt.engine_order
        for name in order:
            try:
                if name == "opus" and CT2_AVAILABLE:
                    self.engines["opus"] = OpusCT2Engine(settings.translation.ct2_dir, self.device)
                elif name == "hymt":
                    from .hymt_translator import HyMTEngine, LLAMA_AVAILABLE

                    if LLAMA_AVAILABLE and self.device == "cuda":
                        self.engines["hymt"] = HyMTEngine()
                    else:
                        logger.info("HY-MT engine skipped (llama-cpp: %s, device: %s)", LLAMA_AVAILABLE, self.device)
                elif name == "cloud":
                    from .cloud_translator import make_cloud_engine

                    eng = make_cloud_engine()
                    if eng is not None:
                        self.engines["cloud"] = eng
            except Exception as e:
                logger.warning("MT engine %s unavailable: %s", name, e)
        logger.info("MT engines: %s", list(self.engines.keys()))

    def add_engine(self, name: str, engine: MTEngine) -> None:
        self.engines[name] = engine
        self._engine_locks[name] = self._make_gate(engine)

    def pick(self, source_lang: str, target_lang: str, prefer: Optional[str] = None) -> Optional[MTEngine]:
        order = list(settings.mt.engine_order)
        if prefer and prefer in self.engines:
            order.remove(prefer) if prefer in order else None
            order.insert(0, prefer)
        for name in order:
            eng = self.engines.get(name)
            if eng is not None and eng.supports(source_lang, target_lang):
                return eng
        return None

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return source_lang == target_lang or self.pick(source_lang, target_lang) is not None

    async def translate(self, text: str, source_lang: str, target_lang: str, fast: bool = True) -> str:
        results = await self.translate_batch([text], source_lang, target_lang, fast)
        return results[0]

    async def translate_batch(
        self,
        texts: List[str],
        source_lang: str,
        target_lang: str,
        fast: bool = True,
        prefer: Optional[str] = None,
    ) -> List[str]:
        if not texts:
            return []
        if source_lang == target_lang:
            return list(texts)
        engine = self.pick(source_lang, target_lang, prefer)
        if engine is None:
            logger.warning("No translation engine for %s->%s", source_lang, target_lang)
            return list(texts)

        lock = self._engine_locks.setdefault(engine.name, self._make_gate(engine))

        def _run():
            with lock:
                t0 = time.time()
                out = engine.translate_batch_sync(list(texts), source_lang, target_lang)
                logger.debug("MT %s %s->%s x%d in %.0f ms", engine.name, source_lang, target_lang, len(texts), (time.time() - t0) * 1000)
                return out

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.error("MT engine %s failed for %s->%s: %s", engine.name, source_lang, target_lang, e)
            # Try the next engine once before giving up
            fallback = None
            for name in settings.mt.engine_order:
                cand = self.engines.get(name)
                if cand is not None and cand is not engine and cand.supports(source_lang, target_lang):
                    fallback = cand
                    break
            if fallback is None:
                return list(texts)
            return await loop.run_in_executor(None, lambda: fallback.translate_batch_sync(list(texts), source_lang, target_lang))

    def status(self) -> dict:
        return {name: eng.status() for name, eng in self.engines.items()}


_translator: Optional[BaseTranslator] = None


def get_base_translator() -> BaseTranslator:
    global _translator
    if _translator is None:
        _translator = BaseTranslator()
    return _translator


async def warmup_models(languages: Optional[List[str]] = None) -> None:
    """Load the models for the default language set so first use is fast."""
    translator = get_base_translator()
    langs = languages or settings.translation.target_languages
    loop = asyncio.get_running_loop()

    hymt = translator.engines.get("hymt")
    if hymt is not None and hasattr(hymt, "warmup"):
        await loop.run_in_executor(None, hymt.warmup)

    opus = translator.engines.get("opus")
    if opus is not None and hasattr(opus, "warmup"):
        pairs = []
        sources = ["en", "zh", "bn"]
        for s in sources:
            for t in langs:
                if s != t and (hymt is None or not hymt.supports(s, t)):
                    pairs.append((s, t))
        await loop.run_in_executor(None, lambda: opus.warmup(pairs))
