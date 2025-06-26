import logging
import os
import tempfile
from typing import Optional
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import User, ItemCreate
from ..crud import create_item_with_classification
from .message_classifier import message_classifier
from .open_ai import openai_service

logger = logging.getLogger(__name__)

class TelegramBotService:
    def __init__(self, token: str):
        self.application = Application.builder().token(token).build()
        self._setup_handlers()

    def _setup_handlers(self):
        """Set up message and command handlers"""
        # Handle /start command
        self.application.add_handler(CommandHandler("start", self.start_command))

        # Handle text messages
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        # Handle voice messages
        self.application.add_handler(MessageHandler(filters.VOICE, self.handle_voice_message))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user

        # Find user by telegram username
        db_user = await self.find_user_by_telegram(user.username)

        if db_user:
            await update.message.reply_text(
                f"Hello {db_user.full_name or user.first_name}! Your account is connected.\n"
                f"You can send me text messages or voice messages, and I'll process and classify them for you."
            )
        else:
            await update.message.reply_text(
                "Hello! To use this bot, please register your Telegram username in your profile settings first.\n"
                "Once registered, you can send me text or voice messages!"
            )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages"""
        user = update.effective_user
        message_text = update.message.text

        # Find the user in your database
        db_user = await self.find_user_by_telegram(user.username)

        if not db_user:
            await update.message.reply_text(
                "I don't recognize you. Please register your Telegram username in your profile first."
            )
            return

        # Process the message
        await self.process_user_message(db_user, message_text, update, message_type="text")

    async def handle_voice_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming voice messages"""
        user = update.effective_user
        voice = update.message.voice

        # Find the user in your database
        db_user = await self.find_user_by_telegram(user.username)

        if not db_user:
            await update.message.reply_text(
                "I don't recognize you. Please register your Telegram username in your profile first."
            )
            return

        # Show processing message
        processing_message = await update.message.reply_text("🎤 Processing your voice message...")

        try:
            # Download and transcribe the voice message
            transcribed_text = await self.transcribe_voice_message(voice)

            if transcribed_text:
                # Update the processing message with transcription
                await processing_message.edit_text(f"📝 I heard: \"{transcribed_text}\"")

                # Process the message in your system
                await self.process_user_message(db_user, transcribed_text, update, message_type="voice")
            else:
                await processing_message.edit_text(
                    "❌ Sorry, I couldn't understand your voice message. Please try again or send a text message."
                )

        except Exception as e:
            logger.error(f"Error processing voice message: {e}")
            await processing_message.edit_text(
                "❌ Sorry, there was an error processing your voice message. Please try again."
            )


    async def transcribe_voice_message(self, voice) -> Optional[str]:
        """Transcribe a Telegram voice message using OpenAI"""
        try:
            # Create temporary file for the voice message
            with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as temp_file:
                temp_path = temp_file.name

            try:
                # Download the voice file
                voice_file = await voice.get_file()
                await voice_file.download_to_drive(temp_path)

                # Use OpenAI service to transcribe
                return await openai_service.transcribe_voice_message(
                    temp_path,
                    output_format="wav"
                )

            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Error transcribing voice message: {e}")
            return None

    async def transcribe_audio_file(self, audio) -> Optional[str]:
        """Transcribe an audio file using OpenAI"""
        try:
            # Create temporary file for the audio
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                temp_path = temp_file.name

            try:
                # Download the audio file
                audio_file = await audio.get_file()
                await audio_file.download_to_drive(temp_path)

                # Use OpenAI service to transcribe
                return await openai_service.transcribe_audio(
                    temp_path,
                    language=settings.OPENAI_WHISPER_LANGUAGE
                )

            finally:
                # Clean up temporary file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Error transcribing audio file: {e}")
            return None

    async def find_user_by_telegram(self, telegram_username: Optional[str]) -> Optional[User]:
        """Find user by telegram username"""
        if not telegram_username:
            return None

        with Session(engine) as session:
            statement = select(User).where(User.telegram_tag == telegram_username)
            return session.exec(statement).first()

    async def process_user_message(self, user: User, message: str, update: Update, message_type: str = "text"):
        """Process message from authenticated user with classification"""
        logger.info(f"Processing {message_type} message from user {user.id}: {message}")

        try:
            # Classify the message first (outside of database transaction)
            classification_result = None
            try:
                classification_result = await message_classifier.classify_message(
                    text=message,
                    source="telegram"
                )
                logger.info(f"Message classified as: {classification_result.get('category', 'unknown')}")
            except Exception as e:
                logger.error(f"Failed to classify message: {e}")


            with Session(engine) as session:
                item_create = ItemCreate(
                    title="",
                    description="",
                    source="telegram",
                    message_type=message_type,
                    original_text=message
                )


                item = create_item_with_classification(
                    session=session,
                    item_in=item_create,
                    owner_id=user.id,
                    classification=classification_result
                )

                # Send response based on classification
                await self.send_classification_response(update, item)

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await update.message.reply_text(
                "❌ Sorry, there was an error processing your message. Please try again."
            )

    async def send_classification_response(self, update: Update, item):
        """Send response based on message classification"""
        if not item.classification:
            await update.message.reply_text("✅ Message saved successfully!")
            return

        classification = item.classification

        # Now access attributes directly since classification is an ItemClassification object
        category = classification.category.value  # Access enum value
        priority = classification.priority.value  # Access enum value
        action_required = classification.action_required
        summary = classification.summary
        confidence = classification.confidence

        # Category emojis
        category_emojis = {
            "meeting": "📅",
            "task": "✅",
            "information": "ℹ️",
            "thought": "💭"
        }

        # Priority emojis
        priority_emojis = {
            "high": "🔴",
            "medium": "🟡",
            "low": "🟢"
        }

        emoji = category_emojis.get(category, "📝")
        priority_emoji = priority_emojis.get(priority, "🟡")

        response_text = f"{emoji} **Message Classified**\n\n"
        response_text += f"**Category:** {category.title()}\n"
        response_text += f"**Priority:** {priority_emoji} {priority.title()}\n"
        response_text += f"**Confidence:** {confidence:.1%}\n"

        if action_required:
            response_text += f"**Action Required:** ⚠️ Yes\n"

        if summary:
            response_text += f"**Summary:** {summary}\n"

        # Add entity information if available
        if (classification.dates or classification.times or classification.contacts or
                classification.projects or classification.keywords):
            response_text += "\n**Extracted Information:**\n"
            if classification.dates:
                response_text += f"📅 Dates: {', '.join(classification.dates)}\n"
            if classification.times:
                response_text += f"⏰ Times: {', '.join(classification.times)}\n"
            if classification.contacts:
                response_text += f"👥 Contacts: {', '.join(classification.contacts)}\n"
            if classification.projects:
                response_text += f"📁 Projects: {', '.join(classification.projects)}\n"
            if classification.keywords:
                response_text += f"🔍 Keywords: {', '.join(classification.keywords[:3])}\n"  # Show first 3 keywords

        response_text += f"\n✅ Message saved successfully!"

        await update.message.reply_text(response_text, parse_mode='Markdown')

    async def start(self):
        """Initialize and start the bot"""
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

    async def stop(self):
        """Stop the bot"""
        if self.application.updater:
            await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()