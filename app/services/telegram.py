import asyncio
import logging
import os
import tempfile
from typing import Optional, Dict, Any
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Message as TelegramMessage
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User, ItemCreate
from ..crud import create_item_with_classification
from .message_classifier import message_classifier
from .open_ai import openai_service

logger = logging.getLogger(__name__)

class TelegramClientService:
    def __init__(self):
        self.clients: Dict[int, TelegramClient] = {}  # user_id -> client
        self.running = False

    async def create_client_session(self, user_id: int, phone: str) -> Dict[str, Any]:
        """Create a new Telegram client session for user authentication"""
        try:
            # Create a temporary client for authentication
            temp_client = TelegramClient(
                StringSession(),
                settings.TELEGRAM_API_ID,
                settings.TELEGRAM_API_HASH
            )

            await temp_client.connect()

            # Send authentication code
            result = await temp_client.send_code_request(phone)

            # Store the temp client for this session
            session_key = f"temp_{user_id}"
            self.clients[session_key] = temp_client

            return {
                "phone_code_hash": result.phone_code_hash,
                "session_key": session_key,
                "message": "Authentication code sent to your phone"
            }

        except Exception as e:
            logger.error(f"Error creating client session: {e}")
            raise

    async def verify_code_and_login(self, user_id: int, session_key: str, phone: str, code: str, password: Optional[str] = None) -> Dict[str, Any]:
        """Verify the authentication code and complete login"""
        try:
            temp_client = self.clients.get(session_key)
            if not temp_client:
                raise ValueError("Invalid session key")

            try:
                # Sign in with the code
                await temp_client.sign_in(phone, code)
            except Exception as e:
                # If 2FA is enabled, we need the password
                if "password" in str(e).lower() and password:
                    await temp_client.sign_in(password=password)
                else:
                    raise e

            # Get the session string to save
            session_string = temp_client.session.save()

            # Create the permanent client
            permanent_client = TelegramClient(
                StringSession(session_string),
                settings.TELEGRAM_API_ID,
                settings.TELEGRAM_API_HASH
            )

            await permanent_client.connect()

            # Store the permanent client
            self.clients[user_id] = permanent_client

            # Clean up temp client
            await temp_client.disconnect()
            del self.clients[session_key]

            # Set up message handler for this user
            await self.setup_message_handler(user_id, permanent_client)

            # Save session string to database
            await self.save_user_session(user_id, session_string)

            return {
                "success": True,
                "message": "Successfully logged in to Telegram"
            }

        except Exception as e:
            logger.error(f"Error verifying code: {e}")
            # Clean up on error
            if session_key in self.clients:
                await self.clients[session_key].disconnect()
                del self.clients[session_key]
            raise

    async def save_user_session(self, user_id: int, session_string: str):
        """Save user's Telegram session to database"""
        with Session(engine) as session:
            user = session.get(User, user_id)
            if user:
                # You'll need to add a telegram_session field to your User model
                user.telegram_session = session_string
                session.add(user)
                session.commit()

    async def restore_user_sessions(self):
        """Restore active Telegram sessions from database on startup"""
        with Session(engine) as session:
            statement = select(User).where(User.telegram_session.isnot(None))
            users = session.exec(statement).all()

            for user in users:
                try:
                    client = TelegramClient(
                        StringSession(user.telegram_session),
                        settings.TELEGRAM_API_ID,
                        settings.TELEGRAM_API_HASH
                    )

                    await client.connect()

                    if await client.is_user_authorized():
                        self.clients[user.id] = client
                        await self.setup_message_handler(user.id, client)
                        logger.info(f"Restored Telegram session for user {user.id}")
                    else:
                        # Session is invalid, remove it
                        user.telegram_session = None
                        session.add(user)
                        session.commit()
                        await client.disconnect()

                except Exception as e:
                    logger.error(f"Error restoring session for user {user.id}: {e}")

    async def setup_message_handler(self, user_id: int, client: TelegramClient):
        """Set up message event handler for a specific user"""
        @client.on(events.NewMessage(incoming=True))
        async def handle_incoming_message(event):
            try:
                await self.process_incoming_message(user_id, event.message, client)
            except Exception as e:
                logger.error(f"Error processing message for user {user_id}: {e}")

    async def process_incoming_message(self, user_id: int, message: TelegramMessage, client: TelegramClient):
        """Process incoming Telegram messages"""
        try:
            # Get user from database
            with Session(engine) as session:
                user = session.get(User, user_id)
                if not user:
                    return

            # Extract message content
            message_text = None
            message_type = "text"

            if message.text:
                message_text = message.text
                message_type = "text"
            elif message.voice:
                # Handle voice messages
                message_text = await self.transcribe_voice_message(message.voice, client)
                message_type = "voice"
            elif message.document and message.document.mime_type and message.document.mime_type.startswith('audio/'):
                # Handle audio files
                message_text = await self.transcribe_audio_message(message.document, client)
                message_type = "audio"
            else:
                # Skip non-text/voice messages for now
                return

            if not message_text:
                return

            # Get sender information
            sender = await message.get_sender()
            sender_name = getattr(sender, 'first_name', 'Unknown')
            if hasattr(sender, 'last_name') and sender.last_name:
                sender_name += f" {sender.last_name}"

            # Add sender context to message
            full_message = f"From {sender_name}: {message_text}"

            # Classify and save the message
            await self.classify_and_save_message(user, full_message, message_type, "telegram")

        except Exception as e:
            logger.error(f"Error processing incoming message: {e}")

    async def transcribe_voice_message(self, voice, client: TelegramClient) -> Optional[str]:
        """Transcribe a voice message using OpenAI"""
        try:
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
                temp_path = temp_file.name

            try:
                # Download the voice file
                await client.download_media(voice, temp_path)

                # Use OpenAI service to transcribe
                return await openai_service.transcribe_voice_message(
                    temp_path,
                    output_format="wav"
                )

            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Error transcribing voice message: {e}")
            return None

    async def transcribe_audio_message(self, document, client: TelegramClient) -> Optional[str]:
        """Transcribe an audio document using OpenAI"""
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                temp_path = temp_file.name

            try:
                # Download the audio file
                await client.download_media(document, temp_path)

                # Use OpenAI service to transcribe
                return await openai_service.transcribe_audio(
                    temp_path,
                    language=settings.OPENAI_WHISPER_LANGUAGE
                )

            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Error transcribing audio message: {e}")
            return None

    async def classify_and_save_message(self, user: User, message: str, message_type: str, source: str):
        """Classify and save incoming message to database"""
        try:
            # Classify the message
            classification_result = None
            try:
                classification_result = await message_classifier.classify_message(
                    text=message,
                    source=source
                )
                logger.info(f"Message classified as: {classification_result.get('category', 'unknown')}")
            except Exception as e:
                logger.error(f"Failed to classify message: {e}")

            # Save to database
            with Session(engine) as session:
                item_create = ItemCreate(
                    title="",
                    description="",
                    source=source,
                    message_type=message_type,
                    original_text=message
                )

                create_item_with_classification(
                    session=session,
                    item_in=item_create,
                    owner_id=user.id,
                    classification=classification_result
                )

        except Exception as e:
            logger.error(f"Error classifying and saving message: {e}")

    async def disconnect_user(self, user_id: int):
        """Disconnect a user's Telegram client"""
        if user_id in self.clients:
            try:
                await self.clients[user_id].disconnect()
                del self.clients[user_id]

                # Remove session from database
                with Session(engine) as session:
                    user = session.get(User, user_id)
                    if user:
                        user.telegram_session = None
                        session.add(user)
                        session.commit()

                logger.info(f"Disconnected Telegram client for user {user_id}")
            except Exception as e:
                logger.error(f"Error disconnecting user {user_id}: {e}")

    async def start(self):
        """Start the Telegram client service"""
        if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
            logger.warning("Telegram API credentials not configured")
            return

        self.running = True
        await self.restore_user_sessions()
        logger.info("Telegram client service started")

    async def stop(self):
        """Stop the Telegram client service"""
        self.running = False

        # Disconnect all clients
        for user_id, client in list(self.clients.items()):
            try:
                await client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting client for user {user_id}: {e}")

        self.clients.clear()
        logger.info("Telegram client service stopped")

# Global instance
telegram_client_service = TelegramClientService()