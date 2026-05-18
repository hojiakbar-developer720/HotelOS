"""
HotelOS — Maintenance Service
================================
Port: 8004

Responsibilities
----------------
* Accept maintenance issue reports with urgency levels
* Implement the Priority Queue Algorithm (Task 1.2):
    - Orders by urgency (Critical → High → Normal → Low)
    - Within same urgency, FIFO by submission time
* Assign issues to technicians
* Track resolution and publish status updates
* Publishes: hotel.maintenance.new, hotel.maintenance.upd
"""

from __future__ import annotations

import heapq
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.broker import BrokerClient
from shared.models import MaintenanceRequest, MaintenanceStatus, MaintenanceUrgency
from shared.validation import (
    validate_maintenance_description,
    validate_room_number,
    validate_urgency,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("hotelos.maintenance")

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
VALID_ROOMS = {101, 102, 103, 104, 105, 201, 202, 203, 204, 205}

# Priority queue (min-heap) of MaintenanceRequest objects
# heapq uses __lt__ defined on MaintenanceRequest (urgency then timestamp)
_PRIORITY_QUEUE: list[MaintenanceRequest] = []

# All requests indexed by ID for O(1) status lookup
_ALL_REQUESTS: dict[str, MaintenanceRequest] = {}

# ---------------------------------------------------------------------------
# Message broker
# ---------------------------------------------------------------------------
broker = BrokerClient("maintenance")


# ---------------------------------------------------------------------------
# Priority Queue Algorithm  (Task 1.2)
# ---------------------------------------------------------------------------

def enqueue_request(request: MaintenanceRequest) -> None:
    """
    Insert a maintenance request into the priority heap.
    Ordering is determined by MaintenanceRequest.__lt__:
      1. Urgency (Critical = 0 → Low = 3)
      2. submitted_at (earlier = higher priority within same urgency)
    """
    heapq.heappush(_PRIORITY_QUEUE, request)
    _ALL_REQUESTS[request.request_id] = request
    logger.info(
        "Queued [%s] request %s for room %s.",
        request.urgency.value,
        request.request_id,
        request.room_number,
    )


def assign_next(technician_name: str) -> Optional[MaintenanceRequest]:
    """
    Dequeue the highest-priority pending request and assign it to the
    given technician.  Returns None if the queue is empty.
    """
    # Find the highest-priority pending request
    pending = [r for r in _PRIORITY_QUEUE if r.status == MaintenanceStatus.PENDING]
    if not pending:
        return None

    # Get the top item (uses __lt__ for comparison)
    top = min(pending)

    # Remove from the main queue and re-heapify
    _PRIORITY_QUEUE.remove(top)
    heapq.heapify(_PRIORITY_QUEUE)

    top.status = MaintenanceStatus.IN_PROGRESS
    top.assigned_to = technician_name

    return top


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="HotelOS — Maintenance Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReportIssueRequest(BaseModel):
    room_number: int
    description: str
    urgency: str


class AssignNextRequest(BaseModel):
    technician_name: str


class ResolveRequest(BaseModel):
    request_id: str
    technician_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return {"service": "maintenance", "status": "ok", "redis": broker.ping()}


@app.post("/report")
def report_issue(req: ReportIssueRequest):
    """Submit a new maintenance issue (TS-05)."""
    try:
        validate_room_number(req.room_number, VALID_ROOMS)
        description = validate_maintenance_description(req.description)
        urgency = validate_urgency(req.urgency)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    request_id = f"MNT-{uuid.uuid4().hex[:8].upper()}"
    request = MaintenanceRequest(
        request_id=request_id,
        room_number=req.room_number,
        description=description,
        urgency=urgency,
    )
    enqueue_request(request)

    broker.publish("hotel.maintenance.new", {
        "event": "maintenance_reported",
        **request.to_dict(),
    })

    logger.info("New %s issue: %s (room %s)", urgency.value, request_id, req.room_number)
    return {
        "success": True,
        "request_id": request_id,
        "urgency": urgency.value,
        "message": f"Issue {request_id} logged with {urgency.value} priority.",
    }


@app.post("/assign-next")
def assign_next_to_technician(req: AssignNextRequest):
    """
    Assign the highest-priority pending issue to the given technician (TS-05).
    Returns 404 if the queue is empty.
    """
    if not req.technician_name or not req.technician_name.strip():
        raise HTTPException(status_code=400, detail="Technician name is required.")

    request = assign_next(req.technician_name.strip())
    if request is None:
        raise HTTPException(status_code=404, detail="No pending maintenance requests in queue.")

    broker.publish("hotel.maintenance.upd", {
        "event": "maintenance_assigned",
        **request.to_dict(),
    })

    logger.info("Assigned %s to %s", request.request_id, req.technician_name)
    return {
        "success": True,
        "request_id": request.request_id,
        "room_number": request.room_number,
        "description": request.description,
        "urgency": request.urgency.value,
        "assigned_to": request.assigned_to,
    }


@app.post("/resolve")
def resolve_issue(req: ResolveRequest):
    """Mark an in-progress issue as resolved (TS-05)."""
    request = _ALL_REQUESTS.get(req.request_id)
    if request is None:
        raise HTTPException(status_code=404, detail=f"Request {req.request_id} not found.")
    if request.status != MaintenanceStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=400,
            detail=f"Request {req.request_id} is not currently in progress.",
        )

    request.status = MaintenanceStatus.RESOLVED
    request.resolved_at = time.time()

    broker.publish("hotel.maintenance.upd", {
        "event": "maintenance_resolved",
        "room_number": request.room_number,
        **request.to_dict(),
    })

    logger.info("Resolved %s by %s", req.request_id, req.technician_name)
    return {"success": True, "message": f"Issue {req.request_id} marked as resolved."}


@app.get("/queue")
def get_queue():
    """Return the current priority queue (pending items only)."""
    pending = sorted(
        [r for r in _ALL_REQUESTS.values() if r.status == MaintenanceStatus.PENDING],
        key=lambda r: r,  # uses __lt__
    )
    return {"queue": [r.to_dict() for r in pending]}


@app.get("/all")
def get_all():
    """Return every maintenance request regardless of status."""
    return {"requests": [r.to_dict() for r in _ALL_REQUESTS.values()]}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Maintenance Service on port 8004 …")
    uvicorn.run("main:app", host="0.0.0.0", port=8004, reload=False)
