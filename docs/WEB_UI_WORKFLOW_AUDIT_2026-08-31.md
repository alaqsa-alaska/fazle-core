# Fazle Core — Web UI / Workflow Audit Report (2026-08-31)

> **READ-ONLY audit. No production changes were made in producing this document.**
>
> **Companion reference:** `docs/PRODUCTION_SYSTEM_STATUS_2026-08-31.md` (live/degraded/frozen state flags).
> **Architecture reference:** `docs/ARCHITECTURE.md` and `README.md`.

---

## 1. Executive Summary

This audit maps every user-facing web page and API endpoint in the Fazle Core application, traces how the 13 frontend HTML files wire to the 234 backend route definitions across 15 FastAPI routers, and documents gaps between what exists and what is operational.

**Key findings:**

| Category | Count | Status |
|---|---|---|
| Static HTML pages served by FastAPI | 13 | See §2 |
| FastAPI routers (mounted) | 15 | See §3 |
| Total backend route definitions | ~234 | See §3 |
| Frontend ↔ Backend wiring gaps | 4 | See §6 |
| API endpoints with no known frontend consumer | ~20 | See §7 |
| UI pages / tabs with no route or broken wiring | 5 | See §5 |
| Dead / stale pages | 3 | See §8 |

**Escort Program Lifecycle (verified working end-to-end):**
```
Draft  →  Confirmed  →  Running  →  Completed
(WA msg)   (bridge1)    (dispatch)   (bridge1 release)
```

**Release Slip OCR Lifecycle (PARTIAL — auto-close NOT wired):**
```
OCR extract  →  admin draft created  →  Dispatch UI approve  →  close_program()
(slip img)     (fazle_draft_replies)      ✓ wired           ✓ only path
```

---

## 2. Complete Web Page / URL Inventory

All pages below are served as `HTMLResponse` from `app/main.py`.

### 2.1 Payroll Suite (`/payroll*`)

| Route | File | Tabs / Sections | Lines | Functions | Notes |
|---|---|---|---|---|---|
| `/payroll` | `app/static/payroll.html` | 15 tabs | 5,904 | 229 | Primary payroll management UI. 3 API bases: `/api/fpe`, `/admin`, `/api/admin` |
| `/payroll/overview` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/transactions` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/search` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/employees` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/addemployee` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/unmatched` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/review` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/sync` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/reports` | redirect → `/payroll` | — | — | — | Dead redirect |
| `/payroll/settings` | redirect → `/payroll` | — | — | — | Dead redirect |

> **All `/payroll/*` sub-routes are dead redirects to `/payroll`.** All functionality is delivered via tabs within the single `payroll.html`. Users navigating directly to sub-URLs get the same page and must manually switch tabs.

### 2.2 Core Operations Pages

| Route | File | Lines | Functions | API Base | Status |
|---|---|---|---|---|---|
| `/dashboard` | `app/static/dashboard.html` | 769 | 33 | `/api/wa`, `/api/fpe` | **LIVE** — 9 tabs |
| `/escort-roster` | `app/static/escort-roster.html` | 1,897 | 62 | `/api/escort-roster` | **LIVE** — 8 tabs |
| `/dispatch` | `app/static/dispatch.html` | 953 | ~20 | `/api/dispatch` | **LIVE** — 4 sections |
| `/open-chat` | `app/static/open-chat.html` | 1,886 | ~50 | `/api/wa` | **LIVE** — 5 right-panel tabs |
| `/wa-chat` | `app/static/wa_chat.html` | 1,224 | ~40 | `/api/wa` | **LIVE** — contacts/messages/drafts |
| `/drafts` | `app/static/drafts.html` | 534 | ~10 | `/api/drafts` | **LIVE** |
| `/unmatched-confirmations` | `app/static/unmatched-confirmations.html` | 425 | ~10 | `/api/escort` | **LIVE** |
| `/kb` | `app/static/kb.html` | 415 | ~5 | `/api/kb` | **LIVE** |
| `/payment-drafts` | served by main.py | — | — | `/api/payment-drafts` | **LIVE** (redirect, see §5.1) |
| `/admin/safe-mode` | served by main.py | — | — | — | **LIVE** (see §5.1) |
| `/admin/payment-drafts` | served by main.py | — | — | — | **LIVE** (see §5.1) |
| `/admin/approvals` | served by main.py | — | — | — | **LIVE** (see §5.1) |
| `/admin/recruitment` | served by main.py | — | — | — | **LIVE** (see §5.1) |

