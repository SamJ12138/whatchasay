"""scripts/lid_alternatives.py (docs/lid-alternatives.md), its pure parts: where a window of
voiced audio ends, how a recognizer's output is scored, and how the summary counts misfires."""

import importlib.util
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "lid_alternatives.py"
_spec = importlib.util.spec_from_file_location("lid_alternatives", SCRIPT)
la = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(la)


def test_a_window_ends_when_that_much_voiced_audio_was_heard():
    silence, voice = np.zeros(7680, np.float32), np.full(16000, 0.1, np.float32)
    ends = la.window_ends(np.concatenate([silence, voice]))
    # 0.48 s of silence first (12 frames): the buffer holds it too; voiced time counts whole 40 ms frames
    assert ends == {0.5: 7680 + 8320, 0.75: 7680 + 12160, 1.0: 7680 + 16000}
    assert la.window_ends(np.zeros(32000, np.float32)) == {}


def test_real_words_per_language():
    assert la.real_word_share("the cat sat zxq", "en", {"the", "cat", "sat"}) == (3, 4)
    assert la.real_word_share("আমি ভাত খাই", "bn", {"আমি", "খাই"}) == (2, 3)
    # Mandarin: 2+ character matches are words, lone characters are not, Latin words are not Mandarin
    assert la.real_word_share("我们今天 OK", "zh", {"我们", "今天"}) == (2, 3)
    assert la.real_word_share("了", "zh", {"我们"}) == (0, 1)
    assert la.real_word_share("", "en", set()) == (0, 0)


def test_agreement_picks_the_most_sure_recognizer_and_its_margin():
    s = {"en": {"tokens": 4, "mean_lp": -1.5, "real": 3, "words": 4},
         "zh": {"tokens": 6, "mean_lp": -0.4, "real": 1, "words": 4},
         "bn": {"tokens": 0, "mean_lp": None, "real": 0, "words": 0}}
    assert la.agreement_decision(s, "logprob") == ("zh", 1.1)
    lang, margin = la.agreement_decision(s, "words")
    assert lang == "en" and abs(margin - 0.5) < 0.01
    none = {l: {"tokens": 0, "mean_lp": None, "real": 0, "words": 0} for l in la.LANGS}
    assert la.agreement_decision(none, "logprob") == (None, 0.0)


def _res():
    def cell(lang, conf):
        return {"whisper-tiny": {"lang": lang, "conf": conf}}
    return {"starts": {
        "zh@0": {"spoken": "zh", "windows": {"0.5": cell("en", 0.98), "1.0": cell("zh", 0.9)}},
        "bn@0": {"spoken": "bn", "windows": {"0.5": cell("en", 0.9), "1.0": cell("bn", 0.7)}},
        "en@0": {"spoken": "en", "windows": {"0.5": cell("en", 0.99), "1.0": cell("en", 0.95)}},
    }}


def test_the_summary_counts_what_the_session_would_have_accepted():
    t = la.table(_res())
    w = t["whisper-tiny@0.5"]
    # English only at 0.97 or more: the Mandarin start's 0.98 is a misfire, the Bengali one's 0.9 is not accepted
    assert (w["right"], w["wrong"], w["acc_right"], w["misfires"], w["misfires_zhbn_as_en"]) == (1, 2, 1, 1, 1)
    w = t["whisper-tiny@1.0"]
    assert (w["right"], w["acc_right"], w["acc_right_zhbn"], w["misfires"]) == (3, 2, 2, 0)
    assert la.zero_misfire_floor(_res(), "whisper-tiny", "0.5") == 0.9801
