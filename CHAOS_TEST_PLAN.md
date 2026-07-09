# Chaos Test Plan — Fazle Core

**Purpose:** Define systematic chaos and soak tests to validate production resilience
before enabling high-volume operations.
**Prerequisite:** PRE_CHAOS_CHECKLIST.md must be fully completed (all 33 items GREEN).
**After each test:** Run POST_CHAOS_VALIDATION.md and record results.

---

## Overview

| Test ID | Name | Duration | Risk | Priority |
|---|---|---|---|---|
| C1 | Redis outage | 5 min | MEDIUM | HIGH |
| C2 | PostgreSQL pause | 3 min | HIGH | HIGH |
| C3 | App restart under load | 2 min | LOW | HIGH |
| C4 | Webhook flood | 5 min | MEDIUM | HIGH |
| C5 | Duplicate message storm | 5 min | LOW | MEDIUM |
| C6 | Concurrent admin commands | 5 min | MEDIUM | MEDIUM |
| C7 | Bridge disconnection | 10 min | MEDIUM | MEDIUM |
| C8 | Ollama outage | 10 min | LOW | MEDIUM |
| C9 | Disk pressure | 10 min | HIGH | LOW |
| S1 | 2-hour soak (normal load) | 2 h | LOW | HIGH |
| S2 | 4-hour soak (high load) | 4 h | MEDIUM | MEDIUM |

**Run order:** C1 → C3 → C4 → C5 → C2 → C6 → C7 → C8 → S1 → S2

---

## Abort Conditions

Stop the chaos test immediately if any of the following occur:

- App stops responding to `GET /health` for > 60 seconds
- PostgreSQL data file corruption detected
- Disk usage > 95%
- Memory usage > 95% sustained for > 5 minutes
- Any cash transaction row deleted (irreversible data loss)
- Test environment leaks into production WhatsApp numbers
- unrecoverable DB connection pool exhaustion

**Emergency abort:**
```bash
# Kill all background load scripts
kill $(jobs -p) 2>/dev/null
# Hard stop app
sudo systemctl stop fazle-core.service
# Verify DB is OK
docker exec ai-postgres psql -U postgres -c "SELECT COUNT(*) FROM wbom_employees;"
```

---

## Test Scripts

### Load Generator (used by multiple tests)

```python
#!/usr/bin/env python3
# scripts/chaos_load.py
# Usage: python scripts/chaos_load.py --rps 10 --duration 300
import asyncio, argparse, aiohttp, time, sys

import os

API_BASE = "http://localhost:8200"
API_KEY = os.environ["INTERNAL_API_KEY"]  # read from .env — never hardcode this value
HEADERS = {"X-Internal-Key": API_KEY, "Content-Type": "application/json"}

async def health_poll(session, results):
    start = time.time()
    try:
        async with session.get(f"{API_BASE}/health", timeout=aiohttp.ClientTimeout(total=5)) as r:
            results.append({"ok": r.status == 200, "latency_ms": (time.time()-start)*1000})
    except Exception as e:
        results.append({"ok": False, "error": str(e), "latency_ms": (time.time()-start)*1000})

async def run(rps, duration):
    results = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        end_time = time.time() + duration
        while time.time() < end_time:
            await asyncio.gather(*[health_poll(session, results) for _ in range(rps)])
            await asyncio.sleep(1)
    ok = sum(1 for r in results if r.get("ok"))
    total = len(results)
    avg_ms = sum(r.get("latency_ms", 0) for r in results) / total if total else 0
    print(f"Results: {ok}/{total} OK ({ok/total*100:.1f}%), avg latency: {avg_ms:.0f}ms")
    errors = [r for r in results if not r.get("ok")]
    if errors:
        print(f"Errors: {len(errors)} - samples: {errors[:3]}")
    return 0 if ok/total > 0.99 else 1

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--rps", type=int, default=5)
    p.add_argument("--duration", type=int, default=60)
    args = p.parse_args()
    sys.exit(asyncio.run(run(args.rps, args.duration)))
```

---

## C1 — Redis Outage

**Goal:** App handles Redis unavailability gracefully; operations that don't need Redis continue.
**Expected:** `/health` returns 200 with Redis degraded; rate limiting falls back; no crash.

