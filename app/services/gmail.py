import asyncio
import base64
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

import httpx
from sqlmodel import Session, select
from fastapi import HTTPException

from app.core.db import engine
from app.models import OAuthAccount, ItemCreate
from app.crud import create_item_with_classification
from .message_classifier import message_classifier
from .oauth import google_oauth_service

logger = logging.getLogger(__name__)


class GmailService:
    def __init__(self):
        self.base_url = "https://gmail.googleapis.com/gmail/v1"
        self.polling_interval = 30  # seconds
        self._polling_tasks = {}  # user_id -> asyncio.Task
        self.auto_start_polling = True  # Enable auto-start by default

    async def auto_start_polling_for_new_user(self, user_id: str):
        """Auto-start Gmail polling for a new user connection"""
        if not self.auto_start_polling:
            logger.info(f"Auto-start polling disabled, skipping for user {user_id}")
            return

        try:
            await self.start_polling_for_user(user_id)
            logger.info(f"Auto-started Gmail polling for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to auto-start Gmail polling for user {user_id}: {e}")
            raise

    async def start_polling_for_user(self, user_id: str):
        """Start polling Gmail for a specific user"""
        # Stop existing polling task if any
        await self.stop_polling_for_user(user_id)

        # Verify user has valid OAuth connection
        oauth_account = await self._get_valid_oauth_account(user_id)
        if not oauth_account:
            raise Exception(f"No valid OAuth account found for user {user_id}")

        # Create and start polling task
        task = asyncio.create_task(self._poll_user_messages(user_id))
        self._polling_tasks[user_id] = task
        logger.info(f"Started Gmail polling for user {user_id}")

    async def stop_polling_for_user(self, user_id: str):
        """Stop polling Gmail for a specific user"""
        if user_id in self._polling_tasks:
            task = self._polling_tasks[user_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            del self._polling_tasks[user_id]
            logger.info(f"Stopped Gmail polling for user {user_id}")

    async def _poll_user_messages(self, user_id: str):
        """Continuously poll Gmail messages for a user"""
        logger.info(f"Starting message polling for user {user_id}")

        while True:
            try:
                # Get unread messages
                messages = await self.get_user_messages(
                    user_id=user_id,
                    query="is:unread",
                    max_results=10
                )

                # Process each message
                for message in messages:
                    try:
                        email_content = self.extract_message_content(message)
                        await self.process_and_classify_email(user_id, email_content)

                        # Mark as read (optional)
                        await self._mark_message_as_read(user_id, message.get("id"))

                    except Exception as e:
                        logger.error(f"Error processing message for user {user_id}: {e}")

                # Wait for next polling interval
                await asyncio.sleep(self.polling_interval)

            except asyncio.CancelledError:
                logger.info(f"Polling cancelled for user {user_id}")
                break
            except Exception as e:
                logger.error(f"Error in polling loop for user {user_id}: {e}")
                # Wait before retrying
                await asyncio.sleep(self.polling_interval)

    async def _mark_message_as_read(self, user_id: str, message_id: str):
        """Mark a Gmail message as read"""
        oauth_account = await self._get_valid_oauth_account(user_id)
        if not oauth_account:
            return

        headers = {
            "Authorization": f"Bearer {oauth_account.access_token}",
            "Content-Type": "application/json"
        }

        url = f"{self.base_url}/users/me/messages/{message_id}/modify"
        data = {
            "removeLabelIds": ["UNREAD"]
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=headers, json=data)
                if response.status_code != 200:
                    logger.error(f"Failed to mark message as read: {response.status_code}")
            except Exception as e:
                logger.error(f"Error marking message as read: {e}")

    async def get_polling_status(self, user_id: str) -> Dict[str, Any]:
        """Get polling status for a user"""
        is_polling = user_id in self._polling_tasks and not self._polling_tasks[user_id].done()
        oauth_account = await self._get_valid_oauth_account(user_id)

        return {
            "is_polling": is_polling,
            "has_oauth_connection": oauth_account is not None,
            "polling_interval": self.polling_interval
        }

    async def shutdown(self):
        """Shutdown all polling tasks"""
        logger.info("Shutting down Gmail polling for all users")
        for user_id in list(self._polling_tasks.keys()):
            await self.stop_polling_for_user(user_id)


    async def get_user_messages(
            self,
            user_id: str,
            query: str = "is:unread",
            max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """Get messages for a user from Gmail API"""
        oauth_account = await self._get_valid_oauth_account(user_id)
        if not oauth_account:
            logger.warning(f"No valid OAuth account found for user {user_id}")
            return []

        headers = {
            "Authorization": f"Bearer {oauth_account.access_token}",
            "Content-Type": "application/json"
        }

        # Get list of messages
        url = f"{self.base_url}/users/me/messages"
        params = {
            "q": query,
            "maxResults": max_results
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers, params=params)

                if response.status_code == 401:
                    # Token expired, try to refresh
                    oauth_account = await self._refresh_token_if_needed(oauth_account)
                    if oauth_account:
                        headers["Authorization"] = f"Bearer {oauth_account.access_token}"
                        response = await client.get(url, headers=headers, params=params)
                    else:
                        logger.error(f"Failed to refresh token for user {user_id}")
                        return []

                if response.status_code != 200:
                    logger.error(f"Gmail API error: {response.status_code} - {response.text}")
                    return []

                data = response.json()
                messages = data.get("messages", [])

                # Get full message details
                full_messages = []
                for message in messages:
                    full_message = await self._get_message_details(
                        message["id"],
                        headers,
                        client
                    )
                    if full_message:
                        full_messages.append(full_message)

                return full_messages

            except Exception as e:
                logger.error(f"Error fetching Gmail messages for user {user_id}: {e}")
                return []

    async def _get_message_details(
            self,
            message_id: str,
            headers: Dict[str, str],
            client: httpx.AsyncClient
    ) -> Optional[Dict[str, Any]]:
        """Get detailed message information"""
        url = f"{self.base_url}/users/me/messages/{message_id}"

        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get message details: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error getting message details: {e}")
            return None

    def extract_message_content(self, message: Dict[str, Any]) -> Dict[str, str]:
        """Extract readable content from Gmail message"""
        payload = message.get("payload", {})
        headers = payload.get("headers", [])

        # Extract headers
        subject = ""
        sender = ""
        date = ""
        for header in headers:
            name = header.get("name", "").lower()
            value = header.get("value", "")
            if name == "subject":
                subject = value
            elif name == "from":
                sender = value
            elif name == "date":
                date = value

        # Extract body
        body = ""
        if "parts" in payload:
            # Multipart message
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    body_data = part.get("body", {}).get("data", "")
                    if body_data:
                        body = base64.urlsafe_b64decode(body_data).decode("utf-8")
                        break
        else:
            # Single part message
            if payload.get("mimeType") == "text/plain":
                body_data = payload.get("body", {}).get("data", "")
                if body_data:
                    body = base64.urlsafe_b64decode(body_data).decode("utf-8")

        return {
            "subject": subject,
            "sender": sender,
            "date": date,
            "body": body,
            "message_id": message.get("id", ""),
            "thread_id": message.get("threadId", "")
        }

    async def process_and_classify_email(
            self,
            user_id: str,
            email_content: Dict[str, str]
    ) -> Optional[Any]:
        """Process and classify an email message"""
        # Combine subject and body for classification
        full_text = f"Subject: {email_content['subject']}\n\nFrom: {email_content['sender']}\n\n{email_content['body']}"

        try:
            # Classify the email
            classification_result = await message_classifier.classify_message(
                text=full_text,
                source="gmail"
            )

            with Session(engine) as session:
                # Create item with email metadata
                item_create = ItemCreate(
                    title=email_content['subject'] or "No Subject",
                    description=email_content['body'][:1000],  # Limit description length
                    source="gmail",
                    message_type="email",
                    original_text=full_text,
                    metadata={
                        "gmail_message_id": email_content['message_id'],
                        "gmail_thread_id": email_content['thread_id'],
                        "sender": email_content['sender'],
                        "date": email_content['date']
                    }
                )

                item = create_item_with_classification(
                    session=session,
                    item_in=item_create,
                    owner_id=user_id,
                    classification=classification_result
                )

                logger.info(f"Processed and classified email for user {user_id}: {email_content['subject']}")
                return item

        except Exception as e:
            logger.error(f"Error processing email for user {user_id}: {e}")
            return None

    async def _get_valid_oauth_account(self, user_id: str) -> Optional[OAuthAccount]:
        """Get a valid OAuth account for the user"""
        with Session(engine) as session:
            statement = select(OAuthAccount).where(
                OAuthAccount.user_id == user_id,
                OAuthAccount.provider == "google"
            )
            oauth_account = session.exec(statement).first()

            if not oauth_account:
                return None

            # Check if token is expired
            if oauth_account.expires_at and oauth_account.expires_at <= datetime.utcnow():
                return await self._refresh_token_if_needed(oauth_account)

            return oauth_account

    async def _refresh_token_if_needed(self, oauth_account: OAuthAccount) -> Optional[OAuthAccount]:
        """Refresh OAuth token if needed"""
        if not oauth_account.refresh_token:
            logger.error(f"No refresh token available for user {oauth_account.user_id}")
            return None

        try:
            token_data = await google_oauth_service.refresh_access_token(
                oauth_account.refresh_token
            )

            with Session(engine) as session:
                # Get fresh instance from database
                statement = select(OAuthAccount).where(OAuthAccount.id == oauth_account.id)
                fresh_account = session.exec(statement).first()

                if fresh_account:
                    fresh_account.access_token = token_data["access_token"]
                    if "expires_in" in token_data:
                        fresh_account.expires_at = datetime.utcnow() + timedelta(
                            seconds=token_data["expires_in"]
                        )
                    fresh_account.updated_at = datetime.utcnow()
                    session.commit()
                    session.refresh(fresh_account)
                    return fresh_account

        except Exception as e:
            logger.error(f"Failed to refresh token for user {oauth_account.user_id}: {e}")

        return None

    async def setup_gmail_watch(self, user_id: str, topic_name: str):
        """Set up Gmail push notifications (alternative to polling)"""
        oauth_account = await self._get_valid_oauth_account(user_id)
        if not oauth_account:
            raise HTTPException(status_code=400, detail="No valid Gmail connection")

        headers = {
            "Authorization": f"Bearer {oauth_account.access_token}",
            "Content-Type": "application/json"
        }

        url = f"{self.base_url}/users/me/watch"
        data = {
            "topicName": topic_name,
            "labelIds": ["INBOX"],
            "labelFilterAction": "include"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=data)

            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to set up Gmail watch: {response.text}"
                )

            return response.json()


# Global Gmail service instance
gmail_service = GmailService()