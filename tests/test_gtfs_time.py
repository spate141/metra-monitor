from datetime import date
from zoneinfo import ZoneInfo

import pytest

from app.ingest.gtfs_time import (
    gtfs_time_to_datetime,
    parse_gtfs_time_to_seconds,
    seconds_to_gtfs_time,
)

CHI = ZoneInfo("America/Chicago")


def test_parse_normal_time():
    assert parse_gtfs_time_to_seconds("07:39:00") == 7 * 3600 + 39 * 60


def test_parse_midnight():
    assert parse_gtfs_time_to_seconds("00:00:00") == 0


def test_parse_over_24h_not_rejected():
    # 25:15:00 = 1:15 AM the next service day -- must parse, not raise.
    assert parse_gtfs_time_to_seconds("25:15:00") == 25 * 3600 + 15 * 60


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_gtfs_time_to_seconds("07:75:00")
    with pytest.raises(ValueError):
        parse_gtfs_time_to_seconds("not-a-time")


def test_gtfs_time_to_datetime_normal():
    d = gtfs_time_to_datetime(date(2026, 7, 1), "07:39:00", CHI)
    assert d.date() == date(2026, 7, 1)
    assert (d.hour, d.minute) == (7, 39)
    assert d.tzinfo is not None


def test_gtfs_time_to_datetime_rolls_to_next_day():
    # A trip departing 25:15:00 on service_date 2026-07-01 is 1:15 AM on 2026-07-02.
    d = gtfs_time_to_datetime(date(2026, 7, 1), "25:15:00", CHI)
    assert d.date() == date(2026, 7, 2)
    assert (d.hour, d.minute) == (1, 15)


def test_gtfs_time_to_datetime_exact_24h():
    d = gtfs_time_to_datetime(date(2026, 7, 1), "24:00:00", CHI)
    assert d.date() == date(2026, 7, 2)
    assert (d.hour, d.minute) == (0, 0)


def test_gtfs_time_dst_spring_forward():
    # 2026-03-08 is the US spring-forward date; 2:30 AM doesn't exist locally,
    # but zoneinfo/datetime must not raise -- it normalizes.
    d = gtfs_time_to_datetime(date(2026, 3, 7), "26:30:00", CHI)  # 2:30 AM on 3/8
    assert d.date() == date(2026, 3, 8)


def test_seconds_roundtrip():
    assert seconds_to_gtfs_time(parse_gtfs_time_to_seconds("25:15:00")) == "25:15:00"
    assert seconds_to_gtfs_time(0) == "00:00:00"
