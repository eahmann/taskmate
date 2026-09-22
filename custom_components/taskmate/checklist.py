"""Validation shared by checklist configuration and persisted progress."""

from __future__ import annotations

from typing import Any

from .models import generate_id


def normalize_checklist_items(items: Any, *, required: bool = False) -> list[dict[str, str]]:
    """Return independent named items with stable, unique identifiers."""
    if not isinstance(items, list) or len(items) > 30:
        raise ValueError("A checklist must contain between 1 and 30 items")
    if required and not items:
        raise ValueError("A checklist must contain between 1 and 30 items")
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each checklist item must have a name")
        name = item.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise ValueError("Checklist item names must contain 1 to 200 characters")
        item_id = item.get("id") or generate_id()
        if not isinstance(item_id, str) or len(item_id) > 64 or not item_id.strip() or item_id in seen:
            raise ValueError("Checklist item IDs must be unique strings of 1 to 64 characters")
        seen.add(item_id)
        result.append({"id": item_id, "name": name.strip()})
    return result


def validate_checklist_chore(chore: Any) -> None:
    """Validate and normalize a chore before any configuration is persisted."""
    if chore.task_type not in ("standard", "timed", "checklist"):
        raise ValueError("Unknown chore type")
    chore.checklist_items = normalize_checklist_items(chore.checklist_items, required=chore.task_type == "checklist")
    if chore.task_type == "checklist" and chore.open_ended:
        raise ValueError("Checklist chores cannot be open-ended")
