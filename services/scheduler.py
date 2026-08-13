import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Mia's timezone (Odessa, Ukraine)
try:
    from zoneinfo import ZoneInfo
    ODESSA_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    ODESSA_TZ = timezone(timedelta(hours=3))


class ProactiveScheduler:
    """
    Asyncio-based background scheduler that runs proactive research
    and messaging loops alongside the main aiogram polling loop.

    Respects Mia's daily schedule — proactive actions only happen
    during her "awake" hours (default 9:00–00:00 Odessa time).

    Reads intervals from system_settings on every tick, so changes
    via the admin panel take effect on the next wake-up.
    """

    def __init__(self, db_manager, proactive_service):
        self._db = db_manager
        self._proactive = proactive_service
        self._tasks: list = []
        self._running = False

    async def start(self):
        """Start all background loops."""
        self._running = True
        self._tasks.append(asyncio.create_task(self._research_loop()))
        self._tasks.append(asyncio.create_task(self._messaging_loop()))
        logger.info("ProactiveScheduler started (research + messaging loops)")

    async def stop(self):
        """Cancel all background tasks gracefully."""
        self._running = False
        for t in self._tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("ProactiveScheduler stopped")

    async def _get_proactive_config(self) -> dict:
        settings = await self._db.get_system_settings()
        return settings.get("proactive", {})

    def _is_awake(self, cfg: dict) -> bool:
        """Check if current Odessa time is within Mia's awake hours."""
        now = datetime.now(ODESSA_TZ)
        awake_start = cfg.get("awake_hour_start", 9)
        awake_end = cfg.get("awake_hour_end", 0)  # 0 = midnight

        current_hour = now.hour

        if awake_start < awake_end:
            # Simple range, e.g. 9..22
            return awake_start <= current_hour < awake_end
        else:
            # Wraps past midnight, e.g. 9..0 means 9:00–23:59
            # awake_end=0 means she sleeps at midnight
            return current_hour >= awake_start or current_hour < awake_end

    def _seconds_until_awake(self, cfg: dict) -> float:
        """Calculate seconds until the next awake_hour_start in Odessa time."""
        now = datetime.now(ODESSA_TZ)
        awake_start = cfg.get("awake_hour_start", 9)

        # Build next wake-up datetime
        wake_up = now.replace(hour=awake_start, minute=0, second=0, microsecond=0)
        if wake_up <= now:
            wake_up += timedelta(days=1)

        delta = (wake_up - now).total_seconds()
        # Add 1-5 min jitter so it doesn't fire exactly at :00
        delta += random.random() * 240 + 60
        return delta

    async def _research_loop(self):
        """Periodically runs web research cycles (only during awake hours)."""
        # Initial delay: wait 60-120 seconds after boot so the bot is fully ready
        await asyncio.sleep(60 + random.random() * 60)

        while self._running:
            try:
                cfg = await self._get_proactive_config()

                # Check if Mia is "awake"
                if not self._is_awake(cfg):
                    sleep_secs = self._seconds_until_awake(cfg)
                    logger.info(f"Proactive research: Mia is sleeping, waiting {sleep_secs / 3600:.1f}h until she wakes up")
                    await asyncio.sleep(sleep_secs)
                    continue

                if cfg.get("research_enabled", True):
                    logger.info("Proactive research loop: starting cycle...")
                    success = await self._proactive.do_research_cycle()
                    logger.info(f"Proactive research loop: cycle {'succeeded' if success else 'failed/skipped'}")

                interval_hours = cfg.get("research_interval_hours", 2)
                # Add ±15% jitter to avoid deterministic patterns
                jitter = interval_hours * 0.15 * (random.random() * 2 - 1)
                sleep_seconds = (interval_hours + jitter) * 3600
                sleep_seconds = max(sleep_seconds, 300)  # Minimum 5 minutes

                logger.info(f"Proactive research: sleeping {sleep_seconds / 3600:.1f} hours until next cycle")
                await asyncio.sleep(sleep_seconds)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Proactive research loop error: {e}", exc_info=True)
                await asyncio.sleep(300)  # Wait 5 min on error before retry

    async def _messaging_loop(self):
        """Periodically checks if Mia wants to message anyone (only during awake hours)."""
        # Initial delay: wait 3-5 minutes so all services are initialized
        await asyncio.sleep(180 + random.random() * 120)

        while self._running:
            try:
                cfg = await self._get_proactive_config()

                # Check if Mia is "awake"
                if not self._is_awake(cfg):
                    sleep_secs = self._seconds_until_awake(cfg)
                    logger.info(f"Proactive messaging: Mia is sleeping, waiting {sleep_secs / 3600:.1f}h until she wakes up")
                    await asyncio.sleep(sleep_secs)
                    continue

                if cfg.get("messaging_enabled", True):
                    logger.info("Proactive messaging loop: starting cycle...")
                    sent = await self._proactive.do_messaging_cycle()
                    logger.info(f"Proactive messaging loop: sent {sent} messages")

                interval_hours = cfg.get("messaging_check_interval_hours", 1.5)
                jitter = interval_hours * 0.15 * (random.random() * 2 - 1)
                sleep_seconds = (interval_hours + jitter) * 3600
                sleep_seconds = max(sleep_seconds, 300)

                logger.info(f"Proactive messaging: sleeping {sleep_seconds / 3600:.1f} hours until next cycle")
                await asyncio.sleep(sleep_seconds)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Proactive messaging loop error: {e}", exc_info=True)
                await asyncio.sleep(300)
