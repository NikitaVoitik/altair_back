import asyncio
import logging

from app.core.config import settings
from .telegram import telegram_client_service

logger = logging.getLogger(__name__)

class TelegramRunner:
    def __init__(self):
        self.telegram_service = telegram_client_service

    async def start_telegram(self):
        """Start the Telegram client service"""
        if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
            logger.warning("Telegram API credentials not configured")
            return

        try:
            await self.telegram_service.start()
            logger.info("Telegram client service started successfully")
        except Exception as e:
            logger.error(f"Failed to start Telegram client service: {e}")

    async def stop_telegram(self):
        """Stop the Telegram client service"""
        try:
            await self.telegram_service.stop()
            logger.info("Telegram client service stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping Telegram client service: {e}")

# Global runner instance
telegram_runner_instance = TelegramRunner()