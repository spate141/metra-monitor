"""Process entrypoint: Telegram bot polling + APScheduler briefing jobs (design §4.4, §8.7).

Started via `metra run` (long-running). Wires:
- periodic static ingest (design §4.1: cheap `published.txt` check every ~10 min)
- APScheduler cron jobs for the morning/evening briefings, Mon-Fri, in `settings.tzinfo`
  (never a fixed UTC offset -- DST-safe per design §8.2)
- cold-start grace (design §8.7): if the process starts within 10 min after a
  briefing's scheduled time and today's briefing hasn't gone out yet (tracked in
  `meta`), send it late with a "(delayed briefing)" tag instead of waiting for
  tomorrow's cron fire.

Briefings and on-demand commands (`/next`, `/train`, etc.) still use a single fresh
`poll_once()` each -- no need to share state for those. The Alert Engine (design
§4.5), by contrast, needs a continuous adaptive-cadence loop (design §4.3) feeding
a shared `StateStore` so it can diff consecutive snapshots; that loop is started
here as a background task (see app/realtime/loop.py).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ingest(settings)  # ensure a schedule DB exists before anything else runs

    application = build_application(settings)

    scheduler = AsyncIOScheduler(timezone=settings.tzinfo)
    scheduler.add_job(_periodic_ingest, "interval", minutes=10, id="static_ingest")

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
    scheduler.start()

    state_store = StateStore()

    async with application:
        await application.start()
        await application.updater.start_polling()
        logger.info("telegram bot polling started")
        await _cold_start_grace(application, settings)

        alert_task = asyncio.create_task(run_loop(settings, state_store, application))
        try:
            await asyncio.Event().wait()  # run forever, until cancelled/killed
        finally:
            alert_task.cancel()
            await application.updater.stop()
            await application.stop()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
