import secrets
import string
from datetime import datetime, timedelta
from typing import Any, Dict
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.config import settings
from ..models import OAuthAccount, OAuthAccountCreate, User, GoogleOAuthUserInfo
from app import crud


class GoogleOAuthService:
    def __init__(self):
        self.client_id = settings.GOOGLE_CLIENT_ID
        self.client_secret = settings.GOOGLE_CLIENT_SECRET
        self.redirect_uri = settings.GOOGLE_REDIRECT_URI
        self.authorization_base_url = "https://accounts.google.com/o/oauth2/v2/auth"
        self.token_url = "https://oauth2.googleapis.com/token"
        self.userinfo_url = "https://www.googleapis.com/oauth2/v2/userinfo"

    def generate_state(self) -> str:
        """Generate a random state string for OAuth2 security."""
        return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))

    def get_authorization_url(self, state: str) -> str:
        """Generate the authorization URL for Google OAuth2."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": "openid email profile https://www.googleapis.com/auth/gmail.readonly",
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{self.authorization_base_url}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token."""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.token_url, data=data)

        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail="Failed to exchange code for token"
            )

        return response.json()

    async def get_user_info(self, access_token: str) -> GoogleOAuthUserInfo:
        """Get user information from Google using access token."""
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(self.userinfo_url, headers=headers)

        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail="Failed to get user information from Google"
            )

        user_data = response.json()
        return GoogleOAuthUserInfo(**user_data)

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh the access token using refresh token."""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(self.token_url, data=data)

        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail="Failed to refresh access token"
            )

        return response.json()


async def create_or_update_oauth_account(
        session: Session, user_id: str, token_data: Dict[str, Any], user_info: GoogleOAuthUserInfo
) -> OAuthAccount:
    """Create or update OAuth account for a user."""
    print(f"DEBUG: Starting create_or_update_oauth_account for user {user_id}")
    # Import here to avoid circular imports
    from .gmail import gmail_service

    # Check if OAuth account already exists
    statement = select(OAuthAccount).where(
        OAuthAccount.user_id == user_id,
        OAuthAccount.provider == "google",
        OAuthAccount.provider_account_id == user_info.id
    )
    oauth_account = session.exec(statement).first()

    expires_at = None
    if "expires_in" in token_data:
        expires_at = datetime.utcnow() + timedelta(seconds=token_data["expires_in"])

    is_new_account = False
    if oauth_account:
        # Update existing account
        oauth_account.access_token = token_data["access_token"]
        oauth_account.refresh_token = token_data.get("refresh_token")
        oauth_account.expires_at = expires_at
        oauth_account.token_type = token_data.get("token_type", "Bearer")
        oauth_account.scope = token_data.get("scope")
        oauth_account.updated_at = datetime.utcnow()
        oauth_account.provider_account_email = user_info.email
    else:
        # Create new account
        is_new_account = True
        oauth_account = OAuthAccount(
            user_id=user_id,
            provider="google",
            provider_account_id=user_info.id,
            provider_account_email=user_info.email,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=expires_at,
            token_type=token_data.get("token_type", "Bearer"),
            scope=token_data.get("scope"),
        )
        session.add(oauth_account)

    session.commit()
    print(f"DEBUG: Successfully saved OAuth account to database")
    session.refresh(oauth_account)

    # Auto-start Gmail polling for new connections
    if is_new_account or not oauth_account:
        try:
            await gmail_service.auto_start_polling_for_new_user(user_id)
        except Exception as e:
            # Don't fail the OAuth flow if polling fails to start
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to auto-start Gmail polling for user {user_id}: {e}")

    return oauth_account


# Initialize the service
google_oauth_service = GoogleOAuthService()