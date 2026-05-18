"""
HotelOS — Shared Domain Models
================================
All dataclasses and enumerations used across every microservice.
Keeping them in one place guarantees consistency and avoids duplication.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RoomStatus(str, Enum):
    CLEAN = "Clean"
    DIRTY = "Dirty"
    BEING_CLEANED = "Being cleaned"
    OCCUPIED = "Occupied"
    MAINTENANCE = "Maintenance"


class RoomType(str, Enum):
    SINGLE = "Single"
    DOUBLE = "Double"
    SUITE = "Suite"
    ACCESSIBLE = "Accessible"


class ProximityPreference(str, Enum):
    ELEVATOR = "Elevator"
    STAIRS = "Stairs"
    NONE = "None"


class OrderStatus(str, Enum):
    RECEIVED = "Received"
    PREPARING = "Preparing"
    OUT_FOR_DELIVERY = "Out for delivery"
    DELIVERED = "Delivered"


class MaintenanceUrgency(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    NORMAL = "Normal"
    LOW = "Low"


class MaintenanceStatus(str, Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    RESOLVED = "Resolved"


# Urgency → numeric priority (lower = higher priority for heapq)
URGENCY_PRIORITY: dict[MaintenanceUrgency, int] = {
    MaintenanceUrgency.CRITICAL: 0,
    MaintenanceUrgency.HIGH: 1,
    MaintenanceUrgency.NORMAL: 2,
    MaintenanceUrgency.LOW: 3,
}


# ---------------------------------------------------------------------------
# Room
# ---------------------------------------------------------------------------

@dataclass
class Room:
    """Represents a single hotel room and its full operational state."""

    room_number: int
    floor: int
    room_type: RoomType
    status: RoomStatus = RoomStatus.CLEAN
    clean_since: float = field(default_factory=lambda: time.time())
    guest_name: Optional[str] = None
    guest_id: Optional[str] = None
    check_in_time: Optional[float] = None
    nightly_rate: float = 0.0

    def to_dict(self) -> dict:
        return {
            "room_number": self.room_number,
            "floor": self.floor,
            "room_type": self.room_type.value,
            "status": self.status.value,
            "clean_since": self.clean_since,
            "guest_name": self.guest_name,
            "guest_id": self.guest_id,
            "check_in_time": self.check_in_time,
            "nightly_rate": self.nightly_rate,
        }


# ---------------------------------------------------------------------------
# Guest / Stay record
# ---------------------------------------------------------------------------

@dataclass
class StayRecord:
    """Tracks a single guest stay including all charges."""

    guest_id: str
    guest_name: str
    room_number: int
    check_in_time: float
    check_out_time: Optional[float] = None
    room_service_charges: list[dict] = field(default_factory=list)
    additional_charges: list[dict] = field(default_factory=list)
    discount_percent: float = 0.0

    def add_room_service_charge(self, description: str, amount: float) -> None:
        self.room_service_charges.append({"description": description, "amount": amount})

    def add_additional_charge(self, description: str, amount: float) -> None:
        self.additional_charges.append({"description": description, "amount": amount})


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """A room-service order."""

    order_id: str
    room_number: int
    items: list[dict]
    status: OrderStatus = OrderStatus.RECEIVED
    created_at: float = field(default_factory=lambda: time.time())
    total: float = 0.0

    def calculate_total(self) -> float:
        self.total = sum(i["price"] * i["quantity"] for i in self.items)
        return self.total

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "room_number": self.room_number,
            "items": self.items,
            "status": self.status.value,
            "created_at": self.created_at,
            "total": self.total,
        }


# ---------------------------------------------------------------------------
# Maintenance request
# ---------------------------------------------------------------------------

@dataclass
class MaintenanceRequest:
    """A maintenance issue with priority-queue ordering support."""

    request_id: str
    room_number: int
    description: str
    urgency: MaintenanceUrgency
    submitted_at: float = field(default_factory=lambda: time.time())
    status: MaintenanceStatus = MaintenanceStatus.PENDING
    assigned_to: Optional[str] = None
    resolved_at: Optional[float] = None

    # Support comparison for heapq (sort by priority then time)
    def __lt__(self, other: "MaintenanceRequest") -> bool:
        self_p = URGENCY_PRIORITY[self.urgency]
        other_p = URGENCY_PRIORITY[other.urgency]
        if self_p != other_p:
            return self_p < other_p
        return self.submitted_at < other.submitted_at

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "room_number": self.room_number,
            "description": self.description,
            "urgency": self.urgency.value,
            "submitted_at": self.submitted_at,
            "status": self.status.value,
            "assigned_to": self.assigned_to,
            "resolved_at": self.resolved_at,
        }


# ---------------------------------------------------------------------------
# Staff hierarchy (OOP — Inheritance & Polymorphism)
# ---------------------------------------------------------------------------

class StaffMember:
    """Base class for all hotel staff. Demonstrates OOP inheritance."""

    def __init__(self, staff_id: str, name: str, role: str) -> None:
        self._staff_id = staff_id          # Encapsulation: private attribute
        self._name = name
        self._role = role
        self._active_tasks: list[str] = []

    # Encapsulated read-only properties
    @property
    def staff_id(self) -> str:
        return self._staff_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def role(self) -> str:
        return self._role

    def assign_task(self, task_id: str) -> None:
        """Polymorphic method — overridden by subclasses."""
        self._active_tasks.append(task_id)

    def complete_task(self, task_id: str) -> bool:
        if task_id in self._active_tasks:
            self._active_tasks.remove(task_id)
            return True
        return False

    def to_dict(self) -> dict:
        return {"staff_id": self._staff_id, "name": self._name, "role": self._role}


class Receptionist(StaffMember):
    """Handles check-in / check-out operations."""

    def __init__(self, staff_id: str, name: str) -> None:
        super().__init__(staff_id, name, "Receptionist")
        self._checkins_today: int = 0

    def assign_task(self, task_id: str) -> None:  # Polymorphism
        super().assign_task(task_id)
        self._checkins_today += 1

    @property
    def checkins_today(self) -> int:
        return self._checkins_today


class Housekeeper(StaffMember):
    """Cleans rooms and updates their status."""

    def __init__(self, staff_id: str, name: str) -> None:
        super().__init__(staff_id, name, "Housekeeper")
        self._rooms_cleaned: int = 0

    def assign_task(self, task_id: str) -> None:  # Polymorphism
        super().assign_task(task_id)

    def record_room_cleaned(self) -> None:
        self._rooms_cleaned += 1


class Technician(StaffMember):
    """Resolves maintenance issues."""

    def __init__(self, staff_id: str, name: str) -> None:
        super().__init__(staff_id, name, "Technician")
        self._issues_resolved: int = 0

    def assign_task(self, task_id: str) -> None:  # Polymorphism
        super().assign_task(task_id)

    def record_resolved(self) -> None:
        self._issues_resolved += 1
