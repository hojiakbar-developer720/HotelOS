# HotelOS

A real-time hotel management system built with Python + FastAPI + Redis + WebSockets, developed for BTEC Higher Nationals Unit 4: Programming.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Operations Dashboard                      │
│              (Browser ← WebSocket → Port 8000)              │
└──────────────────────────┬──────────────────────────────────┘
                           │ WebSocket
                    ┌──────▼──────┐
                    │  Dashboard  │ :8000
                    │   Service   │
                    └──────┬──────┘
                           │ subscribe ALL channels
          ┌────────────────▼─────────────────────┐
          │           Redis Pub/Sub               │
          │         (Message Broker)              │
          └──┬───────────┬──────────┬────────────┘
             │           │          │          │
      ┌──────▼──┐  ┌────▼───┐ ┌───▼────┐ ┌──▼──────────┐
      │Reception│  │House-  │ │  Room  │ │Maintenance  │
      │ :8001   │  │keeping │ │Service │ │  :8004      │
      │         │  │ :8002  │ │ :8003  │ │             │
      └─────────┘  └────────┘ └────────┘ └─────────────┘
```

### Services

| Service | Port | Responsibility |
|---|---|---|
| Dashboard | 8000 | HTML UI + WebSocket server |
| Reception | 8001 | Check-in / check-out / room assignment |
| Housekeeping | 8002 | Room cleaning workflow |
| Room Service | 8003 | Food & beverage orders |
| Maintenance | 8004 | Issue priority queue |

### Message Broker Events

| Channel | Publisher | Subscribers | Key Payload Fields |
|---|---|---|---|
| `hotel.checkin` | Reception | Dashboard | room_number, guest_name, guest_id |
| `hotel.checkout` | Reception | Dashboard, Housekeeping | room_number, guest_name |
| `hotel.room.vacated` | Reception | Housekeeping, Dashboard | room_number, floor, room_type |
| `hotel.room.status` | Housekeeping | Dashboard, Reception | room_number, status, housekeeper |
| `hotel.order.status` | Room Service | Dashboard, Reception | order_id, room_number, status, total |
| `hotel.maintenance.new` | Maintenance | Dashboard | request_id, room_number, urgency |
| `hotel.maintenance.upd` | Maintenance | Dashboard | request_id, status, assigned_to |
| `hotel.broadcast` | Any | Dashboard | (fan-out of all above) |

---

## Installation

### Prerequisites

- Python 3.10+
- Redis 6+

**Install Redis:**
```bash
# Ubuntu / WSL
sudo apt update && sudo apt install -y redis-server

# macOS
brew install redis

# Windows — use WSL or download from https://github.com/tporadowski/redis/releases
```

### Python dependencies

```bash
pip install -r requirements.txt
```

---

## Running HotelOS

### Option A — Bash launcher (recommended)

```bash
bash start_all.sh      # starts Redis + all 5 services
bash stop_all.sh       # stops everything
```

### Option B — Manual (each in a separate terminal)

```bash
# Terminal 1 — Redis
redis-server

# Terminal 2 — Dashboard
cd dashboard && PYTHONPATH=.. python main.py

# Terminal 3 — Reception
cd reception && PYTHONPATH=.. python main.py

# Terminal 4 — Housekeeping
cd housekeeping && PYTHONPATH=.. python main.py

# Terminal 5 — Room Service
cd room_service && PYTHONPATH=.. python main.py

# Terminal 6 — Maintenance
cd maintenance && PYTHONPATH=.. python main.py
```

### Accessing the dashboard

Open **http://localhost:8000** in your browser.

Enter the dashboard token: `hotelos-secret-2024`

### API documentation (Swagger UI)

Each service auto-generates interactive docs:

- Reception:    http://localhost:8001/docs
- Housekeeping: http://localhost:8002/docs
- Room Service: http://localhost:8003/docs
- Maintenance:  http://localhost:8004/docs

---

## Running the automated tests

With all services running:

```bash
python test_scenarios.py
```

This verifies all 8 test scenarios from the assignment brief (TS-01 through TS-08).

---

## Hotel configuration

10 rooms across 2 floors (simplified from the 120-room scenario for the assignment):

| Room | Floor | Type |
|---|---|---|
| 101, 102 | 1 | Single |
| 103, 104 | 1 | Double |
| 105 | 1 | Accessible |
| 201 | 2 | Single |
| 202, 203 | 2 | Double |
| 204, 205 | 2 | Suite |

Nightly rates: Single £80 · Double £120 · Suite £250 · Accessible £100

---

## Quick API examples (curl)

```bash
# Check in a guest
curl -X POST http://localhost:8001/checkin \
  -H "Content-Type: application/json" \
  -d '{"guest_name":"Alice","guest_id":"G001","room_type":"Double","floor_preference":2}'

# Check out
curl -X POST http://localhost:8001/checkout \
  -H "Content-Type: application/json" \
  -d '{"room_number":203}'

# Place a room-service order
curl -X POST http://localhost:8003/orders \
  -H "Content-Type: application/json" \
  -d '{"room_number":203,"items":[{"name":"Coffee","quantity":2,"price":3.50}]}'

# Advance order
curl -X POST http://localhost:8003/orders/advance \
  -H "Content-Type: application/json" \
  -d '{"order_id":"ORD-XXXXXXXX"}'

# Report maintenance issue
curl -X POST http://localhost:8004/report \
  -H "Content-Type: application/json" \
  -d '{"room_number":101,"description":"Broken AC","urgency":"High"}'

# Assign next maintenance issue
curl -X POST http://localhost:8004/assign-next \
  -H "Content-Type: application/json" \
  -d '{"technician_name":"Dave"}'
```

---

## Git log

```
(run `git log --oneline` after initialising the repo to see commit history)
```

---

## Security notes

- Dashboard requires a token (`hotelos-secret-2024`) before WebSocket connection is accepted.
- All inputs are validated before processing (see `shared/validation.py`).
- WebSocket messages expose only operational data — no raw payment details.
- All exceptions are caught and returned as safe error messages; stack traces are logged server-side only.

---

## Project structure

```
HotelOS/
├── shared/
│   ├── models.py          # All domain dataclasses, enums, OOP hierarchy
│   ├── broker.py          # Redis Pub/Sub client wrapper
│   └── validation.py      # Input validation utilities
├── reception/
│   └── main.py            # Reception Service (port 8001)
├── housekeeping/
│   └── main.py            # Housekeeping Service (port 8002)
├── room_service/
│   └── main.py            # Room Service (port 8003)
├── maintenance/
│   └── main.py            # Maintenance Service (port 8004)
├── dashboard/
│   ├── main.py            # Dashboard WebSocket server (port 8000)
│   └── dashboard.html     # Single-page operations UI
├── test_scenarios.py       # Automated test runner (TS-01 → TS-08)
├── start_all.sh            # Launch all services
├── stop_all.sh             # Stop all services
├── requirements.txt
└── README.md
```
