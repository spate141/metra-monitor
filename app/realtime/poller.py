"""Realtime protobuf poller (design §4.3, constraints C2/C3/C4).

Fetches `tripupdates`, `positions`, `alerts` from the Metra realtime feeds using
`Authorization: Bearer <token>` (header only -- C3 forbids the query-param form
because it leaks into logs), parses GTFS-realtime protobuf (C4), and normalizes
into a Snapshot for the StateStore.

Phase 1 exposes a single manual `poll_once()` used by the CLI. The adaptive
30s/5min cadence loop (design §4.3: watch windows vs. awake hours vs. night) is
wired into the app lifespan starting Phase 2+, once there's a long-running
process to host it in.

Gated by `settings.has_realtime`: with no token configured, `poll_once()` returns
an empty Snapshot rather than making a request, so the rest of the app (CLI,
trip resolution) works without one -- see design edge case "no realtime data for
a scheduled trip: assume on schedule."
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from google.transit import gtfs_realtime_pb2

from app.config import Settings
from app.realtime.state_store import AlertEntry, Snapshot, TripUpdateEntry, VehiclePositionEntry

logger = logging.getLogger(__name__)

FEEDS = ("tripupdates", "positions", "alerts")


def _fetch_feed(settings: Settings, client: httpx.Client, feed: str) -> gtfs_realtime_pb2.FeedMessage:
    url = f"{settings.METRA_REALTIME_BASE}/{feed}"
    resp = client.get(url, headers={"Authorization": f"Bearer {settings.METRA_API_TOKEN}"}, timeout=20)
    resp.raise_for_status()
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.ParseFromString(resp.content)
    return msg


def _parse_trip_updates(msg: gtfs_realtime_pb2.FeedMessage) -> dict[str, TripUpdateEntry]:
    out: dict[str, TripUpdateEntry] = {}
    for entity in msg.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        is_annulled = tu.trip.schedule_relationship == gtfs_realtime_pb2.TripDescriptor.CANCELED

        stop_time_updates = []
        overall_delay = tu.delay if tu.HasField("delay") else None
        for stu in tu.stop_time_update:
            entry = {
                "stop_id": stu.stop_id,
                "arrival_delay": stu.arrival.delay if stu.HasField("arrival") else None,
                "departure_delay": stu.departure.delay if stu.HasField("departure") else None,
            }
            stop_time_updates.append(entry)
            if overall_delay is None:
                # Fall back to the most specific delay available (design C7-adjacent:
                # don't fabricate a delay when the feed doesn't provide one).
                overall_delay = entry["departure_delay"] if entry["departure_delay"] is not None else entry["arrival_delay"]

        out[trip_id] = TripUpdateEntry(
            trip_id=trip_id,
            delay_sec=overall_delay,
            stop_time_updates=stop_time_updates,
            is_annulled=is_annulled,
        )
    return out


def _parse_positions(msg: gtfs_realtime_pb2.FeedMessage) -> dict[str, VehiclePositionEntry]:
    out: dict[str, VehiclePositionEntry] = {}
    for entity in msg.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        trip_id = v.trip.trip_id
        if not trip_id:
            continue
        pos = v.position if v.HasField("position") else None
        ts = datetime.fromtimestamp(v.timestamp, tz=timezone.utc) if v.timestamp else None
        out[trip_id] = VehiclePositionEntry(
            trip_id=trip_id,
            lat=pos.latitude if pos else None,
            lon=pos.longitude if pos else None,
            bearing=pos.bearing if pos and pos.HasField("bearing") else None,
            current_stop_id=v.stop_id or None,
            timestamp=ts,
        )
    return out


def _parse_alerts(msg: gtfs_realtime_pb2.FeedMessage) -> dict[str, AlertEntry]:
    out: dict[str, AlertEntry] = {}
    for entity in msg.entity:
        if not entity.HasField("alert"):
            continue
        a = entity.alert
        route_ids = {e.route_id for e in a.informed_entity if e.route_id}
        stop_ids = {e.stop_id for e in a.informed_entity if e.stop_id}
        header = a.header_text.translation[0].text if a.header_text.translation else ""
        desc = a.description_text.translation[0].text if a.description_text.translation else ""
        out[entity.id] = AlertEntry(
            alert_id=entity.id,
            header_text=header,
            description_text=desc,
            informed_route_ids=route_ids,
            informed_stop_ids=stop_ids,
        )
    return out


def poll_once(settings: Settings) -> Snapshot:
    """Fetch all three realtime feeds once and return a normalized Snapshot.

    Returns an empty Snapshot (fetched_at=now) without making a request if no
    METRA_API_TOKEN is configured.
    """
    now = datetime.now(timezone.utc)
    if not settings.has_realtime:
        logger.info("no METRA_API_TOKEN configured -- skipping realtime poll")
        return Snapshot(fetched_at=now)

    with httpx.Client() as client:
        try:
            tu_msg = _fetch_feed(settings, client, "tripupdates")
            pos_msg = _fetch_feed(settings, client, "positions")
            alert_msg = _fetch_feed(settings, client, "alerts")
        except httpx.HTTPError as exc:
            logger.warning("realtime poll failed: %s", exc)
            return Snapshot(fetched_at=now)

    return Snapshot(
        fetched_at=now,
        trip_updates=_parse_trip_updates(tu_msg),
        positions=_parse_positions(pos_msg),
        alerts=_parse_alerts(alert_msg),
    )
