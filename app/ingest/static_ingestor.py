"""Static schedule ingestor (design §4.1, §8.3).

Polls `published.txt` for a timestamp change (or runs on cold start), downloads
`schedule.zip`, and loads a MD-W-filtered subset into SQLite. Rebuild is atomic:
we build into a temp DB file and swap it into place with os.replace so a mid-ingest
crash never leaves a half-populated schedule (design §8.3, edge case #3).

Real-feed quirks this module works around (verified against a live schedule.zip):
- `trips.txt` has no `trip_short_name` column. The train number is embedded in
  `trip_id`, e.g. "MD-W_MW2225_V2_A" -> 2225. We extract it with a regex.
- CSV fields are comma-space separated (", ") -- every field needs `.strip()`.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import sqlite3
import zipfile
from pathlib import Path

import httpx

from app.config import Settings
from app.db import connect, init_schema, set_meta

logger = logging.getLogger(__name__)

TRAIN_NO_RE = re.compile(r"_MW(\d+)_")


def _train_no_from_trip_id(trip_id: str) -> str | None:
    m = TRAIN_NO_RE.search(trip_id)
    return m.group(1) if m else None


def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with zf.open(name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig")
        reader = csv.DictReader(text)
        reader.fieldnames = [fn.strip() for fn in (reader.fieldnames or [])]
        rows = []
        for row in reader:
            rows.append({k: (v.strip() if v is not None else v) for k, v in row.items()})
        return rows


def fetch_published_timestamp(settings: Settings, client: httpx.Client) -> str:
    resp = client.get(f"{settings.METRA_STATIC_BASE}/published.txt", timeout=15)
    resp.raise_for_status()
    return resp.text.strip()


def needs_rebuild(settings: Settings, db_path: Path, client: httpx.Client) -> tuple[bool, str]:
    """Returns (should_rebuild, current_published_ts)."""
    published_ts = fetch_published_timestamp(settings, client)
    if not db_path.exists():
        return True, published_ts
    conn = connect(db_path)
    try:
        init_schema(conn)
        row = conn.execute("SELECT value FROM meta WHERE key = 'published_ts'").fetchone()
        stored = row["value"] if row else None
    finally:
        conn.close()
    return (stored != published_ts), published_ts


def _download_schedule_zip(settings: Settings, client: httpx.Client) -> zipfile.ZipFile:
    resp = client.get(f"{settings.METRA_STATIC_BASE}/schedule.zip", timeout=60)
    resp.raise_for_status()
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _build_db(zf: zipfile.ZipFile, route_id: str, out_path: Path) -> None:
    if out_path.exists():
        out_path.unlink()
    conn = connect(out_path)
    init_schema(conn)

    routes = [r for r in _read_csv(zf, "routes.txt") if r["route_id"] == route_id]
    conn.executemany(
        "INSERT INTO routes (route_id, route_short_name, route_long_name, route_color, route_text_color) "
        "VALUES (?,?,?,?,?)",
        [
            (
                r["route_id"],
                r["route_short_name"],
                r["route_long_name"],
                (f"#{r['route_color']}" if r.get("route_color") else None),
                (f"#{r['route_text_color']}" if r.get("route_text_color") else None),
            )
            for r in routes
        ],
    )

    all_trips = _read_csv(zf, "trips.txt")
    trips = [t for t in all_trips if t["route_id"] == route_id]
    trip_ids = {t["trip_id"] for t in trips}
    conn.executemany(
        "INSERT INTO trips (trip_id, route_id, service_id, trip_short_name, direction_id, trip_headsign) "
        "VALUES (?,?,?,?,?,?)",
        [
            (
                t["trip_id"],
                t["route_id"],
                t["service_id"],
                _train_no_from_trip_id(t["trip_id"]),
                int(t["direction_id"]) if t.get("direction_id") not in (None, "") else None,
                t.get("trip_headsign"),
            )
            for t in trips
        ],
    )
    service_ids = {t["service_id"] for t in trips}

    stop_ids: set[str] = set()
    batch = []
    for row in _read_csv(zf, "stop_times.txt"):
        if row["trip_id"] not in trip_ids:
            continue
        stop_ids.add(row["stop_id"])
        batch.append(
            (
                row["trip_id"],
                row["stop_id"],
                int(row["stop_sequence"]),
                row.get("arrival_time"),
                row.get("departure_time"),
            )
        )
    conn.executemany(
        "INSERT OR REPLACE INTO stop_times (trip_id, stop_id, stop_sequence, arrival_time, departure_time) "
        "VALUES (?,?,?,?,?)",
        batch,
    )

    stops = [s for s in _read_csv(zf, "stops.txt") if s["stop_id"] in stop_ids]
    conn.executemany(
        "INSERT INTO stops (stop_id, stop_name, stop_lat, stop_lon) VALUES (?,?,?,?)",
        [
            (s["stop_id"], s["stop_name"], float(s["stop_lat"]) if s["stop_lat"] else None,
             float(s["stop_lon"]) if s["stop_lon"] else None)
            for s in stops
        ],
    )

    calendar = [c for c in _read_csv(zf, "calendar.txt") if c["service_id"] in service_ids]
    conn.executemany(
        "INSERT INTO calendar (service_id, monday, tuesday, wednesday, thursday, friday, "
        "saturday, sunday, start_date, end_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                c["service_id"],
                int(c["monday"]), int(c["tuesday"]), int(c["wednesday"]), int(c["thursday"]),
                int(c["friday"]), int(c["saturday"]), int(c["sunday"]),
                c["start_date"], c["end_date"],
            )
            for c in calendar
        ],
    )

    cal_dates = [c for c in _read_csv(zf, "calendar_dates.txt") if c["service_id"] in service_ids]
    conn.executemany(
        "INSERT INTO calendar_dates (service_id, date, exception_type) VALUES (?,?,?)",
        [(c["service_id"], c["date"], int(c["exception_type"])) for c in cal_dates],
    )

    shape_ids = {t["shape_id"] for t in trips if t.get("shape_id")}
    if shape_ids and "shapes.txt" in zf.namelist():
        shapes = [s for s in _read_csv(zf, "shapes.txt") if s["shape_id"] in shape_ids]
        conn.executemany(
            "INSERT INTO shapes (shape_id, shape_pt_sequence, shape_pt_lat, shape_pt_lon) VALUES (?,?,?,?)",
            [
                (s["shape_id"], int(s["shape_pt_sequence"]), float(s["shape_pt_lat"]), float(s["shape_pt_lon"]))
                for s in shapes
            ],
        )

    conn.commit()
    conn.close()
    logger.info(
        "static ingest built: %d trips, %d stop_times rows, %d stops, %d service_ids",
        len(trips), len(batch), len(stops), len(service_ids),
    )


def ingest(settings: Settings, force: bool = False) -> bool:
    """Run the static ingest if published.txt changed (or force=True). Returns True if rebuilt."""
    db_path = settings.db_path
    with httpx.Client() as client:
        should_rebuild, published_ts = needs_rebuild(settings, db_path, client)
        if not should_rebuild and not force:
            logger.info("schedule unchanged (published %s) -- skipping rebuild", published_ts)
            return False

        logger.info("rebuilding schedule DB (published %s)", published_ts)
        zf = _download_schedule_zip(settings, client)

    tmp_path = db_path.with_suffix(".tmp.db")
    _build_db(zf, settings.ROUTE_ID, tmp_path)

    conn = connect(tmp_path)
    set_meta(conn, "published_ts", published_ts)
    conn.close()

    tmp_path.replace(db_path)  # atomic swap (design §8.3)
    logger.info("schedule DB swapped into place at %s", db_path)
    return True


def resolve_stop_id(db_path: Path, name_or_id: str) -> str | None:
    """Resolve a configured HOME_STOP/WORK_STOP value to a stop_id.

    Accepts either an exact stop_id (e.g. 'ROSELLE') or falls back to a
    case-insensitive stop_name match.
    """
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT stop_id FROM stops WHERE stop_id = ?", (name_or_id,)).fetchone()
        if row:
            return row["stop_id"]
        row = conn.execute(
            "SELECT stop_id FROM stops WHERE lower(stop_name) = lower(?)", (name_or_id,)
        ).fetchone()
        return row["stop_id"] if row else None
    finally:
        conn.close()
