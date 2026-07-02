"""Shared dataclasses for resolved trips and delay observations (design §4.2, §7)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class StopTime:
    stop_id: str
    stop_sequence: int
    arrival_time: str | None       # raw GTFS HH:MM:SS (may exceed 24:00:00)
    departure_time: str | None


@dataclass(frozen=True)
class ResolvedTrip:
    service_date: date
    slot: str                       # 'morning' | 'evening'
    trip_id: str
    train_no: str | None
    stops: list[StopTime] = field(default_factory=list)

    def stop_time_for(self, stop_id: str) -> StopTime | None:
        return next((s for s in self.stops if s.stop_id == stop_id), None)


@dataclass(frozen=True)
class NoService:
    """Sentinel result when no regular service runs for a slot on a given date."""
    service_date: date
    slot: str
    reason: str = "no regular service (holiday/exception schedule)"


@dataclass(frozen=True)
class DelayObservation:
    trip_id: str
    train_no: str | None
    stop_id: str | None
    delay_sec: int | None
    source: str
