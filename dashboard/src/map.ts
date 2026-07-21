import maplibregl, { Map as MLMap, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, type Position, type TripDetail } from "./api";

// Free vector tiles, no API key/billing (design §6: "avoid Mapbox billing").
const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";

// GTFS direction_id: Metra's static feed uses 0 = outbound (away from
// Chicago), 1 = inbound (toward Chicago). Colors chosen to read clearly on
// both the light and dark map styles.
const DIRECTION_COLORS: Record<string, string> = {
  "0": "#2f6fd6", // outbound
  "1": "#e07b1f", // inbound
  unknown: "#8b949e",
};

// Delay severity is still surfaced, just via the chevron's outline instead of
// its fill (which now encodes direction) -- keeps both dimensions visible.
const DELAY_STROKE_COLORS: Record<string, string> = {
  on_time: "#0008",
  minor: "#eab308",
  major: "#dc2626",
  annulled: "#dc2626",
  unknown: "#0008",
};

function delayBand(delaySec: number | null, isAnnulled: boolean): string {
  if (isAnnulled) return "annulled";
  if (delaySec == null) return "unknown";
  const min = delaySec / 60;
  if (min <= 2) return "on_time";
  if (min <= 9) return "minor";
  return "major";
}

function directionColor(directionId: number | null): string {
  return DIRECTION_COLORS[String(directionId)] ?? DIRECTION_COLORS.unknown;
}

function chevronSvg(fill: string, stroke: string, bearing: number | null): string {
  const rotation = bearing ?? 0;
  return `<svg width="16" height="16" viewBox="0 0 16 16" style="transform: rotate(${rotation}deg)">
    <polygon points="8,1 14,14 8,10 2,14" fill="${fill}" stroke="${stroke}" stroke-width="1.25"/>
  </svg>`;
}

// Onboard GPS is noisy enough that raw fixes render visibly off the rail --
// snap each position onto the nearest point of the route geometry before
// placing its marker. Distances only need to be compared, not measured
// precisely, so a flat-earth projection scaled by the corridor's reference
// latitude is accurate enough at this extent.
const REF_LAT_RAD = (41.95 * Math.PI) / 180;
const M_PER_DEG_LAT = 111320;
const M_PER_DEG_LON = 111320 * Math.cos(REF_LAT_RAD);
const MAX_SNAP_DIST_M = 500; // beyond this, trust the raw fix over the route (e.g. yards, detours)

type Point = [number, number];

let routeLines: Point[][] = [];

function toXY([lon, lat]: Point): Point {
  return [lon * M_PER_DEG_LON, lat * M_PER_DEG_LAT];
}

function toLonLat([x, y]: Point): Point {
  return [x / M_PER_DEG_LON, y / M_PER_DEG_LAT];
}

function nearestPointOnSegment(p: Point, a: Point, b: Point): { point: Point; distSq: number } {
  const [px, py] = p;
  const [ax, ay] = a;
  const [bx, by] = b;
  const abx = bx - ax;
  const aby = by - ay;
  const lenSq = abx * abx + aby * aby;
  const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * abx + (py - ay) * aby) / lenSq));
  const x = ax + t * abx;
  const y = ay + t * aby;
  const dx = px - x;
  const dy = py - y;
  return { point: [x, y], distSq: dx * dx + dy * dy };
}

function snapToRoute(lon: number, lat: number): Point {
  if (routeLines.length === 0) return [lon, lat];
  const p = toXY([lon, lat]);
  let best: { point: Point; distSq: number } | null = null;
  for (const line of routeLines) {
    for (let i = 0; i < line.length - 1; i++) {
      const candidate = nearestPointOnSegment(p, toXY(line[i]), toXY(line[i + 1]));
      if (!best || candidate.distSq < best.distSq) best = candidate;
    }
  }
  if (!best || Math.sqrt(best.distSq) > MAX_SNAP_DIST_M) return [lon, lat];
  return toLonLat(best.point);
}

let map: MLMap;
const markers = new Map<string, Marker>();
let onTrainClick: ((trainNo: string) => void) | null = null;
let lineColor = "#c8102e"; // overwritten by setLineColor() once the feed's route_color loads

export function setTrainClickHandler(fn: (trainNo: string) => void): void {
  onTrainClick = fn;
}

/** The resolved signature accent (feed's route_color once loaded, else the
 * pre-load fallback) -- used by main.ts to theme the header badge / --line CSS var. */
