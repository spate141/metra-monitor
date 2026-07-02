"""Stats aggregation tests (design §12 Phase 6 `/stats` command)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.briefings.builder import build_stats_message
from app.core.stats import compute_stats
from app.db import connect, init_schema

TZ = ZoneInfo("America/Chicago")


def _make_db(tmp_path):
    conn = connect(tmp_path / "stats.db")
    init_schema(conn)
    return conn


def _insert(conn, train_no, ts, delay_sec):
    conn.execute(
        "INSERT INTO delay_history (ts, trip_id, train_no, stop_id, delay_sec, source) VALUES (?, ?, ?, ?, ?, ?)",
        (ts, f"trip-{train_no}", train_no, "ROSELLE", delay_sec, "test"),
    )
    conn.commit()


def test_compute_stats_empty(tmp_path):
    conn = _make_db(tmp_path)
    assert compute_stats(conn, TZ) == {}


def test_compute_stats_basic(tmp_path):
    conn = _make_db(tmp_path)
    now = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)  # a Monday
    _insert(conn, "2222", now.isoformat(), 60)
    _insert(conn, "2222", (now - timedelta(days=1)).isoformat(), 600)  # major delay, Sunday
    _insert(conn, "2222", (now - timedelta(days=40)).isoformat(), 9999)  # outside 30-day window

    stats = compute_stats(conn, TZ, now=now)
    assert set(stats.keys()) == {"2222"}
    s = stats["2222"]
    assert s["n_observations"] == 2
    assert s["on_time_pct"] == 50.0
    assert s["avg_delay_sec"] == 330.0


def test_build_stats_message_no_data(tmp_path):
    from app.config import Settings

    conn = _make_db(tmp_path)
    settings = Settings(_env_file=None)
    text = build_stats_message(conn, settings)
    assert "No delay history yet" in text


def test_build_stats_message_with_data(tmp_path):
    from app.config import Settings

    conn = _make_db(tmp_path)
    now = datetime.now(timezone.utc)
    _insert(conn, "2222", now.isoformat(), 60)
    settings = Settings(_env_file=None)
    text = build_stats_message(conn, settings)
    assert "#2222" in text
    assert "on time" in text
