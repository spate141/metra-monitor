import "./style.css";
import { api } from "./api";
import { initTheme } from "./theme";
import { renderBoard } from "./board";
import { renderAlerts } from "./alerts";
import { renderStats } from "./stats";
import { initMap, updatePositions, renderSheet, setTrainClickHandler, applyMapTheme, getLineColor } from "./map";

const SUMMARY_POSITIONS_INTERVAL_MS = 30_000;
const ALERTS_INTERVAL_MS = 60_000;
const STATS_INTERVAL_MS = 5 * 60_000;

const lastUpdateEl = document.getElementById("last-update")!;
const refreshDotEl = document.getElementById("refresh-dot")!;
const feedBannerEl = document.getElementById("feed-banner")!;

let lastSuccessAt: number | null = null;

function markUpdated(ok: boolean): void {
  refreshDotEl.classList.toggle("active", ok);
  refreshDotEl.classList.toggle("stale", !ok);
  if (ok) lastSuccessAt = Date.now();
}

function tickClock(): void {
  if (lastSuccessAt == null) {
    lastUpdateEl.textContent = "connecting…";
    return;
  }
  const sec = Math.round((Date.now() - lastSuccessAt) / 1000);
  lastUpdateEl.textContent = sec < 5 ? "live · updated just now" : `live · updated ${sec}s ago`;
}

async function pollSummaryAndPositions(): Promise<void> {
  try {
    const [summary, positions] = await Promise.all([api.summary(), api.positions()]);
    renderBoard(summary);
    updatePositions(positions);
    markUpdated(true);
  } catch (err) {
    console.error("poll failed", err);
    markUpdated(false);
  }
}

async function pollAlerts(): Promise<void> {
  try {
    renderAlerts(await api.alerts());
  } catch (err) {
    console.error("alerts poll failed", err);
  }
}

async function pollStats(): Promise<void> {
  try {
    renderStats(await api.stats());
  } catch (err) {
    console.error("stats poll failed", err);
  }
}

async function pollHealth(): Promise<void> {
  try {
    const health = await api.health();
    const stale = health.poller_last_fetch_sec_ago != null && health.poller_last_fetch_sec_ago > 300;
    feedBannerEl.hidden = !stale;
  } catch {
    feedBannerEl.hidden = false; // can't reach the API at all -- also worth flagging
  }
}

class Poller {
  private timer: ReturnType<typeof setInterval> | null = null;
  private fn: () => void;
  private intervalMs: number;

  constructor(fn: () => void, intervalMs: number) {
    this.fn = fn;
    this.intervalMs = intervalMs;
  }
  start(): void {
    this.fn();
    this.timer = setInterval(this.fn, this.intervalMs);
  }
  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}

async function main(): Promise<void> {
  initTheme(() => applyMapTheme());
  setInterval(tickClock, 1000);

  await initMap();
  // The map resolves the feed's real GTFS route_color; mirror it into the CSS
  // custom property so the header badge / trend bars stay in sync with the map.
  document.documentElement.style.setProperty("--line", getLineColor());
  applyMapTheme();

  setTrainClickHandler(async (trainNo) => {
    try {
      renderSheet(await api.trip(trainNo));
    } catch (err) {
      console.error("failed to load trip detail", err);
    }
  });
  document.getElementById("sheet-close")!.addEventListener("click", () => {
    document.getElementById("sheet")!.hidden = true;
  });

  const pollers = [
    new Poller(pollSummaryAndPositions, SUMMARY_POSITIONS_INTERVAL_MS),
    new Poller(pollAlerts, ALERTS_INTERVAL_MS),
    new Poller(pollStats, STATS_INTERVAL_MS),
    new Poller(pollHealth, ALERTS_INTERVAL_MS),
  ];

  // Page Visibility API (design §6): pause polling when the tab isn't visible.
  const startAll = () => pollers.forEach((p) => p.start());
  const stopAll = () => pollers.forEach((p) => p.stop());
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopAll();
    else startAll();
  });

  startAll();
}

main();
