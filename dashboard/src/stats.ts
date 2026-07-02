import type { StatsEntry } from "./api";

const WEEKDAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function weekdayLabel(key: string): string {
  return key.slice(0, 3);
}

function renderBars(byWeekday: Record<string, number>): string {
  const entries = Object.entries(byWeekday).sort(
    (a, b) => WEEKDAY_ORDER.indexOf(a[0].toLowerCase()) - WEEKDAY_ORDER.indexOf(b[0].toLowerCase())
  );
  if (entries.length === 0) return "";
  const maxAbs = Math.max(1, ...entries.map(([, v]) => Math.abs(v) / 60));
  const cols = entries
    .map(([wd, avgSec]) => {
      const min = avgSec / 60;
      const heightPct = Math.max(6, Math.round((Math.abs(min) / maxAbs) * 100));
      return `
        <div class="trend-bar-col" title="${weekdayLabel(wd)}: ${Math.round(min)} min avg">
          <div class="trend-bar" style="height:${heightPct}%"></div>
          <div class="trend-bar-label">${weekdayLabel(wd)}</div>
        </div>
      `;
    })
    .join("");
  return `<div class="trend-bars">${cols}</div>`;
}

export function renderStats(stats: Record<string, StatsEntry>): void {
  const body = document.getElementById("trend-body")!;
  const entries = Object.entries(stats);
  if (entries.length === 0) {
    body.innerHTML = `<div class="trend-empty">No delay history yet — check back after a few commutes.</div>`;
    return;
  }
  body.innerHTML = entries
    .map(([trainNo, s]) => {
      return `
        <div class="trend-card">
          <div class="trend-card-head">
            <span class="trend-card-title">Train #${trainNo}</span>
            <span class="trend-pct">${s.on_time_pct}%</span>
          </div>
          <div class="trend-meta">${s.n_observations} observations · ${Math.round(s.avg_delay_sec / 60)} min avg delay</div>
          ${renderBars(s.avg_delay_by_weekday)}
        </div>
      `;
    })
    .join("");
}
