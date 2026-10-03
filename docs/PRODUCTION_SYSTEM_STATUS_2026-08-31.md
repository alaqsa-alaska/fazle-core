# Fazle Core — Production System Status (2026-08-31)

> **Source of truth for what's LIVE / SHADOW / OFF / DEFERRED / FROZEN / DEGRADED
> on the production fazle-core service as of 2026-08-31.**
>
> **Authoritative read:** this file.
>
> **Companion audit (UI / workflow gaps):** `docs/WEB_UI_WORKFLOW_AUDIT_2026-08-31.md`.
>
> **Stable architecture / setup:** `SYSTEM_ATLAS.md` (canonical entry point and document map) and `README.md` (narrative setup guide). `docs/ARCHITECTURE.md` is a supplementary technical reference.
>
> **READ-ONLY audit. No production changes were made in producing this document.**

---

## 1. Process & Connectivity

| Subsystem | Status | Evidence |
|---|---|---|
| `fazle-core.service` (port 8200) | **LIVE** | systemctl unit present, health endpoint exposes `503` only when DB pool exhausted (per `fazle-core.log`). Process responding to traffic per `backend-runtime.log` and per-second request log timestamps. |
| Bridge 1 — HR WhatsApp (port 8081, `8801958122300`) | **LIVE** | `BRIDGE1_URL=http://localhost:8081` in `.env`; `bridge1.status()` polled by `dashboard.html` legacy tile. |
| Bridge 2 — OPS / Super-Admin WhatsApp (port 8082, `8801880446111`) | **LIVE** | `BRIDGE2_URL=http://localhost:8082`; `ADMIN_BRIDGE2_NUMBER=8801880446111`; outbound for payment relay (see §3). |
| Bridge 3 — REC2 WhatsApp (port 8083, `8801805529211`) | **LIVE** | `BRIDGE3_URL=http://127.0.0.1:8083`; `BRIDGE3_INGEST_ALL_DMS=true`. |
| Meta WhatsApp Cloud API (webhook) | **LIVE** | `BRIDGE_MEDIA_ACQUISITION_ENABLED=true`; webhook signature enforcement intentionally OFF (logged in `resilience/test_failure_recovery.py`); `WEBHOOK_SIGNATURE_ENFORCEMENT=false`. **DEGRADED** for security posture (unsigned bridge1 accepted with `[WEBHOOK_AUTH_AUDIT]` audit). |
| Qdrant (`172.20.0.2:6333`) | **LIVE** | `HYBRID_SEARCH_ENABLED=true`; `QDRANT_HOST/PORT` set. **DEGRADED**: RAG availability recurs daily at 18:00 via scheduler job (per `docs/PRODUCTION_CERTIFICATION_v1.0.md`). |
| PostgreSQL (`172.20.0.6:5432`) | **LIVE** | `FAZLE_AI_READER_DB_URL` set; read-only AI reader role active. |

---

## 2. AI / Hermes Subsystems

