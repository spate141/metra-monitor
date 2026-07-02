"""Trip resolution tests (design §11 Phase 1 exit criteria).

`test_resolve_morning_*` / `test_resolve_evening_*` run against a *real*
`schedule.zip` downloaded from Metra's public static endpoint -- per the design's
exit criteria ("unit tests ... pass against a real schedule.zip"). They're
network tests; skipped automatically if the endpoint is unreachable.

The holiday/no-service test uses a small synthetic DB instead of live data,
because the currently published schedule has no *weekday* holiday exception to
pin a date to (holidays that fall on weekends need no calendar_dates override).
That keeps the no-service branch covered deterministically, independent of
which holidays happen to be in the feed when this runs.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.core.models import NoService, ResolvedTrip
from app.core.trip_resolver import active_service_ids, resolve_evening, resolve_morning, resolve_today
from app.db import connect, init_schema
from app.ingest.static_ingestor import ingest


def _metra_reachable() -> bool:
    try:
        httpx.get("https://schedules.metrarail.com/gtfs/published.txt", timeout=5).raise_for_status()
        return True
    except httpx.HTTPError:
        return False


requires_network = pytest.mark.skipif(not _metra_reachable(), reason="Metra static endpoint unreachable")


@pytest.fixture(scope="module")
def real_db(tmp_path_factory) -> Path:
    """A real MD-W-filtered schedule DB, ingested once and reused across tests."""
    db_path = tmp_path_factory.mktemp("metra") / "metra.db"
    settings = Settings(METRA_DB_PATH=str(db_path))
    ingest(settings, force=True)
    return db_path


# A Wednesday confirmed (at design time) to fall inside a normal weekday service
# period in the live schedule -- see plan verification notes.
A_WEEKDAY = date(2026, 7, 8)


@requires_network
def test_resolve_morning_train_2222_on_weekday(real_db: Path):
    conn = connect(real_db)
    try:
        result = resolve_morning(conn, A_WEEKDAY, "2222", "ROSELLE")
    finally:
        conn.close()
    assert isinstance(result, ResolvedTrip)
    assert result.train_no == "2222"
    roselle = result.stop_time_for("ROSELLE")
    assert roselle is not None
    assert roselle.departure_time is not None


@requires_network
def test_resolve_evening_cus_departure_never_hardcoded(real_db: Path):
    """The evening train is resolved by CUS departure time, not a fixed train number --
    Metra renumbers between schedule seasons (verified live: currently train 2225,
    not 2222). This test only asserts *some* trip resolves at the configured time.
    """
    conn = connect(real_db)
    try:
        result = resolve_evening(conn, A_WEEKDAY, "16:05", "CUS")
    finally:
        conn.close()
    assert isinstance(result, ResolvedTrip)
    cus = result.stop_time_for("CUS")
    assert cus is not None
    assert cus.departure_time == "16:05:00"


@requires_network
def test_resolve_today_persists_resolved_trips(real_db: Path):
    settings = Settings(METRA_DB_PATH=str(real_db))
    result = resolve_today(
        real_db, A_WEEKDAY, settings.MORNING_TRAIN, settings.EVENING_DEPART_CUS,
        settings.HOME_STOP, settings.WORK_STOP,
    )
    assert isinstance(result["morning"], ResolvedTrip)
    assert isinstance(result["evening"], ResolvedTrip)

    conn = connect(real_db)
    try:
        rows = conn.execute(
            "SELECT slot, trip_id, train_no FROM resolved_trips WHERE service_date = ?",
            (A_WEEKDAY.isoformat(),),
        ).fetchall()
    finally:
        conn.close()
    slots = {r["slot"]: r["train_no"] for r in rows}
    assert slots.get("morning") == "2222"
    assert slots.get("evening") is not None


# --- synthetic no-service / holiday test (design §8.1, edge case #1) ---

def _build_synthetic_no_service_db(db_path: Path) -> None:
    conn = connect(db_path)
    init_schema(conn)
    # A single service_id that runs on no day of the week at all (e.g. a
    # standalone "holiday" calendar entry with every weekday flag off), so
    # active_service_ids() returns empty for our target date regardless of
    # which weekday it happens to be.
    conn.execute(
        "INSERT INTO calendar (service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date) VALUES ('HOL', 0,0,0,0,0,0,0, '20260101', '20261231')"
    )
    conn.commit()
    conn.close()


def test_no_service_on_holiday(tmp_path):
    db_path = tmp_path / "holiday.db"
    _build_synthetic_no_service_db(db_path)
    conn = connect(db_path)
    try:
        target = date(2026, 12, 25)  # Friday, Christmas -- no active service in this synthetic DB
        assert active_service_ids(conn, target) == set()

        morning = resolve_morning(conn, target, "2222", "ROSELLE")
        evening = resolve_evening(conn, target, "16:05", "CUS")
    finally:
        conn.close()

    assert isinstance(morning, NoService)
    assert isinstance(evening, NoService)
