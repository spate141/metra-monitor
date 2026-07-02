"""Briefing / on-demand message builders (design §4.4, §4.6).

Pure functions: given a DB connection, a realtime `Snapshot`, and config, return
the Telegram message text. No I/O here (polling/sending happens in the caller) so
these are easy to unit test and to exercise from the CLI without a bot running.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from app.config import Settings
from app.core.delay import delay_glyph, stop_delay
from app.core.models import NoService, ResolvedTrip
from app.core.stats import compute_stats
from app.core.trip_resolver import (
    EVENING_DIRECTION_ID,
    MORNING_DIRECTION_ID,
    active_service_ids,
    list_departures,
    resolve_evening,
    resolve_morning,
)
from app.ingest.gtfs_time import gtfs_time_to_datetime, parse_gtfs_time_to_seconds
from app.realtime.state_store import Snapshot

HOLIDAY_MESSAGE = "🎉 No regular weekday service today (holiday schedule) — check /next."


def _fmt_time(raw: str | None, service_date: date, settings: Settings) -> str:
    if raw is None:
        return "?"
    dt = gtfs_time_to_datetime(service_date, raw, settings.tzinfo)
    return dt.strftime("%-I:%M %p")


def _stop_name(conn: sqlite3.Connection, stop_id: str | None) -> str | None:
    if not stop_id:
        return None
    row = conn.execute("SELECT stop_name FROM stops WHERE stop_id = ?", (stop_id,)).fetchone()
    return row["stop_name"] if row else stop_id


def _estimated_time(sched_raw: str, delay_sec: int | None, service_date: date, settings: Settings) -> str:
    dt = gtfs_time_to_datetime(service_date, sched_raw, settings.tzinfo)
    if delay_sec:
        from datetime import timedelta

        dt = dt + timedelta(seconds=delay_sec)
    return dt.strftime("%-I:%M %p")


def _relevant_alerts(snapshot: Snapshot, settings: Settings) -> list[str]:
    headers = []
    for alert in snapshot.alerts.values():
        if (
            settings.ROUTE_ID in alert.informed_route_ids
            or settings.HOME_STOP in alert.informed_stop_ids
            or settings.WORK_STOP in alert.informed_stop_ids
        ):
            headers.append(alert.header_text or alert.description_text)
    return headers


def _alerts_line(snapshot: Snapshot, settings: Settings) -> str:
    headers = _relevant_alerts(snapshot, settings)
    if not headers:
        return "📢 Line alerts: none affecting MD-W"
    return "📢 Line alerts:\n" + "\n".join(f"   ⚠️ {h}" for h in headers)


def _backup_lines(
    conn: sqlite3.Connection,
    snapshot: Snapshot,
    settings: Settings,
    service_date: date,
    stop_id: str,
    direction_id: int,
    exclude_trip_id: str,
    target_raw: str,
    n_before: int,
    n_after: int,
) -> list[str]:
    active = active_service_ids(conn, service_date)
    departures = [d for d in list_departures(conn, stop_id, direction_id, active) if d["trip_id"] != exclude_trip_id]
    target_sec = parse_gtfs_time_to_seconds(target_raw)

    prior = [d for d in departures if parse_gtfs_time_to_seconds(d["departure_time"]) < target_sec][-n_before:]
    after = [d for d in departures if parse_gtfs_time_to_seconds(d["departure_time"]) >= target_sec][:n_after]

    lines = []
    for d in prior + after:
        entry = snapshot.trip_updates.get(d["trip_id"])
        delay_sec = stop_delay(entry, stop_id)
        glyph = delay_glyph(delay_sec, entry.is_annulled if entry else False)
        status = "on time" if not delay_sec else f"{delay_sec // 60:+d} min"
        lines.append(f"   #{d['train_no']} · {_fmt_time(d['departure_time'], service_date, settings)} · {status} {glyph}")
    return lines


def build_morning_briefing(
    conn: sqlite3.Connection, snapshot: Snapshot, settings: Settings, service_date: date, delayed_tag: bool = False
) -> str:
    result = resolve_morning(conn, service_date, settings.MORNING_TRAIN, settings.HOME_STOP)
    title = "🌅 Morning Briefing — MD-W → Chicago" + (" (delayed briefing)" if delayed_tag else "")
    if isinstance(result, NoService):
        return f"{title}\n━━━━━━━━━━━━━━━━━━━━━\n{HOLIDAY_MESSAGE}"

    home = result.stop_time_for(settings.HOME_STOP)
    entry = snapshot.trip_updates.get(result.trip_id)
    delay_sec = stop_delay(entry, settings.HOME_STOP)
    glyph = delay_glyph(delay_sec, entry.is_annulled if entry else False)

    lines = [title, "━━━━━━━━━━━━━━━━━━━━━", f"🚆 Train #{result.train_no} @ {settings.HOME_STOP}"]
    sched_str = _fmt_time(home.departure_time if home else None, service_date, settings)
    if entry is None:
        lines.append(f"   Scheduled {sched_str} — no live data, assuming on time {glyph}")
    elif delay_sec:
        est_str = _estimated_time(home.departure_time, delay_sec, service_date, settings)
        lines.append(f"   Scheduled {sched_str} → Estimated {est_str}  ({delay_sec // 60:+d} min) {glyph}")
    else:
        lines.append(f"   Scheduled {sched_str} — on time {glyph}")

    pos = snapshot.positions.get(result.trip_id)
    if pos and pos.current_stop_id:
        near = _stop_name(conn, pos.current_stop_id)
        lines.append(f"   Now near: {near} · heading inbound")

    backups = _backup_lines(
        conn, snapshot, settings, service_date, settings.HOME_STOP, MORNING_DIRECTION_ID,
        result.trip_id, home.departure_time, n_before=1, n_after=1,
    )
    if backups:
        lines.append("")
        lines.append(f"🔁 Backups from {settings.HOME_STOP}:")
        lines.extend(backups)

    lines.append("")
    lines.append(_alerts_line(snapshot, settings))
    lines.append(f"🗺 Live map: {settings.CORS_ORIGIN}")
    return "\n".join(lines)


def build_evening_briefing(
    conn: sqlite3.Connection, snapshot: Snapshot, settings: Settings, service_date: date, delayed_tag: bool = False
) -> str:
    result = resolve_evening(conn, service_date, settings.EVENING_DEPART_CUS, settings.WORK_STOP)
    title = "🌆 Evening Briefing — MD-W → Roselle" + (" (delayed briefing)" if delayed_tag else "")
    if isinstance(result, NoService):
        return f"{title}\n━━━━━━━━━━━━━━━━━━━━━\n{HOLIDAY_MESSAGE}"

    work = result.stop_time_for(settings.WORK_STOP)
    entry = snapshot.trip_updates.get(result.trip_id)
    delay_sec = stop_delay(entry, settings.WORK_STOP)
    glyph = delay_glyph(delay_sec, entry.is_annulled if entry else False)

    lines = [title, "━━━━━━━━━━━━━━━━━━━━━", f"🚆 Train #{result.train_no} @ {settings.WORK_STOP}"]
    sched_str = _fmt_time(work.departure_time if work else None, service_date, settings)
    if entry is None:
        lines.append(f"   Scheduled {sched_str} — no live data, assuming on time {glyph}")
    elif delay_sec:
        est_str = _estimated_time(work.departure_time, delay_sec, service_date, settings)
        lines.append(f"   Scheduled {sched_str} → Estimated {est_str}  ({delay_sec // 60:+d} min) {glyph}")
    else:
        lines.append(f"   Scheduled {sched_str} — on time {glyph}")

    pos = snapshot.positions.get(result.trip_id)
    if pos and pos.current_stop_id:
        near = _stop_name(conn, pos.current_stop_id)
        lines.append(f"   Now near: {near} · heading outbound")

    backups = _backup_lines(
        conn, snapshot, settings, service_date, settings.WORK_STOP, EVENING_DIRECTION_ID,
        result.trip_id, work.departure_time, n_before=1, n_after=2,
    )
    if backups:
        lines.append("")
        lines.append(f"🔁 Backups from {settings.WORK_STOP}:")
        lines.extend(backups)

    lines.append("")
    lines.append(_alerts_line(snapshot, settings))
    lines.append(f"🗺 Live map: {settings.CORS_ORIGIN}")
    return "\n".join(lines)


def build_train_status(
    conn: sqlite3.Connection, snapshot: Snapshot, settings: Settings, service_date: date, train_no: str
) -> str:
    active = active_service_ids(conn, service_date)
    if not active:
        return HOLIDAY_MESSAGE
    qmarks = ",".join("?" * len(active))
    row = conn.execute(
        f"SELECT trip_id, trip_short_name FROM trips WHERE trip_short_name = ? AND service_id IN ({qmarks})",
        (train_no, *active),
    ).fetchone()
    if row is None:
        return f"No MD-W train #{train_no} running today."

    stops = conn.execute(
        "SELECT stop_id, departure_time FROM stop_times WHERE trip_id = ? ORDER BY stop_sequence", (row["trip_id"],)
    ).fetchall()
    entry = snapshot.trip_updates.get(row["trip_id"])
    delay_sec = entry.delay_sec if entry else None
    glyph = delay_glyph(delay_sec, entry.is_annulled if entry else False)
    delay_str = f"{delay_sec // 60:+d} min" if delay_sec is not None else "no live data — assuming on time"

    lines = [f"🚆 Train #{train_no} {glyph} — {delay_str}"]
    for s in stops:
        name = _stop_name(conn, s["stop_id"])
        lines.append(f"   {name:<20} sched {_fmt_time(s['departure_time'], service_date, settings)}")
    return "\n".join(lines)


def build_next_departures(
    conn: sqlite3.Connection, snapshot: Snapshot, settings: Settings, service_date: date, now_raw: str
) -> str:
    """Next 3 departures each direction (Roselle inbound / CUS outbound) with live status."""
    active = active_service_ids(conn, service_date)
    now_sec = parse_gtfs_time_to_seconds(now_raw)

    def _next(stop_id: str, direction_id: int) -> list[str]:
        departures = [
            d for d in list_departures(conn, stop_id, direction_id, active)
            if parse_gtfs_time_to_seconds(d["departure_time"]) >= now_sec
        ][:3]
        lines = []
        for d in departures:
            entry = snapshot.trip_updates.get(d["trip_id"])
            delay_sec = stop_delay(entry, stop_id)
            glyph = delay_glyph(delay_sec, entry.is_annulled if entry else False)
            status = "on time" if not delay_sec else f"{delay_sec // 60:+d} min"
            lines.append(f"   #{d['train_no']} · {_fmt_time(d['departure_time'], service_date, settings)} · {status} {glyph}")
        return lines or ["   (none remaining today)"]

    lines = [f"🔜 Next from {settings.HOME_STOP} (inbound):"]
    lines.extend(_next(settings.HOME_STOP, MORNING_DIRECTION_ID))
    lines.append("")
    lines.append(f"🔜 Next from {settings.WORK_STOP} (outbound):")
    lines.extend(_next(settings.WORK_STOP, EVENING_DIRECTION_ID))
    return "\n".join(lines)


def build_stats_message(conn: sqlite3.Connection, settings: Settings) -> str:
    """30-day on-time performance, per train number (design §12 Phase 6)."""
    stats = compute_stats(conn, settings.tzinfo)
    if not stats:
        return "📊 30-Day Stats\n━━━━━━━━━━━━━━━━━━━━━\nNo delay history yet — check back after a few commutes."

    lines = ["📊 30-Day Stats", "━━━━━━━━━━━━━━━━━━━━━"]
    for train_no, s in sorted(stats.items(), key=lambda kv: -kv[1]["n_observations"]):
        glyph = delay_glyph(int(s["avg_delay_sec"]), False)
        lines.append(f"🚆 #{train_no} {glyph}  {s['on_time_pct']}% on time · avg {s['avg_delay_sec'] / 60:+.1f} min ({s['n_observations']} obs)")
        by_weekday = s["avg_delay_by_weekday"]
        if by_weekday:
            weekday_str = " · ".join(
                f"{wd[:3]} {mins / 60:+.1f}m" for wd, mins in sorted(by_weekday.items(), key=lambda kv: _WEEKDAY_ORDER[kv[0]])
            )
            lines.append(f"   {weekday_str}")
    return "\n".join(lines)


_WEEKDAY_ORDER = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}
