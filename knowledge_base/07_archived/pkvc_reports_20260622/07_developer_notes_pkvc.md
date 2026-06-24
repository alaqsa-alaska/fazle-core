---
title: PKVC Report — developer_notes.md
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report — developer_notes.md
**Article:** `06_developer_system/developer_notes.md`
**Wave:** Wave-1 (configuration flags, bridge config, backup) + Wave-2B (REST API, SSE, observability)
**PKVC Date:** 2026-06-22
**Verification Standard:** Three-level Evidence

---

## Critical Claims Verified

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | 23 REST endpoints in wa_chat_frontend | VERIFIED | `grep "@router\." modules/wa_chat_frontend/__init__.py` = 23 router decorator lines (not counting SSE stream) |
| 2 | SSE stream at `GET /api/wa/stream` | VERIFIED | `modules/wa_chat_frontend/__init__.py` line 720: `@router.get("/api/wa/stream")` |
| 3 | SSE emits `new_message` and `new_draft` event types | VERIFIED | Lines 740–773: `"type": "new_message"` and `"type": "new_draft"` JSON payloads |
| 4 | SSE poll interval = 3 seconds | VERIFIED | Line 810: `await asyncio.sleep(3)` |
| 5 | SSE keepalive = `: keepalive\n\n` | VERIFIED | Line 808: `yield ": keepalive\n\n"` |
| 6 | SSE `new_message` payload fields: id, phone, body, direction, platform, received_at, identity_role, intent_detected | VERIFIED | Lines 748–759: exact fields confirmed |
| 7 | SSE `new_draft` payload fields: id, phone, contact_name, draft_body, intent, original_message, created_at | VERIFIED | Lines 778–787: exact fields confirmed |
| 8 | FPE emits 5 bridge health gauges (fpe_bridge_gap_minutes, fpe_bridge_last_hour_count, fpe_skip_ratio_1h, fpe_processed_1h, fpe_retry_storm_count) | VERIFIED | `modules/fazle_payroll_engine/diagnostics.py` lines 191–213 |
| 9 | FPE emits fpe_dlq_backlog gauge | VERIFIED | `modules/fazle_payroll_engine/diagnostics.py` line 237 |
| 10 | Phase 12 gauges: phase12_event_emitted, phase12_event_failed, phase12_ws_clients, phase12_wr_lock_count, phase12_state_version | VERIFIED | `modules/fazle_payroll_engine/diagnostics.py` lines 371–398 |
| 11 | bridge_health_loop polls every 5 minutes | VERIFIED | `_HEALTH_POLL_SECS = 300` in diagnostics.py line 27 |
| 12 | Gap alert threshold = 30 minutes | VERIFIED | `_GAP_ALERT_MINS = 30` in diagnostics.py line 28 |
| 13 | DLQ warn threshold = 20 | VERIFIED | `_DLQ_WARN_THRESH = 20` in diagnostics.py line 31 |
| 14 | `GET /metrics` returns Prometheus text format | VERIFIED | `app/main.py` line 2662–2666: `@app.get("/metrics")` + `obs.render_prometheus()` |
| 15 | `GET /metrics/json` requires API key | VERIFIED | `app/main.py` line 2669: `@app.get("/metrics/json", dependencies=[Depends(require_api_key)])` |
| 16 | 9 AI runtime flags (OLLAMA_REPLY_DISABLED through OUTBOUND_ENABLED) | VERIFIED | `app/config.py` — all flags confirmed in Wave-1 article |
| 17 | bridge1 = port 8082, bridge2 = port 8081, media_processor = port 8090, app = port 8200 | VERIFIED | `app/config.py` — documented in Wave-1 article |

## Unverified / Legacy Claims

None.

## Pre-Correction Issues

None. No corrections required.

## Certification Decision

**CERTIFIED** — All 17 critical claims verified against production code.
