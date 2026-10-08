"""Static ingestor tests: derived/operational state must survive a schedule
rebuild, since `_build_db` only populates static GTFS tables in the fresh temp
DB and `ingest()` atomically swaps the whole file into place.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.db import connect, init_schema, set_meta
from app.ingest.static_ingestor import _build_db, _copy_operational_state, _train_no_from_trip_id


def test_copy_operational_state_preserves_meta_and_history(tmp_path):
    old_path = tmp_path / "old.db"
    new_path = tmp_path / "new.db"

    old_conn = connect(old_path)
    init_schema(old_conn)
    set_meta(old_conn, "notification_mode", "all")
    set_meta(old_conn, "paused_until", "2026-07-08")
    old_conn.execute(
        "INSERT INTO delay_history (service_date, trip_id, stop_id, ts, train_no, delay_sec, source) "
        "VALUES (?,?,?,?,?,?,?)",
        ("2026-07-08", "TRIP1", "ROSELLE", "2026-07-08T12:00:00+00:00", "2222", 120, "realtime"),
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


@pytest.mark.parametrize(
    "trip_id,expected",
    [
        ("MD-W_MW2225_V2_A", "2225"),  # legacy format
        ("MD-W_2225_8_2009_5682524", "2225"),  # format from Metra's 10/1/2026 change notice
        ("BNSF_1200_8_2009_5682524", "1200"),
        ("MD-W_MWWX01_V1_B", None),  # legacy special train: no number, unchanged behavior
        ("garbage", None),
    ],
)
def test_train_no_from_trip_id(trip_id, expected):
    assert _train_no_from_trip_id(trip_id) == expected


def _zip(files: dict[str, str]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    buf.seek(0)
    return zipfile.ZipFile(buf)


# Minimal package in the future layout: new trip/service/shape ID formats, no spaces
# after commas, the four removed stop_times columns absent, pickup_type=3, and the
# five added files (extensions header-only).
NEW_FORMAT_FEED = {
    "routes.txt": "route_id,route_short_name,route_long_name,route_color,route_text_color\n"
                  "MD-W,MD-W,Milwaukee District West,F3A900,000000\n",
    "trips.txt": "route_id,service_id,trip_id,trip_headsign,block_id,shape_id,direction_id\n"
                 "MD-W,8_2009,MD-W_2225_8_2009_5682524,Chicago,5682524,1685230199,1\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type\n"
                      "MD-W_2225_8_2009_5682524,07:00:00,07:00:00,ROSELLE,1,0,0\n"
                      "MD-W_2225_8_2009_5682524,07:40:00,07:40:00,CUS,2,3,3\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                 "ROSELLE,Roselle,41.98,-88.07\nCUS,Chicago Union Station,41.87,-87.64\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                    "8_2009,1,1,1,1,1,0,0,20260608,20270530\n",
    "calendar_dates.txt": "service_id,date,exception_type\n8_2009,20261126,2\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n1685230199,41.98,-88.07,1\n",
    "feed_info.txt": "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date,feed_version\n"
                     "Metra,http://www.metrarail.com/,EN,20260608,20270530,08-JUN-2026--30-MAY-2027\n",
    "transfers.txt": "from_stop_id,to_stop_id,transfer_type,min_transfer_time,from_route_id,to_route_id,"
                     "from_trip_id,to_trip_id\n",
    "notes.txt": "note_id,label,description\n",
    "note_links.txt": "note_id,trip_ids,stop_ids,stop_sequence\n",
    "trip_flexfields.txt": "trip_id,field_code,field_type,value\n",
}


def test_build_db_accepts_new_gtfs_format(tmp_path):
    out = tmp_path / "new.db"
    _build_db(_zip(NEW_FORMAT_FEED), "MD-W", out)

    conn = connect(out)
    try:
        trip = conn.execute("SELECT * FROM trips").fetchone()
        assert trip["trip_id"] == "MD-W_2225_8_2009_5682524"
        assert trip["trip_short_name"] == "2225"
        assert trip["service_id"] == "8_2009"
        assert conn.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0] == 2
        assert conn.execute("SELECT service_id FROM calendar").fetchone()[0] == "8_2009"
        # Numeric-looking shape_id must survive as text, not be coerced.
        assert conn.execute("SELECT shape_id FROM shapes").fetchone()[0] == "1685230199"
    finally:
        conn.close()


def test_build_db_fails_when_no_train_numbers_parse(tmp_path):
    feed = dict(NEW_FORMAT_FEED)
    feed["trips.txt"] = "route_id,service_id,trip_id,direction_id\nMD-W,8_2009,UNPARSEABLE,1\n"
    with pytest.raises(ValueError, match="trip_id format"):
        _build_db(_zip(feed), "MD-W", tmp_path / "bad.db")
