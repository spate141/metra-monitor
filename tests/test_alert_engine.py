"""Alert Engine tests (design §11 Phase 3 exit criteria): simulated feed
transitions must each produce exactly one alert.

The trip-update/alert fixtures are built as real `gtfs_realtime_pb2.FeedMessage`
protobufs and run through the *actual* poller parse functions
(`_parse_trip_updates`/`_parse_alerts`) rather than constructing `Snapshot`
dataclasses by hand -- this exercises the same code path production uses to turn
raw feed bytes into a Snapshot, closer to the design's "recorded protobuf
fixtures" intent than hand-built dataclasses would be, without needing a live
capture pinned to a specific moment of real-world delay.
"""
from __future__ import annotations

from datetime import date, datetime

from google.transit import gtfs_realtime_pb2

from app.alerts.engine import (
    apply_direction_filter,
    apply_notification_mode,
    apply_quiet_hours,
    evaluate,
    in_commute_window,
    in_quiet_hours,
)
from app.config import Settings
from app.core.models import NoService, ResolvedTrip, StopTime
from app.realtime.poller import _parse_alerts, _parse_trip_updates
from app.realtime.state_store import Snapshot

SERVICE_DATE = date(2026, 7, 8)  # Wednesday


def _settings(**overrides) -> Settings:
    defaults = dict(HOME_STOP="ROSELLE", WORK_STOP="CUS", MORNING_TRAIN="2222", TZ="America/Chicago")
    defaults.update(overrides)
    return Settings(**defaults)


def _resolved(morning_delay_stop="ROSELLE", morning_sched="07:39:00"):
    morning = ResolvedTrip(SERVICE_DATE, "morning", "TRIP_MORNING", "2222", [StopTime(morning_delay_stop, 1, None, morning_sched)])
    return {"morning": morning, "evening": NoService(SERVICE_DATE, "evening")}


def _now_at(hh: int, mm: int) -> datetime:
    return datetime(2026, 7, 8, hh, mm, tzinfo=_settings().tzinfo)


def _trip_update_feed(trip_id: str, *, delay_sec: int | None = None, stop_id: str = "ROSELLE", annulled: bool = False):
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    e = msg.entity.add()
    e.id = trip_id
    tu = e.trip_update
    tu.trip.trip_id = trip_id
    if annulled:
        tu.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.CANCELED
    if delay_sec is not None:
        stu = tu.stop_time_update.add()
        stu.stop_id = stop_id
        stu.departure.delay = delay_sec
    return msg


def _alert_feed(
    alert_id: str,
    route_id: str | None = None,
    stop_id: str | None = None,
    header: str = "Delay",
    direction_id: int | None = None,
    trip_direction_id: int | None = None,
):
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    e = msg.entity.add()
    e.id = alert_id
    a = e.alert
    if route_id or stop_id or direction_id is not None or trip_direction_id is not None:
        ie = a.informed_entity.add()
        if route_id:
            ie.route_id = route_id
        if stop_id:
            ie.stop_id = stop_id
        if direction_id is not None:
            ie.direction_id = direction_id
        if trip_direction_id is not None:
            ie.trip.direction_id = trip_direction_id
    a.header_text.translation.add(text=header, language="en")
    return msg


def _snapshot(trip_updates=None, alerts=None, fetched_at=None) -> Snapshot:
    return Snapshot(
        fetched_at=fetched_at or datetime.now(),
        trip_updates=trip_updates or {},
        alerts=alerts or {},
    )


def test_cold_start_never_alerts():
    settings = _settings()
    resolved = _resolved()
    latest = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=600)))
    events = evaluate(None, latest, resolved, settings, _now_at(7, 20))
    assert events == []


def test_delay_band_crossing_produces_exactly_one_alert():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(7, 20)  # inside the 45-min watch window around 07:39

    previous = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=60)))  # on_time
    latest = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=600)))  # major (10 min)

    events = evaluate(previous, latest, resolved, settings, now)
    assert len(events) == 1
    assert "major" in events[0].message