export function getLineColor(): string {
  return lineColor;
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function isDark(): boolean {
  return document.documentElement.getAttribute("data-theme") !== "light";
}

/** Applies theme-aware paint to the stops/labels layers -- fixes the old
 * hardcoded-dark halo that looked broken in light mode. Safe to call before
 * the layers exist (checked via getLayer). */
export function applyMapTheme(): void {
  if (!map || !map.getLayer("md-w-stops")) return;
  const dark = isDark();
  map.setPaintProperty("md-w-stops", "circle-color", dark ? "#0b0d10" : "#ffffff");
  map.setPaintProperty("md-w-stops", "circle-stroke-color", lineColor);
  map.setPaintProperty("md-w-stop-labels", "text-color", cssVar("--ink-muted") || (dark ? "#8b95a1" : "#5b6570"));
  map.setPaintProperty("md-w-stop-labels", "text-halo-color", cssVar("--bg") || (dark ? "#0b0d10" : "#f5f4f0"));
}

/** Sets the signature line color everywhere it's used on the map (route line +
 * stop outlines), called once /api/v1/geometry's route_color resolves. */
export function setLineColor(color: string): void {
  lineColor = color;
  if (!map || !map.getLayer("md-w-line")) return;
  map.setPaintProperty("md-w-line", "line-color", lineColor);
  map.setPaintProperty("md-w-stops", "circle-stroke-color", lineColor);
}

export async function initMap(): Promise<MLMap> {
  map = new maplibregl.Map({
    container: "map",
    style: STYLE_URL,
    center: [-88.05, 41.95], // roughly the MD-W corridor (Roselle <-> Chicago)
    zoom: 9.5,
  });
  map.addControl(new maplibregl.NavigationControl(), "top-right");

  await new Promise<void>((resolve) => map.on("load", () => resolve()));

  try {
    const geometry = await api.geometry();
    if (geometry.route_color) lineColor = geometry.route_color;

    routeLines = geometry.line.features
      .map((f) => (f.geometry.type === "LineString" ? (f.geometry.coordinates as Point[]) : null))
      .filter((coords): coords is Point[] => coords != null);

    map.addSource("md-w-line", { type: "geojson", data: geometry.line });
    map.addLayer({
      id: "md-w-line",
      type: "line",
      source: "md-w-line",
      paint: { "line-color": lineColor, "line-width": 4, "line-opacity": 1 },
    });

    map.addSource("md-w-stops", { type: "geojson", data: geometry.stops });
    map.addLayer({
      id: "md-w-stops",
      type: "circle",
      source: "md-w-stops",
      paint: {
        "circle-radius": 4,
        "circle-color": isDark() ? "#0b0d10" : "#ffffff",
        "circle-stroke-width": 1.5,
        "circle-stroke-color": lineColor,
      },
    });
    map.addLayer({
      id: "md-w-stop-labels",
      type: "symbol",
      source: "md-w-stops",
      layout: {
        "text-field": ["get", "stop_name"],
        "text-font": ["Noto Sans Regular"], // liberty style's glyphs only cover Noto Sans
        "text-size": 10,
        "text-offset": [0, 1],
        "text-anchor": "top",
      },
      paint: {
        "text-color": cssVar("--ink-muted"),
        "text-halo-color": cssVar("--bg"),
        "text-halo-width": 1,
      },
    });
  } catch (err) {
    console.error("failed to load /api/v1/geometry", err);
  }

  return map;
}

export function updatePositions(positions: Position[]): void {
  const seen = new Set<string>();
  for (const pos of positions) {
    // Constraint C7 ghosts (no lat/lon) have nowhere to render without route
    // interpolation, which this dashboard doesn't attempt -- skip on the map.
    if (pos.lat == null || pos.lon == null) continue;
    seen.add(pos.trip_id);

    const band = delayBand(pos.delay_sec, false);
    const fill = directionColor(pos.direction_id);
    const stroke = DELAY_STROKE_COLORS[band];
    const snapped = snapToRoute(pos.lon, pos.lat);

    let marker = markers.get(pos.trip_id);
    if (!marker) {
      const el = document.createElement("div");
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (pos.train_no && onTrainClick) onTrainClick(pos.train_no);
      });
      marker = new maplibregl.Marker({ element: el }).setLngLat(snapped).addTo(map);
      markers.set(pos.trip_id, marker);
    } else {
      marker.setLngLat(snapped);
    }

    const el = marker.getElement();
    el.innerHTML = chevronSvg(fill, stroke, pos.bearing);
    el.className = `train-marker${pos.is_my_train ? " my-train" : ""}${pos.stale ? " stale" : ""}`;
    el.title = `#${pos.train_no ?? "?"} · ${pos.delay_sec != null ? `${Math.round(pos.delay_sec / 60)} min` : "no live data"}`;
  }

  for (const [tripId, marker] of markers) {
    if (!seen.has(tripId)) {
      marker.remove();
      markers.delete(tripId);
    }
  }
}

export function renderSheet(trip: TripDetail): void {
  const sheet = document.getElementById("sheet")!;
  const content = document.getElementById("sheet-content")!;
  const rows = trip.stops
    .map(
      (s) => `<tr>
        <td>${s.stop_id}</td>
        <td>${s.scheduled_departure ?? s.scheduled_arrival ?? "?"}</td>
        <td>${s.delay_sec != null ? `${s.delay_sec > 0 ? "+" : ""}${Math.round(s.delay_sec / 60)}m` : "—"}</td>
      </tr>`
    )
    .join("");
  content.innerHTML = `
    <h3>Train #${trip.train_no}${trip.is_annulled ? " (cancelled)" : ""}</h3>
    <table>
      <thead><tr><td>Stop</td><td>Sched.</td><td>Delay</td></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  sheet.hidden = false;
}
