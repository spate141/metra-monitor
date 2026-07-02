"""In-memory realtime state (design §4.3, §7).

Holds the latest + previous normalized snapshots of positions/tripupdates/alerts.
Never persisted -- rebuilt within ~60s of a restart by the next poll. The Alert
Engine (Phase 3) will diff latest vs. previous to find state transitions; Phase 1
only needs "latest" for the CLI's delay lookup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TripUpdateEntry:
    trip_id: str
    delay_sec: int | None          # None = no realtime data for this trip
    stop_time_updates: list[dict]  # [{stop_id, arrival_delay, departure_delay}]
    is_annulled: bool = False


@dataclass
class VehiclePositionEntry:
    trip_id: str
    lat: float | None
    lon: float | None
    bearing: float | None
    current_stop_id: str | None
    timestamp: datetime | None


@dataclass
class AlertEntry:
    alert_id: str
    header_text: str
    description_text: str
    informed_route_ids: set[str] = field(default_factory=set)
    informed_stop_ids: set[str] = field(default_factory=set)


@dataclass
class Snapshot:
    fetched_at: datetime
    trip_updates: dict[str, TripUpdateEntry] = field(default_factory=dict)
    positions: dict[str, VehiclePositionEntry] = field(default_factory=dict)
    alerts: dict[str, AlertEntry] = field(default_factory=dict)


class StateStore:
    """Latest + previous snapshot, kept in memory only."""

    def __init__(self) -> None:
        self.latest: Snapshot | None = None
        self.previous: Snapshot | None = None

    def update(self, snapshot: Snapshot) -> None:
        self.previous = self.latest
        self.latest = snapshot

    @property
    def is_stale(self) -> bool:
        return self.latest is None

    def delay_for_trip(self, trip_id: str) -> int | None:
        if self.latest is None:
            return None
        entry = self.latest.trip_updates.get(trip_id)
        return entry.delay_sec if entry else None

    def is_annulled(self, trip_id: str) -> bool:
        if self.latest is None:
            return False
        entry = self.latest.trip_updates.get(trip_id)
        return entry.is_annulled if entry else False
