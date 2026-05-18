#!/usr/bin/env bash
# Stop all HotelOS services
LOGS="$(cd "$(dirname "$0")" && pwd)/logs"

for service in dashboard reception housekeeping room_service maintenance; do
  PID_FILE="$LOGS/${service}.pid"
  if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Stopping $service (PID $PID) …"
      kill "$PID"
    fi
    rm -f "$PID_FILE"
  fi
done

# Stop Redis if we started it
if command -v redis-cli &>/dev/null; then
  redis-cli shutdown 2>/dev/null || true
fi

echo "All services stopped."
