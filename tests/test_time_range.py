import pytest

from bot.services.time_range import (
    TimeParseError,
    format_range,
    format_timestamp,
    parse_time_range,
    parse_timestamp,
)


def test_parse_timestamp_plain_seconds():
    assert parse_timestamp("12") == 12.0
    assert parse_timestamp("12.5") == 12.5


def test_parse_timestamp_mmss():
    assert parse_timestamp("1:05") == 65.0


def test_parse_timestamp_hmmss():
    assert parse_timestamp("1:02:03") == 3723.0


def test_parse_timestamp_rejects_garbage():
    with pytest.raises(TimeParseError):
        parse_timestamp("abc")


def test_parse_time_range_dash_seconds():
    assert parse_time_range("5-12", source_duration=100, max_span=60) == (5.0, 12.0)


def test_parse_time_range_dash_mmss():
    assert parse_time_range("0:05-0:20", source_duration=100, max_span=60) == (5.0, 20.0)


def test_parse_time_range_to_word():
    assert parse_time_range("5 to 12", source_duration=100, max_span=60) == (5.0, 12.0)


def test_parse_time_range_start_only_uses_max_span():
    assert parse_time_range("5", source_duration=100, max_span=60) == (5.0, 65.0)


def test_parse_time_range_start_only_clamped_to_duration():
    assert parse_time_range("90", source_duration=100, max_span=60) == (90.0, 100.0)


def test_parse_time_range_end_clamped_to_duration_then_max_span():
    # end requested (200) is beyond the 100s video -> clamped to 100,
    # then the resulting 95s span is beyond max_span(60) -> re-clamped to 60.
    assert parse_time_range("5-200", source_duration=100, max_span=60) == (5.0, 65.0)


def test_parse_time_range_end_before_start_rejected():
    with pytest.raises(TimeParseError):
        parse_time_range("50-10", source_duration=100, max_span=60)


def test_parse_time_range_start_past_end_of_video_rejected():
    with pytest.raises(TimeParseError):
        parse_time_range("200", source_duration=100, max_span=60)


def test_parse_time_range_too_short_rejected():
    with pytest.raises(TimeParseError):
        parse_time_range("5-5.2", source_duration=100, max_span=60, min_span=0.5)


def test_format_timestamp_mmss():
    assert format_timestamp(65) == "1:05"


def test_format_timestamp_hmmss():
    assert format_timestamp(3725) == "1:02:05"


def test_format_range():
    assert format_range(5, 12) == "0:05\u20130:12"
