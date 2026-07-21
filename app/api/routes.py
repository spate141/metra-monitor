"""Public REST API (design §5). All JSON, read-only, no auth (data is public; the
Metra token stays server-side per constraint C1). Realtime feed data is cached
in-process for `_SNAPSHOT_TTL` seconds so N browser tabs polling `/summary` +
`/positions` cost Metra nothing extra beyond our own poll cadence.
"""
from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.delay import delay_glyph, stop_delay
from app.core.models import NoService
from app.core.stats import compute_stats
from app.core.trip_resolver import active_service_ids, resolve_today
from app.db import connect
from app.realtime.poller import poll_once

router = APIRouter(prefix="/api/v1")

_SNAPSHOT_TTL = 20.0
_snapshot_cache: dict = {"value": None, "fetched_at": 0.0}
_geometry_cache: dict = {"value": None}


def _cached_snapshot():
    now = time.monotonic()
    if _snapshot_cache["value"] is None or now - _snapshot_cache["fetched_at"] > _SNAPSHOT_TTL:
        _snapshot_cache["value"] = poll_once(settings)
        _snapshot_cache["fetched_at"] = now
    return _snapshot_cache["value"]


@router.get("/summary")
def get_summary():
    service_date = datetime.now(settings.tzinfo).date()
    resolved = resolve_today(
        settings.db_path, service_date, settings.MORNING_TRAIN, settings.EVENING_DEPART_CUS,
        settings.HOME_STOP, settings.WORK_STOP,
    )
    snapshot = _cached_snapshot()
    watch_stop = {"morning": settings.HOME_STOP, "evening": settings.WORK_STOP}

    out = {}
    for slot, result in resolved.items():
        if isinstance(result, NoService):
            out[slot] = {"status": "no_service", "reason": result.reason}
            continue
        stop_id = watch_stop[slot]
        st = result.stop_time_for(stop_id)
        entry = snapshot.trip_updates.get(result.trip_id)
        pos = snapshot.positions.get(result.trip_id)
        delay_sec = stop_delay(entry, stop_id)
        out[slot] = {
            "status": "resolved",
            "train_no": result.train_no,
            "trip_id": result.trip_id,
            "stop_id": stop_id,
            "scheduled": st.departure_time if st else None,
            "delay_sec": delay_sec,
            "is_annulled": entry.is_annulled if entry else False,
            "glyph": delay_glyph(delay_sec, entry.is_annulled if entry else False),
            "current_stop_id": pos.current_stop_id if pos else None,
            "lat": pos.lat if pos else None,
            "lon": pos.lon if pos else None,
        }
    return out


@router.get("/positions")
def get_positions():
    service_date = datetime.now(settings.tzinfo).date()
    resolved = resolve_today(
        settings.db_path, service_date, settings.MORNING_TRAIN, settings.EVENING_DEPART_CUS,
        settings.HOME_STOP, settings.WORK_STOP,
    )
    my_trip_ids = {r.trip_id for r in resolved.values() if not isinstance(r, NoService)}
    snapshot = _cached_snapshot()

    conn = connect(settings.db_path)
    try:
        trips_by_id = {
            r["trip_id"]: (r["trip_short_name"], r["direction_id"])
            for r in conn.execute("SELECT trip_id, trip_short_name, direction_id FROM trips")
        }
    finally:
        conn.close()

    # The realtime feed is system-wide (all Metra lines); the static DB is already
    # filtered to ROUTE_ID (design §4.1), so restrict to trip_ids known there.
    out = []
    seen: set[str] = set()
    for trip_id, pos in snapshot.positions.items():
        if trip_id not in trips_by_id:
            continue
        seen.add(trip_id)
        entry = snapshot.trip_updates.get(trip_id)
        train_no, direction_id = trips_by_id[trip_id]
        out.append({
            "trip_id": trip_id,
            "train_no": train_no,
            "lat": pos.lat,
            "lon": pos.lon,
            "bearing": pos.bearing,
            "delay_sec": entry.delay_sec if entry else None,
            "next_stop": pos.current_stop_id,
            "is_my_train": trip_id in my_trip_ids,
            "direction_id": direction_id,
            "stale": False,
        })
    # Constraint C7: a trip_update with no matching vehicle position means the
    # train lost GPS (underground/terminal) -- assume in route, flag as a ghost
    # rather than omitting it. (Interpolated coordinates are a dashboard concern.)
    for trip_id, entry in snapshot.trip_updates.items():
        if trip_id not in trips_by_id or trip_id in seen or entry.is_annulled:
            continue
        train_no, direction_id = trips_by_id[trip_id]
        out.append({
            "trip_id": trip_id,
            "train_no": train_no,
            "lat": None,
            "lon": None,
            "bearing": None,
            "delay_sec": entry.delay_sec,
            "next_stop": None,
            "direction_id": direction_id,
            "is_my_train": trip_id in my_trip_ids,
            "stale": True,
        })
    return out


