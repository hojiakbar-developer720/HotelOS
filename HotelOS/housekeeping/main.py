"""
HotelOS — Housekeeping Service
================================
Port: 8002

Responsibilities
----------------
* Subscribes to hotel.room.vacated → adds rooms to the cleaning queue
* Housekeepers progress rooms: Dirty → Being cleaned → Clean
* Publishes hotel.room.status on every state change
* Notifies Reception when a room is Clean (so it becomes re-assignable)
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.broker import BrokerClient
from shared.models import RoomStatus
from shared.validation import validate_room_number

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("hotelos.housekeeping")

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# Room tracking for housekeeping (room_number → status dict)
_ROOM_STATUS: dict[int, dict] = {}

# Cleaning queue: FIFO of room numbers awaiting cleaning
_CLEANING_QUEUE: deque[int] = deque()

# Active cleaning assignments: room_number → housekeeper name
_ASSIGNMENTS: dict[int, str] = {}

# All known valid rooms (mirrors Reception's definition)
VALID_ROOMS = {101, 102, 103, 104, 105, 201, 202, 203, 204, 205}

# ---------------------------------------------------------------------------
# Message broker
# ---------------------------------------------------------------------------
broker = BrokerClient("housekeeping")


def _on_event(channel: str, payload: dict) -> None:
    """Handle incoming broker events."""
    event = payload.get("event")

    if event == "room_vacated":
        room_number = payload.get("room_number")
        if room_number and room_number in VALID_ROOMS:
            _ROOM_STATUS[room_number] = {
                "room_number": room_number,
                "status": RoomStatus.DIRTY.value,
                "updated_at": time.time(),
            }
            if room_number not in _CLEANING_QUEUE:
                _CLEANING_QUEUE.append(room_number)
                logger.info("Room %s added to cleaning queue.", room_number)

    elif event == "maintenance_resolved":
        room_number = payload.get("room_number")
        if room_number and room_number in VALID_ROOMS:
            # After maintenance, room also needs cleaning
            _ROOM_STATUS[room_number] = {
                "room_number": room_number,
                "status": RoomStatus.DIRTY.value,
                "updated_at": time.time(),
            }
            if room_number not in _CLEANING_QUEUE:
                _CLEANING_QUEUE.append(room_number)


broker.subscribe(["hotel.room.vacated", "hotel.maintenance.upd"], _on_event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notify_reception_clean(room_number: int) -> None:
    """POST to Reception so it updates its room inventory."""
    try:
        httpx.post(
            f"http://localhost:8001/rooms/{room_number}/update-status",
            json={"status": RoomStatus.CLEAN.value},
            timeout=3.0,
        )
    except httpx.RequestError as exc:
        logger.warning("Could not notify Reception about room %s: %s", room_number, exc)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="HotelOS — Housekeeping Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartCleaningRequest(BaseModel):
    room_number: int
    housekeeper_name: str


class MarkCleanRequest(BaseModel):
    room_number: int
    housekeeper_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return {"service": "housekeeping", "status": "ok", "redis": broker.ping()}


@app.get("/queue")
def get_queue():
    """Return the current cleaning queue."""
    return {
        "queue": list(_CLEANING_QUEUE),
        "assignments": _ASSIGNMENTS,
        "room_statuses": list(_ROOM_STATUS.values()),
    }


@app.post("/start-cleaning")
def start_cleaning(req: StartCleaningRequest):
    """
    Housekeeper begins cleaning a room.
    Room status: Dirty → Being cleaned.
    """
    try:
        validate_room_number(req.room_number, VALID_ROOMS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    current = _ROOM_STATUS.get(req.room_number, {})
    if current.get("status") not in (RoomStatus.DIRTY.value, None):
        raise HTTPException(
            status_code=400,
            detail=f"Room {req.room_number} is not in Dirty state (current: {current.get('status', 'Unknown')}).",
        )

    _ROOM_STATUS[req.room_number] = {
        "room_number": req.room_number,
        "status": RoomStatus.BEING_CLEANED.value,
        "updated_at": time.time(),
        "housekeeper": req.housekeeper_name,
    }
    _ASSIGNMENTS[req.room_number] = req.housekeeper_name

    # Remove from queue if present
    if req.room_number in _CLEANING_QUEUE:
        _CLEANING_QUEUE.remove(req.room_number)

    broker.publish("hotel.room.status", {
        "event": "room_status_changed",
        "room_number": req.room_number,
        "status": RoomStatus.BEING_CLEANED.value,
        "housekeeper": req.housekeeper_name,
    })

    logger.info("Room %s: Dirty → Being cleaned by %s", req.room_number, req.housekeeper_name)
    return {"success": True, "message": f"Room {req.room_number} cleaning started by {req.housekeeper_name}."}


@app.post("/mark-clean")
def mark_clean(req: MarkCleanRequest):
    """
    Housekeeper marks a room as clean.
    Room status: Being cleaned → Clean.
    """
    try:
        validate_room_number(req.room_number, VALID_ROOMS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    current = _ROOM_STATUS.get(req.room_number, {})
    if current.get("status") != RoomStatus.BEING_CLEANED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Room {req.room_number} must be in 'Being cleaned' state first.",
        )

    now = time.time()
    _ROOM_STATUS[req.room_number] = {
        "room_number": req.room_number,
        "status": RoomStatus.CLEAN.value,
        "updated_at": now,
        "housekeeper": req.housekeeper_name,
    }
    _ASSIGNMENTS.pop(req.room_number, None)

    broker.publish("hotel.room.status", {
        "event": "room_status_changed",
        "room_number": req.room_number,
        "status": RoomStatus.CLEAN.value,
        "housekeeper": req.housekeeper_name,
        "clean_since": now,
    })

    # Notify Reception so room becomes available
    _notify_reception_clean(req.room_number)

    logger.info("Room %s: Being cleaned → Clean by %s", req.room_number, req.housekeeper_name)
    return {"success": True, "message": f"Room {req.room_number} is now Clean."}


@app.get("/status")
def all_statuses():
    """Return all tracked room statuses."""
    return {"room_statuses": list(_ROOM_STATUS.values())}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Housekeeping Service on port 8002 …")
    uvicorn.run("main:app", host="0.0.0.0", port=8002, reload=False)
