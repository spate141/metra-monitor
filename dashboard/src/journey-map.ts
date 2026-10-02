import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { api, type Geometry, type Journey } from './api';
let map: maplibregl.Map | null = null;
let geometry: Geometry | null = null;
let ready: Promise<void> | null = null;
let marker: maplibregl.Marker | null = null;
let bounds: maplibregl.LngLatBounds | null = null;
let version = 0;
let displayedTrip: string | null = null;
async function initialize() {
    geometry = await api.geometry();
    map = new maplibregl.Map({ container: 'map', style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json', center: [-87.9, 41.95], zoom: 10 });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Map timed out')), 15000);
        map!.once('load', () => { clearTimeout(timeout); resolve(); });
        map!.once('error', () => { clearTimeout(timeout); reject(new Error('Map unavailable')); });
    });
    map.addSource('route', { type: 'geojson', data: geometry.line });
    map.addLayer({ id: 'route', type: 'line', source: 'route', paint: { 'line-color': '#00549e', 'line-width': 3, 'line-opacity': .6 } });
    map.addSource('journey-stops', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({ id: 'journey-stops', type: 'circle', source: 'journey-stops', paint: { 'circle-radius': ['case', ['get', 'endpoint'], 8, 4], 'circle-color': '#fff', 'circle-stroke-color': '#00549e', 'circle-stroke-width': 2 } });
    map.addLayer({ id: 'stop-labels', type: 'symbol', source: 'journey-stops', layout: { 'text-field': ['get', 'stop_name'], 'text-font': ['Open Sans Regular'], 'text-size': 11, 'text-offset': [0, 1.4] }, paint: { 'text-color': '#172b46', 'text-halo-color': '#fff', 'text-halo-width': 2 } });
}
export async function showJourney(trip: Journey, _timezone: string) {
    const current = ++version;
    if (!ready)
        ready = initialize().catch(error => { map?.remove(); map = null; ready = null; throw error; });
    await ready;
    if (current !== version || !map || !geometry)
        return;
    map.resize();
    const ids = new Set(trip.stops.map(s => s.stop_id));
    const features = geometry.stops.features.filter(f => ids.has(f.properties?.stop_id)).map(f => ({ ...f, properties: { ...f.properties, endpoint: f.properties?.stop_id === trip.stops[0]?.stop_id || f.properties?.stop_id === trip.stops.at(-1)?.stop_id } }));
    (map.getSource('journey-stops') as maplibregl.GeoJSONSource).setData({ type: 'FeatureCollection', features });
    bounds = new maplibregl.LngLatBounds();
    features.forEach(f => { if (f.geometry.type === 'Point')
        bounds!.extend(f.geometry.coordinates as [
            number,
            number
        ]); });
    marker?.remove();
    marker = null;
    const pos = trip.position;
    if (pos?.lat != null && pos.lon != null) {
        const el = document.createElement('div');
        el.className = 'map-train';
        el.textContent = `Train ${trip.train_no}`;
        marker = new maplibregl.Marker({ element: el }).setLngLat([pos.lon, pos.lat]).addTo(map);
        bounds.extend([pos.lon, pos.lat]);
    }
    document.getElementById('map-status')!.textContent = `Stops on your journey · blue line: MD-W${marker ? ` · Train ${trip.train_no}: last reported position${pos?.timestamp ? `, ${Math.max(0, Math.floor((Date.now() - Date.parse(pos.timestamp)) / 60000))} min ago` : ', age unknown'}` : ' · No live train position available'}`;
    if (displayedTrip !== trip.trip_id)
        recenter();
    displayedTrip = trip.trip_id;
}
export function recenter() { if (map && bounds && !bounds.isEmpty())
    map.fitBounds(bounds, { padding: 45, maxZoom: 13, duration: 0 }); }