def test_delay_band_unchanged_produces_no_alert():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(7, 20)

    previous = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=60)))
    latest = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=90)))  # still on_time band

    assert evaluate(previous, latest, resolved, settings, now) == []


def test_delay_band_change_outside_watch_window_is_suppressed():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(10, 0)  # far outside the 07:39 +-45min watch window

    previous = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=60)))
    latest = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=600)))

    assert evaluate(previous, latest, resolved, settings, now) == []


def test_annulment_produces_exactly_one_alert_any_time_of_day():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(10, 0)  # outside watch window -- annulment (C8) must still fire

    previous = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=0)))
    latest = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", annulled=True)))

    events = evaluate(previous, latest, resolved, settings, now)
    assert len(events) == 1
    assert "CANCELLED" in events[0].message


def test_cancellation_lifted_produces_exactly_one_alert():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(10, 0)

    previous = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", annulled=True)))
    latest = _snapshot(_parse_trip_updates(_trip_update_feed("TRIP_MORNING", delay_sec=0)))

    events = evaluate(previous, latest, resolved, settings, now)
    assert len(events) == 1
    assert "running again" in events[0].message


def test_new_service_alert_produces_exactly_one_alert():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(12, 0)

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W", header="Signal problem")))

    events = evaluate(previous, latest, resolved, settings, now)
    assert len(events) == 1
    assert "Signal problem" in events[0].message


def test_irrelevant_service_alert_is_ignored():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(12, 0)

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="UP-N", header="Unrelated line issue")))

    assert evaluate(previous, latest, resolved, settings, now) == []


def test_parse_alerts_extracts_direction_ids():
    from app.core.trip_resolver import EVENING_DIRECTION_ID

    parsed = _parse_alerts(_alert_feed("A1", route_id="MD-W", trip_direction_id=EVENING_DIRECTION_ID))
    assert parsed["A1"].informed_direction_ids == {EVENING_DIRECTION_ID}


def test_direction_filter_keeps_matching_morning_direction():
    from app.core.trip_resolver import MORNING_DIRECTION_ID

    settings = _settings()
    resolved = _resolved()
    now = _now_at(7, 0)

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W", direction_id=MORNING_DIRECTION_ID)))

    events = evaluate(previous, latest, resolved, settings, now)
    kept = apply_direction_filter(events, now, settings, "commute")
    assert kept == events


def test_direction_filter_drops_mismatched_evening_alert_during_morning():
    from app.core.trip_resolver import EVENING_DIRECTION_ID

    settings = _settings()
    resolved = _resolved()
    now = _now_at(7, 0)

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W", direction_id=EVENING_DIRECTION_ID)))

    events = evaluate(previous, latest, resolved, settings, now)
    assert apply_direction_filter(events, now, settings, "commute") == []


def test_direction_filter_keeps_matching_evening_direction():
    from app.core.trip_resolver import EVENING_DIRECTION_ID

    settings = _settings()
    resolved = _resolved()
    now = _now_at(20, 0)

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W", direction_id=EVENING_DIRECTION_ID)))

    events = evaluate(previous, latest, resolved, settings, now)
    kept = apply_direction_filter(events, now, settings, "commute")
    assert kept == events


def test_direction_filter_drops_mismatched_morning_alert_during_evening():
    from app.core.trip_resolver import MORNING_DIRECTION_ID

    settings = _settings()
    resolved = _resolved()
    now = _now_at(20, 0)

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W", direction_id=MORNING_DIRECTION_ID)))

    events = evaluate(previous, latest, resolved, settings, now)
    assert apply_direction_filter(events, now, settings, "commute") == []


def test_direction_filter_keeps_direction_less_alert():
    settings = _settings()
    resolved = _resolved()
    now = _now_at(7, 0)

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W")))

    events = evaluate(previous, latest, resolved, settings, now)
    kept = apply_direction_filter(events, now, settings, "commute")
    assert kept == events


