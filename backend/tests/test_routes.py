"""/health/json `routes`: for each spoken language, the translation targets the loaded
engines can reach (follow-up 1: the Options page greys out the rest)."""

from app.config import settings
from app.translation import base_translator as bt_mod
from tests.fakes import FakeMTEngine, make_translator


def test_routes_follow_the_loaded_engines(app_env):
    app_env.set_engines(fake=FakeMTEngine("fake", pairs={("en", "zh"), ("zh", "en"), ("bn", "en")}))
    with app_env.client() as c:
        routes = c.get("/health/json").json()["routes"]
    assert set(routes) == set(settings.asr.languages)
    assert routes["en"] == ["zh"]
    assert routes["zh"] == ["en"]
    assert routes["bn"] == ["en"]


def test_opus_has_no_route_from_english_to_korean_or_portuguese(app_env, monkeypatch):
    """The default engine's real routing table (no model files needed to ask it)."""
    from app.translation.base_translator import OpusCT2Engine

    monkeypatch.setattr(bt_mod, "CT2_AVAILABLE", True)
    app_env.set_engines(opus=OpusCT2Engine(settings.translation.ct2_dir))
    with app_env.client() as c:
        routes = c.get("/health/json").json()["routes"]
    for src in ("en", "zh", "bn"):
        assert "ko" not in routes[src] and "pt" not in routes[src], routes[src]
        assert {"vi", "ja", "es", "fr", "de", "ru", "it"} <= set(routes[src])
    assert "zh" in routes["en"] and "bn" in routes["en"] and "bn" in routes["zh"]
