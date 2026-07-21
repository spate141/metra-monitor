"""Regression test for the "one bad iteration kills the alert loop forever" bug:
`run_loop` used to run its whole body with no exception handling, so any error
(a bad Telegram push, a poller hiccup, ...) silently killed the background task
for the rest of the process's uptime -- with the FastAPI process, /health, and
the dashboard API all continuing to look healthy the whole time.
"""
from __future__ import annotations

import asyncio

from app.config import Settings
from app.realtime import loop as loop_module


def _settings(**overrides) -> Settings:
    defaults = dict(
        HOME_STOP="ROSELLE",
        WORK_STOP="CUS",
        MORNING_TRAIN="2222",
        EVENING_DEPART_CUS="16:05",
        CORS_ORIGIN="http://x",
        METRA_API_TOKEN="tok",
    )
    defaults.update(overrides)
    return Settings(**defaults)


class _FakeStateStore:
    latest = None

    def update(self, snapshot):
        pass


def test_run_loop_survives_iteration_exception(monkeypatch):
    """First iteration raises; the loop must log it and keep running rather
    than letting the exception propagate out of the task.
    """
    calls = {"n": 0}

    def fake_resolve_today(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {}

    real_sleep = asyncio.sleep

    async def fake_sleep(_seconds):
        # Still yield control to the event loop (so the run_loop task actually
        # gets scheduled), just without waiting out the real 30s/300s durations.
        await real_sleep(0)

    monkeypatch.setattr(loop_module, "resolve_today", fake_resolve_today)
    monkeypatch.setattr(loop_module, "_is_awake_hours", lambda now: False)
    monkeypatch.setattr(loop_module.asyncio, "sleep", fake_sleep)

    async def _run():
        settings = _settings()
        task = asyncio.create_task(loop_module.run_loop(settings, _FakeStateStore(), None))
        try:
            for _ in range(200):
                if calls["n"] >= 3:
                    break
                await asyncio.sleep(0)
            assert calls["n"] >= 3, "loop stopped calling resolve_today after the first exception"
            assert not task.done(), "run_loop task died instead of continuing past the exception"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())
