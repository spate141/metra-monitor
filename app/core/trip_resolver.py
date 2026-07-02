"""TripResolver (design §4.2): config-driven identification of "my trains,"
resolved fresh per service date from `calendar` + `calendar_dates`.

- Morning: trip with train number == MORNING_TRAIN, inbound (direction_id=1),
  serving HOME_STOP.
- Evening: trip departing WORK_STOP (CUS) at EVENING_DEPART_CUS, outbound
  (direction_id=0) -- matched by scheduled departure time at that stop, never
  hardcoded to a train number (Metra renumbers trains between schedule seasons;
  verified live: the 4:05 PM CUS departure is currently train 2225, not 2222).

Holiday / no-service handling (design §8.1): if the slot has no active service_id
serving the target train/time, a NoService result is returned so the caller can
report "no regular service" instead of silently skipping.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from app.core.models import NoService, ResolvedTrip, StopTime
from app.db import connect

MORNING_DIRECTION_ID = 1   # inbound, headed to CUS
EVENING_DIRECTION_ID = 0   # outbound, headed away from CUS


def _gtfs_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def active_service_ids(conn: sqlite3.Connection, service_date: date) -> set[str]:
    """Service IDs running on service_date, per calendar + calendar_dates exceptions."""
    ymd = _gtfs_date(service_date)
    weekday_col = [
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    ][service_date.weekday()]

    rows = conn.execute(
        f"SELECT service_id FROM calendar WHERE start_date <= ? AND end_date >= ? AND {weekday_col} = 1",
        (ymd, ymd),
    ).fetchall()
    active = {r["service_id"] for r in rows}

    for r in conn.execute("SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (ymd,)):
        if r["exception_type"] == 1:
            active.add(r["service_id"])
        elif r["exception_type"] == 2:
            active.discard(r["service_id"])

    return active


def _stops_for_trip(conn: sqlite3.Connection, trip_id: str) -> list[StopTime]:
    rows = conn.execute(
        "SELECT stop_id, stop_sequence, arrival_time, departure_time FROM stop_times "
        "WHERE trip_id = ? ORDER BY stop_sequence",
        (trip_id,),
    ).fetchall()
    return [StopTime(r["stop_id"], r["stop_sequence"], r["arrival_time"], r["departure_time"]) for r in rows]


def resolve_morning(
    conn: sqlite3.Connection, service_date: date, train_no: str, home_stop_id: str
) -> ResolvedTrip | NoService:
    active = active_service_ids(conn, service_date)
    if not active:
        return NoService(service_date, "morning")

    qmarks = ",".join("?" * len(active))
    row = conn.execute(
        f"SELECT trip_id, trip_short_name FROM trips "
        f"WHERE trip_short_name = ? AND direction_id = ? AND service_id IN ({qmarks})",
        (train_no, MORNING_DIRECTION_ID, *active),
    ).fetchone()
    if row is None:
        return NoService(service_date, "morning")

    stops = _stops_for_trip(conn, row["trip_id"])
    if not any(s.stop_id == home_stop_id for s in stops):
        return NoService(service_date, "morning", reason=f"train {train_no} does not serve {home_stop_id} today")

    return ResolvedTrip(service_date, "morning", row["trip_id"], row["trip_short_name"], stops)


def resolve_evening(
    conn: sqlite3.Connection, service_date: date, depart_cus: str, work_stop_id: str
) -> ResolvedTrip | NoService:
    active = active_service_ids(conn, service_date)
    if not active:
        return NoService(service_date, "evening")

    target_time = depart_cus if len(depart_cus.split(":")) == 3 else f"{depart_cus}:00"
    qmarks = ",".join("?" * len(active))
    row = conn.execute(
        f"""
        SELECT t.trip_id, t.trip_short_name
        FROM trips t
        JOIN stop_times st ON st.trip_id = t.trip_id
        WHERE t.direction_id = ? AND t.service_id IN ({qmarks})
          AND st.stop_id = ? AND st.departure_time = ?
        """,
        (EVENING_DIRECTION_ID, *active, work_stop_id, target_time),
    ).fetchone()
    if row is None:
        return NoService(service_date, "evening", reason=f"no trip departs {work_stop_id} at {target_time} today")

    stops = _stops_for_trip(conn, row["trip_id"])
    return ResolvedTrip(service_date, "evening", row["trip_id"], row["trip_short_name"], stops)


def _persist(conn: sqlite3.Connection, result: ResolvedTrip | NoService) -> None:
    if isinstance(result, NoService):
        conn.execute(
            "INSERT INTO resolved_trips (service_date, slot, trip_id, train_no, scheduled_times_json) "
            "VALUES (?,?,NULL,NULL,NULL) "
            "ON CONFLICT(service_date, slot) DO UPDATE SET trip_id=NULL, train_no=NULL, scheduled_times_json=NULL",
            (result.service_date.isoformat(), result.slot),
        )
    else:
        times_json = json.dumps(
            [{"stop_id": s.stop_id, "seq": s.stop_sequence, "arr": s.arrival_time, "dep": s.departure_time}
             for s in result.stops]
        )
        conn.execute(
            "INSERT INTO resolved_trips (service_date, slot, trip_id, train_no, scheduled_times_json) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(service_date, slot) DO UPDATE SET "
            "trip_id=excluded.trip_id, train_no=excluded.train_no, scheduled_times_json=excluded.scheduled_times_json",
            (result.service_date.isoformat(), result.slot, result.trip_id, result.train_no, times_json),
        )
    conn.commit()


def resolve_today(
    db_path: Path,
    service_date: date,
    morning_train: str,
    evening_depart_cus: str,
    home_stop_id: str,
    work_stop_id: str,
) -> dict[str, ResolvedTrip | NoService]:
    """Resolve both slots for service_date and persist to resolved_trips."""
    conn = connect(db_path)
    try:
        morning = resolve_morning(conn, service_date, morning_train, home_stop_id)
        evening = resolve_evening(conn, service_date, evening_depart_cus, work_stop_id)
        _persist(conn, morning)
        _persist(conn, evening)
        return {"morning": morning, "evening": evening}
    finally:
        conn.close()