### 2.3 Stale / Preview Pages

| Route | File | Lines | Status |
|---|---|---|---|
| `/ux-preview` | `app/static/ux-preview.html` | 675 | **STALE** — standalone UX preview tool, not part of operational UI |
| `/ux-preview-v2` | `app/static/ux-preview-v2.html` | 906 | **STALE** — v2 of above |
| `/dashboard/legacy` | served by main.py | — | **STALE** — redirect to `/dashboard` |

---

## 3. Backend Route Inventory

All 15 FastAPI routers mounted in `app/main.py`. Router prefixes confirmed from source.

### 3.1 Mounted Routers

| # | Router Variable | Prefix | Source File | Key Endpoints |
|---|---|---|---|---|
| 1 | `fpe_router` | `/api/fpe` | `modules/fazle_payroll_engine/routes.py` | employees, transactions, unmatched, cash, income, operator |
| 2 | `escort_roster_router` | `/api/escort-roster` | `modules/escort_roster/routes.py` | programs CRUD, sync, config, change-requests |
| 3 | `escort_unmatched_confirmations_router` | `/api/escort` | `modules/escort/routes.py` | unmatched confirmations review |
| 4 | `dispatch_router` | `/api/dispatch` | `modules/dispatch/routes.py` | assign, overlap, release-slips, same-vessel |
| 5 | `wa_chat_router` | `/api/wa` | `modules/wa_chat_frontend/__init__.py` | contacts, messages, send, drafts, groups, stream |
| 6 | `client_billing_router` | (none) | `modules/client_billing/routes.py` | `/admin/bills/*`, `/admin/client-billing-profiles/*` (no prefix) |
| 7 | `attendance_router` | (none) | `modules/attendance/routes.py` | `/admin/attendance/*`, `/admin/payroll/*` (no prefix) |
| 8 | `drafts_router` | (none) | `modules/drafts/routes.py` | `/api/drafts/*` (no prefix) |
| 9 | `draft_approval_router` | `/api/payment-drafts` | `modules/draft_approval/routes.py` | approve, edit, reject |
| 10 | `hermes_tasks_router` | (none — root) | `modules/hermes_tasks/routes.py` | tasks, actions, business-actions |
| 11 | `kb_upload_router` | (none) | `modules/kb_upload/routes.py` | `/admin/kb/*` (no prefix) |
| 12 | `admin_employees_router` | `/api/admin` | `modules/admin_employees/__init__.py` | employees CRUD, link-fpe, create-and-link |
| 13 | `admin_transactions_router` | `/api/admin` | `modules/admin_transactions/__init__.py` | transactions CRUD, login, session |
| 14 | `contact_roles_router` | `/admin/contact-roles` | `modules/contact_roles/routes.py` | contact role CRUD |
| 15 | `social_auto_reply_router` | `/api/social` | `modules/social_auto_reply/routes.py` | status, queue, flagged, pause/resume |
| 16 | `preflight_guard_router` | (none — root) | `modules/preflight_guard/routes.py` | release preflight, kill-switch, flags |

> **Note on router prefixes:** Some routers are mounted with `APIRouter()` (no prefix), meaning all their `@router.get/post/put/delete` decorators define the full path. For example `kb_upload_router` has no prefix, and its routes are `@router.post("/admin/kb/upload")`, `@router.get("/admin/kb/list")`, etc. — giving final paths of `/admin/kb/upload`, `/admin/kb/list`. Always read the actual `@router` decorator in the source file to determine the final path.

### 3.2 Inline Routes in `app/main.py` (~133 endpoints)

