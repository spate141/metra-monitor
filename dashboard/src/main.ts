import './style.css';
import { api, getCommute, type Commute, type Journey, type Timing, type StatsEntry } from './api';
const $ = (id: string) => document.getElementById(id)!;
const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
let data: Commute | null = null;
let selectedId: string | null = null;
let direction: 'morning' | 'evening' = 'evening';
let stats: Record<string, StatsEntry> | null = null;
let lastSuccess = 0;
let failed = false;
let loading = false;
let mapModule: typeof import('./journey-map') | null = null;
let mapOpen = false;
let lastServiceDate = '';
let inFlight = new Set<string>();
const timestamp = (t: Timing) => t.estimated ?? t.scheduled;
function clock(value: string | null, html = false): string {
    if (!value)
        return '—';
    const formatted = new Intl.DateTimeFormat('en-US', { timeZone: data?.timezone ?? 'America/Chicago', hour: 'numeric', minute: '2-digit' }).format(new Date(value));
    return html ? formatted.replace(/ (AM|PM)/, '<span class="meridiem">$1</span>') : formatted;
}
function stale(): boolean {
    return failed || !data || data.feed_status !== 'available' || Date.now() - Date.parse(data.feed_fetched_at) > 300000;
}
function status(t: Journey): [
    string,
    string
] {
    if (t.is_cancelled)
        return ['cancelled', 'Cancelled'];
    if (stale() || t.departure.delay_sec == null)
        return ['unknown', 'Schedule only'];
    if (t.departure.delay_sec > 120)
        return ['delayed', `${Math.round(t.departure.delay_sec / 60)} min late`];
    if (t.departure.delay_sec < -30)
        return ['', `${Math.round(Math.abs(t.departure.delay_sec) / 60)} min early`];
    return ['', 'On time'];
}
function badge(t: Journey): string { const [kind, label] = status(t); return `<span class="badge ${kind}">${label}</span>`; }
function shown(t: Timing): string | null { return stale() ? t.scheduled : timestamp(t); }
function departureMs(t: Journey): number { return Date.parse(shown(t.departure) ?? ''); }
function countdown(t: Journey): string {
    if (t.is_cancelled)
        return 'Choose another departure';
    const minutes = Math.ceil((departureMs(t) - Date.now()) / 60000);
    const prefix = stale() || !t.departure.estimated ? 'Scheduled ' : '';
    if (minutes < 0)
        return `${prefix}departure passed`;
    if (minutes === 0)
        return `${prefix}departure now`;
    return `${prefix}in ${minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`}`;
}
function allTrips(): Journey[] { return data ? [...data.departures.morning, ...data.departures.evening] : []; }
function selected(): Journey | null { return allTrips().find(t => t.trip_id === selectedId) ?? null; }
function chooseDefault(): Journey | null {
    if (!data)
        return null;
    const morning = data.preferred.find(p => p.slot === 'morning')?.trip;
    if (morning && Date.parse(shown(morning.arrival) ?? '') > Date.now())
        return morning;
    const evening = data.preferred.find(p => p.slot === 'evening')?.trip;
    if (evening && Date.parse(shown(evening.arrival) ?? '') > Date.now())
        return evening;
    return null;
}
function select(id: string) {
    selectedId = id;
    const trip = selected();
    if (trip)
        direction = trip.direction;
    render();
}
function renderHero(trip: Journey | null) {
    if (!trip) {
        $('hero').innerHTML = `<p class="eyebrow">YOUR DAY, AT A GLANCE</p><h2>${data?.preferred.some(p => p.trip) ? 'Today’s preferred journeys are complete.' : 'No preferred trains scheduled today.'}</h2><p class="station">Check other departures below for your next journey.</p>`;
        return;
    }
    const label = data?.preferred.find(p => p.trip?.trip_id === trip.trip_id)?.label ?? 'Selected journey';
    const duration = Math.round((Date.parse(trip.arrival.scheduled ?? '') - Date.parse(trip.departure.scheduled ?? '')) / 60000);
    const estimate = !stale() && trip.departure.estimated;
    $('hero').innerHTML = `<div class="hero-top"><div><p class="eyebrow">${escape(label.toUpperCase())}</p><h2>Train ${escape(trip.train_no)} · ${trip.direction === 'morning' ? 'Into the city' : 'Homeward bound'}</h2></div>${badge(trip)}</div><div class="hero-route"><div><time>${clock(shown(trip.departure), true)}</time><p class="station">${escape(trip.origin)}</p></div><div class="rail">${Number.isFinite(duration) ? `${duration} min` : ''}<div class="rail-line"></div>${estimate ? 'Estimated' : 'Scheduled'}</div><div><time>${clock(shown(trip.arrival), true)}</time><p class="station">${escape(trip.destination)}</p></div></div><div class="hero-bottom"><strong data-countdown="${escape(trip.trip_id)}">${countdown(trip)}</strong><span>${estimate ? `Scheduled ${clock(trip.departure.scheduled)} · ${clock(trip.arrival.scheduled)} arrival` : 'Live estimates appear when available'}</span></div>`;
}
function render() {
    if (!data)
        return;
    const trip = selected() ?? chooseDefault();
    const date = new Date(data.service_date + 'T12:00:00');
    $('date-label').textContent = new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'long', day: 'numeric' }).format(date).toUpperCase() + ' / YOUR DAILY JOURNEY';
    document.querySelector('.subtitle')!.textContent = `${data.home} ↔ ${data.work}`;
    document.querySelector('.line-badge')!.textContent = data.route;
    renderHero(trip);
    $('preferred').innerHTML = data.preferred.map(p => p.trip ? `<button class="trip-card" data-trip="${escape(p.trip.trip_id)}" aria-pressed="${trip?.trip_id === p.trip.trip_id}"><div class="card-top"><span class="card-label">${escape(p.label)}</span>${badge(p.trip)}</div><div class="card-times"><time>${clock(shown(p.trip.departure), true)}</time><span class="card-arrow">→</span><time>${clock(shown(p.trip.arrival), true)}</time></div><p class="small">${escape(p.trip.origin)} → ${escape(p.trip.destination)}</p><div class="card-foot"><span>Train ${escape(p.trip.train_no)}</span><span data-countdown="${escape(p.trip.trip_id)}">${countdown(p.trip)}</span></div></button>` : `<div class="trip-card"><p class="card-label">${escape(p.label)}</p><p class="empty">Not scheduled today</p><p class="small">This preferred trip does not run on today’s service calendar.</p></div>`).join('');
    $('journey-title').textContent = trip ? `Train ${trip.train_no} · ${trip.stops.length} stops` : 'Your journey';
    $('journey-note').textContent = trip ? (trip.position ? `Last reported near ${trip.position.stop_name ?? 'an unknown stop'}${trip.position.timestamp ? ` · ${Math.max(0, Math.floor((Date.now() - Date.parse(trip.position.timestamp)) / 60000))} min ago` : ' · position age unknown'}. ` : 'No live position available. ') + (stale() ? 'Showing scheduled stop times.' : 'Estimates may use the train’s reported delay; they are not confirmed arrivals.') : 'Select a departure to inspect the journey.';
    $('timeline').innerHTML = trip ? trip.stops.map((s, i) => `<div class="stop ${i === 0 || i === trip.stops.length - 1 ? 'endpoint' : ''}"><span class="stop-dot"></span><div>${escape(s.name)}${i === 0 ? '<p class="small">Board here</p>' : i === trip.stops.length - 1 ? '<p class="small">Your destination</p>' : ''}</div><div><time>${clock(shown(s))}</time><p class="small">${!stale() && s.estimated ? 'Est.' : 'Scheduled'}</p></div></div>`).join('') : '<p class="empty">Your trip details will appear here.</p>';
    $('direction-morning').setAttribute('aria-pressed', String(direction === 'morning'));
    $('direction-evening').setAttribute('aria-pressed', String(direction === 'evening'));
    const preferredIds = new Set(data.preferred.map(p => p.trip?.trip_id));
    const options = data.departures[direction].filter(t => !t.is_cancelled && departureMs(t) >= Date.now() && !preferredIds.has(t.trip_id)).sort((a, b) => departureMs(a) - departureMs(b)).slice(0, 3);
    $('alternatives').innerHTML = options.length ? options.map(t => `<button class="alternative" data-trip="${escape(t.trip_id)}"><span><strong>${clock(shown(t.departure))} <span class="card-arrow">→</span> ${clock(shown(t.arrival))}</strong><span class="small">Train ${escape(t.train_no)} · ${escape(t.destination)}</span></span>${badge(t)}</button>`).join('') : '<p class="empty">No more alternative departures in this direction today.</p>';
    if (mapOpen && mapModule && trip)
        void mapModule.showJourney(trip, data.timezone).catch(mapError);
    renderStats();
    tick();
}
function renderStats() {
    if (!stats || !data)
        return;
    const numbers = [...new Set(data.preferred.flatMap(p => p.trip ? [p.trip.train_no] : []))];
    $('history').innerHTML = numbers.map(n => {
        const s = stats![n];
        return s ? `<div class="stat-row"><div class="stat-head"><span>Train ${escape(n)}</span><strong>${s.on_time_pct}%</strong></div><div class="stat-track"><span style="width:${Math.max(0, Math.min(100, s.on_time_pct))}%"></span></div><p class="small">${s.n_observations} observations · ${(s.avg_delay_sec / 60).toFixed(1)} min average delay</p></div>` : `<div class="stat-row"><p class="small">Train ${escape(n)} · No recorded history yet</p></div>`;
    }).join('') || '<p class="empty">No preferred trains running today.</p>';
}
function tick() {
    $('last-update').textContent = failed ? 'Connection interrupted · retrying' : lastSuccess ? `Updated ${Math.max(0, Math.floor((Date.now() - lastSuccess) / 1000))}s ago` : 'Connecting to your commute…';
    const banner = $('feed-banner');
    banner.hidden = !!data && !stale();
    banner.textContent = failed ? 'Unable to refresh. Showing the last available schedule. Try Refresh.' : data?.feed_status === 'schedule_only' ? 'Schedule only · live tracking is not configured.' : data?.feed_status === 'unavailable' ? 'Live feed unavailable · showing scheduled times.' : data && stale() ? 'Live data is stale · showing scheduled times.' : 'Loading your commute…';
    document.querySelectorAll<HTMLElement>('[data-countdown]').forEach(el => { const t = allTrips().find(t => t.trip_id === el.dataset.countdown); if (t)
        el.textContent = countdown(t); });
}
async function commutePoll() {
    if (loading)
        return;
    loading = true;
    try {
        const next = await getCommute();
        if (next.service_date !== lastServiceDate) {
            selectedId = null;
            lastServiceDate = next.service_date;
        }
        data = next;
        failed = false;
        lastSuccess = Date.now();
        if (selectedId && !selected())
            selectedId = null;
        if (!selectedId)
            direction = chooseDefault()?.direction ?? 'evening';
        render();
    }
    catch {
        failed = true;
        if (data)
            render();
        else {
            $('hero').innerHTML = '<p class="eyebrow">CONNECTION INTERRUPTED</p><h2>Your commute is temporarily unavailable.</h2><p class="station">Use Refresh to try again. We’ll also retry automatically.</p>';
            $('preferred').innerHTML = '<p class="empty">Preferred trains could not be loaded.</p>';
            $('alternatives').innerHTML = '<p class="empty">Departures are temporarily unavailable.</p>';
        }
        tick();
    }
    finally {
        loading = false;
    }
}
async function alertsPoll() {
    if (inFlight.has('alerts'))
        return;
    inFlight.add('alerts');
    try {
        const res = await api.alerts();
        $('alerts').innerHTML = res.available === false ? '<p class="alert-clear">Service alerts are currently unavailable.</p>' : res.alerts.length ? res.alerts.map(a => `<details class="alert"><summary>${escape(a.header || 'Service advisory')} ${a.line_wide ? '· Line-wide' : ''}</summary><p>${escape(a.description)}</p></details>`).join('') : '<p class="alert-clear">✓ No reported service alerts for your line</p>';
    }
    catch {
        $('alerts').innerHTML = '<p class="alert-clear">Unable to check service alerts. Retrying automatically.</p>';
    }
    finally {
        inFlight.delete('alerts');
    }
}
async function statsPoll() {
    if (inFlight.has('stats'))
        return;
    inFlight.add('stats');
    try {
        stats = await api.stats();
        renderStats();
    }
    catch {
        $('history').textContent = 'History is temporarily unavailable.';
    }
    finally {
        inFlight.delete('stats');
    }
}
function mapError() { $('map-status').textContent = 'The map could not load. Your stop timeline is still available. Close and reopen the map to retry.'; }
document.addEventListener('click', event => { const button = (event.target as HTMLElement).closest<HTMLElement>('[data-trip]'); if (button?.dataset.trip)
    select(button.dataset.trip); });
$('direction-morning').onclick = () => { direction = 'morning'; render(); };
$('direction-evening').onclick = () => { direction = 'evening'; render(); };
$('map-toggle').onclick = async () => {
    mapOpen = !mapOpen;
    $('map-wrap').hidden = !mapOpen;
    $('map-toggle').setAttribute('aria-expanded', String(mapOpen));
    $('map-toggle').textContent = mapOpen ? 'Hide map' : 'Show live map ↗';
    if (!mapOpen)
        return;
    $('map-status').textContent = 'Loading the live map…';
    try {
        mapModule = await import('./journey-map');
        const t = selected() ?? chooseDefault();
        if (t)
            await mapModule.showJourney(t, data!.timezone);
        else
            $('map-status').textContent = 'Select a train to show its journey on the map.';
    }
    catch {
        mapError();
    }
};
$('map-recenter').onclick = () => mapModule?.recenter();
let theme = 'light';
try {
    theme = localStorage.getItem('metra-theme') === 'dark' ? 'dark' : 'light';
}
catch { /* optional storage */ }
document.documentElement.dataset.theme = theme;
$('theme-toggle').onclick = () => { theme = theme === 'light' ? 'dark' : 'light'; document.documentElement.dataset.theme = theme; try {
    localStorage.setItem('metra-theme', theme);
}
catch { /* optional storage */ } };
function refresh() { void commutePoll(); void alertsPoll(); void statsPoll(); }
$('refresh').onclick = refresh;
let timers: ReturnType<typeof setInterval>[] = [];
function start() { if (timers.length)
    return; refresh(); timers = [setInterval(() => void commutePoll(), 30000), setInterval(() => void alertsPoll(), 60000), setInterval(() => void statsPoll(), 300000), setInterval(tick, 1000)]; }
function stop() { timers.forEach(clearInterval); timers = []; }
document.addEventListener('visibilitychange', () => document.hidden ? stop() : start());
start();
