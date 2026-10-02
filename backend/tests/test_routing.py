"""Language routing: every configured OPUS model must really target the
requested language, pivots go through English, Bengali is first-class."""

import re

import pytest

from app.config import settings, get_opus_model, get_opus_route, get_max_chars_for_language, get_language_display_name


def _model_target(model_name: str) -> str:
    # Helsinki-NLP/opus-mt-<src>-<tgt> or opus-mt-tc-big-<src>-<tgt>; target is the last dash segment
    tail = model_name.split("/")[-1]
    return tail.split("-")[-1].lower()


@pytest.mark.parametrize("src,models", list(settings.translation.opus_models.items()))
def test_every_direct_model_targets_its_language(src, models):
    for tgt, model in models.items():
        expected = {"zh": {"zh", "zho"}, "ja": {"ja", "jap"}}.get(tgt, {tgt})
        assert _model_target(model) in expected, f"{src}->{tgt} maps to {model} which does not output {tgt}"
        assert "mul-en" not in model or tgt == "en"


def test_no_x_to_zh_points_at_english_model():
    for src, models in settings.translation.opus_models.items():
        if "zh" in models:
            assert not models["zh"].endswith("-en"), f"{src}->zh routed to an English-output model"


def test_direct_route():
    assert get_opus_route("en", "zh") == [("en", "zh", "Helsinki-NLP/opus-mt-en-zh")]
    assert get_opus_route("bn", "en") == [("bn", "en", "Helsinki-NLP/opus-mt-bn-en")]


def test_pivot_route_through_english():
    route = get_opus_route("fr", "zh")
    assert route is not None and len(route) == 2
    assert route[0][:2] == ("fr", "en") and route[1][:2] == ("en", "zh")


def test_bengali_routes():
    # en->bn via the community OPUS model, zh->bn via the English pivot, bn->en direct
    assert get_opus_route("en", "bn") == [("en", "bn", "shhossain/opus-mt-en-to-bn")]
    zh_bn = get_opus_route("zh", "bn")
    assert zh_bn and [(s, t) for s, t, _ in zh_bn] == [("zh", "en"), ("en", "bn")]
    assert get_opus_route("bn", "zh") and get_opus_route("bn", "zh")[-1][1] == "zh"


def test_missing_route_is_none_not_english():
    # a pair with neither a direct model nor an English pivot must be None, never an English-output model
    assert get_opus_route("vi", "ko") is None


def test_same_language_is_empty_route():
    assert get_opus_route("en", "en") == []


def test_bengali_is_first_class():
    assert "bn" in settings.translation.supported_languages
    assert "bn" in settings.asr.languages
    assert get_max_chars_for_language("bn") == 38
    assert get_language_display_name("bn") == "Bengali"
    assert "bn" in settings.mt.hymt_languages


def test_defaults_are_fast_mode():
    assert settings.features.fast_mode is True
    assert settings.refiner.enabled is False
    assert settings.features.batch_window_ms > 0
