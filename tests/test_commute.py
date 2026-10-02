from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import sqlite3

import pytest

from app.api.commute import build_commute
from app.db import SCHEMA
from app.realtime.state_store import Snapshot, TripUpdateEntry


@pytest.fixture
def setup():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executemany('INSERT INTO stops(stop_id,stop_name) VALUES (?,?)', [('R','Roselle'),('C','Union Station')])
    conn.execute("INSERT INTO calendar VALUES ('weekday',1,1,1,1,1,0,0,'20260101','20261231')")
    for tid, number, direction, start, end in [('am','2222',1,'07:48:00','08:27:00'),('early','2221',0,'14:30:00','15:17:00'),('pm','2225',0,'16:05:00','16:50:00'),('night','999',0,'25:00:00','25:45:00')]:
        conn.execute('INSERT INTO trips VALUES (?,?,?,?,?,?)',(tid,'MD-W','weekday',number,direction,''))
        a,b = ('R','C') if direction else ('C','R')
        conn.executemany('INSERT INTO stop_times VALUES (?,?,?,?,?)',[(tid,a,1,start,start),(tid,b,2,end,end)])
    settings = SimpleNamespace(TZ='America/Chicago',tzinfo=ZoneInfo('America/Chicago'),ROUTE_ID='MD-W',HOME_STOP='R',WORK_STOP='C',MORNING_TRAIN='2222',EVENING_DEPART_CUS='16:05',EARLY_EVENING_DEPART_CUS='14:30',has_realtime=True)
    now = datetime(2026,10,1,12,tzinfo=settings.tzinfo)
    return conn,settings,Snapshot(fetched_at=now),now


def test_three_preferences_and_timezone(setup):
    result = build_commute(*setup)
    assert [p['trip']['train_no'] for p in result['preferred']] == ['2222','2221','2225']
    assert result['preferred'][0]['trip']['arrival']['scheduled'] == '2026-10-01T08:27:00-05:00'
    assert result['preferred'][0]['trip']['departure']['estimated'] is None
    assert result['departures']['evening'][-1]['departure']['scheduled'] == '2026-10-02T01:00:00-05:00'


def test_stop_estimate_cancellation_and_fallback(setup):
    conn,settings,snapshot,now=setup
    snapshot.trip_updates['am']=TripUpdateEntry('am',300,[{'stop_id':'R','departure_delay':120,'arrival_delay':100}],True)
    trip=build_commute(*setup)['preferred'][0]['trip']
    assert trip['is_cancelled']
    assert trip['departure']['estimated']=='2026-10-01T07:50:00-05:00'
    assert trip['departure']['estimate_source']=='stop'
    assert trip['arrival']['estimate_source']=='trip'


def test_weekend_holiday_and_missing_destination(setup):
    conn,settings,snapshot,now=setup
    weekend=build_commute(conn,settings,snapshot,now.replace(day=3))
    assert all(p['trip'] is None for p in weekend['preferred'])
    conn.execute("INSERT INTO calendar_dates VALUES ('weekday','20261001',2)")
    assert not build_commute(*setup)['departures']['morning']
    conn.execute('DELETE FROM calendar_dates')
    conn.execute("DELETE FROM stop_times WHERE trip_id='early' AND stop_id='R'")
    assert build_commute(*setup)['preferred'][1]['trip'] is None


def test_schedule_only_and_failure(setup):
    conn,settings,snapshot,now=setup
    snapshot.fetch_ok=False
    assert build_commute(*setup)['feed_status']=='unavailable'
    settings.has_realtime=False
    assert build_commute(*setup)['feed_status']=='schedule_only'


def test_service_date_uses_chicago_not_caller_timezone(setup):
    conn,settings,snapshot,now=setup
    result=build_commute(conn,settings,snapshot,datetime(2026,10,2,1,tzinfo=timezone.utc))
    assert result['service_date']=='2026-10-01'


def test_winter_timezone_offset(setup):
    conn,settings,snapshot,now=setup
    trip=build_commute(conn,settings,snapshot,now.replace(month=12,day=1))['preferred'][0]['trip']
    assert trip['departure']['scheduled'].endswith('-06:00')


def test_endpoint_serializes_commute_without_starting_background_jobs(setup, monkeypatch):
    from app.api import routes
    conn, settings, snapshot, now = setup
    # Test the HTTP adapter separately from real feeds, scheduler, and Telegram.
    monkeypatch.setattr(routes, 'settings', settings)
    monkeypatch.setattr(routes, '_cached_snapshot', lambda: snapshot)
    settings.db_path = '/unused'
    monkeypatch.setattr(routes, 'connect', lambda _: conn)
    import app.api.commute as commute
    original = commute.build_commute
    monkeypatch.setattr(commute, 'build_commute', lambda c, s, snap: original(c, s, snap, now))
    # SQLite test connection belongs to this thread, so invoke the route directly.
    from fastapi.encoders import jsonable_encoder
    payload = jsonable_encoder(routes.get_commute())
    assert payload['preferred'][1]['trip']['departure']['scheduled'].endswith('14:30:00-05:00')
