// Thin client for metra-monitor's public REST API (design §5). All read-only,
// no auth needed -- the frontend never sees the Metra token.
const API_BASE = import.meta.env.VITE_API_BASE as string;

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
  stale: boolean;
}

export interface AlertItem {
  id: string;
  header: string;
  description: string;
  line_wide: boolean;
}

export interface AlertsResponse {
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
  const res = await fetch(`${API_BASE}${path}`);
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
