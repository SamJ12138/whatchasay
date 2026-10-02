"""
Optional cloud translation tier (D4: off by default).

    google - Cloud Translation v2 REST ("basic" NMT, ~100 ms, bn/zh/en direct)
    azure  - Azure Translator v3 (150-300 ms, bn/zh/en direct)

Used only with SUBTITLE_CLOUD__ENABLED=true and a key in the backend's own
config (environment or backend/.env); the extension never holds or sends keys.
Keys travel in request headers (Google: X-Goog-Api-Key; Azure:
Ocp-Apim-Subscription-Key), never in a URL, and every failure is raised as a
CloudMTError whose message names the provider and the HTTP status only: no
URL, no response body, no key. Called synchronously from the router's thread
pool via httpx.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# Provider-specific codes
GOOGLE_CODES = {"zh": "zh-CN"}
AZURE_CODES = {"zh": "zh-Hans"}

# Who receives the text when a provider is used (startup log line, /health/json, options page)
PROVIDERS: Dict[str, Dict[str, str]] = {
    "google": {"provider": "Google Cloud Translation", "host": "translation.googleapis.com"},
    "azure": {"provider": "Azure Translator", "host": "api.cognitive.microsofttranslator.com"},
}

GOOGLE_URL = "https://translation.googleapis.com/language/translate/v2"
AZURE_URL = "https://api.cognitive.microsofttranslator.com/translate"


class CloudMTError(RuntimeError):
    """A cloud translation failed. The message never contains the key, the URL or the body."""


def _post(client: httpx.Client, provider: str, url: str, **kw) -> httpx.Response:
    try:
        resp = client.post(url, **kw)
    except httpx.TimeoutException:
        raise CloudMTError(f"{provider}: timed out") from None
    except httpx.HTTPError as e:
        raise CloudMTError(f"{provider}: {type(e).__name__}") from None
    if resp.status_code >= 400:
        raise CloudMTError(f"{provider}: HTTP {resp.status_code}")
    return resp


def _json(resp: httpx.Response, provider: str):
    try:
        return resp.json()
    except ValueError:
        raise CloudMTError(f"{provider}: reply is not JSON") from None


class GoogleTranslateEngine:
    name = "cloud"
    provider = "google"
    max_concurrency = 4

    def __init__(self, api_key: str, transport: Optional[httpx.BaseTransport] = None):
        self.api_key = api_key
        self.client = httpx.Client(timeout=5.0, transport=transport, headers={"X-Goog-Api-Key": api_key})

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return bool(self.api_key)

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        resp = _post(self.client, "google", GOOGLE_URL, json={
            "q": texts,
            "source": GOOGLE_CODES.get(source_lang, source_lang),
            "target": GOOGLE_CODES.get(target_lang, target_lang),
            "format": "text",
        })
        try:
            return [d["translatedText"] for d in _json(resp, "google")["data"]["translations"]]
        except (KeyError, TypeError):
            raise CloudMTError("google: unexpected reply shape") from None

    def status(self) -> dict:
        return {"engine": "google-translate", "available": bool(self.api_key)}


class AzureTranslatorEngine:
    name = "cloud"
    provider = "azure"
    max_concurrency = 4

    def __init__(self, key: str, region: str, transport: Optional[httpx.BaseTransport] = None):
        self.key = key
        self.region = region
        headers = {"Ocp-Apim-Subscription-Key": key}
        if region:
            headers["Ocp-Apim-Subscription-Region"] = region
        self.client = httpx.Client(timeout=5.0, transport=transport, headers=headers)

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return bool(self.key)

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        resp = _post(self.client, "azure", AZURE_URL, params={
            "api-version": "3.0",
            "from": AZURE_CODES.get(source_lang, source_lang),
            "to": AZURE_CODES.get(target_lang, target_lang),
        }, json=[{"text": t} for t in texts])
        try:
            return [item["translations"][0]["text"] for item in _json(resp, "azure")]
        except (KeyError, TypeError, IndexError):
            raise CloudMTError("azure: unexpected reply shape") from None

    def status(self) -> dict:
        return {"engine": "azure-translator", "available": bool(self.key)}


def configured_provider() -> Optional[str]:
    """"google" / "azure" when cloud MT is enabled and has a key; else None."""
    c = settings.cloud
    if not c.enabled:
        return None
    if c.mt_provider == "google" and c.google_api_key:
        return "google"
    if c.mt_provider == "azure" and c.azure_translator_key:
        return "azure"
    if c.google_api_key:
        return "google"
    if c.azure_translator_key:
        return "azure"
    return None


def make_cloud_engine() -> Optional[object]:
    """The configured cloud MT engine, or None: cloud off (the default) or no key."""
    c = settings.cloud
    provider = configured_provider()
    if provider == "google":
        return GoogleTranslateEngine(c.google_api_key)
    if provider == "azure":
        return AzureTranslatorEngine(c.azure_translator_key, c.azure_translator_region)
    return None


REFINER_PROVIDERS: Dict[str, Dict[str, str]] = {
    "groq": {"provider": "Groq", "host": "api.groq.com"},
    "gemini": {"provider": "Google Gemini", "host": "generativelanguage.googleapis.com"},
}


def cloud_receivers() -> List[Dict[str, str]]:
    """Every provider that will receive subtitle text with the current config (empty
    = nothing leaves the machine): the startup log line, /health/json and Options."""
    out: List[Dict[str, str]] = []
    if not settings.cloud.enabled:
        return out
    provider = configured_provider()
    if provider:
        out.append({"use": "translation", **PROVIDERS[provider]})
    r = settings.refiner
    if r.provider in REFINER_PROVIDERS and getattr(r, f"{r.provider}_api_key", ""):
        # any session may ask for refinement (W7), so a configured cloud refiner counts
        out.append({"use": "refinement", **REFINER_PROVIDERS[r.provider]})
    return out