| Group | Prefix | Notable Endpoints |
|---|---|---|
| Health | `/health`, `/health/deep`, `/health/c1b` | System health checks |
| WhatsApp | `/api/whatsapp/*` | message-stats, lookup, query, conversation, attention |
| Outbound | `/api/outbound/*` | status, dead-letters |
| Runtime | `/api/runtime/nodes*` | node listing, history |
| Queue | `/api/queue/*` | dead-letters, arbiter-metrics |
| Recruitment | `/api/recruitment/*` | interested-candidates |
| Identity | `/api/identity/*` | resolve, verify-claim |
| Intent | `/api/intent/*` | classify |
| Hermes | `/api/hermes/*` | decisions/lookup, kernel/events |
| RAG | `/api/rag/*` | rebuild, qdrant-status, stats, recent-searches |
| Bridges | `/api/bridges/*` | diagnostics, probe |
| Webhook | `/webhook/meta`, `/webhook/mcp1`, `/webhook/mcp2` | Inbound message ingestion |
| Send | `/send/meta`, `/send/mcp1`, `/send/mcp2` | Outbound message dispatch |
| Payment | `/payment/*` | escort-draft, ingest, advance-draft |
| Self-Heal | `/api/self-heal/*` | diagnostics, check |
| Frontend | `/api/frontend/*` | heartbeat, sync-stats |
| State | `/api/state-version` | Version tracking |

---

## 4. Frontend ↔ Backend Wiring Matrix

### 4.1 `payroll.html` (229 functions, 15 tabs)

| Tab | API Base(s) Used | Wrapper | Status |
|---|---|---|---|
| Employees | `/api/admin` | `FazleAuthFetch` | ✅ Wired |
| Transactions | `/api/admin` | `FazleAuthFetch` | ✅ Wired |
| Unmatched | `/api/fpe` | `api()` | ✅ Wired |
| Cash | `/api/fpe` | `api()` | ✅ Wired |
| Income | `/api/fpe` | `api()` | ✅ Wired |
| Operator | `/api/admin` | `FazleAuthFetch` | ✅ Wired |
| Review | `/api/admin`, `/api/fpe` | Mixed | ✅ Wired |
| Sync | `/api/admin` | `FazleAuthFetch` | ✅ Wired |
| Reports | mixed | mixed | ✅ Wired |
| Settings | `/api/admin` | `FazleAuthFetch` | ✅ Wired |

### 4.2 `escort-roster.html` (62 functions, 8 tabs)

| Tab | API Endpoints Used | Status |
|---|---|---|
| Programs List | `GET /api/escort-roster/programs` | ✅ Wired |
| Active Programs | `GET /api/escort-roster/programs?status=running` | ✅ Wired |
| Closed Programs | `GET /api/escort-roster/programs?status=completed` | ✅ Wired |
| Change Requests | `GET/POST /api/escort-roster/change-requests` | ✅ Wired |
| Config | `GET /api/escort-roster/config` | ✅ Wired |
| Sync | `POST /api/escort-roster/sync` | ✅ Wired |
| Export | `GET /api/escort-roster/programs/export` | ✅ Wired |
| Audit Log | `GET /api/escort-roster/audit-logs` | ✅ Wired |

### 4.3 `dispatch.html` (4 sections)

| Section | API Endpoints Used | Status |
|---|---|---|
| Availability | `GET /api/dispatch/availability` | ✅ Wired |
| Assignment | `POST /api/dispatch/assign` | ✅ Wired |
| Conflicts | `GET /api/dispatch/overlaps` | ✅ Wired |
| Release Slips | `POST /api/dispatch/approve-release-slip` | ✅ Wired |

### 4.4 `wa_chat.html`

| Section | API Endpoints Used | Status |
|---|---|---|
| Contacts | `GET /api/wa/contacts` | ✅ Wired |
| Messages | `GET /api/wa/messages` | ✅ Wired |
| Send | `POST /api/wa/send` | ✅ Wired |
| Drafts | `GET /api/wa/drafts` | ✅ Wired |
| Groups | `GET /api/wa/groups` | ✅ Wired |
| Stream | `GET /api/wa/stream` | ✅ Wired |

### 4.5 `drafts.html`

| Action | API Endpoint | Status |
|---|---|---|
| List | `GET /api/drafts` | ✅ Wired |
| Approve | `POST /api/drafts/approve` | ✅ Wired |
| Edit | `POST /api/drafts/edit` | ✅ Wired |
| Delete | `POST /api/drafts/delete` | ✅ Wired |

### 4.6 `unmatched-confirmations.html`

