"""Adaptive-cadence realtime polling loop (design §4.3), the continuous process
that feeds the Alert Engine (design §4.5) consecutive snapshots to diff.

Cadence:
- watch window (either resolved trip active within ±45 min): poll every 30s
- awake hours (05:30-22:00 CT) outside any watch window: poll every 5 min
  (constraint C8 -- catch early annulments even mid-day)
- night: poll paused entirely; the loop just sleeps until the next awake check

Also implements the feed-staleness watchdog (design §4.3 resilience): one deduped
Telegram warning if the realtime feeds return nothing for >5 min while inside a
watch window.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from app.alerts.engine import apply_direction_filter, apply_notification_mode, apply_quiet_hours, evaluate, in_watch_window
from app.config import Settings
from app.core.delay import stop_delay
from app.core.models import NoService
from app.core.trip_resolver import resolve_today
from app.db import (
    connect,
    fingerprint_recently_sent,
    get_notification_mode,
    get_paused_until,
    mark_fingerprint_sent,
)
from app.ingest.gtfs_time import gtfs_time_to_datetime
from app.realtime.poller import poll_once
from app.realtime.state_store import Snapshot, StateStore
from app.telegram.bot import push_message

logger = logging.getLogger(__name__)

WATCH_POLL_SECONDS = 30
AWAKE_POLL_SECONDS = 300
AWAKE_START = time(5, 30)
AWAKE_END = time(22, 0)
STALE_THRESHOLD = timedelta(minutes=5)
FINGERPRINT_COOLDOWN = timedelta(minutes=30)


def _watch_stop_map(settings: Settings) -> dict[str, str]:
    return {"morning": settings.HOME_STOP, "evening": settings.WORK_STOP}


def _any_watch_window_active(now: datetime, resolved: dict, settings: Settings) -> bool:
    watch_stop = _watch_stop_map(settings)
    for slot, result in resolved.items():
        if isinstance(result, NoService):
            continue
        st = result.stop_time_for(watch_stop[slot])
        if st is None or st.departure_time is None:
            continue
        scheduled_dt = gtfs_time_to_datetime(result.service_date, st.departure_time, settings.tzinfo)
        if in_watch_window(now, scheduled_dt):
            return True
    return False


def _is_awake_hours(now: datetime) -> bool:
    return AWAKE_START <= now.time() <= AWAKE_END


async def _dispatch_events(application, settings: Settings, events, now: datetime) -> None:
    events = apply_quiet_hours(events, now, settings)
    if not events:
        return
    conn = connect(settings.db_path)
    try:
        paused_until = get_paused_until(conn)
        if paused_until and now.date().isoformat() <= paused_until:
            return
        mode = get_notification_mode(conn)
        events = apply_notification_mode(events, now, settings, mode)
        events = apply_direction_filter(events, now, settings, mode)
        if not events:
            return
        for event in events:
            if fingerprint_recently_sent(conn, event.fingerprint, FINGERPRINT_COOLDOWN):
                continue
            await push_message(application, settings, event.message)
            mark_fingerprint_sent(conn, event.fingerprint)
    finally:
        conn.close()


def _record_delay_history(settings: Settings, resolved: dict, snapshot: Snapshot, now: datetime) -> None:
    """Append a delay observation per resolved trip (design §7 `delay_history`) --
    feeds the `/api/v1/stats` on-time% aggregate. Skipped when there's no live delay
    to record (never fabricate a value -- design edge case #4).
    """
    watch_stop = _watch_stop_map(settings)
    rows = []
    for slot, result in resolved.items():
        if isinstance(result, NoService):
            continue
        stop_id = watch_stop[slot]
        entry = snapshot.trip_updates.get(result.trip_id)
        delay_sec = stop_delay(entry, stop_id)
        if delay_sec is None:
            continue
        rows.append((datetime.now(timezone.utc).isoformat(), result.trip_id, result.train_no, stop_id, delay_sec, "realtime"))
    if not rows:
        return
    conn = connect(settings.db_path)
    try:
        conn.executemany(
            "INSERT INTO delay_history (ts, trip_id, train_no, stop_id, delay_sec, source) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


async def run_loop(settings: Settings, state_store: StateStore, application) -> None:
    """Runs forever. Call as a background asyncio task from app.main."""
    if not settings.has_realtime:
        logger.info("no METRA_API_TOKEN configured -- realtime loop / alert engine disabled")
        return

    empty_since: datetime | None = None
    stale_alerted_for: datetime | None = None

    while True:
        now = datetime.now(settings.tzinfo)
        service_date = now.date()
        resolved = resolve_today(
            settings.db_path, service_date, settings.MORNING_TRAIN, settings.EVENING_DEPART_CUS,
            settings.HOME_STOP, settings.WORK_STOP,
        )
        watch = _any_watch_window_active(now, resolved, settings)
        awake = _is_awake_hours(now)

        if not watch and not awake:
            stale_alerted_for = None
            await asyncio.sleep(AWAKE_POLL_SECONDS)
            continue

        snapshot = poll_once(settings)
        previous = state_store.latest
        state_store.update(snapshot)
        _record_delay_history(settings, resolved, snapshot, now)

        if previous is not None:
            events = evaluate(previous, snapshot, resolved, settings, now)
            await _dispatch_events(application, settings, events, now)

        if watch:
            if not snapshot.trip_updates and not snapshot.positions:
                empty_since = empty_since or now
                if now - empty_since > STALE_THRESHOLD and stale_alerted_for != service_date:
                    await push_message(application, settings, "⚠️ Metra realtime feed unreachable/stale.")
                    stale_alerted_for = service_date
            else:
                empty_since = None
        else:
            empty_since = None

        await asyncio.sleep(WATCH_POLL_SECONDS if watch else AWAKE_POLL_SECONDS)
