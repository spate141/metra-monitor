"""Static ingestor tests: derived/operational state must survive a schedule
rebuild, since `_build_db` only populates static GTFS tables in the fresh temp
DB and `ingest()` atomically swaps the whole file into place.
"""
from __future__ import annotations

from app.db import connect, init_schema, set_meta
from app.ingest.static_ingestor import _copy_operational_state


def test_copy_operational_state_preserves_meta_and_history(tmp_path):
    old_path = tmp_path / "old.db"
    new_path = tmp_path / "new.db"

    old_conn = connect(old_path)
    init_schema(old_conn)
    set_meta(old_conn, "notification_mode", "all")
    set_meta(old_conn, "paused_until", "2026-07-08")
    old_conn.execute(
        "INSERT INTO delay_history (ts, trip_id, train_no, stop_id, delay_sec, source) VALUES (?,?,?,?,?,?)",
        ("2026-07-08T12:00:00+00:00", "TRIP1", "2222", "ROSELLE", 120, "realtime"),
    )
    old_conn.execute(
        "INSERT INTO alert_fingerprints (fingerprint, first_seen, last_sent) VALUES (?,?,?)",
        ("fp1", "2026-07-08T12:00:00+00:00", "2026-07-08T12:00:00+00:00"),
    )
    old_conn.commit()
    old_conn.close()

    new_conn = connect(new_path)
    init_schema(new_conn)
    _copy_operational_state(old_path, new_conn)

    assert new_conn.execute("SELECT value FROM meta WHERE key='notification_mode'").fetchone()["value"] == "all"
    assert new_conn.execute("SELECT value FROM meta WHERE key='paused_until'").fetchone()["value"] == "2026-07-08"
    assert new_conn.execute("SELECT COUNT(*) AS n FROM delay_history").fetchone()["n"] == 1
    assert new_conn.execute("SELECT COUNT(*) AS n FROM alert_fingerprints").fetchone()["n"] == 1
    new_conn.close()


def test_copy_operational_state_noop_when_old_db_missing(tmp_path):
    new_path = tmp_path / "new.db"
    new_conn = connect(new_path)
    init_schema(new_conn)
    _copy_operational_state(tmp_path / "does_not_exist.db", new_conn)
    assert new_conn.execute("SELECT COUNT(*) AS n FROM meta").fetchone()["n"] == 0
    new_conn.close()
