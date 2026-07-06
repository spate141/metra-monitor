"""Telegram bot: push briefings/alerts + interactive commands (design §4.6).

Single authorized chat (`settings.TELEGRAM_CHAT_ID`) -- any other chat is silently
ignored, no reply given, so the bot doesn't confirm its own existence to strangers.
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.briefings.builder import (
    build_evening_briefing,
    build_morning_briefing,
    build_next_departures,
    build_notification_status_message,
    build_stats_message,
    build_train_status,
)
from app.config import Settings
from app.db import connect, set_notification_mode, set_paused_until
from app.realtime.poller import poll_once

logger = logging.getLogger(__name__)


def _authorized(update: Update, settings: Settings) -> bool:
    chat_id = str(update.effective_chat.id) if update.effective_chat else None
    return bool(settings.TELEGRAM_CHAT_ID) and chat_id == settings.TELEGRAM_CHAT_ID


def build_application(settings: Settings) -> Application:
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    application.bot_data["settings"] = settings

    application.add_handler(CommandHandler("next", _cmd_next))
    application.add_handler(CommandHandler("morning", _cmd_morning))
    application.add_handler(CommandHandler("evening", _cmd_evening))
    application.add_handler(CommandHandler("train", _cmd_train))
    application.add_handler(CommandHandler("stats", _cmd_stats))
    application.add_handler(CommandHandler("commute_mode", _cmd_commute_mode))
    application.add_handler(CommandHandler("monitor_all", _cmd_monitor_all))
    application.add_handler(CommandHandler("pause_today", _cmd_pause_today))
    application.add_handler(CommandHandler("resume", _cmd_resume))
    application.add_handler(CommandHandler("status", _cmd_status))
    return application


async def _cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        now = datetime.now(settings.tzinfo)
        snapshot = poll_once(settings)
        text = build_next_departures(conn, snapshot, settings, now.date(), now.strftime("%H:%M:%S"))
    finally:
        conn.close()
    await update.message.reply_text(text)


async def _cmd_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        service_date = datetime.now(settings.tzinfo).date()
        snapshot = poll_once(settings)
        text = build_morning_briefing(conn, snapshot, settings, service_date)
    finally:
        conn.close()
    await update.message.reply_text(text)


async def _cmd_evening(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        service_date = datetime.now(settings.tzinfo).date()
        snapshot = poll_once(settings)
        text = build_evening_briefing(conn, snapshot, settings, service_date)
    finally:
        conn.close()
    await update.message.reply_text(text)


async def _cmd_train(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    if not context.args:
        await update.message.reply_text("Usage: /train <number>, e.g. /train 2222")
        return
    train_no = context.args[0]
    conn = connect(settings.db_path)
    try:
        service_date = datetime.now(settings.tzinfo).date()
        snapshot = poll_once(settings)
        text = build_train_status(conn, snapshot, settings, service_date, train_no)
    finally:
        conn.close()
    await update.message.reply_text(text)


async def _cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        text = build_stats_message(conn, settings)
    finally:
        conn.close()
    await update.message.reply_text(text)


async def _cmd_commute_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        set_notification_mode(conn, "commute")
    finally:
        conn.close()
    await update.message.reply_text(
        f"✅ Commute mode: alerts only before {settings.COMMUTE_MORNING_END} "
        f"and at/after {settings.COMMUTE_EVENING_START} CST."
    )


async def _cmd_monitor_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        set_notification_mode(conn, "all")
    finally:
        conn.close()
    await update.message.reply_text("✅ Now monitoring all MD-W trains all day (quiet hours still apply).")


async def _cmd_pause_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        today = datetime.now(settings.tzinfo).date().isoformat()
        set_paused_until(conn, today)
    finally:
        conn.close()
    await update.message.reply_text("🔕 Alerts paused for the rest of today. Resumes automatically tomorrow, or use /resume.")


async def _cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        set_paused_until(conn, None)
    finally:
        conn.close()
    await update.message.reply_text("🔔 Alerts resumed.")


async def _cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.bot_data["settings"]
    if not _authorized(update, settings):
        return
    conn = connect(settings.db_path)
    try:
        text = build_notification_status_message(conn, settings)
    finally:
        conn.close()
    await update.message.reply_text(text)


async def push_message(application: Application, settings: Settings, text: str) -> None:
    if not settings.TELEGRAM_CHAT_ID:
        logger.warning("no TELEGRAM_CHAT_ID configured -- cannot push message")
        return
    await application.bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=text)
