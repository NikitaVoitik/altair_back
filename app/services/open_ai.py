import logging
import tempfile
import os
from typing import Optional
from openai import AsyncOpenAI
from pydub import AudioSegment

from app.core.config import settings

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("OpenAI API key not configured")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def transcribe_audio(self, audio_file_path: str, language: Optional[str] = None) -> Optional[str]:
        """
        Transcribe audio file using OpenAI Whisper API

        Args:
            audio_file_path: Path to the audio file
            language: Optional language code (e.g., 'en', 'es', 'fr')

        Returns:
            Transcribed text or None if transcription fails
        """
        if not self.client:
            logger.error("OpenAI client not initialized - API key missing")
            return None

        try:
            with open(audio_file_path, "rb") as audio_file:
                # Use OpenAI Whisper API for transcription
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language,  # Optional: specify language for better accuracy
                    response_format="text"
                )

                logger.info(f"Successfully transcribed audio file: {audio_file_path}")
                return transcript.strip()

        except Exception as e:
            logger.error(f"Error transcribing audio with OpenAI: {e}")
            return None

    async def transcribe_voice_message(self, voice_file_path: str, output_format: str = "wav") -> Optional[str]:
        """
        Convert and transcribe a voice message

        Args:
            voice_file_path: Path to the voice file (usually .oga from Telegram)
            output_format: Format to convert to before transcription

        Returns:
            Transcribed text or None if transcription fails
        """
        if not self.client:
            logger.error("OpenAI client not initialized - API key missing")
            return None

        # Create temporary file for converted audio
        with tempfile.NamedTemporaryFile(suffix=f".{output_format}", delete=False) as temp_file:
            temp_path = temp_file.name

        try:
            # Convert audio to the specified format
            audio = AudioSegment.from_file(voice_file_path)

            # Optimize for speech recognition
            # Normalize audio levels and convert to mono
            audio = audio.normalize()
            if audio.channels > 1:
                audio = audio.set_channels(1)

            # Export to temporary file
            audio.export(temp_path, format=output_format)

            # Transcribe using OpenAI
            return await self.transcribe_audio(temp_path)

        except Exception as e:
            logger.error(f"Error processing voice message: {e}")
            return None

        finally:
            # Clean up temporary file
            if os.path.exists(temp_path):
                os.unlink(temp_path)


# Global OpenAI service instance
openai_service = OpenAIService()