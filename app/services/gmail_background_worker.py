import asyncio
import logging
from typing import Dict, Set
from sqlmodel import Session, select

from app.core.db import engine
from app.models import OAuthAccount
from .gmail import gmail_service

logger = logging.getLogger(__name__)


class GmailBackgroundWorker:
    def __init__(self):
        self.running = False
        self.worker_tasks: Dict[str, asyncio.Task] = {}
        self.processed_messages: Dict[str, Set[str]] = {}
        self.polling_interval = 30

    async def start(self):
        """Start the background worker"""
        if self.running:
            logger.info("Gmail background worker already running")
            return

        self.running = True
        logger.info("Starting Gmail background worker")

        asyncio.create_task(self._monitor_users())

    async def stop(self):
        """Stop the background worker"""
        self.running = False
        logger.info("Stopping Gmail background worker")

        for user_id, task in self.worker_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.worker_tasks.clear()
        self.processed_messages.clear()

    async def _monitor_users(self):
        """Monitor and manage user polling tasks"""
        while self.running:
            try:
                await self._sync_user_tasks()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in user monitoring: {e}")
                await asyncio.sleep(60)

    async def _sync_user_tasks(self):
        """Sync tasks with users who have Gmail connected"""
        try:
            with Session(engine) as session:
                statement = select(OAuthAccount).where(
                    OAuthAccount.provider == "google",
                    OAuthAccount.access_token.isnot(None)
                )
                oauth_accounts = session.exec(statement).all()

                active_user_ids = {str(account.user_id) for account in oauth_accounts}
                current_task_ids = set(self.worker_tasks.keys())

                # Start tasks for new users
                for user_id in active_user_ids - current_task_ids:
                    await self._start_user_task(user_id)

                # Stop tasks for users who no longer have Gmail connected
                for user_id in current_task_ids - active_user_ids:
                    await self._stop_user_task(user_id)

        except Exception as e:
            logger.error(f"Error syncing user tasks: {e}")

    async def _start_user_task(self, user_id: str):
        """Start polling task for a specific user"""
        if user_id in self.worker_tasks:
            return

        async def poll_user_gmail():
            logger.info(f"Started Gmail polling worker for user {user_id}")
            self.processed_messages[user_id] = set()

            while self.running:
                try:
                    # Get unread messages
                    messages = await gmail_service.get_user_messages(
                        user_id=user_id,
                        query="is:unread",
                        max_results=10
                    )

                    for message in messages:
                        message_id = message.get("id")
                        if message_id and message_id not in self.processed_messages[user_id]:
                            email_content = gmail_service.extract_message_content(message)
                            if email_content["body"]:
                                await gmail_service.process_and_classify_email(user_id, email_content)
                                self.processed_messages[user_id].add(message_id)

                    # Cleanup old processed messages
                    if len(self.processed_messages[user_id]) > 1000:
                        self.processed_messages[user_id] = set(
                            list(self.processed_messages[user_id])[-500:]
                        )

                    await asyncio.sleep(self.polling_interval)

                except asyncio.CancelledError:
                    logger.info(f"Gmail polling worker cancelled for user {user_id}")
                    break
                except Exception as e:
                    logger.error(f"Error in Gmail polling worker for user {user_id}: {e}")
                    await asyncio.sleep(self.polling_interval)

        task = asyncio.create_task(poll_user_gmail())
        self.worker_tasks[user_id] = task

    async def _stop_user_task(self, user_id: str):
        """Stop polling task for a specific user"""
        if user_id in self.worker_tasks:
            task = self.worker_tasks[user_id]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            del self.worker_tasks[user_id]
            if user_id in self.processed_messages:
                del self.processed_messages[user_id]
            logger.info(f"Stopped Gmail polling worker for user {user_id}")

    def get_status(self) -> Dict[str, any]:
        """Get worker status"""
        return {
            "running": self.running,
            "active_users": len(self.worker_tasks),
            "user_ids": list(self.worker_tasks.keys())
        }


# Global worker instance
gmail_worker = GmailBackgroundWorker()