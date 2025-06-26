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

    # Base query conditions
    base_conditions = []
    if not current_user.is_superuser:
        base_conditions.append(Item.owner_id == current_user.id)

    # Search conditions
    search_conditions = []
    if search:
        search_term = f"%{search}%"
        search_conditions.extend([
            Item.title.contains(search_term),
            Item.description.contains(search_term),
            Item.original_text.contains(search_term)
        ])

    # Filter conditions
    filter_conditions = []
    if source:
        filter_conditions.append(Item.source == source)
    if message_type:
        filter_conditions.append(Item.message_type == message_type)

    # Classification filter conditions
    classification_conditions = []
    if category is not None:
        classification_conditions.append(ItemClassification.category == category)
    if priority is not None:
        classification_conditions.append(ItemClassification.priority == priority)
    if action_required is not None:
        classification_conditions.append(ItemClassification.action_required == action_required)
    if contact is not None:
        # Simple string comparison for single contact field
        classification_conditions.append(ItemClassification.contact.contains(contact))

    # Build the query
    if classification_conditions:
        # Join with ItemClassification when filtering by classification fields
        base_query = select(Item).join(ItemClassification, Item.id == ItemClassification.item_id, isouter=False)
        count_query = select(func.count(Item.id)).join(ItemClassification, Item.id == ItemClassification.item_id,
                                                       isouter=False)

        # Add classification conditions
        if classification_conditions:
            base_query = base_query.where(and_(*classification_conditions))
            count_query = count_query.where(and_(*classification_conditions))
    else:
        # Regular query without classification join
        base_query = select(Item)
        count_query = select(func.count(Item.id))

    # Apply base conditions (user ownership)
    if base_conditions:
        base_query = base_query.where(and_(*base_conditions))
        count_query = count_query.where(and_(*base_conditions))

    # Apply search conditions
    if search_conditions:
        base_query = base_query.where(or_(*search_conditions))
        count_query = count_query.where(or_(*search_conditions))

    # Apply filter conditions
    if filter_conditions:
        base_query = base_query.where(and_(*filter_conditions))
        count_query = count_query.where(and_(*filter_conditions))

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
