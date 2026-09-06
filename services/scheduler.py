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

    def __init__(self, db_manager, proactive_service, bot=None):
        self._db = db_manager
        self._proactive = proactive_service
        self._bot = bot or getattr(proactive_service, "_bot", None)
        self._tasks: list = []
        self._running = False

    async def start(self):
        """Start all background loops."""
        self._running = True
        self._tasks.append(asyncio.create_task(self._research_loop()))
        self._tasks.append(asyncio.create_task(self._messaging_loop()))
        self._tasks.append(asyncio.create_task(self._scheduled_tasks_loop()))
        logger.info("ProactiveScheduler started (research + messaging + scheduled_tasks loops)")

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

    # --- Scheduled Tasks Engine ---

    async def _scheduled_tasks_loop(self):
        """Periodically checks and executes user-scheduled tasks."""
        # Initial delay to ensure everything is connected and bot is polling
        await asyncio.sleep(10)

        while self._running:
            try:
                now_utc = datetime.now(timezone.utc)
                due_tasks = await self._db.get_due_scheduled_tasks(now_utc)

                for task in due_tasks:
                    try:
                        await self._execute_scheduled_task(task)
                    except Exception as te:
                        logger.error(f"Error executing scheduled task {task.get('_id')}: {te}", exc_info=True)

                # Poll every 20 seconds for due tasks
                await asyncio.sleep(20)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Scheduled tasks loop error: {e}", exc_info=True)
                await asyncio.sleep(30)

    async def _execute_scheduled_task(self, task: dict):
        """Executes a single scheduled task by triggering Gemini and delivering the result to the chat."""
        task_id = task.get("_id")
        chat_id = task.get("chat_id")
        if not chat_id or not task_id:
            logger.warning(f"Skipping malformed scheduled task: {task}")
            return
        chat_id = int(chat_id)
        task_desc = task.get("task_description", "")
        is_recurring = bool(task.get("is_recurring", False))
        interval_minutes = task.get("interval_minutes")

        logger.info(f"Executing scheduled task {task_id} for chat {chat_id}: {task_desc}")

        if not self._bot:
            logger.error("Bot instance not available in ProactiveScheduler")
            return

        # Pre-flight check: verify bot can actually send messages in this chat
        try:
            from bot.handlers import can_bot_send_messages
            if not await can_bot_send_messages(self._bot, chat_id):
                logger.warning(f"Cannot send message to chat {chat_id}: bot lacks permissions. Skipping task {task_id}")
                await self._db.update_scheduled_task_after_run(
                    task_id=task_id,
                    next_run_at=None,
                    is_recurring=False,
                    error="Bot lacks permissions to send messages to chat"
                )
                return
        except Exception as pe:
            logger.warning(f"Permission check failed for {chat_id}: {pe}")

        # Resolve chat context
        chat_context = await self._db.get_chat_context(chat_id)
        if not chat_context:
            logger.error(f"Could not resolve chat context for chat {chat_id}")
            return

        # Construct prompt for the model
        prompt = (
            f"[⏰ НАПОМИНАНИЕ / ЗАДАЧА ПО РАСПИСАНИЮ]\n"
            f"ID задачи: {task_id}\n"
            f"Инструкция/задача: {task_desc}\n\n"
            f"Сейчас наступило время выполнить эту задачу.\n"
            f"ОБЯЗАТЕЛЬНОЕ ПРАВИЛО: Если задача требует фактических данных (прогноз погоды, мировые новости, текущие события, праздники), перед ответом ОБЯЗАТЕЛЬНО используй инструмент веб-поиска (ask_grok), чтобы взять настоящие данные на сегодня, а не придумывать цифры и события из головы.\n"
            f"Обратись к участникам чата и выполни задачу естественно, дружелюбно и живо в своем характере (Миа). "
            f"Не упоминай системные теги и технический ID вслух, просто выполни задачу."
        )

        from services.ai_service import get_ai_service
        ai_service = get_ai_service()

        try:
            response_text, tool_calls = await ai_service.generate_response(
                text=prompt,
                chat_context=chat_context
            )

            sent_msg = None
            if response_text:
                import html
                from bot.handlers import split_and_balance_html

                parts = split_and_balance_html(response_text)
                for part in parts:
                    try:
                        sent_msg = await self._bot.send_message(
                            chat_id=chat_id,
                            text=part,
                            parse_mode="HTML"
                        )
                    except Exception:
                        safe_part = html.escape(part)
                        sent_msg = await self._bot.send_message(
                            chat_id=chat_id,
                            text=safe_part
                        )

            # Record bot's message into history
            if sent_msg and response_text:
                await chat_context.add_message(
                    role="model",
                    text=response_text,
                    message_id=sent_msg.message_id,
                    timestamp=datetime.now(ODESSA_TZ).strftime("%Y-%m-%d %H:%M")
                )

            # Schedule next run or mark completed
            now_utc = datetime.now(timezone.utc)
            if is_recurring and interval_minutes:
                safe_interval = max(int(interval_minutes), 30)
                scheduled_run = task.get("next_run_at")
                if scheduled_run:
                    # Keep fixed anchor time to prevent schedule drift
                    if scheduled_run.tzinfo is None:
                        scheduled_run = scheduled_run.replace(tzinfo=timezone.utc)
                    next_run = scheduled_run
                    while next_run <= now_utc:
                        next_run += timedelta(minutes=safe_interval)
                else:
                    next_run = now_utc + timedelta(minutes=safe_interval)

                await self._db.update_scheduled_task_after_run(
                    task_id=task_id,
                    next_run_at=next_run,
                    is_recurring=True
                )
                logger.info(f"Recurring task {task_id} completed run. Next run at: {next_run.isoformat()}")
            else:
                await self._db.update_scheduled_task_after_run(
                    task_id=task_id,
                    next_run_at=None,
                    is_recurring=False
                )
                logger.info(f"One-time scheduled task {task_id} completed successfully.")

        except Exception as e:
            logger.error(f"Failed to execute scheduled task {task_id}: {e}", exc_info=True)
            await self._db.update_scheduled_task_after_run(
                task_id=task_id,
                next_run_at=None,
                is_recurring=False,
                error=str(e)
            )
