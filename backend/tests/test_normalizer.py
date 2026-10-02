from app.translation.text_normalizer import TextNormalizer
from app.translation.language_detection import LanguageDetector


def test_strips_html_and_normalizes_quotes():
    n = TextNormalizer().normalize("<i>It’s <b>fine</b></i>")
    assert "<" not in n.text
    assert "It's" in n.text
    assert n.should_translate


def test_music_marker_only_is_not_translated():
    n = TextNormalizer().normalize("♪")
    assert n.is_music
    assert not n.should_translate


def test_sound_effect_only_is_not_translated():
    n = TextNormalizer().normalize("[sighs]")
    assert n.is_sound_effect
    assert not n.should_translate


def test_speaker_label_extracted():
    n = TextNormalizer().normalize("JOHN: Where are the keys?")
    assert n.speaker and "JOHN" in n.speaker
    assert n.text.startswith("Where")


def test_bengali_script_detected():
    lang, conf = LanguageDetector().detect("আমি পাঁচ মিনিটের মধ্যে ফিরে আসব।")
    assert lang == "bn"
    assert conf >= 0.8


def test_chinese_and_english_detected():
    assert LanguageDetector().detect("我五分钟后回来")[0] == "zh"
    assert LanguageDetector().detect("Where did you put the keys?")[0] == "en"
