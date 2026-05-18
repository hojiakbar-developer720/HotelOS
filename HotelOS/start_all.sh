#!/usr/bin/env bash
# =============================================================================
# HotelOS — Start All Services
# =============================================================================
# Usage:  bash start_all.sh
#
# Starts Redis, then all four microservices and the dashboard in separate
# background processes. Logs are written to logs/<service>.log
# Run `bash stop_all.sh` (or Ctrl+C and kill %%) to stop everything.
# =============================================================================

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGS="$ROOT/logs"
mkdir -p "$LOGS"

echo "=============================================="
echo "  HotelOS — Starting services"
echo "=============================================="

# ── Redis ──────────────────────────────────────────
if command -v redis-server &>/dev/null; then
  echo "[1/6] Starting Redis …"
  redis-server --daemonize yes --logfile "$LOGS/redis.log" --port 6379
  sleep 1
else
  echo "[WARN] redis-server not found. Make sure Redis is running on localhost:6379"
fi

# ── Helper to launch a service ─────────────────────
start_service() {
  local name="$1"
  local dir="$2"
  echo "[*] Starting $name …"
  cd "$dir"
  PYTHONPATH="$ROOT" python main.py > "$LOGS/${name}.log" 2>&1 &
  echo $! > "$LOGS/${name}.pid"
  cd "$ROOT"
  sleep 0.5
}

# ── Dashboard (port 8000) ──────────────────────────
start_service "dashboard"   "$ROOT/dashboard"

# ── Reception (port 8001) ─────────────────────────
start_service "reception"   "$ROOT/reception"

# ── Housekeeping (port 8002) ──────────────────────
start_service "housekeeping" "$ROOT/housekeeping"

# ── Room Service (port 8003) ──────────────────────
start_service "room_service" "$ROOT/room_service"

# ── Maintenance (port 8004) ───────────────────────
start_service "maintenance"  "$ROOT/maintenance"

echo ""
echo "=============================================="
echo "  All services started!"
echo "  Dashboard:   http://localhost:8000"
echo "  Reception:   http://localhost:8001/docs"
echo "  Housekeeping:http://localhost:8002/docs"
echo "  Room Service:http://localhost:8003/docs"
echo "  Maintenance: http://localhost:8004/docs"
echo ""
echo "  Dashboard token: hotelos-secret-2024"
echo ""
echo "  Logs in: $LOGS/"
echo "  Stop:    bash stop_all.sh"
echo "=============================================="
