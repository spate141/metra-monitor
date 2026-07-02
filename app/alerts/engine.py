"""Alert Engine (design §4.5): diffs consecutive realtime snapshots for state
*transitions* and produces deduped Telegram-ready events. Never fires on the raw
state of a single snapshot -- only on a change from the previous one -- so a
delayed train doesn't re-alert every poll.

Three transition sources (design §4.5):
1. My-train delay-band change (0-2 / 3-9 / 10+ min / cancelled), inside the
   train's ±45 min watch window only.
2. Annulment / cancellation-lifted for my resolved trips, any time of day
   (constraint C8 -- these can appear hours early and must not wait for a
   watch window to be reported).
3. GTFS service alerts newly present (or, if `ALERT_CLEARED_PUSH`, newly absent)
   whose `informed_entity` matches the configured route or home/work stops.

`evaluate()` returns `AlertEvent`s with a stable fingerprint; the caller (the
realtime loop) is responsible for the DB-backed dedup/cooldown
(`fingerprint_recently_sent`/`mark_fingerprint_sent` in app/db.py) and for
respecting quiet hours via `in_quiet_hours()`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings
from app.core.delay import delay_band, stop_delay
from app.core.models import NoService, ResolvedTrip
from app.ingest.gtfs_time import gtfs_time_to_datetime
from app.realtime.state_store import Snapshot

WATCH_WINDOW = timedelta(minutes=45)

_BAND_GLYPH = {"on_time": "✅", "minor": "🟡", "major": "🔴", "annulled": "⛔", "unknown": "⚪"}


@dataclass(frozen=True)
class AlertEvent:
    fingerprint: str
    message: str
    exempt_from_quiet_hours: bool = False


def _fingerprint(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def in_watch_window(now: datetime, scheduled: datetime) -> bool:
    return abs(now - scheduled) <= WATCH_WINDOW


def in_quiet_hours(now: datetime, quiet_hours: str) -> bool:
    start_s, end_s = quiet_hours.split("-")
    start_h, start_m = (int(x) for x in start_s.split(":"))
    end_h, end_m = (int(x) for x in end_s.split(":"))
    start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # window wraps midnight, e.g. 22:00-05:30


def _is_relevant_alert(alert, settings: Settings) -> bool:
    return (
        settings.ROUTE_ID in alert.informed_route_ids
        or settings.HOME_STOP in alert.informed_stop_ids
        or settings.WORK_STOP in alert.informed_stop_ids
    )


def _my_trip_events(
    previous: Snapshot,
    latest: Snapshot,
    resolved: dict[str, ResolvedTrip | NoService],
    watch_stop: dict[str, str],
    now: datetime,
    settings: Settings,
) -> list[AlertEvent]:
    events: list[AlertEvent] = []
    for slot, result in resolved.items():
        if isinstance(result, NoService):
            continue
        stop_id = watch_stop[slot]
        st = result.stop_time_for(stop_id)
        if st is None or st.departure_time is None:
            continue
        scheduled_dt = gtfs_time_to_datetime(result.service_date, st.departure_time, settings.tzinfo)

        prev_entry = previous.trip_updates.get(result.trip_id)
        latest_entry = latest.trip_updates.get(result.trip_id)
        prev_annulled = prev_entry.is_annulled if prev_entry else False
        latest_annulled = latest_entry.is_annulled if latest_entry else False

        # Annulment transitions fire any time of day (C8) -- never gated on the watch window.
        if latest_annulled and not prev_annulled:
            events.append(
                AlertEvent(
                    _fingerprint("annul", result.trip_id, "true"),
                    f"🚫 Train #{result.train_no} ({slot}) has been CANCELLED for today.",
                    exempt_from_quiet_hours=(slot == "morning"),
                )
            )
        elif prev_annulled and not latest_annulled:
            events.append(
                AlertEvent(
                    _fingerprint("annul", result.trip_id, "false"),
                    f"✅ Train #{result.train_no} ({slot}) is running again (cancellation lifted).",
                )
            )

        if not in_watch_window(now, scheduled_dt):
            continue

        prev_band = delay_band(stop_delay(prev_entry, stop_id), prev_annulled)
        latest_band = delay_band(stop_delay(latest_entry, stop_id), latest_annulled)
        # Annulment already reported above; don't double-report the band flip that comes with it.
        if latest_band != prev_band and not latest_annulled and not prev_annulled:
            glyph = _BAND_GLYPH[latest_band]
            events.append(
                AlertEvent(
                    _fingerprint("band", result.trip_id, latest_band),
                    f"{glyph} Train #{result.train_no} ({slot}) is now {latest_band.replace('_', ' ')}.",
                )
            )
    return events


def _service_alert_events(previous: Snapshot, latest: Snapshot, settings: Settings) -> list[AlertEvent]:
    events: list[AlertEvent] = []
    prev_relevant = {aid: a for aid, a in previous.alerts.items() if _is_relevant_alert(a, settings)}
    latest_relevant = {aid: a for aid, a in latest.alerts.items() if _is_relevant_alert(a, settings)}

    for aid, alert in latest_relevant.items():
        if aid not in prev_relevant:
            events.append(
                AlertEvent(_fingerprint("alert_new", aid), f"📢 New alert: {alert.header_text or alert.description_text}")
            )
    if settings.ALERT_CLEARED_PUSH:
        for aid, alert in prev_relevant.items():
            if aid not in latest_relevant:
                events.append(
                    AlertEvent(
                        _fingerprint("alert_cleared", aid),
                        f"✅ Alert cleared: {alert.header_text or alert.description_text}",
                    )
                )
    return events


def evaluate(
    previous: Snapshot | None,
    latest: Snapshot,
    resolved: dict[str, ResolvedTrip | NoService],
    settings: Settings,
    now: datetime,
) -> list[AlertEvent]:
    """Diff `previous` -> `latest` and return alert events for state transitions.

    Returns [] if `previous` is None (first poll after a cold start/restart --
    design never mass-alerts on startup just because there's no prior snapshot
    to compare against).
    """
    if previous is None:
        return []
    watch_stop = {"morning": settings.HOME_STOP, "evening": settings.WORK_STOP}
    events = _my_trip_events(previous, latest, resolved, watch_stop, now, settings)
    events += _service_alert_events(previous, latest, settings)
    return events


def apply_quiet_hours(events: list[AlertEvent], now: datetime, settings: Settings) -> list[AlertEvent]:
    if not in_quiet_hours(now, settings.QUIET_HOURS):
        return events
    return [e for e in events if e.exempt_from_quiet_hours]
