import type { Summary, SlotSummary } from "./api";

function delayBand(delaySec: number | null | undefined, annulled: boolean | undefined): string {
  if (annulled) return "annulled";
  if (delaySec == null) return "unknown";
  const min = delaySec / 60;
  if (min <= 2) return "on_time";
  if (min <= 9) return "minor";
  return "major";
}

function formatScheduled(raw: string | null | undefined): string {
  if (!raw) return "?";
  // GTFS HH:MM:SS, possibly >24h -- render as a 12h clock, rolling the day.
  const [hStr, mStr] = raw.split(":");
  let h = parseInt(hStr, 10) % 24;
  const m = mStr;
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${m} ${ampm}`;
}

function countdownLabel(raw: string | null | undefined): string {
  if (!raw) return "";
  const [hStr, mStr, sStr] = raw.split(":").map((p) => parseInt(p, 10));
  const now = new Date();
  const target = new Date(now);
  target.setHours(0, 0, 0, 0);
  target.setSeconds(hStr * 3600 + mStr * 60 + sStr);
  const diffMs = target.getTime() - now.getTime();
  if (diffMs < -30 * 60 * 1000) return "departed";
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin <= 0) return "departing now";
  if (diffMin < 60) return `in ${diffMin} min`;
  return `in ${Math.floor(diffMin / 60)}h ${diffMin % 60}m`;
}

function renderSlot(el: HTMLElement, slot: SlotSummary): void {
  el.classList.remove("delayed-minor", "delayed-major", "annulled");

  if (slot.status === "no_service") {
    el.querySelector(".hero-card-body")!.innerHTML = `<div class="hero-status">🎉 ${slot.reason ?? "No regular service today"}</div>`;
    return;
  }

  const band = delayBand(slot.delay_sec, slot.is_annulled);
  if (band === "major") el.classList.add("delayed-major");
  else if (band === "minor") el.classList.add("delayed-minor");
  if (slot.is_annulled) el.classList.add("annulled");

  const delayText =
    slot.delay_sec == null
      ? "no live data"
      : slot.delay_sec === 0
      ? "on time"
      : `${slot.delay_sec > 0 ? "+" : ""}${Math.round(slot.delay_sec / 60)} min`;

  el.querySelector(".hero-card-body")!.innerHTML = `
    <div class="hero-train-no">Train #${slot.train_no}</div>
    <div class="hero-countdown">${formatScheduled(slot.scheduled)} · ${countdownLabel(slot.scheduled)}</div>
    <div class="hero-status">${delayText}<span class="hero-badge badge-${band}">${slot.glyph ?? ""}</span></div>
    ${slot.current_stop_id ? `<div class="hero-countdown">near ${slot.current_stop_id}</div>` : ""}
  `;
}

export function renderHero(summary: Summary): void {
  const morning = document.getElementById("card-morning");
  const evening = document.getElementById("card-evening");
  if (morning) renderSlot(morning, summary.morning);
  if (evening) renderSlot(evening, summary.evening);
}
