import uuid
from typing import Any, Optional, Dict, List
import logging

from sqlmodel import Session, select

from app.core.security import get_password_hash, verify_password
from .models import (
    Item, ItemCreate, User, UserCreate, UserUpdate,
    ItemClassification, CategoryEnum, PriorityEnum
)

logger = logging.getLogger(__name__)

def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user


def get_user_by_email(*, session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    session_user = session.exec(statement).first()
    return session_user


def authenticate(*, session: Session, email: str, password: str) -> User | None:
    db_user = get_user_by_email(session=session, email=email)
    if not db_user:
        return None
    if not verify_password(password, db_user.hashed_password):
        return None
    return db_user


def create_item(*, session: Session, item_in: ItemCreate, owner_id: uuid.UUID) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item


def create_classification_from_dict(
        *,
        session: Session,
        item_id: uuid.UUID,
        classification_data: Dict[str, Any]
) -> ItemClassification:
    """Create a classification record from dictionary data"""

    # Extract entities or set defaults
    entities = classification_data.get("entities", {})

    # Map category string to enum
    category_str = classification_data.get("category", "information")
    try:
        category = CategoryEnum(category_str)
    except ValueError:
        category = CategoryEnum.INFORMATION

    # Map priority string to enum
    priority_str = classification_data.get("priority", "medium")
    try:
        priority = PriorityEnum(priority_str)
    except ValueError:
        priority = PriorityEnum.MEDIUM

    classification = ItemClassification(
        item_id=item_id,
        category=category,
        confidence=max(0.0, min(1.0, classification_data.get("confidence", 0.5))),
        priority=priority,
        action_required=classification_data.get("action_required", False),
        summary=classification_data.get("summary", "")[:500],
        dates=entities.get("dates", []),
        times=entities.get("times", []),
        contact=entities.get("contact"),  # Single contact now
        projects=entities.get("projects", []),
        keywords=entities.get("keywords", [])
    )

    session.add(classification)
    session.commit()
    session.refresh(classification)
    return classification


def create_item_with_classification(
        session: Session,
        item_in: ItemCreate,
        owner_id: str,
        classification: Optional[Dict[str, Any]] = None
) -> Item:
    """Create an item with classification data"""

    # Use the title from classification if available, otherwise use original title
    title = item_in.title
    if classification and classification.get("title"):
        title = classification["title"]
    elif not title and item_in.original_text:
        # Generate a simple title from original text if no title provided
        words = item_in.original_text.split()[:6]
        title = " ".join(words)
        if len(title) > 50:
            title = title[:47] + "..."

    # Create the item with the generated/provided title
    item_data = item_in.model_dump()
    item_data["title"] = title
    item_data["owner_id"] = owner_id

    item = Item.model_validate(item_data)
    session.add(item)
    session.flush()  # Get the item ID without committing

    # Create classification if provided
    if classification:
        classification_data = ItemClassification(
            item_id=item.id,
            category=classification.get("category", "information"),
            confidence=classification.get("confidence", 0.5),
            priority=classification.get("priority", "medium"),
            action_required=classification.get("action_required", False),
            summary=classification.get("summary", ""),
            dates=classification.get("entities", {}).get("dates", []),
            times=classification.get("entities", {}).get("times", []),
            contact=classification.get("entities", {}).get("contact"),  # Single contact now
            projects=classification.get("entities", {}).get("projects", []),
            keywords=classification.get("entities", {}).get("keywords", [])
        )
        session.add(classification_data)

    session.commit()
    session.refresh(item)
    return item


def get_item_with_classification(*, session: Session, item_id: uuid.UUID) -> Item | None:
    """Get an item with its classification"""
    statement = select(Item).where(Item.id == item_id)
    return session.exec(statement).first()


def update_item_classification(
        *,
        session: Session,
        item_id: uuid.UUID,
        classification_data: Dict[str, Any]
) -> ItemClassification:
    """Update or create classification for an item"""

    # Try to find existing classification
    statement = select(ItemClassification).where(ItemClassification.item_id == item_id)
    existing_classification = session.exec(statement).first()

    if existing_classification:
        # Update existing classification
        entities = classification_data.get("entities", {})

        # Map category string to enum
        category_str = classification_data.get("category", "information")
        try:
            category = CategoryEnum(category_str)
        except ValueError:
            category = CategoryEnum.INFORMATION

        # Map priority string to enum
        priority_str = classification_data.get("priority", "medium")
        try:
            priority = PriorityEnum(priority_str)
        except ValueError:
            priority = PriorityEnum.MEDIUM

        existing_classification.category = category
        existing_classification.confidence = max(0.0, min(1.0, classification_data.get("confidence", 0.5)))
        existing_classification.priority = priority
        existing_classification.action_required = classification_data.get("action_required", False)
        existing_classification.summary = classification_data.get("summary", "")[:500]
        existing_classification.dates = entities.get("dates", [])
        existing_classification.times = entities.get("times", [])
        existing_classification.contact = entities.get("contact")
        existing_classification.projects = entities.get("projects", [])
        existing_classification.keywords = entities.get("keywords", [])

        session.add(existing_classification)
        session.commit()
        session.refresh(existing_classification)
        return existing_classification
    else:
        # Create new classification
        return create_classification_from_dict(
            session=session,
            item_id=item_id,
            classification_data=classification_data
        )