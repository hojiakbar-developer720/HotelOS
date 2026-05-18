"""
HotelOS — Message Broker Client
=================================
Thin wrapper around Redis Pub/Sub that all microservices use.
Every service publishes and subscribes through this module so that
the broker implementation can be swapped without touching service code.

Published event channels
------------------------
Channel                 Publisher           Subscribers
----------------------  ------------------  --------------------------
hotel.room.vacated      Reception           Housekeeping, Dashboard
hotel.room.status       Housekeeping        Dashboard, Reception
hotel.order.status      Room Service        Dashboard, Reception
hotel.maintenance.new   Maintenance         Dashboard
hotel.maintenance.upd   Maintenance         Dashboard
hotel.checkin           Reception           Dashboard
hotel.checkout          Reception           Dashboard, Housekeeping
hotel.broadcast         Any                 Dashboard (all events)
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional

import redis

logger = logging.getLogger("hotelos.broker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# All known channels — used by the dashboard to subscribe to everything
ALL_CHANNELS = [
    "hotel.room.vacated",
    "hotel.room.status",
    "hotel.order.status",
    "hotel.maintenance.new",
    "hotel.maintenance.upd",
    "hotel.checkin",
    "hotel.checkout",
    "hotel.broadcast",
]


# ---------------------------------------------------------------------------
# Broker client
# ---------------------------------------------------------------------------

class BrokerClient:
    """
    Wraps a Redis connection for Pub/Sub.

    Each microservice creates one BrokerClient instance.  Publishing is
    synchronous; subscriptions run in a background daemon thread so they
    never block the FastAPI event loop.
    """

    def __init__(self, service_name: str) -> None:
        self._service = service_name
        self._pub: redis.Redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True
        )
        self._sub_client: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, channel: str, payload: dict) -> None:
        """Publish a JSON-encoded payload to a named channel."""
        payload["_source"] = self._service
        message = json.dumps(payload)
        try:
            self._pub.publish(channel, message)
            # Also fan-out to the broadcast channel so the dashboard sees everything
            if channel != "hotel.broadcast":
                self._pub.publish("hotel.broadcast", message)
            logger.debug("Published to %s: %s", channel, message)
        except redis.RedisError as exc:
            logger.error("Publish failed on channel %s: %s", channel, exc)

    # ------------------------------------------------------------------
    # Subscribing
    # ------------------------------------------------------------------

    def subscribe(
        self, channels: list[str], handler: Callable[[str, dict], None]
    ) -> None:
        """
        Subscribe to one or more channels.  Messages are dispatched to
        *handler(channel, payload_dict)* in a background daemon thread.
        """
        self._sub_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True
        )
        self._pubsub = self._sub_client.pubsub(ignore_subscribe_messages=True)
        self._pubsub.subscribe(*channels)

        def _listen() -> None:
            for raw in self._pubsub.listen():
                if raw is None or raw["type"] != "message":
                    continue
                try:
                    data = json.loads(raw["data"])
                    handler(raw["channel"], data)
                except (json.JSONDecodeError, KeyError) as exc:
                    logger.warning("Malformed message on %s: %s", raw["channel"], exc)

        self._thread = threading.Thread(target=_listen, daemon=True, name=f"sub-{self._service}")
        self._thread.start()
        logger.info("%s subscribed to: %s", self._service, channels)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Return True if Redis is reachable."""
        try:
            return self._pub.ping()
        except redis.RedisError:
            return False