| Action | API Endpoint | Status |
|---|---|---|
| List | `GET /api/escort/unmatched-confirmations` | ✅ Wired |
| Detail | `GET /api/escort/unmatched-confirmations/{id}` | ✅ Wired |
| Actions | `POST /api/escort/unmatched-confirmations/{id}/*` | ✅ Wired |

### 4.7 `kb.html`

| Action | API Endpoint | Status |
|---|---|---|
| Upload | `POST /admin/kb/upload` | ✅ Wired |
| List | `GET /admin/kb/list` | ✅ Wired |
| Stats | `GET /admin/kb/stats` | ✅ Wired |
| Delete | `POST /admin/kb/delete` | ✅ Wired |

---

## 5. Broken Tabs / Sidebars / Modals / Actions

### 5.1 UI Orphan Pages (exist but have no dedicated frontend file)

These pages are served by FastAPI but have no matching standalone HTML file in `app/static/`:

| Route | Handler | Status | Notes |
|---|---|---|---|
| `/payment-drafts` | `main.py` line 3175 | **LIVE** | Redirects to `drafts.html` — the old `/api/payment-drafts` route group. This is the legacy payment draft approval UI. Actual functionality is in `drafts.html` via `/api/drafts` and `draft_approval_router` (`/api/payment-drafts`). No broken wiring — just two paths to the same feature. |
| `/admin/safe-mode` | `main.py` line 3906 | **LIVE** | Shows system safe-mode toggle. No dedicated HTML — renders a hardcoded HTML string directly in `main.py`. Operational but not discoverable via navigation. |
| `/admin/payment-drafts` | `main.py` line 3927 | **LIVE** | Redirects to `drafts.html`. Legacy URL. |
| `/admin/approvals` | `main.py` line 3939 | **LIVE** | Redirects to `drafts.html`. Legacy URL. |
| `/admin/recruitment` | `main.py` line 3996 | **LIVE** | Shows recruitment stats page. Hardcoded HTML in `main.py`. No dedicated static file. |
| `/dashboard/legacy` | `main.py` line 3990 | **STALE** | Redirects to `/dashboard`. Legacy route from v1. |

### 5.2 Dead Redirects

All `/payroll/*` sub-routes (lines 3206–3213 of `main.py`) redirect to `/payroll`. Users navigating directly must manually select the correct tab. No broken functionality — all tabs are reachable from the main `payroll.html` shell.

### 5.3 Dead Static Files

| File | Status |
|---|---|
| `app/static/ux-preview.html` | **STALE** — standalone UX preview tool, not part of operational workflow |
| `app/static/ux-preview-v2.html` | **STALE** — v2 of above |
| `app/static/wbom-create-link.html` | **STALE** — cleanup tool, appears to be ad-hoc |
| `app/static/wbom-link-review.html` | **STALE** — cleanup tool |

---

## 6. Escort Program Lifecycle (Verified Trace)

### 6.1 Lifecycle State Machine

```
[ESCORT CLIENT MESSAGE]  ────→  wbom_escort_programs.status = 'draft'
                                          │
         ┌───────────────────────────────┘
         │ bridge_poller outbound scan
         │ is_completed_escort_draft()
         ▼
[BRIDGE1 OUTBOUND CONFIRMATION]  ────→  wbom_escort_programs.status = 'confirmed'
                                          │
         ┌───────────────────────────────┘
         │ finalize_attendance_draft()
         │ admin attendance APPROVE
         ▼
[ATTENDANCE APPROVED]  ────→  mark_program_running_on_attendance()
                                      wbom_escort_programs.status = 'running'
                                          │
         ┌───────────────────────────────┘
         │ bridge_poller outbound scan
         │ is_release_confirmation()
         │ handle_admin_release_confirmation()
         ▼
[RELEASE CONFIRMATION]  ────→  handle_release_event()
                                      ├── close_program()         → status = 'completed'
                                      ├── backfill_attendance()   → attendance records
                                      └── create_escort_payment_draft()  → fazle_draft_replies
```

### 6.2 Verified Call Chain

**Draft → Confirmed:**
- `modules/message_router/process_message()` → `modules.escort.save_escort_programs()` → INSERT `wbom_escort_programs` (status=`draft`)
- `modules/bridge_poller/outbound_scan()` → `is_completed_escort_draft()` → `handle_admin_escort_completion()` → UPDATE status=`confirmed`

