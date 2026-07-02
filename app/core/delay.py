"""Delay status glyph bands (design §4.4): ✅≤2, 🟡3-9, 🔴≥10 min, ⛔ annulled, ⚪ no data.

Shared by the CLI (`metra delays`) and the Telegram briefing builders so the two
surfaces never drift on what counts as "on time."
"""
from __future__ import annotations

from app.realtime.state_store import TripUpdateEntry


def delay_glyph(delay_sec: int | None, is_annulled: bool) -> str:
    if is_annulled:
        return "⛔"
    if delay_sec is None:
        return "⚪"
    minutes = delay_sec / 60
    if minutes <= 2:
        return "✅"
    if minutes <= 9:
        return "🟡"
    return "🔴"


def delay_band(delay_sec: int | None, is_annulled: bool) -> str:
    """Named band matching the glyph thresholds -- used by the Alert Engine (design
    §4.5) to detect *transitions* ("crosses a band boundary"), not just raw delay.
    """
    if is_annulled:
        return "annulled"
    if delay_sec is None:
        return "unknown"
    minutes = delay_sec / 60
    if minutes <= 2:
        return "on_time"
    if minutes <= 9:
        return "minor"
    return "major"


def stop_delay(entry: TripUpdateEntry | None, stop_id: str) -> int | None:
    if entry is None:
        return None
    for stu in entry.stop_time_updates:
        if stu["stop_id"] == stop_id:
            return stu["departure_delay"] if stu["departure_delay"] is not None else stu["arrival_delay"]
    return entry.delay_sec
