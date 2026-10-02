from app.translation.line_breaker import format_subtitle_lines, SmartLineBreaker


def test_short_english_stays_single_line():
    lines, single = format_subtitle_lines("Where did you put the keys?", "en")
    assert lines == ["Where did you put the keys?"]
    assert single == "Where did you put the keys?"


def test_long_english_breaks_within_limit():
    text = "I will be back in five minutes, so please do not touch anything on the table."
    lines, single = format_subtitle_lines(text, "en", max_chars=42, max_lines=2)
    assert len(lines) == 2
    for line in lines:
        assert len(line) <= 42
    assert " ".join(l.strip() for l in lines) == single.strip()


def test_chinese_breaks_on_punctuation():
    text = "我五分钟后回来，什么都别碰，我们等一下再说这件事情好吗"
    lines, _ = format_subtitle_lines(text, "zh", max_chars=22, max_lines=2)
    assert len(lines) <= 2
    assert all(len(l) <= 22 + 2 for l in lines)


def test_bengali_danda_is_a_break_point():
    assert "। " in SmartLineBreaker.BREAK_SCORES
    text = "আমি পাঁচ মিনিটের মধ্যে ফিরে আসব। কিছু ছুঁয়ো না।"
    lines, _ = format_subtitle_lines(text, "bn", max_chars=38, max_lines=2)
    assert len(lines) == 2
    # preferred break lands right after the danda
    assert lines[0].rstrip().endswith("।")
    assert all(len(l) <= 38 for l in lines)
