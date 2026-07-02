import type { AlertsResponse } from "./api";

export function renderAlerts(data: AlertsResponse): void {
  const ticker = document.getElementById("alerts-ticker")!;
  if (data.alerts.length === 0) {
    ticker.hidden = true;
    return;
  }
  ticker.hidden = false;
  const summaryLine = `⚠ ${data.alerts.length} alert${data.alerts.length > 1 ? "s" : ""} affecting MD-W${data.line_wide ? " (line-wide)" : ""} — tap to expand`;
  const items = data.alerts.map((a) => `<li><strong>${a.header || "Alert"}</strong>${a.description ? ` — ${a.description}` : ""}</li>`).join("");
  ticker.innerHTML = `${summaryLine}<ul>${items}</ul>`;
  ticker.onclick = () => ticker.classList.toggle("expanded");
}
