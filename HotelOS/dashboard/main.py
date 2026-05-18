"""
HotelOS — Operations Dashboard (WebSocket Server)
==================================================
Port: 8000

Responsibilities
----------------
* Serve the HTML dashboard (single-page app)
* Accept WebSocket connections from browsers
* Subscribe to ALL broker channels and push every event to connected clients
* Require a simple token for WebSocket authentication (Security 3.2)
* Aggregate live state from all microservices on demand
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Set

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.broker import BrokerClient, ALL_CHANNELS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("hotelos.dashboard")

# ---------------------------------------------------------------------------
# Simple authentication (Security 3.2)
# ---------------------------------------------------------------------------
DASHBOARD_TOKEN = "hotelos-secret-2024"

# ---------------------------------------------------------------------------
# Connected WebSocket clients
# ---------------------------------------------------------------------------
_CLIENTS: Set[WebSocket] = set()


async def _broadcast(message: dict) -> None:
    """Push a JSON message to all connected dashboard clients."""
    if not _CLIENTS:
        return
    text = json.dumps(message)
    disconnected = set()
    for ws in list(_CLIENTS):
        try:
            await ws.send_text(text)
        except Exception:
            disconnected.add(ws)
    _CLIENTS.difference_update(disconnected)


# ---------------------------------------------------------------------------
# Broker subscription  (event-driven: push events from Redis → WebSocket)
# ---------------------------------------------------------------------------
broker = BrokerClient("dashboard")

# We use asyncio to bridge the synchronous broker callback thread into
# the async FastAPI event loop.
_LOOP: asyncio.AbstractEventLoop | None = None


def _on_broker_event(channel: str, payload: dict) -> None:
    """Synchronous callback — schedules an async broadcast on the event loop."""
    if _LOOP and not _LOOP.is_closed():
        try:
            asyncio.run_coroutine_threadsafe(
                _broadcast({"channel": channel, **payload}),
                _LOOP,
            )
        except RuntimeError:
            pass


broker.subscribe(ALL_CHANNELS, _on_broker_event)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="HotelOS — Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _on_startup():
    global _LOOP
    _LOOP = asyncio.get_running_loop()
    logger.info("Dashboard started — WebSocket event loop captured.")


# ---------------------------------------------------------------------------
# WebSocket endpoint (event-driven programming — Task 2.4)
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    """
    WebSocket endpoint.  Clients must supply ?token=<DASHBOARD_TOKEN>.

    On connect:
      1. Validate token — close with 1008 if invalid (Security 3.2).
      2. Register in _CLIENTS set.
      3. Send an initial state snapshot from all microservices.
      4. Keep alive — broker callbacks push events from another thread.

    On disconnect or error:
      Remove from _CLIENTS set.
    """
    # Authentication check
    if token != DASHBOARD_TOKEN:
        await websocket.close(code=1008)   # Policy Violation
        logger.warning("WebSocket rejected — invalid token.")
        return

    await websocket.accept()
    _CLIENTS.add(websocket)
    logger.info("WebSocket client connected. Total: %d", len(_CLIENTS))

    # Send initial snapshot
    snapshot = await _build_snapshot()
    await websocket.send_text(json.dumps({"type": "snapshot", **snapshot}))

    try:
        while True:
            # Keep the connection alive; echo any ping messages from the client
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        _CLIENTS.discard(websocket)
        logger.info("WebSocket client disconnected. Total: %d", len(_CLIENTS))


async def _build_snapshot() -> dict:
    """Fetch current state from all microservices asynchronously."""
    async with httpx.AsyncClient(timeout=3.0) as client:
        rooms, orders, maintenance = await asyncio.gather(
            _safe_get(client, "http://localhost:8001/rooms"),
            _safe_get(client, "http://localhost:8003/orders"),
            _safe_get(client, "http://localhost:8004/all"),
            return_exceptions=True,
        )
    return {
        "rooms": rooms.get("rooms", []) if isinstance(rooms, dict) else [],
        "orders": orders.get("orders", []) if isinstance(orders, dict) else [],
        "maintenance": maintenance.get("requests", []) if isinstance(maintenance, dict) else [],
    }


async def _safe_get(client: httpx.AsyncClient, url: str) -> dict:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Snapshot fetch failed for %s: %s", url, exc)
        return {}


# ---------------------------------------------------------------------------
# Snapshot REST endpoint (for dashboard polling fallback)
# ---------------------------------------------------------------------------

@app.get("/snapshot")
async def get_snapshot():
    return await _build_snapshot()


# ---------------------------------------------------------------------------
# Serve the HTML dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the single-page operations dashboard."""
    html_path = Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Dashboard HTML not found. Place dashboard.html next to main.py</h1>")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Dashboard on port 8000 …")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
