"""delay_history is a per-trip record, not a per-poll log."""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.config import Settings
from app.core.models import NoService, ResolvedTrip, StopTime
from app.db import connect, init_schema
from app.realtime.loop import _record_delay_history
from app.realtime.state_store import Snapshot, TripUpdateEntry

SERVICE_DATE = date(2026, 7, 8)


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        HOME_STOP="ROSELLE", WORK_STOP="CUS", MORNING_TRAIN="2222",
        EVENING_DEPART_CUS="16:05", CORS_ORIGIN="https://example.test",
        METRA_DB_PATH=str(tmp_path / "history.db"),
    )


def _resolved():
    morning = ResolvedTrip(
        SERVICE_DATE, "morning", "TRIP_MORNING", "2222", [StopTime("ROSELLE", 1, None, "07:39:00")]
    )
    return {"morning": morning, "evening": NoService(SERVICE_DATE, "evening")}


def _snapshot(delay_sec, stop_id="ROSELLE"):
    entry = TripUpdateEntry(
        trip_id="TRIP_MORNING",
        delay_sec=delay_sec,
        stop_time_updates=[{"stop_id": stop_id, "arrival_delay": delay_sec, "departure_delay": None}],
    )
    return Snapshot(fetched_at=datetime.now(timezone.utc), trip_updates={"TRIP_MORNING": entry})


def _rows(settings):
    conn = connect(settings.db_path)
    try:
        return conn.execute(
            "SELECT service_date, train_no, stop_id, delay_sec FROM delay_history"
        ).fetchall()
    finally:
        conn.close()


def _init(settings):
    conn = connect(settings.db_path)
    init_schema(conn)
    conn.close()


def test_repeated_polls_collapse_to_one_row(tmp_path):
    settings = _settings(tmp_path)
    _init(settings)
    now = datetime(2026, 7, 8, 7, 30, tzinfo=settings.tzinfo)

    for delay in (0, 120, 300):
        _record_delay_history(settings, _resolved(), _snapshot(delay), now)

    rows = _rows(settings)
    assert len(rows) == 1
    assert rows[0]["delay_sec"] == 300  # last reading wins
    assert rows[0]["service_date"] == "2026-07-08"
    assert rows[0]["train_no"] == "2222"


def test_no_row_when_our_stop_is_not_in_the_feed(tmp_path):
    """Train already passed ROSELLE -- record nothing rather than the next stop's delay."""
    settings = _settings(tmp_path)
    _init(settings)
    now = datetime(2026, 7, 8, 7, 55, tzinfo=settings.tzinfo)

    _record_delay_history(settings, _resolved(), _snapshot(600, stop_id="BENSENVIL"), now)

    assert _rows(settings) == []


def test_no_row_when_delay_unknown(tmp_path):
    settings = _settings(tmp_path)
    _init(settings)
    now = datetime(2026, 7, 8, 7, 30, tzinfo=settings.tzinfo)

    _record_delay_history(settings, _resolved(), _snapshot(None), now)

    assert _rows(settings) == []


def test_migration_drops_prefix_delay_history(tmp_path):
    """Old-schema rows are all fabricated zeros -- init_schema must discard them."""
    db = tmp_path / "old.db"
    conn = connect(db)
    conn.executescript(
        "CREATE TABLE delay_history (ts TEXT NOT NULL, trip_id TEXT NOT NULL, train_no TEXT, "
        "stop_id TEXT, delay_sec INTEGER, source TEXT);"
    )
    conn.execute(
        "INSERT INTO delay_history (ts, trip_id, train_no, stop_id, delay_sec, source) VALUES (?,?,?,?,?,?)",
        ("2026-07-08T12:00:00+00:00", "TRIP1", "2222", "ROSELLE", 0, "realtime"),
    )
    conn.commit()

    init_schema(conn)

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(delay_history)")}
    assert "service_date" in cols
    assert conn.execute("SELECT COUNT(*) AS n FROM delay_history").fetchone()["n"] == 0
    conn.close()


def test_migration_leaves_new_schema_rows_alone(tmp_path):
    db = tmp_path / "new.db"
    conn = connect(db)
    init_schema(conn)
    conn.execute(
        "INSERT INTO delay_history (service_date, trip_id, stop_id, ts, train_no, delay_sec, source) "
        "VALUES (?,?,?,?,?,?,?)",
        ("2026-07-08", "TRIP1", "ROSELLE", "2026-07-08T12:00:00+00:00", "2222", 300, "realtime"),
    )
    conn.commit()

    init_schema(conn)  # idempotent -- a restart must not wipe real history

    assert conn.execute("SELECT COUNT(*) AS n FROM delay_history").fetchone()["n"] == 1
    conn.close()
