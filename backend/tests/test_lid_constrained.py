"""Spoken-language ID constrained to the supported languages (no model needed).

whisper-tiny's own answer is the argmax over ~100 languages; on Bengali film audio it
says Hindi or Nepali (docs/observations.md, A7), which used to mean "undecided" and a
fallback to English. The choice is now the argmax over the allowed languages only,
renormalised, with a confidence floor; the unconstrained top 3 is logged every call."""

import json

import numpy as np
import pytest

from app import obs
from app.asr import lid as lid_mod
from app.asr.lid import SpokenLanguageId, choose_language, log_mel
from app.config import Settings

# whisper-tiny's probabilities on this repo's YouTube clip (attempt 1, docs/live-test-youtube.md)
CLIP_LIKE = {"hi": 0.646, "bn": 0.189, "mr": 0.039, "ne": 0.03, "en": 0.0006, "zh": 0.0002}


def test_choice_is_the_argmax_over_the_allowed_languages_renormalised():
    lang, info = choose_language(CLIP_LIKE, ["en", "zh", "bn"], floor=0.6)
    assert lang == "bn"
    assert info["constrained"]["bn"] == pytest.approx(0.189 / (0.189 + 0.0006 + 0.0002), abs=1e-3)
    assert sum(info["constrained"].values()) == pytest.approx(1.0, abs=1e-3)
    assert [c for c, _ in info["top3"]] == ["hi", "bn", "mr"]  # unconstrained, as whisper saw it


def test_below_the_floor_the_choice_is_undecided():
    lang, info = choose_language({"bn": 0.30, "en": 0.25, "es": 0.4}, ["en", "zh", "bn"], floor=0.6)
    assert lang is None
    assert info["chosen"] is None and info["best"] == "bn" and info["confidence"] < 0.6


def test_no_allowed_language_scored_is_undecided():
    lang, _ = choose_language({"hi": 0.9, "ne": 0.1}, ["en", "zh", "bn"], floor=0.6)
    assert lang is None


def test_log_mel_is_whisper_shaped():
    mel = log_mel(np.zeros(16000 * 3, dtype=np.float32))
    assert mel.shape == (80, 3000) and mel.dtype == np.float32
    tone = log_mel((0.1 * np.sin(2 * np.pi * 440 * np.arange(16000 * 2) / 16000)).astype(np.float32))
    assert tone.max() <= 1.6 and tone.min() >= tone.max() - 2.0 - 1e-6  # (log10 + 4) / 4, 8 decades of range


def test_identify_logs_the_unconstrained_top3_on_every_call(monkeypatch, tmp_path):
    records = []
    monkeypatch.setattr(obs, "log", lambda stage, event, **kw: records.append((stage, event, kw)))
    lid = SpokenLanguageId(tmp_path, min_confidence=0.6)
    monkeypatch.setattr(lid, "available", lambda: True)
    monkeypatch.setattr(lid, "probabilities", lambda samples: dict(CLIP_LIKE))
    assert lid.identify(np.zeros(16000, np.float32), ["en", "zh", "bn"]) == "bn"
    monkeypatch.setattr(lid, "probabilities", lambda samples: {"es": 0.5, "en": 0.25, "bn": 0.25})
    assert lid.identify(np.zeros(16000, np.float32), ["en", "zh", "bn"]) is None
    scores = [r for r in records if r[0] == "lid_scores"]
    assert len(scores) == 2
    assert scores[0][1] == "success" and scores[0][2]["chosen"] == "bn" and scores[0][2]["top3"][0][0] == "hi"
    assert scores[1][1] == "skip" and scores[1][2]["chosen"] is None and scores[1][2]["error_type"] == "input_invalid"
    json.dumps([r[2] for r in scores])  # plain JSON for the run log


def test_floor_setting_default():
    assert Settings().asr.lid_min_confidence == 0.6


def test_language_tables_come_from_the_model_metadata():
    meta = {"all_language_tokens": "50259,50260,50302,", "all_language_codes": "en,zh,bn,", "sot": "50258"}
    assert lid_mod.language_tokens(meta) == ({"en": 50259, "zh": 50260, "bn": 50302}, 50258)
