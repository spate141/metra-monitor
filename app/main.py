"""FastAPI app + lifespan (design §10, §3): serves `/api/v1/*` + `/health`
(design §5), and in its lifespan starts the Telegram bot polling, the
APScheduler briefing cron jobs (design §4.4), and the realtime alert-engine loop
(design §4.3/§4.5) as background tasks -- one asyncio process per design §3.

Bot/briefings/alert-engine are gated on `TELEGRAM_BOT_TOKEN` being configured so
the API can be run and tested standalone (e.g. local dev, or the Phase 1-3
components not yet wired) without Telegram credentials.

Started via `metra run` (uvicorn) or directly: `uv run uvicorn app.main:app`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.briefings.builder import build_evening_briefing, build_morning_briefing
from app.config import Settings, settings
from app.db import briefing_already_sent, connect, mark_briefing_sent
from app.ingest.static_ingestor import ingest
from app.realtime.loop import run_loop
from app.realtime.poller import poll_once
from app.realtime.state_store import StateStore
from app.telegram.bot import build_application, push_message

logger = logging.getLogger(__name__)

GRACE_WINDOW = timedelta(minutes=10)

state_store = StateStore()


def _parse_hhmm(raw: str) -> tuple[int, int]:
    h, m = raw.split(":")
    return int(h), int(m)


async def send_briefing(application, settings: Settings, slot: str, delayed_tag: bool = False) -> None:
    conn = connect(settings.db_path)
    try:
        service_date = datetime.now(settings.tzinfo).date()
        if briefing_already_sent(conn, slot, service_date):
            logger.info("%s briefing already sent for %s -- skipping", slot, service_date)
            return
        snapshot = poll_once(settings)
        builder = build_morning_briefing if slot == "morning" else build_evening_briefing
        text = builder(conn, snapshot, settings, service_date, delayed_tag=delayed_tag)
        await push_message(application, settings, text)
        mark_briefing_sent(conn, slot, service_date)
        logger.info("%s briefing sent for %s", slot, service_date)
    finally:
        conn.close()


async def _cold_start_grace(application, settings: Settings) -> None:
    now = datetime.now(settings.tzinfo)
    if now.weekday() >= 5:  # Sat/Sun -- no weekday briefings to catch up on
        return
    for slot, raw in (("morning", settings.MORNING_BRIEFING), ("evening", settings.EVENING_BRIEFING)):
        h, m = _parse_hhmm(raw)
        scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if scheduled <= now <= scheduled + GRACE_WINDOW:
            logger.info("cold-start grace window active for %s briefing -- sending late", slot)
            await send_briefing(application, settings, slot, delayed_tag=True)


def _periodic_ingest() -> None:
    try:
        ingest(settings)
    except Exception:
        logger.exception("periodic ingest failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ingest(settings)  # ensure a schedule DB exists before anything else runs

    scheduler = AsyncIOScheduler(timezone=settings.tzinfo)
    scheduler.add_job(_periodic_ingest, "interval", minutes=10, id="static_ingest")

    application = None
    alert_task = None
    if settings.TELEGRAM_BOT_TOKEN:
        application = build_application(settings)
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("telegram bot polling started")

        m_h, m_m = _parse_hhmm(settings.MORNING_BRIEFING)
        e_h, e_m = _parse_hhmm(settings.EVENING_BRIEFING)
        scheduler.add_job(
            lambda: asyncio.ensure_future(send_briefing(application, settings, "morning")),
            CronTrigger(day_of_week="mon-fri", hour=m_h, minute=m_m, timezone=settings.tzinfo),
            id="morning_briefing",
        )
        scheduler.add_job(
            lambda: asyncio.ensure_future(send_briefing(application, settings, "evening")),
            CronTrigger(day_of_week="mon-fri", hour=e_h, minute=e_m, timezone=settings.tzinfo),
            id="evening_briefing",
        )
        await _cold_start_grace(application, settings)
        alert_task = asyncio.create_task(run_loop(settings, state_store, application))
    else:
        logger.info("no TELEGRAM_BOT_TOKEN configured -- bot, briefings, and alert engine disabled")

    scheduler.start()
    app.state.application = application

    try:
        yield
    finally:
        if alert_task is not None:
            alert_task.cancel()
        if application is not None:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        scheduler.shutdown()


app = FastAPI(title="metra-agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.CORS_ORIGIN, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/health")
def health():
    db_age_sec = None
    if settings.db_path.exists():
        db_age_sec = round(time.time() - settings.db_path.stat().st_mtime, 1)
    poller_fresh_sec = None
    if state_store.latest is not None:
        poller_fresh_sec = round((datetime.now(timezone.utc) - state_store.latest.fetched_at).total_seconds(), 1)
    return {
        "status": "ok",
        "db_age_sec": db_age_sec,
        "poller_last_fetch_sec_ago": poller_fresh_sec,
        "has_realtime": settings.has_realtime,
        "has_telegram": bool(settings.TELEGRAM_BOT_TOKEN),
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="info")


if __name__ == "__main__":
    main()
