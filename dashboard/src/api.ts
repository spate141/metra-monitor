// Thin client for metra-monitor's public REST API (design §5). All read-only,
// no auth needed -- the frontend never sees the Metra token.
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export interface SlotSummary {
  status: "resolved" | "no_service";
  reason?: string;
  train_no?: string;
  trip_id?: string;
  stop_id?: string;
  scheduled?: string | null;
  delay_sec?: number | null;
  is_annulled?: boolean;
  glyph?: string;
  current_stop_id?: string | null;
  lat?: number | null;
  lon?: number | null;
}

export interface Summary {
  morning: SlotSummary;
  evening: SlotSummary;
}

export interface Position {
  trip_id: string;
  train_no: string | null;
  lat: number | null;
  lon: number | null;
  bearing: number | null;
  delay_sec: number | null;
  next_stop: string | null;
  is_my_train: boolean;
  direction_id: number | null;
  stale: boolean;
}

export interface AlertItem {
  id: string;
  header: string;
  description: string;
  line_wide: boolean;
}

export interface AlertsResponse {
  available?: boolean;
  alerts: AlertItem[];
  line_wide: boolean;
}

export interface TripStop {
  stop_id: string;
  stop_sequence: number;
  scheduled_arrival: string | null;
  scheduled_departure: string | null;
  delay_sec: number | null;
}

export interface TripDetail {
  train_no: string;
  trip_id: string;
  is_annulled: boolean;
  position: { lat: number; lon: number; bearing: number | null; current_stop_id: string | null } | null;
  stops: TripStop[];
}

export interface Geometry {
  line: GeoJSON.FeatureCollection;
  stops: GeoJSON.FeatureCollection;
  route_color?: string | null;
  route_text_color?: string | null;
}

export interface StatsEntry {
  n_observations: number;
  on_time_pct: number;
  avg_delay_sec: number;
  avg_delay_by_weekday: Record<string, number>;
}

export interface Health {
  status: string;
  db_age_sec: number | null;
  poller_last_fetch_sec_ago: number | null;
  has_realtime: boolean;
  has_telegram: boolean;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { signal: AbortSignal.timeout(25000) });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  summary: () => get<Summary>("/api/v1/summary"),
  positions: () => get<Position[]>("/api/v1/positions"),
  trip: (trainNo: string) => get<TripDetail>(`/api/v1/trip/${encodeURIComponent(trainNo)}`),
  alerts: () => get<AlertsResponse>("/api/v1/alerts"),
  geometry: () => get<Geometry>("/api/v1/geometry"),
  stats: () => get<Record<string, StatsEntry>>("/api/v1/stats"),
  health: () => get<Health>("/health"),
};

export interface Timing {
  scheduled: string | null;
  estimated: string | null;
  delay_sec: number | null;
  estimate_source: string | null;
}
export interface Journey {
  trip_id: string;
  train_no: string;
  direction: 'morning' | 'evening';
  origin: string;
  destination: string;
  departure: Timing;
  arrival: Timing;
  is_cancelled: boolean;
  position: {lat: number | null; lon: number | null; stop_name: string | null; timestamp: string | null} | null;
  stops: (Timing & {stop_id: string; name: string})[];
}
export interface Commute {
  service_date: string;
  timezone: string;
  route: string;
  generated_at: string;
  feed_fetched_at: string;
  feed_status: 'available' | 'unavailable' | 'schedule_only';
  home: string;
  work: string;
  preferred: {slot: string; label: string; trip: Journey | null}[];
  departures: {morning: Journey[]; evening: Journey[]};
}
export const getCommute = () => get<Commute>('/api/v1/commute');
