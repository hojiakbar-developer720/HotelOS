"""
HotelOS — Reception Service
=============================
Port: 8001

Responsibilities
----------------
* Guest check-in  → executes the Room Assignment Algorithm (Task 1.1)
* Guest check-out → executes the Billing Calculation Algorithm (Task 1.2)
* Room inventory queries
* Publishes: hotel.checkin, hotel.checkout, hotel.room.vacated
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Path fix so `shared` is importable when running this service directly
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.broker import BrokerClient
from shared.models import (
    Housekeeper,
    ProximityPreference,
    Receptionist,
    Room,
    RoomStatus,
    RoomType,
    StayRecord,
)
from shared.validation import (
    validate_floor,
    validate_guest_id,
    validate_guest_name,
    validate_proximity,
    validate_room_number,
    validate_room_type,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("hotelos.reception")

# ---------------------------------------------------------------------------
# In-memory data store  (10 rooms, 2 floors)
# ---------------------------------------------------------------------------

NIGHTLY_RATES: dict[RoomType, float] = {
    RoomType.SINGLE: 80.0,
    RoomType.DOUBLE: 120.0,
    RoomType.SUITE: 250.0,
    RoomType.ACCESSIBLE: 100.0,
}

# Build 10 rooms: floors 1 & 2, various types
_ROOM_DEFINITIONS = [
    (101, 1, RoomType.SINGLE),
    (102, 1, RoomType.SINGLE),
    (103, 1, RoomType.DOUBLE),
    (104, 1, RoomType.DOUBLE),
    (105, 1, RoomType.ACCESSIBLE),
    (201, 2, RoomType.SINGLE),
    (202, 2, RoomType.DOUBLE),
    (203, 2, RoomType.DOUBLE),
    (204, 2, RoomType.SUITE),
    (205, 2, RoomType.SUITE),
]

# Stagger clean_since so the algorithm has meaningful timestamps to compare
_ROOMS: dict[int, Room] = {
    num: Room(
        room_number=num,
        floor=floor,
        room_type=rtype,
        status=RoomStatus.CLEAN,
        clean_since=time.time() - (i * 300),   # each room cleaned 5 min apart
        nightly_rate=NIGHTLY_RATES[rtype],
    )
    for i, (num, floor, rtype) in enumerate(_ROOM_DEFINITIONS)
}

VALID_ROOMS: set[int] = set(_ROOMS.keys())
VALID_FLOORS: set[int] = {1, 2}

# Active stays: room_number → StayRecord
_STAYS: dict[int, StayRecord] = {}

# Sample staff
_STAFF = {
    "R001": Receptionist("R001", "Alice"),
    "H001": Housekeeper("H001", "Bob"),
}

# ---------------------------------------------------------------------------
# Message broker
# ---------------------------------------------------------------------------
broker = BrokerClient("reception")


# ---------------------------------------------------------------------------
# Room Assignment Algorithm  (Task 1.1)
# ---------------------------------------------------------------------------

def assign_room(
    room_type: RoomType,
    floor_preference: Optional[int],
    proximity_preference: ProximityPreference,
) -> Optional[Room]:
    """
    Select the single best available room using the multi-criteria algorithm
    described in Task 1.1.

    Decision order
    --------------
    1. Filter by room_type match (hard constraint).
    2. Filter by status == Clean (hard constraint).
    3. Apply floor preference (soft — fall back to any floor if needed).
    4. Among survivors, pick room with the earliest clean_since (longest clean).
    5. Break ties using proximity_preference (Elevator → lower room number,
       Stairs → higher room number on same floor).
    6. Final tie-break: lowest room number (deterministic).

    Returns the selected Room, or None if no eligible room exists.
    """
    # Step 1 & 2: hard filters
    candidates = [
        r for r in _ROOMS.values()
        if r.room_type == room_type and r.status == RoomStatus.CLEAN
    ]

    if not candidates:
        return None

    # Step 3: floor preference (soft)
    if floor_preference is not None:
        floor_candidates = [r for r in candidates if r.floor == floor_preference]
        if floor_candidates:
            candidates = floor_candidates
        # else: discard preference and use all eligible rooms

    # Step 4: sort by longest-clean first (earliest timestamp)
    candidates.sort(key=lambda r: r.clean_since)

    # Step 5 & 6: proximity tiebreak among rooms sharing the same clean_since
    oldest_ts = candidates[0].clean_since
    tied = [r for r in candidates if r.clean_since == oldest_ts]

    if len(tied) == 1:
        return tied[0]

    if proximity_preference == ProximityPreference.ELEVATOR:
        # Prefer lower room numbers (typically near elevator lobbies)
        tied.sort(key=lambda r: r.room_number)
    elif proximity_preference == ProximityPreference.STAIRS:
        # Prefer higher room numbers (typically near stairwells)
        tied.sort(key=lambda r: -r.room_number)
    else:
        tied.sort(key=lambda r: r.room_number)

    return tied[0]


# ---------------------------------------------------------------------------
# Billing Calculation Algorithm  (Task 1.2)
# ---------------------------------------------------------------------------

def calculate_bill(room_number: int, check_out_time: float) -> dict:
    """
    Assemble the final guest invoice.

    Steps
    -----
    1. Retrieve the stay record (raises if not found — edge case handled).
    2. Compute number of nights (partial nights count as a full night per
       hotel policy; minimum charge = 1 night).
    3. Calculate base room charge = nightly_rate × nights.
    4. Sum all room-service charges.
    5. Sum additional charges (minibar, late checkout, etc.).
    6. Apply discount if present.
    7. Return structured breakdown.
    """
    stay = _STAYS.get(room_number)
    if stay is None:
        raise ValueError(f"No active stay found for room {room_number}.")

    stay.check_out_time = check_out_time
    room = _ROOMS[room_number]

    # Step 2: nights (ceil to full nights; minimum 1)
    elapsed_seconds = check_out_time - stay.check_in_time
    nights = max(1, int(-(-elapsed_seconds // 86400)))   # ceiling division

    # Step 3: base accommodation
    base_charge = room.nightly_rate * nights

    # Step 4: room service
    rs_total = sum(c["amount"] for c in stay.room_service_charges)

    # Step 5: additional charges
    add_total = sum(c["amount"] for c in stay.additional_charges)

    # Step 6: discount
    subtotal = base_charge + rs_total + add_total
    discount_amount = subtotal * (stay.discount_percent / 100.0)
    grand_total = subtotal - discount_amount

    return {
        "guest_name": stay.guest_name,
        "room_number": room_number,
        "room_type": room.room_type.value,
        "check_in": stay.check_in_time,
        "check_out": check_out_time,
        "nights": nights,
        "nightly_rate": room.nightly_rate,
        "base_charge": round(base_charge, 2),
        "room_service_charges": stay.room_service_charges,
        "room_service_total": round(rs_total, 2),
        "additional_charges": stay.additional_charges,
        "additional_total": round(add_total, 2),
        "discount_percent": stay.discount_percent,
        "discount_amount": round(discount_amount, 2),
        "grand_total": round(grand_total, 2),
    }


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="HotelOS — Reception Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas (Pydantic)
# ---------------------------------------------------------------------------

class CheckInRequest(BaseModel):
    guest_name: str
    guest_id: str
    room_type: str
    floor_preference: Optional[int] = None
    proximity_preference: Optional[str] = None
    discount_percent: float = 0.0


class CheckOutRequest(BaseModel):
    room_number: int
    additional_charges: list[dict] = []


class RoomServiceChargeRequest(BaseModel):
    room_number: int
    description: str
    amount: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    """Basic health probe."""
    return {"service": "reception", "status": "ok", "redis": broker.ping()}


@app.get("/rooms")
def list_rooms():
    """Return the full room inventory."""
    return {"rooms": [r.to_dict() for r in _ROOMS.values()]}


@app.get("/rooms/{room_number}")
def get_room(room_number: int):
    """Return a single room's details."""
    try:
        validate_room_number(room_number, VALID_ROOMS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _ROOMS[room_number].to_dict()


@app.post("/checkin")
def check_in(req: CheckInRequest):
    """
    Guest check-in endpoint.

    Validates all inputs, runs the Room Assignment Algorithm, updates
    room state, records the stay, and publishes events.
    """
    try:
        guest_name = validate_guest_name(req.guest_name)
        guest_id = validate_guest_id(req.guest_id)
        room_type = validate_room_type(req.room_type)
        floor_pref = validate_floor(req.floor_preference, VALID_FLOORS)
        proximity = validate_proximity(req.proximity_preference)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Run the room assignment algorithm (abstracted call — OOP abstraction)
    room = assign_room(room_type, floor_pref, proximity)

    if room is None:
        return {
            "success": False,
            "message": (
                f"No clean {room_type.value} rooms are currently available. "
                "Please choose an alternative room type or ask to be added to the waitlist."
            ),
        }

    # Update room state
    now = time.time()
    room.status = RoomStatus.OCCUPIED
    room.guest_name = guest_name
    room.guest_id = guest_id
    room.check_in_time = now

    # Create stay record
    stay = StayRecord(
        guest_id=guest_id,
        guest_name=guest_name,
        room_number=room.room_number,
        check_in_time=now,
        discount_percent=max(0.0, min(100.0, req.discount_percent)),
    )
    _STAYS[room.room_number] = stay

    # Publish event
    broker.publish("hotel.checkin", {
        "event": "guest_checked_in",
        "room_number": room.room_number,
        "floor": room.floor,
        "room_type": room.room_type.value,
        "guest_name": guest_name,
        "guest_id": guest_id,
        "check_in_time": now,
    })

    logger.info("Check-in: %s → room %s", guest_name, room.room_number)
    return {
        "success": True,
        "room_number": room.room_number,
        "floor": room.floor,
        "room_type": room.room_type.value,
        "nightly_rate": room.nightly_rate,
        "message": f"Welcome, {guest_name}! You have been assigned room {room.room_number}.",
    }


@app.post("/checkout")
def check_out(req: CheckOutRequest):
    """
    Guest check-out endpoint.

    Validates room, calculates the final bill using the Billing Algorithm,
    resets room status to Dirty, and publishes a room-vacated event.
    """
    try:
        validate_room_number(req.room_number, VALID_ROOMS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    room = _ROOMS[req.room_number]
    if room.status != RoomStatus.OCCUPIED:
        raise HTTPException(
            status_code=400,
            detail=f"Room {req.room_number} is not currently occupied.",
        )

    # Add any last-minute additional charges
    stay = _STAYS.get(req.room_number)
    if stay and req.additional_charges:
        for charge in req.additional_charges:
            if isinstance(charge, dict) and "description" in charge and "amount" in charge:
                stay.add_additional_charge(charge["description"], float(charge["amount"]))

    now = time.time()

    try:
        bill = calculate_bill(req.room_number, now)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Reset room
    guest_name = room.guest_name
    room.status = RoomStatus.DIRTY
    room.guest_name = None
    room.guest_id = None
    room.check_in_time = None

    # Remove stay record
    _STAYS.pop(req.room_number, None)

    # Publish events
    broker.publish("hotel.checkout", {
        "event": "guest_checked_out",
        "room_number": req.room_number,
        "guest_name": guest_name,
        "check_out_time": now,
    })
    broker.publish("hotel.room.vacated", {
        "event": "room_vacated",
        "room_number": req.room_number,
        "floor": room.floor,
        "room_type": room.room_type.value,
    })

    logger.info("Checkout: room %s — total £%.2f", req.room_number, bill["grand_total"])
    return {"success": True, "bill": bill}


@app.post("/room-service-charge")
def add_room_service_charge(req: RoomServiceChargeRequest):
    """
    Called by the Room Service microservice to post charges to a room's bill.
    In a real system this would go via the broker; here we expose a direct
    REST endpoint for simplicity within the student scope.
    """
    try:
        validate_room_number(req.room_number, VALID_ROOMS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    stay = _STAYS.get(req.room_number)
    if stay is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active stay for room {req.room_number}.",
        )
    if req.amount < 0:
        raise HTTPException(status_code=400, detail="Charge amount must be non-negative.")

    stay.add_room_service_charge(req.description, req.amount)
    return {"success": True, "message": f"£{req.amount:.2f} charge added to room {req.room_number}."}


@app.post("/rooms/{room_number}/update-status")
def update_room_status_internal(room_number: int, body: dict):
    """
    Called by Housekeeping to sync room status back into Reception's store.
    Allows the room to become available for new assignments once cleaned.
    """
    try:
        validate_room_number(room_number, VALID_ROOMS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    new_status_str = body.get("status", "")
    try:
        new_status = RoomStatus(new_status_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown status: {new_status_str}")

    room = _ROOMS[room_number]
    room.status = new_status
    if new_status == RoomStatus.CLEAN:
        room.clean_since = time.time()

    return {"success": True, "room_number": room_number, "status": new_status.value}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Reception Service on port 8001 …")
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False)
