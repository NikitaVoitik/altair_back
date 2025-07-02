import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import func, select, or_, and_

from app.api.deps import CurrentUser, SessionDep
from ...models import Item, ItemCreate, ItemPublic, ItemsPublic, ItemUpdate, Message, CategoryEnum, PriorityEnum, \
    ItemClassification

router = APIRouter(prefix="/items", tags=["items"])


@router.get("/", response_model=ItemsPublic)
def read_items(
        session: SessionDep,
        current_user: CurrentUser,
        skip: int = 0,
        limit: int = 100,
        search: Optional[str] = Query(None, description="Search in title, description, and original text"),
        category: Optional[CategoryEnum] = Query(None, description="Filter by category"),
        priority: Optional[PriorityEnum] = Query(None, description="Filter by priority"),
        source: Optional[str] = Query(None, description="Filter by source"),
        message_type: Optional[str] = Query(None, description="Filter by message type"),
        action_required: Optional[bool] = Query(None, description="Filter by action required"),
        contact: Optional[str] = Query(None, description="Filter by contact name"),
) -> Any:
    """
    Retrieve items with search and filtering capabilities.
    Items are returned in descending order by creation date (latest first).
    """

    # Determine if we need to join with ItemClassification
    needs_classification_join = any([
        category is not None,
        priority is not None,
        action_required is not None,
        contact is not None
    ])

    # Build base query with optional join
    if needs_classification_join:
        base_query = select(Item).join(ItemClassification, Item.id == ItemClassification.item_id)
        count_query = select(func.count(Item.id)).join(ItemClassification, Item.id == ItemClassification.item_id)
    else:
        base_query = select(Item)
        count_query = select(func.count(Item.id))

    # Collect all WHERE conditions
    conditions = []

    # User ownership conditions
    if not current_user.is_superuser:
        conditions.append(Item.owner_id == current_user.id)

    # Search conditions (OR together, then AND with other conditions)
    if search:
        search_term = f"%{search.strip()}%"
        search_conditions = [
            Item.title.ilike(search_term),
            Item.description.ilike(search_term),
            Item.original_text.ilike(search_term)
        ]
        conditions.append(or_(*search_conditions))

    # Basic item filters with partial matching
    if source:
        source_term = f"%{source.strip()}%"
        conditions.append(Item.source.ilike(source_term))

    if message_type:
        message_type_term = f"%{message_type.strip()}%"
        conditions.append(Item.message_type.ilike(message_type_term))

    # Classification filters (only if we have the join)
    if needs_classification_join:
        if category is not None:
            conditions.append(ItemClassification.category == category)

        if priority is not None:
            conditions.append(ItemClassification.priority == priority)

        if action_required is not None:
            conditions.append(ItemClassification.action_required == action_required)

        if contact is not None:
            contact_term = f"%{contact.strip()}%"
            conditions.append(ItemClassification.contact.ilike(contact_term))

    # Apply all conditions
    if conditions:
        base_query = base_query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    # Order by created_at descending (latest first) and apply pagination
    base_query = base_query.order_by(Item.created_at.desc()).offset(skip).limit(limit)

    # Execute queries
    count = session.exec(count_query).one()
    items = session.exec(base_query).all()

    return ItemsPublic(data=items, count=count)


@router.get("/{id}", response_model=ItemPublic)
def read_item(session: SessionDep, current_user: CurrentUser, id: uuid.UUID) -> Any:
    """
    Get item by ID.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    return item


@router.post("/", response_model=ItemPublic)
def create_item(
        *, session: SessionDep, current_user: CurrentUser, item_in: ItemCreate
) -> Any:
    """
    Create new item.
    """
    item = Item.model_validate(item_in, update={"owner_id": current_user.id})
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.put("/{id}", response_model=ItemPublic)
def update_item(
        *,
        session: SessionDep,
        current_user: CurrentUser,
        id: uuid.UUID,
        item_in: ItemUpdate,
) -> Any:
    """
    Update an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    update_dict = item_in.model_dump(exclude_unset=True)
    item.sqlmodel_update(update_dict)
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@router.delete("/{id}")
def delete_item(
        session: SessionDep, current_user: CurrentUser, id: uuid.UUID
) -> Message:
    """
    Delete an item.
    """
    item = session.get(Item, id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if not current_user.is_superuser and (item.owner_id != current_user.id):
        raise HTTPException(status_code=400, detail="Not enough permissions")
    session.delete(item)
    session.commit()
    return Message(message="Item deleted successfully")