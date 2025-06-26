from typing import Any
import uuid
from datetime import datetime

from app.api.deps import SessionDep, CurrentUser, get_current_user
from app.core.config import settings
from app.models import Message, OAuthAccount, OAuthAccountPublic
from app.services.oauth import google_oauth_service, create_or_update_oauth_account
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from sqlmodel import select

router = APIRouter(prefix="/auth", tags=["oauth"])

# Store state temporarily (in production, use Redis or database)
_oauth_states = {}

@router.get("/google")
async def google_oauth_login(request: Request, current_user: CurrentUser) -> dict[str, str]:
    """Initiate Google OAuth2 login flow."""
    state = google_oauth_service.generate_state()

    # Store user ID with state for later retrieval
    _oauth_states[state] = {
        "user_id": str(current_user.id),
        "created_at": datetime.utcnow()
    }

    authorization_url = google_oauth_service.get_authorization_url(state)

    return {
        "authorization_url": authorization_url,
        "state": state
    }


@router.get("/google/callback")
async def google_oauth_callback(
        code: str,
        state: str,
        session: SessionDep,
        error: str | None = None,
) -> Any:
    """Handle Google OAuth2 callback."""
    if error:
        redirect_url = f"{settings.FRONTEND_HOST}/settings?oauth_error=true&error={error}"
        return RedirectResponse(url=redirect_url)

    if not code:
        redirect_url = f"{settings.FRONTEND_HOST}/settings?oauth_error=true&error=authorization_code_not_provided"
        return RedirectResponse(url=redirect_url)

    # Validate state and get user ID
    state_data = _oauth_states.get(state)
    if not state_data:
        redirect_url = f"{settings.FRONTEND_HOST}/settings?oauth_error=true&error=invalid_state"
        return RedirectResponse(url=redirect_url)

    user_id = state_data["user_id"]
    # Clean up state after use
    del _oauth_states[state]

    try:
        # Exchange code for token
        token_data = await google_oauth_service.exchange_code_for_token(code)

        # Get user info from Google
        user_info = await google_oauth_service.get_user_info(token_data["access_token"])

        # Create or update OAuth account
        oauth_account = create_or_update_oauth_account(
            session, uuid.UUID(user_id), token_data, user_info
        )

        # Redirect to frontend success page
        redirect_url = f"{settings.FRONTEND_HOST}/settings?oauth_success=true&provider=google"
        return RedirectResponse(url=redirect_url)

    except Exception as e:
        # Redirect to frontend error page
        redirect_url = f"{settings.FRONTEND_HOST}/settings?oauth_error=true&error={str(e)}"
        return RedirectResponse(url=redirect_url)


@router.get("/google/accounts", response_model=list[OAuthAccountPublic])
def get_google_accounts(
        session: SessionDep,
        current_user: CurrentUser,
) -> Any:
    """Get user's connected Google accounts."""
    statement = select(OAuthAccount).where(
        OAuthAccount.user_id == current_user.id,
        OAuthAccount.provider == "google"
    )
    accounts = session.exec(statement).all()
    return accounts


@router.delete("/google/accounts/{account_id}")
def disconnect_google_account(
        account_id: str,
        session: SessionDep,
        current_user: CurrentUser,
) -> Message:
    """Disconnect a Google account."""
    statement = select(OAuthAccount).where(
        OAuthAccount.id == account_id,
        OAuthAccount.user_id == current_user.id,
        OAuthAccount.provider == "google"
    )
    oauth_account = session.exec(statement).first()

    if not oauth_account:
        raise HTTPException(status_code=404, detail="OAuth account not found")

    session.delete(oauth_account)
    session.commit()

    return Message(message="Google account disconnected successfully")


@router.post("/google/refresh/{account_id}")
async def refresh_google_token(
        account_id: str,
        session: SessionDep,
        current_user: CurrentUser,
) -> Message:
    """Refresh Google OAuth token."""
    statement = select(OAuthAccount).where(
        OAuthAccount.id == account_id,
        OAuthAccount.user_id == current_user.id,
        OAuthAccount.provider == "google"
    )
    oauth_account = session.exec(statement).first()

    if not oauth_account:
        raise HTTPException(status_code=404, detail="OAuth account not found")

    if not oauth_account.refresh_token:
        raise HTTPException(status_code=400, detail="No refresh token available")

    try:
        # Refresh the token
        token_data = await google_oauth_service.refresh_access_token(oauth_account.refresh_token)

        # Update the account with new token
        oauth_account.access_token = token_data["access_token"]
        if "refresh_token" in token_data:
            oauth_account.refresh_token = token_data["refresh_token"]
        if "expires_in" in token_data:
            from datetime import datetime, timedelta
            oauth_account.expires_at = datetime.utcnow() + timedelta(seconds=token_data["expires_in"])

        oauth_account.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(oauth_account)

        return Message(message="Token refreshed successfully")

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to refresh token: {str(e)}")