| Subsystem | Status | Evidence |
|---|---|---|
| `AUTO_REPLY_ENABLED` | **LIVE** | `true` — customer-facing replies active for non-DRAFT roles. |
| `SOCIAL_AUTO_REPLY_ENABLED` | **LIVE** | `true` — single-engine mode. |
| `RECRUITMENT_AUTOREPLY_ENABLED` | **LIVE** | `true` — explicit bypass even when `AUTO_REPLY_ENABLED=false`. |
| `AI_CONSOLE_CHAT_ENABLED` | **LIVE** | `true` — `/open-chat` page (`/chat/message`, `/chat/models`, `/chat/memory/stats`, `/chat/memory/history`). |
| `HYBRID_SEARCH_ENABLED` (RAG, Milestone 3.9) | **LIVE** (with recurring degradation) | `true`; daily 18:00 gap. |
| `AGENT_ADMIN_FORWARD_ENABLED` | **OFF** | `false` in `.env`; explicit OFF. |
| `AGENT_PROACTIVE_OUTBOUND_ENABLED` | **LIVE** | `true`. |
| `CLIENT_MEMORY_SHADOW_RETRIEVAL_ENABLED` | **SHADOW** | `false` — explicitly disabled; Stage 2/3 stay OFF until Owner-approved checkpoint. |
| `CLIENT_MEMORY_SHADOW_ROUTE_B_ENABLED` | **SHADOW** | `false`. |
| `CONVERSATION_POLICY_SHADOW_ENABLED` | **SHADOW** | `true` — bridge_poller computes proposed intent/role + logs `[CONV_POLICY_SHADOW]` beside actual live outcome; never influences reply. |
| `REVIEWED_REPLY_MEMORY_ENABLED` | **LIVE** | `true`. |
| `CONVERSATION_MEMORY_ENABLED` | **LIVE** | `true`. |
| `WHATSAPP_THREAD_RECONSTRUCTION_ENABLED` | **LIVE** | `true`. |
| `MULTI_MESSAGE_ESCORT_PARSER_ENABLED` | **LIVE** | `true`. |
| `BANGLA_SEMANTIC_PARSER_ENABLED` | **LIVE** | `true`. |
| `DRAFT_CREATION_ENABLED` | **LIVE** | `true` — drafts (never auto-sent) for designated roles. |
| Hermes admin `HERMES <request>` relay (bridge2 self-chat) | **LIVE** | `process_admin_ai_command()` reachable via bridge2 self-relay; `HERMES_RUNNER_URL=http://127.0.0.1:8093`; `HERMES_RUNNER_SECRET` set. |
| Hermes alert investigation | **LIVE** | `HERMES_ALERT_INVESTIGATION_ENABLED=true`, cooldown 3600s. |
| Hermes decision / event audit | **LIVE** | `HERMES_DECISION_AUDIT_ENABLED=true`, `HERMES_EVENT_LOG_ENABLED=true`. |
| Ollama local model | **LIVE** | `OLLAMA_REPLY_DISABLED=false`; default `qwen3:8b`. |

---

## 3. PAYMENT PATH — FROZEN

> ⚠ **FROZEN PATH — DO NOT MODIFY.** The payment relay + accounting + reconciliation chain is the only path that moves real money; it is intentionally isolated, idempotent, and idempotency-proven. Any change must be reviewed against `tests/unit/test_blocker_a_b_relay_accounting.py` and the `Rule B` provenance rule (commit `5e62ed5`).

| Subsystem | Status | Evidence |
|---|---|---|
| `PAYMENT_RELAY_ENABLED` | **FROZEN** | `true`; inbound `bridge2`, source `8801817201596`, dest `8801844836824`. Owner/Super Admin relay only. |
| `PAYMENT_RELAY_ACCOUNTING_ENABLED` | **FROZEN** | `true`; `fazle.payment_relay_accounting` records `[relay_acct] BOOKED` lines alongside FPE cash `txn` creation. |
| `FPE_PAYMENT_INACTIVITY_SWEEP_ENABLED` | **OFF** (intentional) | `false` — 3-month inactivity sweep is intentionally OFF pending Owner approval; comment in `.env` is explicit. |
| `USE_OUTBOUND_QUEUE` | **LIVE** | `true`. |
| `OUTBOUND_ENABLED` | **LIVE** | `true`. |
| `INTERNAL_NOTIFICATIONS_ENABLED` | **LIVE** | `true`. |

### 3.1 Payment Relay / Accounting Checkpoint

| Anchor | Value | Source | Notes |
|---|---|---|---|
| Queue → Txn canonical | **queue 6725 → txn 4477** | `fazle-core.log` lines 31644–31645: `2026-08-31 05:39:57,069 [fpe.acct] created txn id=4477 ref=fpe-90f4df8d emp=319 amount=300.0 method=PayoutMethod.nagad source=whatsapp`; `2026-08-31 05:39:57,083 [relay_acct] BOOKED queue id=6725 line 0 txn_id=4477 ref=fpe-90f4df8d emp=319 amount=300.00 match=exact_id_phone` | Canonical example of a healthy relay → accounting booking. |
| Rule B reconciliation link | **queue 6738 → txn 4476** | `.env` lines 365–366: *"Queue 6738 is reconciled to the existing manual txn 4476 via an explicit provenance link (Rule B, commit 5e62ed5)"*; `fazle-core.log` lines 3146–3147: `2026-08-30 17:50:28,625 [fpe.acct] created txn id=4476 ref=fpe-a6c079d2 emp=1342 amount=400 method=PayoutMethod.bkash source=manual`; `2026-08-30 17:50:28,649 [admin_txn] created txn id=4476 emp=1342 ref=fpe-a6c079d2c11a` | Manual-origin txn, never reversed, never auto-booked. |
| `PAYMENT_RELAY_ACCOUNTING_ENABLED=true` (gateway) | **FROZEN** | `.env` line 368 | Do not disable. |
| `USE_OUTBOUND_QUEUE=true` | **FROZEN** | `.env` | Do not disable. |
| `OCR_CONCURRENCY=2` | **LIVE** | `.env` line 173 | Active. |

