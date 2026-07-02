import maplibregl, { Map as MLMap, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, type Position, type TripDetail } from "./api";

// Free vector tiles, no API key/billing (design §6: "avoid Mapbox billing").
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

const DELAY_COLORS: Record<string, string> = {
  on_time: "#4e7f2f",
  minor: "#eab308",
  major: "#f87171",
  annulled: "#f87171",
  unknown: "#8b949e",
};

function delayBand(delaySec: number | null, isAnnulled: boolean): string {
  if (isAnnulled) return "annulled";
  if (delaySec == null) return "unknown";
  const min = delaySec / 60;
  if (min <= 2) return "on_time";
  if (min <= 9) return "minor";
  return "major";
}

function chevronSvg(color: string, bearing: number | null): string {
  const rotation = bearing ?? 0;
  return `<svg width="16" height="16" viewBox="0 0 16 16" style="transform: rotate(${rotation}deg)">
    <polygon points="8,1 14,14 8,10 2,14" fill="${color}" stroke="#0008" stroke-width="0.5"/>
  </svg>`;
}

let map: MLMap;
const markers = new Map<string, Marker>();
let onTrainClick: ((trainNo: string) => void) | null = null;

export function setTrainClickHandler(fn: (trainNo: string) => void): void {
  onTrainClick = fn;
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
    map.addSource("md-w-line", { type: "geojson", data: geometry.line });
    map.addLayer({
      id: "md-w-line",
      type: "line",
      source: "md-w-line",
      paint: { "line-color": "#60a5fa", "line-width": 3, "line-opacity": 0.7 },
    });

    map.addSource("md-w-stops", { type: "geojson", data: geometry.stops });
    map.addLayer({
      id: "md-w-stops",
      type: "circle",
      source: "md-w-stops",
      paint: {
        "circle-radius": 4,
        "circle-color": "#e6edf3",
        "circle-stroke-width": 1,
        "circle-stroke-color": "#60a5fa",
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
      paint: { "text-color": "#8b949e", "text-halo-color": "#1f1f1e", "text-halo-width": 1 },
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
    const color = DELAY_COLORS[band];

    let marker = markers.get(pos.trip_id);
    if (!marker) {
      const el = document.createElement("div");
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        if (pos.train_no && onTrainClick) onTrainClick(pos.train_no);
      });
      marker = new maplibregl.Marker({ element: el }).setLngLat([pos.lon, pos.lat]).addTo(map);
      markers.set(pos.trip_id, marker);
    } else {
      marker.setLngLat([pos.lon, pos.lat]);
    }

    const el = marker.getElement();
    el.innerHTML = chevronSvg(color, pos.bearing);
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

export function renderDrawer(trip: TripDetail): void {
  const drawer = document.getElementById("drawer")!;
  const content = document.getElementById("drawer-content")!;
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
  drawer.hidden = false;
}
