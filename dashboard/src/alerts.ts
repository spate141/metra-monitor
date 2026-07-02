import type { AlertsResponse } from "./api";

export function renderAlerts(data: AlertsResponse): void {
  const ribbon = document.getElementById("alerts-ribbon")!;
  if (data.alerts.length === 0) {
    ribbon.hidden = true;
    return;
  }
  ribbon.hidden = false;
  const summaryLine = `📢 ${data.alerts.length} alert${data.alerts.length > 1 ? "s" : ""} affecting MD-W${data.line_wide ? " (line-wide)" : ""} — tap to expand`;
  const items = data.alerts.map((a) => `<li><strong>${a.header || "Alert"}</strong>${a.description ? ` — ${a.description}` : ""}</li>`).join("");
  ribbon.innerHTML = `${summaryLine}<ul>${items}</ul>`;
  ribbon.onclick = () => ribbon.classList.toggle("expanded");
}