> **Hardening note from prior cert (`PRODUCTION_CERTIFICATION_v1.0.md`):**
> real money moves via free-text admin-typed amount, with no cross-check against the three advisory payroll formulas. Treat the path as advisory above the FPE cash transaction layer; every FPE `cash_transactions` row is the audit-of-record.

---

## 4. WhatsApp & Outreach

| Subsystem | Status | Evidence |
|---|---|---|
| `WEBHOOK_SIGNATURE_ENFORCEMENT` | **OFF** (intentional interim) | `false`; every unsigned bridge1 webhook logs `[WEBHOOK_AUTH_AUDIT] unsigned bridge1 webhook accepted; enforcement disabled`. **DEGRADED.** Owner must re-confirm. |
| Outbound queue | **LIVE** | `OUTBOUND_ENABLED=true`; bridge timeout 10s. |
| Recurring outreach | **LIVE** | `AGENT_PROACTIVE_OUTBOUND_ENABLED=true`. |
| HR Officer accounts (`HROFFICER_01_NAME`, `HROFFICER_02_NAME`) | **DEAD** | `.env` explicitly notes the API keys/passwords were removed `2026-07-10`; hash mismatch, never read by any code path. Names kept for historical attribution only. |
| `ux-preview.html`, `ux-preview-v2.html` | **DEAD/OBSOLETE** | Files exist in `app/static/` but no route in `main.py` and no nav links from any other page. Mentioned only in `docs/DEVELOPER_GUIDE.md` §124 and `docs/AUDIT_REPORT_2026-07-17.md`. |
| `wbom-create-link.html`, `wbom-link-review.html` | **LIVE** (one-time cleanup) | Both routed at `/wbom-create-link` and `/wbom-link-review`; consume `/reports/unresolved_fpe_employees` and call `admin_employees/link-fpe` + `admin_employees/create-and-link`. |
| `dashboard.html` (legacy tile-only) | **LIVE** (legacy) | `/dashboard/legacy` produces the simple grid; primary dashboard is `dashboard.html`. |
| `/static/service-worker.js` + `/manifest.json` | **LIVE** (PWA) | PWA registration in `payroll.html` `registerPayrollPwa()`. |

---

## 5. Escort / Dispatch / Media Pipeline

| Subsystem | Status | Evidence |
|---|---|---|
| Escort draft → confirmed → running → completed lifecycle (code path) | **LIVE** | `modules/escort/`, `modules/escort_lifecycle/`, `modules/dispatch/`. |
| WhatsApp image → OCR → release slip extraction | **LIVE** | `modules/escort_slip_extractor/extract_escort_slip()`; bridge_poller `_extract_slip()`; `/api/dispatch/release-slips/upload` (multipart). |
| Release-slip OCR → `handle_ocr_release_slip()` admin review draft | **LIVE** | `fazle_draft_replies` populated with `intent='release_slip_ocr'`. **Does NOT close the program.** |
| Release-slip admin UI → `approve_release_slip()` → `close_program()` | **LIVE** | `/api/dispatch/release-slips/{extraction_id}/approve` (operator+); calls `close_program()` from `escort_lifecycle`. |
| `[RELEASE CONFIRMED]` admin message → `handle_admin_release_confirmation()` → `close_program()` | **LIVE** | `modules/bridge_poller/__init__.py` lines 1546, 1635. |
| Dispatch UI `/dispatch` — 9 endpoints | **LIVE** | availability / check-conflict / suggestions / overlaps / assign / assign-override / replace-escort / unassign / complete / incomplete-drafts / release-slips/pending / release-slips/{id}/candidates / release-slips/{id}/approve / release-slips/upload / overlaps/review / same-vessel-review. |
| Unmatched-confirmations queue `/unmatched-confirmations` | **LIVE** | 5 routes (`GET list`, `GET item`, `POST link-existing`, `POST confirm-new`, `POST reject`, `POST defer`). |
| `/payroll/transactions/{employee_id}` and other payroll sub-routes | **UI_ONLY stub** | All return the same `payroll.html` page; tab switch is JS-side (`tabFromPath()`). |
| Pay-roll Formulas — 3 advisory formulas (KB / salary / spec) | **DEGRADED** | None of the three formulas actually gates real money (per `PRODUCTION_CERTIFICATION_v1.0.md`); admin-typed amount is the audit-of-record. |
| `escort-slip/extractions`, `escort-slip/test-report`, `escort-slip/extract`, `escort/release` | **BACKEND_ONLY** | Mounted but not wired to a frontend page; see `docs/WEB_UI_WORKFLOW_AUDIT_2026-08-31.md` §6 (API Orphans). `/escort/release` returns HTTP 409 (direct finalization intentionally disabled). |

