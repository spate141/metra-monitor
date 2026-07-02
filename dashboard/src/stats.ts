import type { StatsEntry } from "./api";

export function renderStats(stats: Record<string, StatsEntry>): void {
  const body = document.getElementById("stats-body")!;
  const entries = Object.entries(stats);
  if (entries.length === 0) {
    body.innerHTML = `<div class="stat-card-row">No delay history yet — check back after a few commutes.</div>`;
    return;
  }
  body.innerHTML = entries
    .map(([trainNo, s]) => {
      const weekdayRows = Object.entries(s.avg_delay_by_weekday)
        .map(([wd, avg]) => `<div class="stat-card-row"><span>${wd}</span><span>${Math.round(avg / 60)} min avg</span></div>`)
        .join("");
      return `
        <div class="stat-card">
          <div class="stat-card-title">Train #${trainNo}</div>
          <div class="stat-card-row"><span>On-time</span><span>${s.on_time_pct}%</span></div>
          <div class="stat-card-row"><span>Avg delay</span><span>${Math.round(s.avg_delay_sec / 60)} min</span></div>
          <div class="stat-card-row"><span>Observations</span><span>${s.n_observations}</span></div>
          ${weekdayRows}
        </div>
      `;
    })
    .join("");
}
