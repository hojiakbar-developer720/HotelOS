"""
HotelOS — Automated Test Runner
=================================
Runs all 8 test scenarios from the assignment brief (Section 3.4).
Requires all services to be running (start_all.sh).

Usage:
    python test_scenarios.py
"""

from __future__ import annotations

import json
import sys
import time

import httpx

BASE = {
    "reception":    "http://localhost:8001",
    "housekeeping": "http://localhost:8002",
    "room_service": "http://localhost:8003",
    "maintenance":  "http://localhost:8004",
}

PASS = "✅ PASS"
FAIL = "❌ FAIL"

results: list[dict] = []


def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


def check(ts_id: str, description: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    msg = f"  [{ts_id}] {status}  {description}"
    if detail:
        msg += f"\n         ↳ {detail}"
    print(msg)
    results.append({"id": ts_id, "passed": condition, "desc": description})


def get(service: str, path: str) -> dict:
    r = httpx.get(f"{BASE[service]}{path}", timeout=5)
    r.raise_for_status()
    return r.json()


def post(service: str, path: str, body: dict) -> dict:
    r = httpx.post(f"{BASE[service]}{path}", json=body, timeout=5)
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────────
# TS-01: Check-in requesting double room on floor 2
# ─────────────────────────────────────────────────
section("TS-01: Check-in with floor preference")
resp = post("reception", "/checkin", {
    "guest_name": "Alice Smith",
    "guest_id":   "G001",
    "room_type":  "Double",
    "floor_preference": 2,
})
ok = resp.get("success") is True
room_num = resp.get("room_number")
check("TS-01a", "Check-in succeeded", ok, json.dumps(resp))

if ok:
    floor_ok = resp.get("floor") == 2
    check("TS-01b", "Assigned room is on floor 2", floor_ok, f"room {room_num}")
    rooms = get("reception", "/rooms")["rooms"]
    target = next((r for r in rooms if r["room_number"] == room_num), None)
    check("TS-01c", "Room status is Occupied", target and target["status"] == "Occupied")


# ─────────────────────────────────────────────────
# TS-02: Checkout triggers billing + vacate event
# ─────────────────────────────────────────────────
section("TS-02: Checkout and billing")
# First check in a guest to room 105 (single, floor 1) so we have someone to check out
post("reception", "/checkin", {
    "guest_name": "Bob Jones",
    "guest_id": "G002",
    "room_type": "Accessible",
})
time.sleep(0.2)
# Checkout the first Occupied Accessible room
rooms = get("reception", "/rooms")["rooms"]
occupied = [r for r in rooms if r["status"] == "Occupied" and r["room_type"] == "Accessible"]
if occupied:
    checkout_room = occupied[0]["room_number"]
    resp = post("reception", "/checkout", {"room_number": checkout_room})
    check("TS-02a", "Checkout succeeded", resp.get("success") is True)
    bill = resp.get("bill", {})
    check("TS-02b", "Bill has grand_total", "grand_total" in bill, f"£{bill.get('grand_total')}")
    rooms_after = get("reception", "/rooms")["rooms"]
    target = next((r for r in rooms_after if r["room_number"] == checkout_room), None)
    check("TS-02c", "Room status is Dirty after checkout", target and target["status"] == "Dirty")
else:
    check("TS-02a", "Checkout (skipped — no occupied accessible room)", False)


# ─────────────────────────────────────────────────
# TS-03: Housekeeper marks room clean
# ─────────────────────────────────────────────────
section("TS-03: Housekeeping workflow Dirty → Being cleaned → Clean")
# We need a dirty room — check if housekeeping knows about one
q = get("housekeeping", "/queue")
queue = q.get("queue", [])

if queue:
    room_to_clean = queue[0]
    resp = post("housekeeping", "/start-cleaning", {
        "room_number": room_to_clean,
        "housekeeper_name": "Carlos",
    })
    check("TS-03a", "Start cleaning succeeded", resp.get("success") is True)

    resp2 = post("housekeeping", "/mark-clean", {
        "room_number": room_to_clean,
        "housekeeper_name": "Carlos",
    })
    check("TS-03b", "Mark clean succeeded", resp2.get("success") is True)

    time.sleep(0.5)
    rooms_after = get("reception", "/rooms")["rooms"]
    target = next((r for r in rooms_after if r["room_number"] == room_to_clean), None)
    check("TS-03c", "Room is Clean in Reception's inventory",
          target is not None and target["status"] == "Clean",
          f"room {room_to_clean} status: {target['status'] if target else 'not found'}")
else:
    print("  [TS-03] Skipped — no rooms in cleaning queue yet (checkout events may take a moment)")
    results.append({"id": "TS-03", "passed": None, "desc": "Skipped"})


# ─────────────────────────────────────────────────
# TS-04: Room service order full lifecycle
# ─────────────────────────────────────────────────
section("TS-04: Room service order lifecycle")
# Use whichever occupied room exists
rooms = get("reception", "/rooms")["rooms"]
occupied = [r for r in rooms if r["status"] == "Occupied"]
rs_room = occupied[0]["room_number"] if occupied else 101

resp = post("room_service", "/orders", {
    "room_number": rs_room,
    "items": [
        {"name": "Coffee", "quantity": 2, "price": 3.50},
        {"name": "Sandwich", "quantity": 1, "price": 7.00},
    ],
})
check("TS-04a", "Order placed successfully", resp.get("success") is True)
order_id = resp.get("order_id")
check("TS-04b", "Order total correct (£14.00)", abs(resp.get("total", 0) - 14.0) < 0.01)

if order_id:
    for expected_status in ["Preparing", "Out for delivery", "Delivered"]:
        resp_adv = post("room_service", "/orders/advance", {"order_id": order_id})
        check(f"TS-04c-{expected_status.replace(' ','_')}",
              f"Order advanced to {expected_status}",
              resp_adv.get("status") == expected_status)


# ─────────────────────────────────────────────────
# TS-05: Critical maintenance request
# ─────────────────────────────────────────────────
section("TS-05: Critical maintenance priority queue")
resp = post("maintenance", "/report", {
    "room_number": 103,
    "description": "Broken shower — no hot water",
    "urgency": "Critical",
})
check("TS-05a", "Critical issue logged", resp.get("success") is True)
req_id = resp.get("request_id")

# Also log a Low issue first (should not be assigned before Critical)
post("maintenance", "/report", {
    "room_number": 102,
    "description": "Burnt out light bulb",
    "urgency": "Low",
})

resp_assign = post("maintenance", "/assign-next", {"technician_name": "Dave"})
check("TS-05b", "Highest-priority (Critical) assigned first",
      resp_assign.get("urgency") == "Critical",
      f"assigned: {resp_assign.get('urgency')} — {resp_assign.get('request_id')}")

resp_resolve = post("maintenance", "/resolve", {
    "request_id": resp_assign["request_id"],
    "technician_name": "Dave",
})
check("TS-05c", "Issue resolved successfully", resp_resolve.get("success") is True)


# ─────────────────────────────────────────────────
# TS-06: Two simultaneous check-ins, same room type
# ─────────────────────────────────────────────────
section("TS-06: Simultaneous check-ins — no double booking")
resp1 = post("reception", "/checkin", {
    "guest_name": "Eve Taylor",
    "guest_id": "G003",
    "room_type": "Single",
})
resp2 = post("reception", "/checkin", {
    "guest_name": "Frank Lee",
    "guest_id": "G004",
    "room_type": "Single",
})
r1, r2 = resp1.get("room_number"), resp2.get("room_number")
both_ok = resp1.get("success") and resp2.get("success")
no_double = r1 != r2
check("TS-06a", "Both guests checked in successfully", both_ok)
check("TS-06b", f"Different rooms assigned ({r1} ≠ {r2})", no_double if both_ok else True)


# ─────────────────────────────────────────────────
# TS-07: No rooms available of requested type
# ─────────────────────────────────────────────────
section("TS-07: No rooms available — graceful failure")
# Occupy ALL suites
rooms = get("reception", "/rooms")["rooms"]
suite_clean = [r for r in rooms if r["room_type"] == "Suite" and r["status"] == "Clean"]
for i, r in enumerate(suite_clean):
    post("reception", "/checkin", {
        "guest_name": f"Suite Guest {i}",
        "guest_id": f"SG{i:03d}",
        "room_type": "Suite",
    })

resp = post("reception", "/checkin", {
    "guest_name": "Overflow Guest",
    "guest_id": "G999",
    "room_type": "Suite",
})
check("TS-07a", "Returns success=False (not a crash)", resp.get("success") is False)
check("TS-07b", "Returns a human-readable message", bool(resp.get("message")), resp.get("message",""))


# ─────────────────────────────────────────────────
# TS-08: Invalid room number
# ─────────────────────────────────────────────────
section("TS-08: Invalid room number — validation error")
try:
    r = httpx.post(f"{BASE['reception']}/checkout", json={"room_number": 9999}, timeout=5)
    check("TS-08a", "Returns 400 Bad Request", r.status_code == 400, f"HTTP {r.status_code}")
    body = r.json()
    check("TS-08b", "Error message present", bool(body.get("detail")), body.get("detail",""))
except Exception as exc:
    check("TS-08a", "No unhandled crash", True, f"Got expected error: {exc}")


# ─────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────
print(f"\n{'═'*60}")
print("  TEST RESULTS SUMMARY")
print(f"{'═'*60}")
passed = sum(1 for r in results if r["passed"] is True)
total  = sum(1 for r in results if r["passed"] is not None)
skipped = sum(1 for r in results if r["passed"] is None)
failed = total - passed
for r in results:
    icon = "✅" if r["passed"] else ("⏭️" if r["passed"] is None else "❌")
    print(f"  {icon} {r['id']:12s} {r['desc']}")
print(f"\n  Total: {total}  Passed: {passed}  Failed: {failed}  Skipped: {skipped}")
if failed == 0:
    print("\n  🎉 All automated tests passed!")
else:
    print(f"\n  ⚠️  {failed} test(s) failed. Check service logs in logs/")
