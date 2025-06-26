from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.services.telegram import telegram_client_service
from app.models import Message

router = APIRouter(prefix="/telegram", tags=["telegram"])

class TelegramAuthRequest(BaseModel):
    phone: str

class TelegramVerifyRequest(BaseModel):
    session_key: str
    phone: str
    code: str
    password: Optional[str] = None

@router.post("/auth/start")
async def start_telegram_auth(
        request: TelegramAuthRequest,
        current_user: CurrentUser
) -> Any:
    """Start Telegram authentication process"""
    try:
        result = await telegram_client_service.create_client_session(
            current_user.id,
            request.phone
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/auth/verify")
async def verify_telegram_auth(
        request: TelegramVerifyRequest,
        current_user: CurrentUser
) -> Any:
    """Verify Telegram authentication code"""
    try:
        result = await telegram_client_service.verify_code_and_login(
            current_user.id,
            request.session_key,
            request.phone,
            request.code,
            request.password
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/disconnect")
async def disconnect_telegram(current_user: CurrentUser) -> Message:
    """Disconnect user's Telegram client"""
    try:
        await telegram_client_service.disconnect_user(current_user.id)
        return Message(message="Telegram disconnected successfully")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/status")
async def get_telegram_status(current_user: CurrentUser) -> Any:
    """Get user's Telegram connection status"""
    is_connected = current_user.id in telegram_client_service.clients
    return {
        "connected": is_connected,
        "has_session": bool(current_user.telegram_session)
    }