@router.get("/trip/{train_no}")
def get_trip(train_no: str):
    conn = connect(settings.db_path)
    try:
        service_date = datetime.now(settings.tzinfo).date()
        active = active_service_ids(conn, service_date)
        if not active:
            raise HTTPException(404, "no MD-W service today")
        qmarks = ",".join("?" * len(active))
        row = conn.execute(
            f"SELECT trip_id FROM trips WHERE trip_short_name = ? AND service_id IN ({qmarks})",
            (train_no, *active),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"no MD-W train #{train_no} running today")

        stops = conn.execute(
            "SELECT stop_id, stop_sequence, arrival_time, departure_time FROM stop_times "
            "WHERE trip_id = ? ORDER BY stop_sequence",
            (row["trip_id"],),
        ).fetchall()
    finally:
        conn.close()

    snapshot = _cached_snapshot()
    entry = snapshot.trip_updates.get(row["trip_id"])
    pos = snapshot.positions.get(row["trip_id"])

    timeline = [
        {
            "stop_id": s["stop_id"],
            "stop_sequence": s["stop_sequence"],
            "scheduled_arrival": s["arrival_time"],
            "scheduled_departure": s["departure_time"],
            "delay_sec": stop_delay(entry, s["stop_id"]),
        }
        for s in stops
    ]
    return {
        "train_no": train_no,
        "trip_id": row["trip_id"],
        "is_annulled": entry.is_annulled if entry else False,
        "position": (
            {"lat": pos.lat, "lon": pos.lon, "bearing": pos.bearing, "current_stop_id": pos.current_stop_id}
            if pos else None
        ),
        "stops": timeline,
    }


@router.get("/alerts")
def get_alerts():
    snapshot = _cached_snapshot()
    out = []
    line_wide = False
    for alert in snapshot.alerts.values():
        relevant = (
            settings.ROUTE_ID in alert.informed_route_ids
            or settings.HOME_STOP in alert.informed_stop_ids
            or settings.WORK_STOP in alert.informed_stop_ids
        )
        if not relevant:
            continue
        is_line_wide = settings.ROUTE_ID in alert.informed_route_ids and not alert.informed_stop_ids
        line_wide = line_wide or is_line_wide
        out.append({
            "id": alert.alert_id,
            "header": alert.header_text,
            "description": alert.description_text,
            "line_wide": is_line_wide,
        })
    return {"alerts": out, "line_wide": line_wide}


def _build_geometry() -> dict:
    conn = connect(settings.db_path)
    try:
        shapes = conn.execute(
            "SELECT shape_id, shape_pt_sequence, shape_pt_lat, shape_pt_lon FROM shapes ORDER BY shape_id, shape_pt_sequence"
        ).fetchall()
        stops = conn.execute("SELECT stop_id, stop_name, stop_lat, stop_lon FROM stops").fetchall()
        route = conn.execute(
            "SELECT route_color, route_text_color FROM routes WHERE route_id = ?", (settings.ROUTE_ID,)
        ).fetchone()
    finally:
        conn.close()

    by_shape: dict[str, list] = {}
    for r in shapes:
        by_shape.setdefault(r["shape_id"], []).append([r["shape_pt_lon"], r["shape_pt_lat"]])
    line_features = [
        {"type": "Feature", "properties": {"shape_id": sid}, "geometry": {"type": "LineString", "coordinates": coords}}
        for sid, coords in by_shape.items() if len(coords) >= 2
    ]
    stop_features = [
        {
            "type": "Feature",
            "properties": {"stop_id": s["stop_id"], "stop_name": s["stop_name"]},
            "geometry": {"type": "Point", "coordinates": [s["stop_lon"], s["stop_lat"]]},
        }
        for s in stops if s["stop_lat"] is not None and s["stop_lon"] is not None
    ]
    return {
        "line": {"type": "FeatureCollection", "features": line_features},
        "stops": {"type": "FeatureCollection", "features": stop_features},
        "route_color": route["route_color"] if route else None,
        "route_text_color": route["route_text_color"] if route else None,
    }


@router.get("/geometry")
def get_geometry():
    if _geometry_cache["value"] is None:
        _geometry_cache["value"] = _build_geometry()
    return JSONResponse(content=_geometry_cache["value"], headers={"Cache-Control": "max-age=86400"})


@router.get("/stats")
def get_stats():
    conn = connect(settings.db_path)
    try:
        return compute_stats(conn, settings.tzinfo)
    finally:
        conn.close()
