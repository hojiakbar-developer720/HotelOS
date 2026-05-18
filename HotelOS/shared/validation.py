"""
HotelOS — Input Validation Utilities
======================================
Every piece of data entering the system from outside passes through
these functions before reaching business logic (Security requirement 3.2).
"""

from __future__ import annotations

import re
from typing import Optional

from shared.models import RoomType, MaintenanceUrgency, ProximityPreference


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _non_empty_str(value: str, field_name: str, max_len: int = 200) -> str:
    """Strip and validate that a string is non-empty and within length."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty.")
    if len(value) > max_len:
        raise ValueError(f"{field_name} must not exceed {max_len} characters.")
    return value


def _safe_name(name: str, field_name: str = "Name") -> str:
    """Allow letters, spaces, hyphens, and apostrophes only."""
    name = _non_empty_str(name, field_name, max_len=100)
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ '\-]+", name):
        raise ValueError(f"{field_name} contains invalid characters.")
    return name


# ---------------------------------------------------------------------------
# Domain-specific validators
# ---------------------------------------------------------------------------

def validate_room_number(room_number: int, valid_rooms: set[int]) -> int:
    """Ensure room_number is an integer and exists in the hotel inventory."""
    if not isinstance(room_number, int):
        raise ValueError("Room number must be an integer.")
    if room_number not in valid_rooms:
        raise ValueError(
            f"Room {room_number} does not exist. Valid rooms: {sorted(valid_rooms)}"
        )
    return room_number


def validate_guest_name(name: str) -> str:
    return _safe_name(name, "Guest name")


def validate_guest_id(guest_id: str) -> str:
    guest_id = _non_empty_str(guest_id, "Guest ID", max_len=50)
    if not re.fullmatch(r"[A-Za-z0-9\-_]+", guest_id):
        raise ValueError("Guest ID may only contain letters, digits, hyphens, or underscores.")
    return guest_id


def validate_room_type(room_type: str) -> RoomType:
    try:
        return RoomType(room_type)
    except ValueError:
        valid = [rt.value for rt in RoomType]
        raise ValueError(f"Invalid room type '{room_type}'. Must be one of: {valid}")


def validate_floor(floor: Optional[int], valid_floors: set[int]) -> Optional[int]:
    if floor is None:
        return None
    if not isinstance(floor, int) or floor not in valid_floors:
        raise ValueError(f"Floor must be one of: {sorted(valid_floors)} or null.")
    return floor


def validate_proximity(pref: Optional[str]) -> ProximityPreference:
    if pref is None or pref == "":
        return ProximityPreference.NONE
    try:
        return ProximityPreference(pref)
    except ValueError:
        valid = [p.value for p in ProximityPreference]
        raise ValueError(f"Proximity preference must be one of: {valid}")


def validate_urgency(urgency: str) -> MaintenanceUrgency:
    try:
        return MaintenanceUrgency(urgency)
    except ValueError:
        valid = [u.value for u in MaintenanceUrgency]
        raise ValueError(f"Invalid urgency '{urgency}'. Must be one of: {valid}")


def validate_order_items(items: list) -> list[dict]:
    """Validate a list of order items from the request body."""
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("Order must contain at least one item.")
    cleaned = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"Item {idx} must be an object.")
        name = _non_empty_str(str(item.get("name", "")), f"Item {idx} name", max_len=100)
        try:
            quantity = int(item["quantity"])
            if quantity < 1:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Item {idx} quantity must be a positive integer.")
        try:
            price = float(item["price"])
            if price < 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Item {idx} price must be a non-negative number.")
        cleaned.append({"name": name, "quantity": quantity, "price": price})
    return cleaned


def validate_maintenance_description(desc: str) -> str:
    return _non_empty_str(desc, "Description", max_len=500)
