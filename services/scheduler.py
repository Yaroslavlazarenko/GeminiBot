import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)


class ProactiveScheduler:
    """
    Asyncio-based background scheduler that runs proactive research
    and messaging loops alongside the main aiogram polling loop.

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

    async def _research_loop(self):
        """Periodically runs web research cycles."""
        # Initial delay: wait 60-120 seconds after boot so the bot is fully ready
        await asyncio.sleep(60 + random.random() * 60)

        while self._running:
            try:
                cfg = await self._get_proactive_config()

                if cfg.get("research_enabled", True):
                    logger.info("Proactive research loop: starting cycle...")
                    success = await self._proactive.do_research_cycle()
                    logger.info(f"Proactive research loop: cycle {'succeeded' if success else 'failed/skipped'}")

                interval_hours = cfg.get("research_interval_hours", 6)
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
        """Periodically checks if Mia wants to message anyone."""
        # Initial delay: wait 3-5 minutes so all services are initialized
        await asyncio.sleep(180 + random.random() * 120)

        while self._running:
            try:
                cfg = await self._get_proactive_config()

                if cfg.get("messaging_enabled", True):
                    logger.info("Proactive messaging loop: starting cycle...")
                    sent = await self._proactive.do_messaging_cycle()
                    logger.info(f"Proactive messaging loop: sent {sent} messages")

                interval_hours = cfg.get("messaging_check_interval_hours", 4)
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
