import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Run from the backend dir so relative data/ paths resolve like the server does.
os.chdir(ROOT)

MODELS_DIR = ROOT / "data" / "models" / "asr"


def zipformer_dir(lang: str) -> Path:
    from app.asr.sherpa_engine import DEFAULT_MODELS

    return MODELS_DIR / DEFAULT_MODELS[lang].name


def has_zipformer(lang: str) -> bool:
    d = zipformer_dir(lang)
    return (d / "tokens.txt").exists()


def server_up(url: str = "http://127.0.0.1:8765/health/json") -> bool:
    try:
        import httpx

        return httpx.get(url, timeout=1.5).status_code == 200
    except Exception:
        return False


@pytest.fixture
def clear_memory_cache():
    from app.cache.memory_cache import get_translation_cache

    cache = get_translation_cache()
    cache.clear()
    yield cache
    cache.clear()