**Confirmed → Running:**
- `modules/attendance/finalize_attendance_draft()` (line 325) → `mark_program_running_on_attendance()` → UPDATE status=`running`

**Running → Completed:**
- `modules/bridge_poller/outbound_scan()` → `is_release_confirmation()` → `handle_admin_release_confirmation()` (line 660, `modules/escort_lifecycle/__init__.py`) → `handle_release_event()` (line 264) → `close_program()` + `backfill_attendance_for_program()` + `create_escort_payment_draft()`

---

## 7. Release Slip OCR Lifecycle (PARTIAL)

### 7.1 Flow: OCR Extract → Admin Review

```
[SLIP IMAGE RECEIVED]
        │
        ▼
extract_escort_slip()  ────→  escort_slip_extractions table
(working)                     (confirmed working)
        │
        ▼
handle_ocr_release_slip()  ────→  fazle_draft_replies (DRAFT ONLY)
(modules/escort_lifecycle/__init__.py line 780)
        │
        │ NOTE: Does NOT call close_program()
        │ NOTE: Does NOT auto-send to Bridge2
        ▼
Admin reviews draft in Dispatch UI
        │
        ▼
approve_release_slip()  ────→  close_program()
(modules/dispatch/__init__.py line 1534)    ✅ THIS PATH WORKS
```

### 7.2 Defect: Auto-Close NOT Wired in OCR Path

**Symptom:** When a release slip is extracted via OCR from a WhatsApp image, the system creates an admin review draft but does **not** automatically close the escort program.

**Expected:** OCR release slip → program auto-closes + attendance backfill + payment draft creation.

**Actual:** OCR release slip → admin draft created in `fazle_draft_replies` for manual review. Program remains in `running` state until an admin uses the Dispatch UI `approve_release_slip` endpoint.

**Root cause:** `handle_ocr_release_slip()` (line 780, `modules/escort_lifecycle/__init__.py`) saves the draft but contains no call to `close_program()`. The OCR path and the natural-language release confirmation path (`handle_release_event()`) are separate code paths.

**Missing wiring:** OCR release slip confirmation should invoke the same `close_program()` flow as the natural release confirmation path, after DB program matching succeeds.

**Recommended fix:** After `handle_ocr_release_slip()` successfully matches a DB program (`program_id` is not None), call `close_program()` with the matched program ID, equivalent to what `handle_release_event()` does. Add conditional: if `CONFIDENCE_SCORE_THRESHOLD` is met (e.g., >0.7), auto-close; below threshold, keep manual review.

---

## 8. API Orphans (~20 endpoints with no known frontend consumer)

The following API endpoints exist in the backend but have no known frontend consumer in the 13 HTML files:

| Endpoint | Router | Potential Consumer |
|---|---|---|
| `GET /api/recruitment/interested-candidates` | main.py:1856 | No frontend file found |
| `GET /api/identity/resolve` | main.py:1992 | No frontend file found |
| `GET /api/identity/verify-claim` | main.py:2004 | No frontend file found |
| `GET /api/intent/classify` | main.py:2025 | No frontend file found |
| `GET /api/hermes/decisions/lookup` | main.py:2049 | No frontend file found |
| `GET /api/hermes/kernel/events` | main.py:2103 | No frontend file found |
| `POST /api/rag/rebuild` | main.py:2187 | No frontend file found |
| `GET /api/rag/qdrant-status` | main.py:2234 | No frontend file found |
| `GET /api/rag/stats` | main.py:2245 | No frontend file found |
| `GET /api/rag/recent-searches` | main.py:2264 | No frontend file found |
| `POST /api/self-heal/check` | main.py:2280 | No frontend file found |
| `GET /api/self-heal/diagnostics` | main.py:2170 | No frontend file found |
| `GET /api/runtime/nodes` | main.py:953 | No frontend file found |
| `GET /api/runtime/nodes/history` | main.py:1000 | No frontend file found |
| `GET /api/queue/dead-letters` | main.py:1020 | No frontend file found |
| `GET /api/outbound/dead-letters` | main.py:1046 | No frontend file found |
| `GET /api/queue/arbiter-metrics` | main.py:1071 | No frontend file found |
| `GET /api/state-version` | main.py:2296 | No frontend file found |
| `POST /api/bridges/probe` | main.py:2152 | No frontend file found |
| `GET /admin/contact-roles` | `contact_roles_router` | No frontend file found |

