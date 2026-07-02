"""`metra` CLI entrypoint (design §11, Phase 1 + Phase 2 exit criteria).

    metra ingest              -- force a static schedule rebuild
    metra resolve             -- print today's resolved morning/evening trips
    metra delays              -- resolve + one realtime poll, print live delay per train
    metra briefing <slot>     -- print today's morning/evening briefing text (--send to push it)
    metra run                 -- start the long-running Telegram bot + APScheduler process

`delays` is the Phase 1 exit-criteria command. `briefing`/`run` are Phase 2: `briefing`
lets the message content be verified without waiting for a scheduled fire time or
running the bot; `run` is the long-running process that serves /next /morning
/evening /train and fires the cron briefings.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from app.config import settings
from app.core.delay import delay_glyph, stop_delay
from app.core.models import NoService, ResolvedTrip
from app.core.trip_resolver import resolve_today
from app.db import connect
from app.ingest.static_ingestor import ingest
from app.realtime.poller import poll_once

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("metra.cli")


def _today() -> datetime:
    return datetime.now(settings.tzinfo)


def cmd_ingest(args: argparse.Namespace) -> int:
    rebuilt = ingest(settings, force=args.force)
    print("rebuilt" if rebuilt else "unchanged (published.txt timestamp matches)")
    return 0


def _print_resolved(slot: str, result: ResolvedTrip | NoService) -> None:
    if isinstance(result, NoService):
        print(f"{slot.capitalize()}: 🎉 {result.reason}")
        return
    print(f"{slot.capitalize()}: train #{result.train_no} (trip {result.trip_id})")
    for s in result.stops:
        print(f"    {s.stop_id:<14} sched {s.departure_time}")


def cmd_resolve(args: argparse.Namespace) -> int:
    ingest(settings)  # cold-start / staleness safety: ensure a DB exists (design §8.7)
    service_date = _today().date()
    result = resolve_today(
        settings.db_path, service_date, settings.MORNING_TRAIN, settings.EVENING_DEPART_CUS,
        settings.HOME_STOP, settings.WORK_STOP,
    )
    print(f"Resolved trips for {service_date.isoformat()}:")
    for slot in ("morning", "evening"):
        _print_resolved(slot, result[slot])
    return 0


def cmd_delays(args: argparse.Namespace) -> int:
    ingest(settings)
    service_date = _today().date()
    result = resolve_today(
        settings.db_path, service_date, settings.MORNING_TRAIN, settings.EVENING_DEPART_CUS,
        settings.HOME_STOP, settings.WORK_STOP,
    )
    snapshot = poll_once(settings)

    watch_stop = {"morning": settings.HOME_STOP, "evening": settings.WORK_STOP}
    print(f"Live delays for {service_date.isoformat()}:")
    for slot in ("morning", "evening"):
        trip = result[slot]
        if isinstance(trip, NoService):
            print(f"{slot.capitalize()}: 🎉 {trip.reason}")
            continue

        entry = snapshot.trip_updates.get(trip.trip_id)
        stop_id = watch_stop[slot]
        sched = trip.stop_time_for(stop_id)
        sched_str = sched.departure_time if sched else "?"

        if entry is None:
            glyph = "⚪"
            print(
                f"{slot.capitalize()}: {glyph} train #{trip.train_no} @ {stop_id} -- "
                f"scheduled {sched_str} -- no live data, assuming on time"
            )
            continue

        delay_sec = stop_delay(entry, stop_id)
        glyph = delay_glyph(delay_sec, entry.is_annulled)
        delay_min = f"{delay_sec // 60:+d} min" if delay_sec is not None else "unknown"
        print(
            f"{slot.capitalize()}: {glyph} train #{trip.train_no} @ {stop_id} -- "
            f"scheduled {sched_str} -- delay {delay_min}"
        )

    if not settings.has_realtime:
        print("\n(note: METRA_API_TOKEN not set -- realtime feeds were not polled)")
    return 0


def cmd_briefing(args: argparse.Namespace) -> int:
    from app.briefings.builder import build_evening_briefing, build_morning_briefing

    ingest(settings)
    service_date = _today().date()
    conn = connect(settings.db_path)
    try:
        snapshot = poll_once(settings)
        builder = build_morning_briefing if args.slot == "morning" else build_evening_briefing
        text = builder(conn, snapshot, settings, service_date)
    finally:
        conn.close()
    print(text)

    if args.send:
        from app.telegram.bot import build_application, push_message

        async def _send() -> None:
            application = build_application(settings)
            async with application:
                await push_message(application, settings, text)

        asyncio.run(_send())
        print("\n(sent via Telegram)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from app.main import main as run_main

    run_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="metra", description="metra-monitor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="force a static schedule rebuild")
    p_ingest.add_argument("--force", action="store_true", help="rebuild even if published.txt is unchanged")
    p_ingest.set_defaults(func=cmd_ingest)

    p_resolve = sub.add_parser("resolve", help="print today's resolved morning/evening trips")
    p_resolve.set_defaults(func=cmd_resolve)

    p_delays = sub.add_parser("delays", help="resolve + one realtime poll, print live delays")
    p_delays.set_defaults(func=cmd_delays)

    p_briefing = sub.add_parser("briefing", help="print today's briefing text (--send to push via Telegram)")
    p_briefing.add_argument("slot", choices=["morning", "evening"])
    p_briefing.add_argument("--send", action="store_true", help="also send the briefing via Telegram")
    p_briefing.set_defaults(func=cmd_briefing)

    p_run = sub.add_parser("run", help="start the long-running Telegram bot + briefing scheduler")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