```bash
# Step 1: Start background load
python scripts/chaos_load.py --rps 5 --duration 300 &
LOAD_PID=$!

# Step 2: Record baseline
docker exec ai-postgres psql -U postgres -d postgres \
  -c "SELECT COUNT(*) FROM fazle_admin_audit;" > /tmp/chaos_c1_baseline.txt

# Step 3: Kill Redis
docker stop ai-redis
echo "Redis stopped at $(date)"

# Step 4: Monitor for 5 minutes
for i in $(seq 1 30); do
  HTTP=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:8200/health 2>/dev/null || echo "000")
  echo "$(date +%H:%M:%S) health=$HTTP"
  sleep 10
done

# Step 5: Restore Redis
docker start ai-redis
sleep 3
docker exec ai-redis redis-cli PING

# Step 6: Stop load
kill $LOAD_PID 2>/dev/null

# Step 7: Validate
echo "=== C1 Validation ==="
curl -sf http://localhost:8200/health
curl -sf -H "X-Internal-Key: $INTERNAL_KEY" http://localhost:8200/health/deep | python3 -m json.tool
```

**Pass criteria:**
- App responded to `/health` throughout outage (HTTP 200 or 503 with degraded status)
- No app crash (process did not restart)
- After Redis restore, deep health shows Redis as healthy
- No data loss in PostgreSQL

---

## C2 — PostgreSQL Pause

**Goal:** App handles DB unavailability; returns 503 on DB-dependent routes; recovers on reconnect.
**Expected:** `/health/deep` shows DB unhealthy; `/health` may still return 200; no infinite loop.

> **CAUTION:** This is the highest-risk test. The DB pause means no writes succeed.
> Run this test only after C1 and C3 pass.

```bash
# Step 1: Record baseline
docker exec ai-postgres psql -U postgres -d postgres \
  -c "SELECT COUNT(*) FROM wbom_employees;" > /tmp/chaos_c2_baseline.txt

# Step 2: Start background health poll
python scripts/chaos_load.py --rps 2 --duration 300 &
LOAD_PID=$!

# Step 3: Pause PostgreSQL container (SIGSTOP — no data loss)
docker pause ai-postgres
echo "PostgreSQL paused at $(date)"

# Step 4: Monitor for 3 minutes
for i in $(seq 1 18); do
  HTTP=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:8200/health 2>/dev/null || echo "000")
  DEEP=$(curl -sf -H "X-Internal-Key: $INTERNAL_KEY" http://localhost:8200/health/deep 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('db','?'))" 2>/dev/null || echo "err")
  echo "$(date +%H:%M:%S) health=$HTTP db=$DEEP"
  sleep 10
done

# Step 5: Unpause PostgreSQL
docker unpause ai-postgres
echo "PostgreSQL unpaused at $(date)"
sleep 5

# Step 6: Stop load
kill $LOAD_PID 2>/dev/null

# Step 7: Validate
echo "=== C2 Validation ==="
curl -sf http://localhost:8200/health
curl -sf -H "X-Internal-Key: $INTERNAL_KEY" http://localhost:8200/health/deep | python3 -m json.tool
docker exec ai-postgres psql -U postgres -d postgres \
  -c "SELECT COUNT(*) FROM wbom_employees;"
```

**Pass criteria:**
- App returned 503 or 200-with-db-error during pause (not a crash)
- After unpause, deep health recovers to all-green within 60 seconds
- Row count matches baseline (no data loss)
- Connection pool reconnected without restart

---

## C3 — App Restart Under Load

**Goal:** App restarts cleanly with no in-flight request loss that causes data corruption.
**Expected:** Brief 503 during restart; full recovery within 30 seconds.

```bash
# Step 1: Start load
python scripts/chaos_load.py --rps 10 --duration 120 &
LOAD_PID=$!
sleep 10  # Let load stabilize

# Step 2: Record baseline
docker exec ai-postgres psql -U postgres -d postgres \
  -c "SELECT COUNT(*) FROM fazle_payment_drafts WHERE status='pending';" \
  > /tmp/chaos_c3_baseline.txt

# Step 3: Restart service
sudo systemctl restart fazle-core.service
echo "App restarted at $(date)"

# Step 4: Poll recovery time
START_RESTART=$(date +%s)
while true; do
  HTTP=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:8200/health 2>/dev/null || echo "000")
  if [ "$HTTP" = "200" ]; then
    ELAPSED=$(($(date +%s) - START_RESTART))
    echo "Recovered after ${ELAPSED}s"
    break
  fi
  echo "$(date +%H:%M:%S) still down ($HTTP)..."
  sleep 1
done

# Step 5: Stop load and validate
kill $LOAD_PID 2>/dev/null
echo "=== C3 Validation ==="
curl -sf http://localhost:8200/health/deep | python3 -m json.tool
```