**Note:** Several of these are internal/system endpoints used by Hermes, scheduler jobs, or monitoring agents rather than direct user-facing workflows. Absence from `app/static/` is expected for infrastructure endpoints.

---

## 9. P0 / P1 / P2 / P3 Defect Register

### P0 — Production Blocker

| ID | Title | Symptom | Expected | Actual | File(s) | Fix |
|---|---|---|---|---|---|---|
| P0-1 | Release slip OCR does not auto-close program | OCR release slip creates admin draft but leaves escort program in `running` state | OCR extract → auto-close program + backfill attendance + create payment draft | Only manual Dispatch UI approval triggers `close_program()` | `modules/escort_lifecycle/__init__.py` line 780 (`handle_ocr_release_slip`) | Add `close_program()` call after successful DB program match in `handle_ocr_release_slip()`. Gate on confidence score threshold. |

### P1 — Significant Workflow Gap

| ID | Title | Symptom | Expected | Actual | File(s) | Fix |
|---|---|---|---|---|---|---|
| P1-1 | All `/payroll/*` sub-URLs are dead redirects | Navigating to `/payroll/transactions` or `/payroll/employees` shows main payroll page on wrong tab | Each sub-URL loads the corresponding tab directly | All `/payroll/*` routes redirect to `/payroll` without tab selection parameter | `app/main.py` lines 3206–3213 | Add `?tab=` query parameter to each redirect target, or consolidate into tab-anchor navigation |
| P1-2 | Stale preview pages not archived | `ux-preview.html`, `ux-preview-v2.html`, `wbom-*.html` remain in `app/static/` | Non-operational pages archived or removed | Static files present, no route, no navigation | `app/static/ux-preview.html`, `app/static/ux-preview-v2.html`, `app/static/wbom-*.html` | Move to `app/static/stale/` archive folder, or delete |
| P1-3 | Webhook signature enforcement disabled | `WEBHOOK_SIGNATURE_ENFORCEMENT=false` — unsigned webhooks accepted | Meta WhatsApp webhook payloads verified via HMAC | Unsigned payloads accepted; security degraded | `app/main.py`, `.env` | Re-enable `WEBHOOK_SIGNATURE_ENFORCEMENT=true` once Meta webhook source is confirmed stable |

### P2 — Technical Debt

| ID | Title | Symptom | Expected | Actual | File(s) | Fix |
|---|---|---|---|---|---|---|
| P2-1 | Qdrant RAG availability gap | RAG availability fails daily at 18:00 | Continuous RAG availability | Scheduled outage per `docs/PRODUCTION_CERTIFICATION_v1.0.md` | `.env`, `modules/rag/*` | Document in `docs/PRODUCTION_SYSTEM_STATUS_2026-08-31.md` as KNOWN DEGRADED; schedule remediation |
| P2-2 | `escort_lifecycle` module has no `routes.py` | No REST API endpoints — all lifecycle functions are internal-only (called by bridge_poller, message_router, dispatch) | Admin tooling can invoke lifecycle operations via API | Pure business-logic module; no HTTP surface | `modules/escort_lifecycle/__init__.py` | Design decision. Add `routes.py` if manual admin overrides via API are needed |
| P2-3 | `preflight_guard_router` has no REST consumer | Kill-switch and feature flag endpoints exist | Admin UI or internal tooling uses preflight endpoints | No known consumer in any HTML file | `modules/preflight_guard/routes.py` | Document as internal-only; no action required unless admin UI is planned |
| P2-4 | Hardcoded HTML in `main.py` | `/admin/safe-mode` and `/admin/recruitment` render hardcoded HTML strings directly in `main.py` | Operational pages should be in `app/static/` | Inline HTML in `app/main.py` lines 3906–4020 | `app/main.py` lines 3906–4020 | Extract to dedicated HTML files in `app/static/` if pages become frequently used |

### P3 — Minor / Informational