---

## 6. Payroll / FPE / Attendance

| Subsystem | Status | Evidence |
|---|---|---|
| FPE (`/api/fpe/*`) — payroll engine router | **LIVE** | 50+ routes mounted via `app.include_router(fpe_router)`; payroll.html is its primary consumer. |
| Office Attendance (`/admin/attendance/*`) | **LIVE** | 11 routes; payroll.html attendance-office tab. |
| Employee edit requests (`/employees/edit-requests*`) | **LIVE** | payroll.html `tab-empeditreq`; FPE operator → admin approval flow. |
| Operator pending (`/operator/pending|submit|approve|reject`) | **LIVE** | payroll.html `tab-operator`; HR officer submit → admin approve/reject. |
| Cash ledger (`/cash*`, `/staging-payments*`) | **LIVE** | payroll.html `tab-cash`; `fpe_cash_transactions` row is canonical. |
| Income ledger (`/income*`) | **LIVE** | payroll.html `tab-income`; manual + system-sourced. |
| Reconciliation / eligibility / dry-run (`/admin/payroll/*`) | **LIVE** | payroll.html `tab-admin` (admin-only). |
| Salary apply-uniform (`/admin/salary/apply-uniform`) | **LIVE** | admin-only. |
| `payroll/compute`, `payroll/runs`, `payroll/run/{id}/transition` | **LIVE** (backend) | No dedicated UI; triggered by scheduler at `PAYROLL_AUTO_COMPUTE_HOUR=2`. |
| DLQ / gap-scan (`/admin/dlq`, `/admin/gap-scan/*`) | **LIVE** | Admin dashboard. |
| Client billing (`/admin/bills/*`, `/admin/client-billing-profiles/*`) | **LIVE** | payroll.html `tab-admin` (admin-only). |

---

## 7. Drafts / KB / Memory / Recruitment

| Subsystem | Status | Evidence |
|---|---|---|
| Drafts engine (`/api/drafts/*` + legacy `/admin/drafts`) | **LIVE** | `drafts.html` calls `/api/drafts/{id}/{approve,edit,delete,notify-admin}`; `attendance`-typed approve goes through `finalize_attendance_draft()`. |
| Payment drafts (`/api/payment-drafts/*`) | **LIVE** | `/admin/payment-drafts` and `dashboard.html` `loadOverview()` call it; `draft_approval.routes` mounted. |
| Knowledge Base (`/admin/kb/{upload,stats,list,delete}`) | **LIVE** | `kb.html` page. |
| Reviewed-reply memory (`/admin/memory/reviewed-replies*`) | **LIVE** | `dashboard.html` legacy link; routes mounted. |
| Recruitment (`/api/recruitment/interested-candidates`) | **LIVE** (backend) | No dedicated UI page; consumed by `chat`/ + AI Console tools. |
| Identity resolve / verify (`/api/identity/*`) | **LIVE** (backend) | AI tools only. |
| Intent classify (`/api/intent/classify`) | **LIVE** (backend) | Internal use. |
| Hermes decisions / kernel events (`/api/hermes/*`) | **LIVE** (backend) | Open-chat surfaces some via Trace tab; not in legacy dashboard. |

---

## 8. Hermes Tasks / Actions (`/api/tasks/*`, `/api/actions/*`, `/api/business-actions/*`)

| Subsystem | Status | Evidence |
|---|---|---|
| Persistent task state | **LIVE (backend)** | 13 endpoints mounted. |
| Action approvals (`/api/actions/{id}/approve|reject|execution-result`) | **LIVE (backend)** | Used by Hermes CLI; **NO frontend consumer** — see API orphan report. |
| Authorizing path (`/api/tasks/authorizing-path`) | **LIVE (backend)** | No frontend consumer. |

