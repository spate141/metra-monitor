"""GTFS time parsing (design §8.2).

GTFS `stop_times.arrival_time`/`departure_time` use HH:MM:SS where HH may exceed 24
(e.g. "25:15:00") to represent a trip continuing past midnight *of the same service
day*. These must never be rejected -- they roll into the next calendar day relative
to the service_date, not treated as invalid.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def parse_gtfs_time_to_seconds(raw: str) -> int:
    """Parse 'HH:MM:SS' (HH unbounded) into seconds since service-day midnight."""
    parts = raw.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid GTFS time: {raw!r}")
    h, m, s = (int(p) for p in parts)
    if m < 0 or m > 59 or s < 0 or s > 59 or h < 0:
        raise ValueError(f"invalid GTFS time: {raw!r}")
    return h * 3600 + m * 60 + s


def gtfs_time_to_datetime(service_date: date, raw: str, tz: ZoneInfo) -> datetime:
    """Convert a (service_date, GTFS HH:MM:SS) pair into a tz-aware datetime.

    Times >= 24:00:00 roll onto the calendar day(s) after service_date, but still
    belong to the same service day for calendar/schedule purposes.
    """
    total_seconds = parse_gtfs_time_to_seconds(raw)
    day_overflow, remainder = divmod(total_seconds, 24 * 3600)
    midnight = datetime.combine(service_date, datetime.min.time(), tzinfo=tz)
    return midnight + timedelta(days=day_overflow, seconds=remainder)


def seconds_to_gtfs_time(total_seconds: int) -> str:
    """Inverse of parse_gtfs_time_to_seconds, for round-tripping / display."""
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