def test_direction_filter_passthrough_in_all_mode():
    from app.core.trip_resolver import EVENING_DIRECTION_ID

    settings = _settings()
    resolved = _resolved()
    now = _now_at(7, 0)  # morning, but mismatched direction

    previous = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))
    latest = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W", direction_id=EVENING_DIRECTION_ID)))

    events = evaluate(previous, latest, resolved, settings, now)
    kept = apply_direction_filter(events, now, settings, "all")
    assert kept == events


def test_cleared_alert_off_by_default():
    settings = _settings()  # ALERT_CLEARED_PUSH defaults to False
    resolved = _resolved()
    now = _now_at(12, 0)

    previous = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W")))
    latest = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))

    assert evaluate(previous, latest, resolved, settings, now) == []


def test_cleared_alert_when_enabled():
    settings = _settings(ALERT_CLEARED_PUSH=True)
    resolved = _resolved()
    now = _now_at(12, 0)

    previous = _snapshot(alerts=_parse_alerts(_alert_feed("A1", route_id="MD-W", header="Signal problem")))
    latest = _snapshot(alerts=_parse_alerts(gtfs_realtime_pb2.FeedMessage()))

    events = evaluate(previous, latest, resolved, settings, now)
    assert len(events) == 1
    assert "cleared" in events[0].message


def test_quiet_hours_suppresses_non_exempt_alerts():
    settings = _settings()
    now = datetime(2026, 7, 8, 23, 0, tzinfo=settings.tzinfo)  # inside 22:00-05:30 quiet hours
    assert in_quiet_hours(now, settings.QUIET_HOURS)

    from app.alerts.engine import AlertEvent

    events = [AlertEvent("fp1", "some alert"), AlertEvent("fp2", "morning cancellation", exempt_from_quiet_hours=True)]
    kept = apply_quiet_hours(events, now, settings)
    assert kept == [events[1]]


def test_quiet_hours_does_not_suppress_during_the_day():
    settings = _settings()
    now = datetime(2026, 7, 8, 12, 0, tzinfo=settings.tzinfo)
    assert not in_quiet_hours(now, settings.QUIET_HOURS)

    from app.alerts.engine import AlertEvent

    events = [AlertEvent("fp1", "some alert")]
    assert apply_quiet_hours(events, now, settings) == events


def test_in_commute_window_before_morning_cutoff():
    settings = _settings()
    assert in_commute_window(_now_at(7, 0), settings)


def test_in_commute_window_at_and_after_evening_start():
    settings = _settings()
    assert in_commute_window(_now_at(15, 0), settings)
    assert in_commute_window(_now_at(20, 0), settings)


def test_in_commute_window_false_at_noon():
    settings = _settings()
    assert not in_commute_window(_now_at(12, 0), settings)


def test_in_commute_window_boundary_at_9am_and_3pm():
    settings = _settings()
    assert in_commute_window(_now_at(8, 59), settings)
    assert not in_commute_window(_now_at(9, 0), settings)
    assert not in_commute_window(_now_at(14, 59), settings)
    assert in_commute_window(_now_at(15, 0), settings)


def test_apply_notification_mode_all_passes_through():
    settings = _settings()
    from app.alerts.engine import AlertEvent

    events = [AlertEvent("fp1", "some alert")]
    assert apply_notification_mode(events, _now_at(12, 0), settings, "all") == events


def test_apply_notification_mode_commute_drops_midday_alerts():
    settings = _settings()
    from app.alerts.engine import AlertEvent

    events = [AlertEvent("fp1", "some alert")]
    assert apply_notification_mode(events, _now_at(12, 0), settings, "commute") == []


def test_apply_notification_mode_commute_keeps_morning_and_evening_alerts():
    settings = _settings()
    from app.alerts.engine import AlertEvent

    events = [AlertEvent("fp1", "some alert")]
    assert apply_notification_mode(events, _now_at(7, 0), settings, "commute") == events
    assert apply_notification_mode(events, _now_at(20, 0), settings, "commute") == events
