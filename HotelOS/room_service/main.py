"""
HotelOS — Room Service
========================
Port: 8003

Responsibilities
----------------
* Accept food / drink orders linked to a room number
* Progress orders: Received → Preparing → Out for delivery → Delivered
* Post charges to Reception's billing system
* Publish hotel.order.status on every state change
"""

from __future__ import annotations

import logging
import sys
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.broker import BrokerClient
from shared.models import Order, OrderStatus
from shared.validation import validate_order_items, validate_room_number

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("hotelos.roomservice")

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
VALID_ROOMS = {101, 102, 103, 104, 105, 201, 202, 203, 204, 205}

# All orders: order_id → Order
_ORDERS: dict[str, Order] = {}

# Order lifecycle (must follow this sequence)
_STATUS_SEQUENCE = [
    OrderStatus.RECEIVED,
    OrderStatus.PREPARING,
    OrderStatus.OUT_FOR_DELIVERY,
    OrderStatus.DELIVERED,
]

# ---------------------------------------------------------------------------
# Message broker
# ---------------------------------------------------------------------------
broker = BrokerClient("room_service")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_charge_to_reception(room_number: int, description: str, amount: float) -> None:
    """Post a charge to the Reception billing service (TS-04 requirement)."""
    try:
        httpx.post(
            "http://localhost:8001/room-service-charge",
            json={"room_number": room_number, "description": description, "amount": amount},
            timeout=3.0,
        )
    except httpx.RequestError as exc:
        logger.warning("Could not post charge to Reception: %s", exc)


def _next_status(current: OrderStatus) -> OrderStatus:
    idx = _STATUS_SEQUENCE.index(current)
    if idx + 1 >= len(_STATUS_SEQUENCE):
        raise ValueError("Order is already at the final status.")
    return _STATUS_SEQUENCE[idx + 1]


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="HotelOS — Room Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlaceOrderRequest(BaseModel):
    room_number: int
    items: list[dict]   # [{"name": str, "quantity": int, "price": float}]


class AdvanceOrderRequest(BaseModel):
    order_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return {"service": "room_service", "status": "ok", "redis": broker.ping()}


@app.post("/orders")
def place_order(req: PlaceOrderRequest):
    """Place a new room-service order (TS-04)."""
    try:
        validate_room_number(req.room_number, VALID_ROOMS)
        items = validate_order_items(req.items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    order = Order(order_id=order_id, room_number=req.room_number, items=items)
    order.calculate_total()
    _ORDERS[order_id] = order

    broker.publish("hotel.order.status", {
        "event": "order_status_changed",
        "order_id": order_id,
        "room_number": req.room_number,
        "status": OrderStatus.RECEIVED.value,
        "items": items,
        "total": order.total,
    })

    logger.info("New order %s for room %s — £%.2f", order_id, req.room_number, order.total)
    return {"success": True, "order_id": order_id, "total": order.total, "status": order.status.value}


@app.post("/orders/advance")
def advance_order(req: AdvanceOrderRequest):
    """
    Move an order to its next status in the lifecycle.

    Sequence: Received → Preparing → Out for delivery → Delivered
    When delivered, the total is posted to Reception as a charge.
    """
    order = _ORDERS.get(req.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {req.order_id} not found.")

    if order.status == OrderStatus.DELIVERED:
        raise HTTPException(status_code=400, detail="Order is already delivered.")

    try:
        new_status = _next_status(order.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    order.status = new_status

    broker.publish("hotel.order.status", {
        "event": "order_status_changed",
        "order_id": order.order_id,
        "room_number": order.room_number,
        "status": new_status.value,
        "total": order.total,
    })

    # On delivery, post charge to Reception (TS-04)
    if new_status == OrderStatus.DELIVERED:
        description = f"Room service order {order.order_id}"
        _post_charge_to_reception(order.room_number, description, order.total)

    logger.info("Order %s → %s", req.order_id, new_status.value)
    return {"success": True, "order_id": req.order_id, "status": new_status.value}


@app.get("/orders")
def list_orders():
    """Return all orders."""
    return {"orders": [o.to_dict() for o in _ORDERS.values()]}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    """Return a single order."""
    order = _ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found.")
    return order.to_dict()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Room Service on port 8003 …")
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=False)
