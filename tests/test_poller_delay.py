"""Delay derivation from Metra's realtime feed.

Metra publishes predicted absolute times (`arrival { time: ... }`) and never a
`delay` field, so delay has to be derived against the static schedule. These
tests pin the regression that made every observation read as exactly on time.
"""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2

from app.core.delay import explicit_stop_delay, stop_delay
from app.realtime.poller import _parse_trip_updates, scheduled_stop_times
from app.db import connect, init_schema

TZ = ZoneInfo("America/Chicago")
SERVICE_DATE = date(2026, 7, 8)  # Wednesday


def _feed(trip_id="TRIP1", stop_id="ROSELLE", *, arrival_time=None, arrival_delay=None):
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    e = msg.entity.add()
    e.id = trip_id
    tu = e.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.start_date = SERVICE_DATE.strftime("%Y%m%d")
    stu = tu.stop_time_update.add()
    stu.stop_id = stop_id
    if arrival_time is not None:
        stu.arrival.time = arrival_time
    if arrival_delay is not None:
        stu.arrival.delay = arrival_delay
    return msg


def _scheduled_epoch(hhmmss: str) -> int:
    from app.ingest.gtfs_time import gtfs_time_to_datetime

    return int(gtfs_time_to_datetime(SERVICE_DATE, hhmmss, TZ).timestamp())


def test_time_only_arrival_derives_real_delay():
    """The actual bug: a present `arrival` with no `delay` must not read as 0."""
    scheduled = {("TRIP1", "ROSELLE"): ("07:39:00", "07:39:00")}
    msg = _feed(arrival_time=_scheduled_epoch("07:39:00") + 420)  # 7 min late

    entries = _parse_trip_updates(msg, scheduled, TZ)

    assert entries["TRIP1"].stop_time_updates[0]["arrival_delay"] == 420
    assert stop_delay(entries["TRIP1"], "ROSELLE") == 420


def test_time_only_arrival_on_schedule_is_zero():
    scheduled = {("TRIP1", "ROSELLE"): ("07:39:00", "07:39:00")}
    msg = _feed(arrival_time=_scheduled_epoch("07:39:00"))

    entries = _parse_trip_updates(msg, scheduled, TZ)

    assert entries["TRIP1"].stop_time_updates[0]["arrival_delay"] == 0


def test_early_arrival_is_negative():
    scheduled = {("TRIP1", "ROSELLE"): ("07:39:00", "07:39:00")}
    msg = _feed(arrival_time=_scheduled_epoch("07:39:00") - 120)

    entries = _parse_trip_updates(msg, scheduled, TZ)

    assert entries["TRIP1"].stop_time_updates[0]["arrival_delay"] == -120


def test_explicit_delay_field_wins_over_derivation():
    """If Metra ever starts sending `delay`, trust it over our subtraction."""
    scheduled = {("TRIP1", "ROSELLE"): ("07:39:00", "07:39:00")}
    msg = _feed(arrival_time=_scheduled_epoch("07:39:00") + 420, arrival_delay=60)

    entries = _parse_trip_updates(msg, scheduled, TZ)

    assert entries["TRIP1"].stop_time_updates[0]["arrival_delay"] == 60


def test_unknown_schedule_yields_none_not_zero():
    """No scheduled time to compare against means unknown -- never 'on time'."""
    msg = _feed(arrival_time=_scheduled_epoch("07:39:00") + 420)

    entries = _parse_trip_updates(msg, scheduled={}, tz=TZ)

    assert entries["TRIP1"].stop_time_updates[0]["arrival_delay"] is None
    assert stop_delay(entries["TRIP1"], "ROSELLE") is None


def test_explicit_stop_delay_does_not_fall_back_to_trip_delay():
    """Once our stop drops off the feed (train passed it), on-time history must
    report nothing rather than the next stop's delay."""
    scheduled = {("TRIP1", "BENSENVIL"): ("07:50:00", "07:50:00")}
    msg = _feed(stop_id="BENSENVIL", arrival_time=_scheduled_epoch("07:50:00") + 600)
    entry = _parse_trip_updates(msg, scheduled, TZ)["TRIP1"]

    assert stop_delay(entry, "ROSELLE") == 600  # trip-level fallback, fine for display
    assert explicit_stop_delay(entry, "ROSELLE") is None  # strict, for history


def test_scheduled_stop_times_lookup(tmp_path):
    db = tmp_path / "sched.db"
    conn = connect(db)
    init_schema(conn)
    conn.execute(
        "INSERT INTO stop_times (trip_id, stop_id, stop_sequence, arrival_time, departure_time) "
        "VALUES (?,?,?,?,?)",
        ("TRIP1", "ROSELLE", 3, "07:39:00", "07:40:00"),
    )
    conn.commit()
    conn.close()

    assert scheduled_stop_times(db, {"TRIP1"}) == {("TRIP1", "ROSELLE"): ("07:39:00", "07:40:00")}
    assert scheduled_stop_times(db, set()) == {}
