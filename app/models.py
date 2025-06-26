import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List

from pydantic import EmailStr
from sqlmodel import Field, Relationship, SQLModel, JSON, Column


# Shared properties
class UserBase(SQLModel):
    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)
    telegram_session: Optional[str] = Field(default=None, index=True)


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=40)


class UserRegister(SQLModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=40)
    full_name: str | None = Field(default=None, max_length=255)
    telegram_tag: str | None = Field(default=None, max_length=50)


# Properties to receive via API on update, all are optional
class UserUpdate(UserBase):
    email: EmailStr | None = Field(default=None, max_length=255)  # type: ignore
    password: str | None = Field(default=None, min_length=8, max_length=40)


class UserUpdateMe(SQLModel):
    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)
    telegram_tag: str | None = Field(default=None, max_length=50)


class UpdatePassword(SQLModel):
    current_password: str = Field(min_length=8, max_length=40)
    new_password: str = Field(min_length=8, max_length=40)


# OAuth Account model
class OAuthAccount(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", nullable=False)
    provider: str = Field(max_length=50)  # 'google', 'github', etc.
    provider_account_id: str = Field(max_length=255)  # OAuth provider's user ID
    provider_account_email: EmailStr = Field(max_length=255)
    access_token: str = Field(max_length=2048)
    refresh_token: str | None = Field(default=None, max_length=2048)
    expires_at: datetime | None = Field(default=None)
    token_type: str | None = Field(default="Bearer", max_length=50)
    scope: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationship
    user: "User" = Relationship(back_populates="oauth_accounts")


# Database model, database table inferred from class name
class User(UserBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    items: list["Item"] = Relationship(back_populates="owner", cascade_delete=True)
    oauth_accounts: list["OAuthAccount"] = Relationship(back_populates="user", cascade_delete=True)


# OAuth models for API
class OAuthAccountCreate(SQLModel):
    provider: str
    provider_account_id: str
    provider_account_email: EmailStr
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    token_type: str | None = "Bearer"
    scope: str | None = None


class OAuthAccountPublic(SQLModel):
    id: uuid.UUID
    provider: str
    provider_account_email: EmailStr
    created_at: datetime
    expires_at: datetime | None


class GoogleOAuthUserInfo(SQLModel):
    id: str
    email: str
    name: str
    picture: str | None = None
    given_name: str | None = None
    family_name: str | None = None


# Properties to return via API, id is always required
class UserPublic(UserBase):
    id: uuid.UUID


class UsersPublic(SQLModel):
    data: list[UserPublic]
    count: int


# Classification models
class CategoryEnum(str, Enum):
    MEETING = "meeting"
    TASK = "task"
    INFORMATION = "information"
    THOUGHT = "thought"


class PriorityEnum(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClassificationEntities(SQLModel):
    dates: List[str] = Field(default_factory=list)
    times: List[str] = Field(default_factory=list)
    contact: str | None = Field(default=None)  # Changed from contacts array to single contact
    projects: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


class ItemClassification(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    item_id: uuid.UUID = Field(foreign_key="item.id", nullable=False)

    category: CategoryEnum = Field(default=CategoryEnum.INFORMATION)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    priority: PriorityEnum = Field(default=PriorityEnum.MEDIUM)
    action_required: bool = Field(default=False)
    summary: str = Field(default="", max_length=500)

    # Entity fields - changed contacts to single contact
    dates: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    times: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    contact: str | None = Field(default=None, max_length=255)  # Single contact instead of array
    projects: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    keywords: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationship
    item: "Item" = Relationship(back_populates="classification")


# Shared properties
class ItemBase(SQLModel):
    title: str = Field(default="", max_length=255)
    description: str | None = Field(default=None, max_length=255)


# Properties to receive on item creation
class ItemCreate(ItemBase):
    source: str | None = Field(default=None, max_length=100)
    message_type: str | None = Field(default=None, max_length=50)
    original_text: str | None = Field(default=None)


# Properties to receive on item update
class ItemUpdate(ItemBase):
    title: str | None = Field(default=None, min_length=1, max_length=255)  # type: ignore


# Database model, database table inferred from class name
class Item(ItemBase, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        foreign_key="user.id",
        nullable=False,
    )
    owner: User | None = Relationship(back_populates="items")

    source: str | None = Field(default=None, max_length=100)  # telegram, web, etc.
    message_type: str | None = Field(default=None, max_length=50)  # text, voice
    original_text: str | None = Field(default=None)  # Original message text
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationship to classification
    classification: Optional["ItemClassification"] = Relationship(back_populates="item")


class ClassificationPublic(SQLModel):
    id: uuid.UUID
    category: CategoryEnum
    confidence: float
    priority: PriorityEnum
    action_required: bool
    summary: str
    dates: List[str]
    times: List[str]
    contact: str | None  # Changed from contacts array to single contact
    projects: List[str]
    keywords: List[str]
    created_at: datetime


class ItemPublic(ItemBase):
    id: uuid.UUID
    owner_id: uuid.UUID
    source: str | None = None
    message_type: str | None = None
    created_at: datetime
    classification: Optional[ClassificationPublic] = None


class ItemsPublic(SQLModel):
    data: list[ItemPublic]
    count: int


# Generic message
class Message(SQLModel):
    message: str


# JSON payload containing access token
class Token(SQLModel):
    access_token: str
    token_type: str = "bearer"


# Contents of JWT token
class TokenPayload(SQLModel):
    sub: str | None = None


class NewPassword(SQLModel):
    token: str
    new_password: str = Field(min_length=8, max_length=40)
