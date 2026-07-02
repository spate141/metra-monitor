"""SQLite connection + schema bootstrap (design §7). Single file, WAL mode.

Static GTFS tables are rebuilt atomically by the ingestor (build into a temp file,
then swap) -- see ingest/static_ingestor.py. This module only owns the schema DDL
and a couple of small connection helpers used across the app.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
-- Static schedule (MD-W filtered), rebuilt whenever published.txt changes.
CREATE TABLE IF NOT EXISTS routes (
    route_id TEXT PRIMARY KEY,
    route_short_name TEXT,
    route_long_name TEXT
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    trip_short_name TEXT,       -- train number
    direction_id INTEGER,
    trip_headsign TEXT
);

CREATE TABLE IF NOT EXISTS stop_times (
    trip_id TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    stop_sequence INTEGER NOT NULL,
    arrival_time TEXT,          -- raw GTFS HH:MM:SS, may exceed 24:00:00
    departure_time TEXT,
    PRIMARY KEY (trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_stop_times_trip ON stop_times(trip_id);
CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times(stop_id);

CREATE TABLE IF NOT EXISTS stops (
    stop_id TEXT PRIMARY KEY,
    stop_name TEXT,
    stop_lat REAL,
    stop_lon REAL
);

CREATE TABLE IF NOT EXISTS calendar (
    service_id TEXT PRIMARY KEY,
    monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER,
    friday INTEGER, saturday INTEGER, sunday INTEGER,
    start_date TEXT, end_date TEXT
);

CREATE TABLE IF NOT EXISTS calendar_dates (
    service_id TEXT NOT NULL,
    date TEXT NOT NULL,
    exception_type INTEGER NOT NULL,   -- 1 = added, 2 = removed
    PRIMARY KEY (service_id, date)
);

CREATE TABLE IF NOT EXISTS shapes (
    shape_id TEXT NOT NULL,
    shape_pt_sequence INTEGER NOT NULL,
    shape_pt_lat REAL,
    shape_pt_lon REAL,
    PRIMARY KEY (shape_id, shape_pt_sequence)
);

-- Derived / operational tables (not wiped on schedule rebuild)
CREATE TABLE IF NOT EXISTS resolved_trips (
    service_date TEXT NOT NULL,
    slot TEXT NOT NULL,             -- 'morning' | 'evening'
    trip_id TEXT,
    train_no TEXT,
    scheduled_times_json TEXT,      -- per-stop scheduled times, JSON
    PRIMARY KEY (service_date, slot)
);

CREATE TABLE IF NOT EXISTS delay_history (
    ts TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    train_no TEXT,
    stop_id TEXT,
    delay_sec INTEGER,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_delay_history_trip ON delay_history(trip_id, ts);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def get_conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        init_schema(conn)
        yield conn
    finally:
        conn.close()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def briefing_already_sent(conn: sqlite3.Connection, slot: str, service_date) -> bool:
    """Cold-start grace bookkeeping (design §8.7): has today's `slot` briefing gone out?"""
    return get_meta(conn, f"briefing_sent:{slot}:{service_date.isoformat()}") == "1"


def mark_briefing_sent(conn: sqlite3.Connection, slot: str, service_date) -> None:
    set_meta(conn, f"briefing_sent:{slot}:{service_date.isoformat()}", "1")
