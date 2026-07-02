"""30-day on-time performance stats, shared by the public API (`/api/v1/stats`)
and the Telegram `/stats` command so both surfaces report the same numbers.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ON_TIME_THRESHOLD_SEC = 120
STATS_WINDOW_DAYS = 30


def compute_stats(conn: sqlite3.Connection, tzinfo: ZoneInfo, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=STATS_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT train_no, ts, delay_sec FROM delay_history WHERE ts >= ? AND delay_sec IS NOT NULL", (cutoff,)
    ).fetchall()

    by_train: dict[str, list] = {}
    for r in rows:
        by_train.setdefault(r["train_no"], []).append(r)

    result = {}
    for train_no, obs in by_train.items():
        on_time = sum(1 for o in obs if o["delay_sec"] <= ON_TIME_THRESHOLD_SEC)
        by_weekday: dict[str, list[int]] = {}
        for o in obs:
            weekday = datetime.fromisoformat(o["ts"]).astimezone(tzinfo).strftime("%A")
            by_weekday.setdefault(weekday, []).append(o["delay_sec"])
        result[train_no] = {
            "n_observations": len(obs),
            "on_time_pct": round(100 * on_time / len(obs), 1),
            "avg_delay_sec": round(sum(o["delay_sec"] for o in obs) / len(obs), 1),
            "avg_delay_by_weekday": {
                wd: round(sum(vals) / len(vals), 1) for wd, vals in by_weekday.items()
            },
        }
    return result