**Pass criteria:**
- App recovers within 30 seconds
- No zombie processes after restart
- No partial-write corruption (pending drafts count matches or exceeds baseline)
- Load tool reports > 95% success rate overall

---

## C4 — Webhook Flood

**Goal:** App handles high webhook volume without crashing or dropping legitimate messages.
**Expected:** Rate limiting kicks in (429), legitimate messages still processed.

```python
#!/usr/bin/env python3
# scripts/chaos_flood.py
# Usage: python scripts/chaos_flood.py --count 500 --concurrency 50
import asyncio, aiohttp, argparse, time
import os

API_BASE = "http://localhost:8200"
API_KEY = os.environ["INTERNAL_API_KEY"]  # read from .env — never hardcode this value

FAKE_WEBHOOK = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "test", "changes": [{"value": {
        "messaging_product": "whatsapp",
        "messages": [{"from": "8801999999999", "type": "text",
                       "text": {"body": "test flood message"},
                       "id": "chaos_flood_msg", "timestamp": "1700000000"}]
    }, "field": "messages"}]}]
}

async def send_webhook(session, results):
    start = time.time()
    try:
        async with session.post(f"{API_BASE}/webhook/meta",
                                json=FAKE_WEBHOOK,
                                timeout=aiohttp.ClientTimeout(total=5)) as r:
            results.append({"status": r.status, "ms": (time.time()-start)*1000})
    except Exception as e:
        results.append({"status": "err", "error": str(e), "ms": (time.time()-start)*1000})

async def run(count, concurrency):
    results = []
    async with aiohttp.ClientSession() as session:
        for i in range(0, count, concurrency):
            batch = min(concurrency, count - i)
            await asyncio.gather(*[send_webhook(session, results) for _ in range(batch)])
            await asyncio.sleep(0.1)
    by_status = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"Flood results ({count} messages): {by_status}")
    avg_ms = sum(r["ms"] for r in results) / len(results)
    print(f"Avg latency: {avg_ms:.0f}ms")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=500)
    p.add_argument("--concurrency", type=int, default=50)
    args = p.parse_args()
    asyncio.run(run(args.count, args.concurrency))
```

```bash
# Run flood
python scripts/chaos_flood.py --count 500 --concurrency 50

# Check app is still alive
curl http://localhost:8200/health

# Check for errors in logs
journalctl -u fazle-core -n 50 --no-pager | grep -E "ERROR|Exception" | wc -l
```

**Pass criteria:**
- App still responds to `/health` after flood
- Returned mix of 200/202/429 (not all 500)
- No process crash
- Log error count < 20

---

## C5 — Duplicate Message Storm

**Goal:** Idempotency guards prevent duplicate payment drafts on duplicate webhook delivery.
**Expected:** Only one draft created per unique escort event, even with 10x duplicate delivery.

```bash
# Record draft count before
BEFORE=$(docker exec ai-postgres psql -U postgres -d postgres -t \
  -c "SELECT COUNT(*) FROM fazle_payment_drafts;" | tr -d ' ')
echo "Drafts before: $BEFORE"

# Send same release event 10 times
for i in $(seq 1 10); do
  curl -sf -X POST \
    -H "X-Internal-Key: $INTERNAL_KEY" \
    -H "Content-Type: application/json" \
    -d '{"employee_id": 1, "extracted": {"days": 3, "amount": 1500}, "source": "test_duplicate_storm"}' \
    http://localhost:8200/escort/release
  echo "Request $i sent"
done

# Check draft count after
AFTER=$(docker exec ai-postgres psql -U postgres -d postgres -t \
  -c "SELECT COUNT(*) FROM fazle_payment_drafts;" | tr -d ' ')
echo "Drafts after: $AFTER"
echo "New drafts created: $((AFTER - BEFORE))"
# Expected: 1 (idempotency working) or 0 (no active program)
```

**Pass criteria:**
- No more than 1 new draft created per unique (employee_id, source)
- No app crash
- Appropriate error/ok response on duplicate requests

---

## C6 — Concurrent Admin Commands

**Goal:** Admin command dedup prevents double-finalize under concurrent WhatsApp-style delivery.
**Expected:** At most 1 cash transaction created per approve attempt.

