"""Runtime configuration, loaded from .env (see .env.example for the full key list)."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Upstream Metra API
    METRA_API_TOKEN: str | None = None
    METRA_REALTIME_BASE: str = "https://gtfspublic.metrarr.com/gtfs/public"
    METRA_STATIC_BASE: str = "https://schedules.metrarail.com/gtfs"

    # Telegram (unused until Phase 2, kept optional so Phase 1 runs without it)
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None

    # Line / station / train targeting -- keep configurable per design §1 non-goals
    TZ: str = "America/Chicago"
    ROUTE_ID: str = "MD-W"
    HOME_STOP: str = "ROSELLE"
    WORK_STOP: str = "CUS"
    MORNING_TRAIN: str = "2222"
    EVENING_DEPART_CUS: str = "16:05"

    # Briefing / quiet-hours config (used starting Phase 2, harmless here)
    MORNING_BRIEFING: str = "07:15"
    EVENING_BRIEFING: str = "15:30"
    QUIET_HOURS: str = "22:00-05:30"
    CORS_ORIGIN: str = "https://metra.snehal.ai"

    # Local storage
    METRA_DB_PATH: str = "metra.db"

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.TZ)

    @property
    def has_realtime(self) -> bool:
        """Whether a Metra realtime token is configured. Poller no-ops without one."""
        return bool(self.METRA_API_TOKEN)

    @property
    def db_path(self) -> Path:
        return Path(self.METRA_DB_PATH)


settings = Settings()