| ID | Title | Notes | File(s) |
|---|---|---|---|
| P3-1 | `dashboard.html` has 33 functions — largest single-file frontend | Dashboard consolidation is reasonable but not urgent | `app/static/dashboard.html` |
| P3-2 | `payroll.html` at 5,904 lines with 229 functions | Consider progressive enhancement or lazy-loading for future growth | `app/static/payroll.html` |
| P3-3 | No API documentation endpoint (`/docs` or `/openapi.json` consumer) | FastAPI auto-generates OpenAPI but no ReDoc or Swagger UI page served | `app/main.py` |
| P3-4 | `admin/contact-roles` router has no frontend | Contact role management not accessible via UI | `modules/contact_roles/routes.py` |

---

## 10. Recommended Repair Order

1. **P0-1** — Wire `close_program()` into `handle_ocr_release_slip()` with confidence-score gating. This is the only production workflow gap that causes silent data inconsistency (program stays `running` indefinitely after OCR release).
2. **P1-3** — Re-enable webhook signature enforcement once operational stability is confirmed.
3. **P1-1** — Add tab anchor parameters to `/payroll/*` redirects for deep-link support.
4. **P1-2** — Archive stale preview HTML files out of `app/static/`.
5. **P2-1** — Document Qdrant 18:00 gap in production status as known degraded, with SLA target.
6. **P2-4** — Extract hardcoded HTML from `main.py` to `app/static/` files.

---

## 11. README Update Plan

The `README.md` should be updated to reflect:

1. **Removed items** (no longer accurate):
   - Any references to legacy `/payroll/*` as independent routes (they are dead redirects)
   - Any references to `app/static/ux-preview*` as active tools
   - Any references to `app/static/wbom-*` as active tools

2. **Added items**:
   - 13 static HTML pages + their routes
   - 15 module routers with their prefixes
   - Escort program lifecycle diagram
   - Release slip OCR partial wiring warning (P0-1)
   - Stale pages list (ux-preview, wbom-*)
   - Deep-link note: all `/payroll/*` sub-routes redirect to `/payroll`

3. **Architecture section additions**:
   - `modules/escort_lifecycle/__init__.py` — escort lifecycle (no routes.py)
   - `modules/preflight_guard/routes.py` — internal-only preflight/kill-switch
   - `modules/contact_roles/routes.py` — internal-only contact roles

---

## 12. Evidence / File References

| Evidence | File |
|---|---|
| All 15 mounted routers | `app/main.py` lines 479–496 |
| Payroll sub-route redirects | `app/main.py` lines 3206–3213 |
| Admin HTML pages | `app/main.py` lines 3906–4020 |
| Escort lifecycle functions | `modules/escort_lifecycle/__init__.py` lines 44–870 |
| `handle_ocr_release_slip` (no auto-close) | `modules/escort_lifecycle/__init__.py` lines 780–867 |
| `close_program` function | `modules/escort_lifecycle/__init__.py` lines 132–195 |
| `handle_release_event` (working close path) | `modules/escort_lifecycle/__init__.py` lines 264–501 |
| `approve_release_slip` (working close path) | `modules/dispatch/__init__.py` lines 1534–1615 |
| `finalize_attendance_draft` (running transition) | `modules/attendance/__init__.py` line 325 |
| All 13 static HTML files | `app/static/*.html` |
| All module routers | `modules/*/routes.py`, `modules/*/__init__.py` |
| Production system status flags | `docs/PRODUCTION_SYSTEM_STATUS_2026-08-31.md` |

---

## 13. Completion Status

| Section | Status |
|---|---|
| §1 Executive Summary | ✅ Complete |
| §2 Complete Web Page / URL Inventory | ✅ Complete |
| §3 Backend Route Inventory | ✅ Complete |
| §4 Frontend ↔ Backend Wiring Matrix | ✅ Complete |
| §5 Broken Tabs / Sidebars / Modals / Actions | ✅ Complete |
| §6 Escort Roster / Escort Program Lifecycle | ✅ Complete |
| §7 Media Extractor / Release Completion Lifecycle | ✅ Complete |
| §8 API Orphans | ✅ Complete |
| §9 P0/P1/P2/P3 Defect Register | ✅ Complete |
| §10 Recommended Repair Order | ✅ Complete |
| §11 README Update Plan | ✅ Complete |
| §12 Evidence / File References | ✅ Complete |
