import type { Summary, SlotSummary } from "./api";

const CHIP_LABEL: Record<string, string> = {
  on_time: "ON TIME",
  minor: "DELAYED",
  major: "DELAYED",
  annulled: "CANCELLED",
  unknown: "NO LIVE DATA",
};

// Split-flap countdown: only the characters that changed since the previous
// render get the flip animation, so the row reads like a Solari board updating
// in place rather than a full repaint every tick.
const prevCountdown = new Map<string, string>();

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
  if (diffMin < 60) return `departs in ${diffMin} min`;
  return `departs in ${Math.floor(diffMin / 60)}h ${diffMin % 60}m`;
}

function renderFlap(container: HTMLElement, text: string, key: string): void {
  const prev = prevCountdown.get(key) ?? "";
  const len = Math.max(text.length, prev.length);
  let html = "";
  for (let i = 0; i < len; i++) {
    const ch = text[i] ?? "";
    const changed = ch !== prev[i];
    const display = ch === " " ? "&nbsp;" : ch;
    html += `<span class="flap-char${changed ? " flip" : ""}">${display}</span>`;
  }
  container.innerHTML = `<span class="flap">${html}</span>`;
  prevCountdown.set(key, text);
}

function renderSlot(el: HTMLElement, slot: SlotSummary): void {
  el.classList.remove("status-on_time", "status-minor", "status-major", "status-annulled", "status-unknown");
  const body = el.querySelector(".board-row-body")!;

  if (slot.status === "no_service") {
    prevCountdown.delete(el.id);
    body.innerHTML = `<div class="board-status-text">🎉 ${slot.reason ?? "No regular service today"}</div>`;
    return;
  }

  const band = delayBand(slot.delay_sec, slot.is_annulled);
  el.classList.add(`status-${band}`);

  const delayText =
    slot.delay_sec == null
      ? CHIP_LABEL.unknown
      : slot.delay_sec === 0
      ? CHIP_LABEL.on_time
      : `${slot.delay_sec > 0 ? "+" : ""}${Math.round(slot.delay_sec / 60)} MIN`;

  body.innerHTML = `
    <div class="board-main">
      <span class="board-trainno">#${slot.train_no}</span>
      <span class="board-time">${formatScheduled(slot.scheduled)}</span>
      <span class="board-chip chip-${band}">${slot.glyph ?? ""} ${delayText}</span>
    </div>
    <div class="board-sub">
      <span class="board-countdown" id="countdown-${el.id}"></span>
      ${slot.current_stop_id ? `<span class="board-loc">near ${slot.current_stop_id}</span>` : ""}
    </div>
  `;

  const countdownEl = body.querySelector<HTMLElement>(`#countdown-${el.id}`)!;
  renderFlap(countdownEl, countdownLabel(slot.scheduled), el.id);
}

export function renderBoard(summary: Summary): void {
  const morning = document.getElementById("row-morning");
  const evening = document.getElementById("row-evening");
  if (morning) renderSlot(morning, summary.morning);
  if (evening) renderSlot(evening, summary.evening);
}
