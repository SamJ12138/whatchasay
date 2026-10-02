"""
Optional cloud translation tier (user-supplied API key).

    google - Cloud Translation v2 REST ("basic" NMT, ~100 ms, bn/zh/en direct)
    azure  - Azure Translator v3 (150-300 ms, bn/zh/en direct)

Both are called synchronously from the router's thread pool via httpx.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# Provider-specific codes
GOOGLE_CODES = {"zh": "zh-CN"}
AZURE_CODES = {"zh": "zh-Hans"}


class GoogleTranslateEngine:
    name = "cloud"
    max_concurrency = 4

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.Client(timeout=5.0)

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return bool(self.api_key)

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        resp = self.client.post(
            "https://translation.googleapis.com/language/translate/v2",
            params={"key": self.api_key},
            json={
                "q": texts,
                "source": GOOGLE_CODES.get(source_lang, source_lang),
                "target": GOOGLE_CODES.get(target_lang, target_lang),
                "format": "text",
            },
        )
        resp.raise_for_status()
        data = resp.json()["data"]["translations"]
        return [d["translatedText"] for d in data]

    def status(self) -> dict:
        return {"engine": "google-translate", "available": bool(self.api_key)}


class AzureTranslatorEngine:
    name = "cloud"
    max_concurrency = 4

    def __init__(self, key: str, region: str):
        self.key = key
        self.region = region
        self.client = httpx.Client(timeout=5.0)

    def supports(self, source_lang: str, target_lang: str) -> bool:
        return bool(self.key)

    def translate_batch_sync(self, texts: List[str], source_lang: str, target_lang: str) -> List[str]:
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/json"}
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region
        resp = self.client.post(
            "https://api.cognitive.microsofttranslator.com/translate",
            params={
                "api-version": "3.0",
                "from": AZURE_CODES.get(source_lang, source_lang),
                "to": AZURE_CODES.get(target_lang, target_lang),
            },
            headers=headers,
            json=[{"text": t} for t in texts],
        )
        resp.raise_for_status()
        return [item["translations"][0]["text"] for item in resp.json()]

    def status(self) -> dict:
        return {"engine": "azure-translator", "available": bool(self.key)}


def make_cloud_engine() -> Optional[object]:
    c = settings.cloud
    if c.mt_provider == "google" and c.google_api_key:
        return GoogleTranslateEngine(c.google_api_key)
    if c.mt_provider == "azure" and c.azure_translator_key:
        return AzureTranslatorEngine(c.azure_translator_key, c.azure_translator_region)
    if c.google_api_key:
        return GoogleTranslateEngine(c.google_api_key)
    if c.azure_translator_key:
        return AzureTranslatorEngine(c.azure_translator_key, c.azure_translator_region)
    return None
