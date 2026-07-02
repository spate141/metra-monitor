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
    build_train_status,
)
from app.config import Settings
from app.db import connect
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


async def push_message(application: Application, settings: Settings, text: str) -> None:
    if not settings.TELEGRAM_CHAT_ID:
        logger.warning("no TELEGRAM_CHAT_ID configured -- cannot push message")
        return
    await application.bot.send_message(chat_id=settings.TELEGRAM_CHAT_ID, text=text)