```bash
# Find a pending draft to test with
DRAFT_ID=$(docker exec ai-postgres psql -U postgres -d postgres -t \
  -c "SELECT id FROM fazle_payment_drafts WHERE status='pending' LIMIT 1;" | tr -d ' ')
echo "Testing with draft_id=$DRAFT_ID"

if [ -z "$DRAFT_ID" ] || [ "$DRAFT_ID" = "" ]; then
  echo "No pending drafts — create one first via escort flow"
  exit 1
fi

# Record baseline
BEFORE=$(docker exec ai-postgres psql -U postgres -d postgres -t \
  -c "SELECT COUNT(*) FROM wbom_cash_transactions WHERE draft_id=$DRAFT_ID;" | tr -d ' ')
echo "Transactions before: $BEFORE"

# Send 10 concurrent approve commands
for i in $(seq 1 10); do
  curl -sf -X POST \
    -H "X-Internal-Key: $INTERNAL_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"draft_id\": $DRAFT_ID, \"approved_amount\": 1500, \"method\": \"bkash\"}" \
    http://localhost:8200/payment/finalize &
done
wait

# Check result
AFTER=$(docker exec ai-postgres psql -U postgres -d postgres -t \
  -c "SELECT COUNT(*) FROM wbom_cash_transactions WHERE draft_id=$DRAFT_ID;" | tr -d ' ')
echo "Transactions after: $AFTER"
echo "New transactions: $((AFTER - BEFORE))"
# Expected: 1 (application-level dedup working)
# Known: may be > 1 (see TESTING_STATUS.md Known Limitations)
```

**Pass criteria:**
- No app crash
- Between 1 and 3 new cash transactions (known race window is acceptable)
- Audit log entries created for all attempts

---

## C7 — Bridge Disconnection

**Goal:** App handles bridge unavailability gracefully; resumes polling on reconnect.
**Expected:** Inbound poll errors logged but not fatal; bridge health endpoint shows degraded.

```bash
# Stop bridge2 (OPS)
sudo systemctl stop bridge2
echo "Bridge2 stopped at $(date)"

# Monitor for 10 minutes
for i in $(seq 1 20); do
  HEALTH=$(curl -sf http://localhost:8200/health 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "error")
  echo "$(date +%H:%M:%S) app=$HEALTH"
  sleep 30
done

# Restart bridge
sudo systemctl start bridge2
sleep 5
echo "Bridge2 restarted. Health:"
curl -sf http://localhost:8081/health

# Verify app is still polling
echo "App health after bridge restore:"
curl -sf http://localhost:8200/health/deep | python3 -m json.tool
```

**Pass criteria:**
- App still responds to `/health` throughout
- Log shows bridge poll errors (expected, not fatal)
- After bridge restore, polling resumes within 30 seconds
- No crash or infinite retry loop

---

## C8 — Ollama Outage

**Goal:** App falls back to keyword-based intent classification when Ollama is unavailable.
**Expected:** Message processing continues in degraded mode; `/health/deep` shows Ollama warning.

```bash
# Stop Ollama
docker stop ollama
echo "Ollama stopped at $(date)"

# Send test messages and observe fallback behavior
for i in $(seq 1 5); do
  curl -sf -X POST \
    -H "Content-Type: application/json" \
    -d '{"entry":[{"changes":[{"value":{"messages":[{"from":"8801811111111","type":"text","text":{"body":"status"},"id":"chaos_ollama_test_'$i'","timestamp":"1700000000"}]}}]}]}' \
    "http://localhost:8200/webhook/mcp2" &
done
wait

# Check health
curl -sf -H "X-Internal-Key: $INTERNAL_KEY" http://localhost:8200/health/deep | python3 -m json.tool

# Restore Ollama
docker start ollama
sleep 10
curl -sf http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print('Ollama OK:', [m['name'] for m in d.get('models',[])])"
```

**Pass criteria:**
- App responds to webhooks with fallback classification (no crash)
- `/health/deep` shows Ollama as warning (not hard failure)
- After Ollama restore, subsequent messages use LLM classification

---

## C9 — Disk Pressure

**Goal:** App handles low disk conditions gracefully; backup scheduler fails safely.
**Expected:** App continues operating; backup job logs error but does not crash app.

> **WARNING:** This test requires temporarily filling disk. Verify you have > 20 GB
> free before running or skip this test.