---

## 9. Preflight / Feature Flags / Kill-Switch

| Subsystem | Status | Evidence |
|---|---|---|
| Preflight reports (`/api/assistant/release/preflight*`, `/api/assistant/release/gates/*`) | **LIVE (backend)** | Used by Hermes runner; not in legacy dashboard. |
| `release/gate/update`, `ops/kill-switch/{feature}`, `ops/activate/{feature}` | **LIVE (backend)** | Owner-facing kill-switch. |
| `ops/flags` (read feature flags) | **LIVE (backend)** | AI Console consumes. |
| `internal/location/{employee_id,device_id,health}` | **LIVE (backend)** | Internal API; assistant reads. |

---

## 10. Admin / Roles / RBAC

| Subsystem | Status | Evidence |
|---|---|---|
| Admin roles / users (`/admin/users*`, `/admin/audit`) | **LIVE (backend)** | Routes mounted; command-gated (`user_list`, `user_add`, `user_role`, `user_remove`, `user_apikey`). |
| `dashboard.html` Users / Audit / Backups tabs | **LIVE (frontend)** | consume `/admin/users*`, `/admin/audit`, `/backup/status|list|run|rotate`. |
| Directives (`/admin/directives/*`) | **LIVE (backend)** | command-gated; not in legacy dashboard. |
| `admin/send-whatsapp` | **LIVE (backend)** | Owner-only. |
| `admin/overview` | **LIVE (frontend)** | `dashboard.html` `loadOverview()` calls it directly. |
| `admin/safe-mode`, `admin/recruitment`, `admin/memory/reviewed-replies*` | **LIVE (backend)** | No dedicated frontend page; `/admin/memory/reviewed-replies` listed in `dashboard.html` but no UI tab. |
| `/admin/users/{phone}/role/disable/enable/apikey` | **LIVE (backend)** | command-gated. |

---

## 11. Reports / Backup / Scheduler / Observability

| Subsystem | Status | Evidence |
|---|---|---|
| Reports (`/reports`, `/reports/{name}`) | **LIVE (backend)** | Dispatched by name; only `dashboard.html` tab "Reports" calls it (and `wbom-create-link` / `wbom-link-review` call `/reports/unresolved_fpe_employees` directly). |
| Backup (`/backup/{status,list,run,rotate}`) | **LIVE** | `dashboard.html` tab. |
| Scheduler (`/scheduler/{status,run/{job_name}}`) | **LIVE** | `dashboard.html` tab; `SCHEDULER_ENABLED=true`. |
| Observability (`/observability/{errors,summary}`, `/metrics`, `/metrics/json`) | **LIVE** | `dashboard.html` tab. |
| RAG admin (`/rag/{stats,search,answer,reindex,recent-searches}`, `/api/rag/{stats,rebuild,qdrant-status}`) | **LIVE** | `dashboard.html` tab. |

---

## 12. WhatsApp Chat Frontend (`wa_chat.html` + `/api/wa/*`)

| Subsystem | Status | Evidence |
|---|---|---|
| `wa_chat.html` UI (contacts / messages / drafts / groups / broadcast / block) | **LIVE** | `/wa-chat` and `/dashboard/wa-chat`. |
| `wa_chat_frontend.__init__.py` 25 routes | **LIVE** | Mounted via `app.include_router(wa_chat_router)`. |
| SSE stream (`/api/wa/stream?last_id=&last_draft_id=`) | **LIVE** | `wa_chat.html` connects via `EventSource`; pushes `new_message` + `new_draft` events. |
| Outbound `/api/wa/send` and `/api/wa/broadcast` | **LIVE (mutating)** | Owner/Admin only via UI; `OUTBOUND_ENABLED=true` and outbound queue governs transport. |

---

## 13. Open Chat (`open-chat.html`)

| Subsystem | Status | Evidence |
|---|---|---|
| Hermes chat lab UI | **LIVE** | `/chat-lab`, `/open-chat`; calls `/chat/models`, `/chat/memory/stats`, `/chat/memory/history`, `/chat/message`, `/chat/voice`, `/chat/upload`. |

---

## 14. Scheduled Jobs

