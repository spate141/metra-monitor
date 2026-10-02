"""Read-only, service-day-aware data for the personal commute dashboard."""
from datetime import datetime, timedelta

from app.core.trip_resolver import active_service_ids
from app.ingest.gtfs_time import gtfs_time_to_datetime


def build_commute(conn, settings, snapshot, now=None):
    now = now or datetime.now(settings.tzinfo)
    day = now.astimezone(settings.tzinfo).date()
    active = active_service_ids(conn, day)
    names = {r['stop_id']: r['stop_name'] for r in conn.execute('SELECT stop_id, stop_name FROM stops')}
    journeys = {'morning': [], 'evening': []}
    for row in conn.execute('SELECT * FROM trips WHERE route_id = ?', (settings.ROUTE_ID,)):
        if row['service_id'] not in active or row['direction_id'] not in (0, 1):
            continue
        direction = 'morning' if row['direction_id'] == 1 else 'evening'
        origin, destination = (settings.HOME_STOP, settings.WORK_STOP) if direction == 'morning' else (settings.WORK_STOP, settings.HOME_STOP)
        stops = list(conn.execute('SELECT * FROM stop_times WHERE trip_id = ? ORDER BY stop_sequence', (row['trip_id'],)))
        ids = [s['stop_id'] for s in stops]
        if origin not in ids or destination not in ids or ids.index(origin) >= ids.index(destination):
            continue
        entry = snapshot.trip_updates.get(row['trip_id'])
        position = snapshot.positions.get(row['trip_id'])
        def timing(stop, arrival=False):
            raw = stop['arrival_time' if arrival else 'departure_time'] or stop['departure_time' if arrival else 'arrival_time']
            scheduled = gtfs_time_to_datetime(day, raw, settings.tzinfo) if raw else None
            delay, source = None, None
            if entry:
                update = next((s for s in entry.stop_time_updates if s['stop_id'] == stop['stop_id']), None)
                if update:
                    delay = update.get('arrival_delay' if arrival else 'departure_delay')
                    if delay is None:
                        delay = update.get('departure_delay' if arrival else 'arrival_delay')
                    if delay is not None:
                        source = 'stop'
                if delay is None and entry.delay_sec is not None:
                    delay, source = entry.delay_sec, 'trip'
            return {'scheduled': scheduled.isoformat() if scheduled else None,
                    'estimated': (scheduled + timedelta(seconds=delay)).isoformat() if scheduled and delay is not None else None,
                    'delay_sec': delay, 'estimate_source': source}
        segment = stops[ids.index(origin):ids.index(destination) + 1]
        departure, arrival = timing(segment[0]), timing(segment[-1], True)
        if not departure['scheduled']:
            continue
        journeys[direction].append({
            'trip_id': row['trip_id'], 'train_no': row['trip_short_name'], 'direction': direction,
            'origin': names.get(origin, origin), 'destination': names.get(destination, destination),
            'departure': departure, 'arrival': arrival, 'is_cancelled': bool(entry and entry.is_annulled),
            'position': {'lat': position.lat, 'lon': position.lon, 'stop_name': names.get(position.current_stop_id, position.current_stop_id),
                         'timestamp': position.timestamp.isoformat() if position.timestamp else None} if position else None,
            'stops': [{'stop_id': s['stop_id'], 'name': names.get(s['stop_id'], s['stop_id']),
                       **timing(s, i == len(segment) - 1)} for i, s in enumerate(segment)],
        })
    for trips in journeys.values():
        trips.sort(key=lambda t: t['departure']['scheduled'])
    def preferred(slot, label, match):
        trip = next((t for t in journeys['morning' if slot == 'morning' else 'evening'] if match(t)), None)
        return {'slot': slot, 'label': label, 'trip': trip}
    def at_time(trip, value):
        return datetime.fromisoformat(trip['departure']['scheduled']).strftime('%H:%M') == value[:5]
    return {'service_date': day.isoformat(), 'timezone': settings.TZ, 'route': settings.ROUTE_ID,
            'generated_at': now.isoformat(), 'feed_fetched_at': snapshot.fetched_at.isoformat(),
            'feed_status': 'schedule_only' if not settings.has_realtime else ('available' if snapshot.fetch_ok else 'unavailable'),
            'home': names.get(settings.HOME_STOP, settings.HOME_STOP), 'work': names.get(settings.WORK_STOP, settings.WORK_STOP),
            'preferred': [preferred('morning', 'Morning commute', lambda t: t['train_no'] == settings.MORNING_TRAIN),
                          preferred('early', 'Early option', lambda t: at_time(t, settings.EARLY_EVENING_DEPART_CUS)),
                          preferred('evening', 'Usual ride home', lambda t: at_time(t, settings.EVENING_DEPART_CUS))],
            'departures': journeys}
