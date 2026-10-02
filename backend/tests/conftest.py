import os
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Run from the backend dir so relative data/ paths resolve like the server does.
os.chdir(ROOT)
# Keep test-run obs logs apart from real server runs (backend/logs/ is gitignored).
os.environ.setdefault("SUBTITLE_OBS_DIR", str(ROOT / "logs" / "pytest"))
# Never touch backend/data/ (real translation memory, user corrections): every
# writable data path points at a scratch dir for the whole test session.
_SCRATCH = Path(tempfile.mkdtemp(prefix="st-tests-"))
os.environ.setdefault("SUBTITLE_DATA_DIR", str(_SCRATCH))
os.environ.setdefault("SUBTITLE_CORRECTIONS_FILE", str(_SCRATCH / "corrections.jsonl"))
os.environ.setdefault("SUBTITLE_CACHE__TM_DATABASE_PATH", str(_SCRATCH / "translation_memory.db"))

MODELS_DIR = ROOT / "data" / "models" / "asr"


def zipformer_dir(lang: str) -> Path:
    from app.asr.sherpa_engine import DEFAULT_MODELS

    return MODELS_DIR / DEFAULT_MODELS[lang].name


def has_zipformer(lang: str) -> bool:
    d = zipformer_dir(lang)
    return (d / "tokens.txt").exists()


@pytest.fixture
def clear_memory_cache():
    from app.cache.memory_cache import get_translation_cache

    cache = get_translation_cache()
    cache.clear()
    yield cache
    cache.clear()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_mt():
    """Factory for fake MT engines: fake_mt(name, mode='ok'|'fail'|'timeout'|'source'|'empty', pairs=None)."""
    from tests.fakes import FakeMTEngine

    return FakeMTEngine


@pytest.fixture
def fake_asr():
    from tests.fakes import FakeASREngine

    return FakeASREngine()


@pytest_asyncio.fixture
async def scratch_tm(tmp_path):
    from app.cache.translation_memory import TranslationMemory

    tm = TranslationMemory(db_path=tmp_path / "tm.db")
    await tm.initialize()
    yield tm
    await tm.close()


class AppEnv:
    """The real FastAPI app wired to fake engines and a scratch TM.

    env.engines        name -> FakeMTEngine, in router order (default: one "fake" engine)
    env.asr            FakeASREngine behind /ws/asr
    env.client(sid)    TestClient (use as a context manager: runs the lifespan on one loop),
                       sends X-Session-Id: sid on every HTTP request
    env.ws_url(path, sid, **query)  WebSocket URL with session_id (+ query)
    env.set_engines(**engines)      replace the MT engines (order kept)
    """

    def __init__(self, monkeypatch, tmp_path):
        from app.config import settings
        from app import asr as asr_pkg
        from app import websocket_handler as wsh
        from app.cache import translation_memory as tm_mod
        from app.cache.memory_cache import get_translation_cache
        from app.translation import base_translator as bt_mod
        from app.translation import pipeline as pipe_mod
        from tests.fakes import FakeASREngine, FakeMTEngine, make_translator

        self.settings = settings
        self._mp = monkeypatch
        self.tm = tm_mod.TranslationMemory(db_path=tmp_path / "tm.db")
        self.engines = {"fake": FakeMTEngine("fake")}
        self.translator = make_translator(self.engines)
        self.asr = FakeASREngine()

        monkeypatch.setattr(settings.features, "warmup_on_start", False)
        monkeypatch.setattr(settings.mt, "engine_order", list(self.engines))
        monkeypatch.setattr(settings.translation, "target_languages", ["en", "zh"])
        monkeypatch.setattr(bt_mod, "_translator", self.translator)
        monkeypatch.setattr(tm_mod, "_translation_memory", self.tm)
        self.pipeline = pipe_mod.TranslationPipeline()
        self.pipeline._memory_cache = get_translation_cache()
        self.pipeline._translation_memory = self.tm
        monkeypatch.setattr(pipe_mod, "_pipeline", self.pipeline)
        monkeypatch.setattr(wsh, "_micro_batcher", None)
        monkeypatch.setattr(asr_pkg, "_engines", {self.asr.name: self.asr})
        monkeypatch.setattr(asr_pkg, "_lid_identify", lambda audio, allowed: None)

    def set_engines(self, **engines) -> None:
        self.engines.clear()
        self.engines.update(engines)
        self.translator.engines = dict(engines)
        self.translator._engine_locks = {}
        self._mp.setattr(self.settings.mt, "engine_order", list(engines))

    def client(self, session_id: str = "test-session", **kw):
        from fastapi.testclient import TestClient
        from app.main import app

        headers = dict(kw.pop("headers", {}) or {})
        if session_id:
            headers.setdefault("X-Session-Id", session_id)
        return TestClient(app, headers=headers, **kw)

    @staticmethod
    def ws_url(path: str, session_id: str = "test-session", **query) -> str:
        from urllib.parse import urlencode

        q = dict(query)
        if session_id:
            q["session_id"] = session_id
        return path + ("?" + urlencode(q) if q else "")


@pytest.fixture
def app_env(monkeypatch, tmp_path, clear_memory_cache):
    return AppEnv(monkeypatch, tmp_path)


@pytest.fixture
def fake_llama(tmp_path):
    """Factory: fake_llama(*stub_args) -> a HyMTEngine whose llama-server is
    tests/fake_llama_server.py (see its docstring for the flags). Every engine
    created here is shut down after the test."""
    from tests.fakes import FAKE_LLAMA_SERVER
    from app.translation.hymt_translator import HyMTEngine

    made = []

    class FakeLlamaHyMT(HyMTEngine):
        def __init__(self, stub_args):
            super().__init__(gguf_path=tmp_path / "fake.gguf")
            self.available = True
            self.stub_args = list(stub_args)

        def _command(self, port):
            return [sys.executable, str(FAKE_LLAMA_SERVER), "--port", str(port), *self.stub_args]

    def make(*stub_args):
        eng = FakeLlamaHyMT(stub_args)
        made.append(eng)
        return eng

    yield make
    for eng in made:
        eng.shutdown()
