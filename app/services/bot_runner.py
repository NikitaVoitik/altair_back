import asyncio
import logging

from app.core.config import settings
from .telegram_bot import TelegramBotService

logger = logging.getLogger(__name__)

class BotRunner:
    def __init__(self):
        self.bot_service = None

    async def start_bot(self):
        """Start the Telegram bot"""
        if not settings.TELEGRAM_BOT_TOKEN:
            logger.warning("Telegram bot token not configured, skipping bot startup")
            return

        try:
            self.bot_service = TelegramBotService(settings.TELEGRAM_BOT_TOKEN)
            await self.bot_service.start()
            logger.info("Telegram bot started successfully")
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}")

    async def stop_bot(self):
        """Stop the Telegram bot"""
        if self.bot_service:
            try:
                await self.bot_service.stop()
                logger.info("Telegram bot stopped successfully")
            except Exception as e:
                logger.error(f"Error stopping Telegram bot: {e}")

# Global bot runner instance
bot_runner_instance = BotRunner()