```bash
# Check available space first
df -h /home
# Only run if > 20 GB available

# Fill to 90% of free space with a test file
FREE_KB=$(df -k /home | awk 'NR==2 {print $4}')
FILL_KB=$((FREE_KB * 9 / 10))   # fill 90% of free space
echo "Filling $((FILL_KB / 1024)) MB..."
dd if=/dev/zero of=/tmp/disk_fill_test bs=1M count=$((FILL_KB / 1024)) 2>/dev/null

# Trigger backup (should fail gracefully)
HTTP=$(curl -sf -o /dev/null -w "%{http_code}" \
  -X POST -H "X-Internal-Key: $INTERNAL_KEY" http://localhost:8200/backup/run)
echo "Backup response: $HTTP (expected 500 or 507 or similar)"

# Verify app still healthy
curl -sf http://localhost:8200/health

# ALWAYS clean up immediately
rm /tmp/disk_fill_test
echo "Disk space restored:"
df -h /home
```

**Pass criteria:**
- App still serves `/health` during disk pressure
- Backup job fails with error (not crash)
- After cleanup, backup job succeeds

---

## S1 — 2-Hour Soak (Normal Load)

**Goal:** System remains stable under sustained normal operational load.
**Duration:** 2 hours
**Load:** 5 req/s health polls + periodic payment draft listing

```bash
#!/bin/bash
# scripts/soak_2h.sh

set -e
echo "Starting 2-hour soak test at $(date)"
echo "Press Ctrl+C to abort"

API_BASE="http://localhost:8200"
API_KEY="$INTERNAL_KEY"

# Baseline counts
docker exec ai-postgres psql -U postgres -d postgres -c "
  SELECT 'before' AS phase,
    (SELECT COUNT(*) FROM fazle_payment_drafts) AS drafts,
    (SELECT COUNT(*) FROM wbom_cash_transactions) AS txns,
    (SELECT COUNT(*) FROM fazle_outbound_queue WHERE status='dead') AS dlq;
"

# Start load (background)
python scripts/chaos_load.py --rps 5 --duration 7200 &
LOAD_PID=$!

# Monitor loop (every 10 minutes)
for i in $(seq 1 12); do
  sleep 600
  echo ""
  echo "=== Checkpoint $i/12 at $(date) ==="
  HTTP=$(curl -sf -o /dev/null -w "%{http_code}" $API_BASE/health)
  echo "Health: $HTTP"
  docker exec ai-postgres psql -U postgres -d postgres -t -c "
    SELECT 'dlq_count=' || COUNT(*) FROM fazle_outbound_queue WHERE status='dead';
  "
  FREE=$(df -k /home | awk 'NR==2 {print $4}')
  echo "Free disk: $((FREE / 1024)) MB"
done

# Stop load
kill $LOAD_PID 2>/dev/null

# Final counts
echo ""
echo "=== Final State ==="
docker exec ai-postgres psql -U postgres -d postgres -c "
  SELECT 'after' AS phase,
    (SELECT COUNT(*) FROM fazle_payment_drafts) AS drafts,
    (SELECT COUNT(*) FROM wbom_cash_transactions) AS txns,
    (SELECT COUNT(*) FROM fazle_outbound_queue WHERE status='dead') AS dlq;
"

echo "Soak test complete at $(date)"
```

**Pass criteria:**
- App responds to `/health` throughout (> 99.9% uptime)
- DLQ count does not grow beyond pre-test level
- Memory usage does not grow unboundedly (no memory leak)
- No OOM kills (`dmesg | grep -i "out of memory"`)
- Average response latency < 500 ms
- No DB connection pool exhaustion

---

## S2 — 4-Hour Soak (High Load)

**Same as S1 but with higher load: 20 req/s for 4 hours.**

Only run after S1 passes.

```bash
# Same as soak_2h.sh but:
python scripts/chaos_load.py --rps 20 --duration 14400 &
```

---

## Results Log

| Test ID | Date | Duration | Pass/Fail | Issues Found | Notes |
|---|---|---|---|---|---|
| C1 | | | | | |
| C2 | | | | | |
| C3 | | | | | |
| C4 | | | | | |
| C5 | | | | | |
| C6 | | | | | |
| C7 | | | | | |
| C8 | | | | | |
| C9 | | | | | |
| S1 | | | | | |
| S2 | | | | | |

---

## After All Tests Pass

When all chaos tests and both soak tests pass:

```bash
# Create post-chaos stable tag
git tag -a post-chaos-stable-$(date +%Y-%m-%d) \
  -m "All chaos and soak tests passed. System validated for production load."

# Update TESTING_STATUS.md (add chaos test results)

# Schedule regular soak tests (weekly recommended)
```
