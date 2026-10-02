"""
Asynchronous translation refiner (never in the hot path).

Given fast MT output for a few cues, ask an LLM to make it read like a human
subtitle without changing meaning. Called by pipeline.refine_batch() under a
deadline; if it is late the fast output simply stays on screen.

Providers:
    ollama - local model via the existing PostEditor (Qwen3-4B recommended on 8 GB)
    groq   - hosted small models, ~0.2-0.35 s
    gemini - Gemini 2.5 Flash-Lite, ~0.5 s

All providers return, per item, {lang: refined_text}. Empty dict = no change.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings, get_language_display_name, get_max_chars_for_language

logger = logging.getLogger(__name__)


def _build_prompt(items: List[Dict[str, Any]], langs: List[str], prev_cues: Optional[List[str]]) -> str:
    lines = [
        "You polish machine-translated video subtitles. Keep the exact meaning, names and numbers.",
        "Make each line sound like natural spoken subtitle text and keep it short.",
        "Limits: " + ", ".join(f"{get_language_display_name(l)} <= {get_max_chars_for_language(l) * settings.translation.max_lines} chars" for l in langs) + ".",
        f"Return ONLY a JSON array of {len(items)} objects, each with keys {langs}. No markdown.",
    ]
    if prev_cues:
        lines.append("Previous subtitles for context: " + " | ".join(prev_cues[-3:]))
    lines.append("")
    for i, it in enumerate(items, 1):
        lines.append(f"[{i}] {get_language_display_name(it['source_lang'])}: {it['source_text']}")
        for l in langs:
            if l in it["base_translations"]:
                lines.append(f"    {get_language_display_name(l)} draft: {it['base_translations'][l]}")
    return "\n".join(lines)


def _parse(text: str, n: int, langs: List[str]) -> List[Dict[str, str]]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("["), text.rfind("]") + 1
    out: List[Dict[str, str]] = [{} for _ in range(n)]
    if start < 0 or end <= start:
        return out
    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError:
        return out
    if not isinstance(data, list):
        return out
    for i, obj in enumerate(data[:n]):
        if isinstance(obj, dict):
            out[i] = {l: str(obj[l]).strip() for l in langs if isinstance(obj.get(l), str) and obj[l].strip()}
    return out


class OllamaRefiner:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=settings.ollama.base_url, timeout=settings.ollama.timeout)

    async def refine(self, items, langs, prev_cues=None):
        resp = await self.client.post(
            "/api/chat",
            json={
                "model": settings.ollama.model,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 60 * len(items) * len(langs) + 40},
                "messages": [{"role": "user", "content": _build_prompt(items, langs, prev_cues)}],
            },
        )
        resp.raise_for_status()
        return _parse(resp.json()["message"]["content"], len(items), langs)


class GroqRefiner:
    def __init__(self, api_key: str, model: str):
        self.client = httpx.AsyncClient(base_url="https://api.groq.com/openai/v1", timeout=5.0, headers={"Authorization": f"Bearer {api_key}"})
        self.model = model

    async def refine(self, items, langs, prev_cues=None):
        resp = await self.client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "temperature": 0,
                "max_tokens": 60 * len(items) * len(langs) + 40,
                "messages": [{"role": "user", "content": _build_prompt(items, langs, prev_cues)}],
            },
        )
        resp.raise_for_status()
        return _parse(resp.json()["choices"][0]["message"]["content"], len(items), langs)


class GeminiRefiner:
    def __init__(self, api_key: str, model: str):
        self.client = httpx.AsyncClient(timeout=5.0)
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    async def refine(self, items, langs, prev_cues=None):
        resp = await self.client.post(
            self.url,
            json={
                "contents": [{"parts": [{"text": _build_prompt(items, langs, prev_cues)}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 60 * len(items) * len(langs) + 40, "thinkingConfig": {"thinkingBudget": 0}},
            },
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return _parse(text, len(items), langs)


_refiner = None
_refiner_key = None


def get_refiner():
    """Build (or rebuild, if settings changed) the configured refiner."""
    global _refiner, _refiner_key
    r = settings.refiner
    key = (r.provider, r.groq_api_key, r.gemini_api_key, settings.ollama.model)
    if _refiner is not None and _refiner_key == key:
        return _refiner
    _refiner_key = key
    if r.provider == "groq" and r.groq_api_key:
        _refiner = GroqRefiner(r.groq_api_key, r.groq_model)
    elif r.provider == "gemini" and r.gemini_api_key:
        _refiner = GeminiRefiner(r.gemini_api_key, r.gemini_model)
    elif r.provider == "ollama":
        _refiner = OllamaRefiner()
    else:
        _refiner = None
    return _refiner