| Job | Source | Schedule | Notes |
|---|---|---|---|
| Payroll auto-compute | `.env` `PAYROLL_AUTO_COMPUTE_HOUR=2` | Daily 02:00 Asia/Dhaka | Calls `POST /payroll/compute` per the daily_summary plan. |
| Daily gap-scan | `/admin/gap-scan/trigger` | Hourly + daily | `/admin/gap-scan/runs` exposes history. |
| Escort stale-draft sweep | `ESCORT_STALE_DAYS=30` | Periodic | `/api/escort-roster/cleanup-stale` (backend). |
| DLQ alert | `DLQ_ALERT_INTERVAL_MIN=15` | Every 15 minutes | `/admin/dlq`. |
| FPE payment-inactivity sweep | **OFF** | Would be daily 04:00 Asia/Dhaka if enabled | `FPE_PAYMENT_INACTIVITY_SWEEP_ENABLED=false`. |
| Backup rotation | `BACKUP_STALE_HOURS=48` | Daily | `/backup/rotate`. |
| RAG availability gap | hidden internal scheduler | Daily 18:00 | **DEGRADED**: gap recurs daily; logged separately. |

---

## 15. Open Defects / Risk (System-Level)

These are tracked in detail in `WEB_UI_WORKFLOW_AUDIT_2026-08-31.md` and reproduced here at summary:

- **5** 1.1 — Schema governance gap: ~150 tables / ~88 columns exist in production without tracked migrations. Disaster-recovery risk (per `PRODUCTION_CERTIFICATION_v1.0.md`).
- **5** 1.2 — Webhook signature enforcement disabled (logged, intentional interim). **DEGRADED.**
- **5** 1.3 — Three advisory payroll formulas that do not gate real payments (real money is admin-typed). **DEGRADED.**
- **5** 1.4 — RAG daily 18:00 gap. **DEGRADED.**
- **5** 1.5 — Escort release slip OCR (WhatsApp image path) builds a draft but does NOT auto-close the program — explicit owner decision (per `app/main.py` `auto_close = False`). Domain action is correct but only fired on admin `[RELEASE CONFIRMED]` or `/api/dispatch/release-slips/{id}/approve`. **See `WEB_UI_WORKFLOW_AUDIT_2026-08-31.md` Part 4 for the lifecycle gap.**

---

## 16. Hardcoded Numbers / Identifiers (Reference)

| Token | Value |
|---|---|
| BRIDGE1_NUMBER | `8801958122300` (HR) |
| BRIDGE2_NUMBER | `8801880446111` (OPS / Super Admin) |
| BRIDGE3_NUMBER | `8801805529211` (REC2) |
| ADMIN_META_NUMBER | `8801880446111` |
| ADMIN_BRIDGE1_NUMBER | `8801958122300` |
| ADMIN_BRIDGE2_NUMBER | `8801880446111` |
| PAYMENT_RELAY_RECEIVING_NUMBER | `8801880446111` |
| PAYMENT_RELAY_SOURCE_NUMBER | `8801817201596` |
| PAYMENT_RELAY_DEST_NUMBER | `8801844836824` |
| HERMES_RUNNER_URL | `http://127.0.0.1:8093` |
| META_WABA_ID | `3119866501549616` |

> This document does NOT contain any password, API key, or signing secret. `.env` values for `OMNIROUTE_API_KEY`, `HERMES_RUNNER_SECRET`, `HERMES_RUNNER_CUSTOMER_SECRET`, and `FAZLE_AI_READER_PASSWORD` are intentionally omitted.

---

## 17. How to Use This Document

- **LIVE** = the feature is reachable and working today.
- **SHADOW** = runs in parallel only for diagnostics/observation; does not influence production outcomes.
- **OFF** = explicitly disabled by `.env` (intentional).
- **DEFERRED** = built but behind an Owner-approved checkpoint; not yet activated.
- **FROZEN** = do not modify without a dedicated test gate and Owner approval. Currently applies to the payment relay + accounting + reconciliation chain (`PAYMENT_RELAY_*`, `PAYMENT_RELAY_ACCOUNTING_ENABLED`, `USE_OUTBOUND_QUEUE`, and the canonical `queue 6725 → txn 4477` + `queue 6738 ↔ txn 4476` Rule-B anchors).
- **DEGRADED** = live but operating with a known deficit that should be raised in the next hardening pass.

Last verified: 2026-08-31 (read-only audit, no service restarts).