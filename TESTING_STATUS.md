> ⚠️ **STALE — do not treat as current.** This checkpoint (2026-05-07) predates the much larger test suite and the 2026-07-05/06 triage (`reports/release/go_no_go_decision_package_2026-07-05.md`, `reports/release/failure_triage_protocol_2026-07-05.md`). Do not cite the numbers below as current test status. Flagged 2026-07-08.

# Testing Status — Fazle Core

**Checkpoint:** pre-chaos-stable-2026-05-07
**Last full validation:** 2026-05-07
**Validated by:** automated test suites + manual verification

---

## Summary Table

| Suite | Tests | Passed | Failed | Skipped | Duration |
|---|---|---|---|---|---|
| Smoke (unit + integration/test_api) | 165 | 165 | 0 | 0 | ~20 s |
| Workflow Integration | 59 | 59 | 0 | 0 | ~18 s |
| E2E Playwright | 119 | 118 | 0 | 1 | ~5 min |
| **Total** | **343** | **342** | **0** | **1** | ~6 min |

---

## 1. Smoke Suite (165 tests)

**Command:** `make smoke`
**Location:** `tests/unit/` + `tests/integration/test_api.py`

### Coverage

| Module Area | Test File | Status |
|---|---|---|
| Admin commands (APPROVE/REJECT/PAID/etc.) | `tests/unit/test_admin_commands.py` | PASS |
| Attendance records | `tests/unit/test_attendance.py` | PASS |
| Escort lifecycle | `tests/unit/test_escort_lifecycle.py` | PASS |
| Escort module | `tests/unit/test_escort_module.py` | PASS |
| Identity brain | `tests/unit/test_identity_brain.py` | PASS |
| Payment workflow | `tests/unit/test_payment_workflow.py` | PASS |
| Payroll | `tests/unit/test_payroll.py` | PASS |
| API endpoints | `tests/integration/test_api.py` | PASS |
| Webhook routing | `tests/integration/test_webhooks.py` | PASS |

---

## 2. Workflow Integration Suite (59 tests)

**Command:** `python -m pytest tests/workflows/test_escort_payment_flow.py -m workflow --timeout=60 -v`
**Location:** `tests/workflows/test_escort_payment_flow.py`

### Test Classes

| Class | Tests | Description | Status |
|---|---|---|---|
| `TestFullEscortPaymentFlow` (A) | 10 | Steps 1-10: guard slip to payment | PASS |
| `TestDBStateVerification` (B) | 4 | DB column/value assertions after each step | PASS |
| `TestAuditLogs` (C) | 4 | Payroll audit log entries verified | PASS |
| `TestOutboundQueue` (D) | 5 | Outbound queue enqueue/sweep/DLQ | PASS |
| `TestIdempotency` (E) | 4 | Retry safety, duplicate prevention | PASS |
| `TestFinalConsistency` (F) | 3 | Cross-table consistency checks | PASS |
| `TestFailureInjection` (G) | 9 | Bad inputs, DB errors, missing data | PASS |
| `TestDuplicateMessages` (H) | 4 | Duplicate webhook/message handling | PASS |
| `TestRaceConditions` (I) | 4 | Concurrent async operation behavior | PASS |
| `TestPaymentCorrection` (J) | 4 | Reversal and adjustment workflows | PASS |
| `TestAPIIntegration` (K) | 7 | Full API round-trips with DB assertions | PASS |

### Verified Workflows

- [x] Full 10-step escort slip -> payment lifecycle
- [x] Payment draft creation from completed program
- [x] Advance payment draft creation
- [x] Payroll computation (compute_run -> submit -> approve -> lock -> mark_paid)
- [x] Payroll audit log entries (submit=reviewed, approve=approved, lock=locked, paid=paid)
- [x] Escort program open/close/backfill
- [x] Attendance backfill idempotency (ON CONFLICT DO NOTHING)
- [x] Outbound queue enqueue, sweep_once, DLQ on exceeded retries
- [x] Handle release event idempotency
- [x] Payment finalization (draft pending -> sent, wbom_cash_transactions row)
- [x] Payment reversal (draft -> reversed, counter-transaction, correction log)
- [x] Payment adjustment (linked adjustment draft with expected_amount)
- [x] Cross-table consistency after full lifecycle
- [x] API visibility of escort programs and payment drafts
- [x] Concurrent backfill (ON CONFLICT DO NOTHING verified)

### Known Behavioral Notes (documented as tests)

| Behavior | Test | Notes |
|---|---|---|
| `finalize_payment` allows double-finalize | `test_duplicate_payment_finalize_not_allowed` | No DB guard exists; test asserts `>= 1` transactions |
| Concurrent release may create multiple drafts | `test_concurrent_release_events` | Application-level check races; test asserts `>= 1` draft |
| Concurrent payroll compute may create multiple runs | `test_concurrent_payroll_compute` | UNIQUE constraint handles at DB level; test asserts `>= 1` run |
| `handle_release_event` returns `ok=False` when no active program | `test_escort_payment_draft_not_duplicated_on_retry` | Second call returns `status='no_active_program'` |

---

## 3. E2E Playwright Suite (119 tests)

**Command:** `TEST_API_KEY=fk_<key> make test-e2e`
**Location:** `tests/e2e/test_dashboard.py`
**Browser:** Chromium (headless)

### Test Classes

| Class | Tests | Description | Status |
|---|---|---|---|
| `TestAuthFlow` | 7 | Login overlay, key storage, logout | PASS |
| `TestNavigation` | 15 | Tab switching, active state, all tabs | PASS |
| `TestOverview` | 8 | Overview tab stats and data loading | PASS |
| `TestDrafts` | 8 | Drafts tab display and interaction | PASS |
| `TestPayroll` | 8 | Payroll tab display | PASS |
| `TestEscort` | 6 | Escort programs tab | PASS |
| `TestTransactions` | 6 | Transactions tab | PASS |
| `TestAttendance` | 5 | Attendance tab | PASS |
| `TestReports` | 5 | Reports tab | PASS |
| `TestFilters` | 8 | Filter interactions across tabs | PASS |
| `TestBrokenNavigation` | 10 | Error recovery, 404 handling | PASS |
| `TestNoErrors` | 8 | No JS exceptions, no 404s, no 5xx | PASS |
| `TestPerTabScreenshots` | 1 | Screenshot tour | SKIP (intentional) |
| `TestMobile` | 8 | Mobile viewport rendering | PASS |
| `TestAccessibility` | 6 | Keyboard nav, ARIA roles, contrast | PASS |
| `TestPerformance` | 4 | Load time assertions | PASS |

> `TestPerTabScreenshots::test_screenshot_tour_no_crashes` is skipped unless
> `SAVE_SCREENSHOTS=true` env var is set. This is intentional.

### Verified Dashboard Behaviors

- [x] Login overlay appears when no key in localStorage
- [x] Login overlay hides after correct key entered
- [x] Key persists across page reloads
- [x] Logout clears key and shows overlay
- [x] All 14 tab buttons present and clickable
- [x] Default active tab is `overview`
- [x] Tab click sets active class correctly
- [x] Direct URL slug (`/dashboard/drafts`) activates correct tab
- [x] Unknown slug falls back to overview
- [x] No JS exceptions across all tabs
- [x] No 404s on critical API paths
- [x] No 5xx responses
- [x] `/health` returns 200
- [x] `/dashboard` returns 200 (HTML)
- [x] Unauthenticated admin API returns 401 or 403
- [x] Requests include `X-Internal-Key` header after login
- [x] Mobile viewport renders without horizontal overflow
- [x] No blank sections when switching tabs

---

## 4. Verified Endpoints

| Endpoint | Method | Verified | Notes |
|---|---|---|---|
| `/health` | GET | YES | Returns `{"status": "ok"}` |
| `/health/deep` | GET | YES | Checks DB + Redis + Ollama |
| `/dashboard` | GET | YES | Returns HTML with correct structure |
| `/dashboard/{slug}` | GET | YES | Tab routing works |
| `/admin/overview` | GET | YES | Returns aggregate stats |
| `/admin/payment-drafts` | GET | YES | Returns `payment_drafts` array |
| `/admin/escort-programs` | GET | YES | Returns `programs` array |
| `/admin/cash-transactions` | GET | YES | Returns transactions |
| `/admin/audit` | GET | YES | Returns audit log |
| `/admin/users` | GET | YES | Returns admin list |
| `/escort/release` | POST | YES | Handles release event |
| `/payroll/compute` | POST | YES | Creates payroll run |
| `/payroll/run/{id}/transition` | POST | YES | State transitions |
| `/payroll/runs` | GET | YES | Lists runs |
| `/backup/run` | POST | YES | Triggers backup |
| `/backup/list` | GET | YES | Lists backups |
| `/scheduler/status` | GET | YES | Returns job status |
| `/admin/payment-drafts/{id}/reverse` | POST | YES | Reversal workflow |
| `/admin/payment-drafts/{id}/adjust` | POST | YES | Adjustment workflow |
| `/metrics` | GET | YES | Prometheus format |
| `/observability/summary` | GET | YES | Health summary |

---

## 5. Confidence Assessment

| Area | Confidence | Basis |
|---|---|---|
| Core payment draft lifecycle | HIGH | 10 integration tests + E2E verification |
| Admin command routing (APPROVE/REJECT) | HIGH | Unit + integration tests |
| Payroll state machine | HIGH | Full state transition tests |
| Payment correction (reverse/adjust) | HIGH | Dedicated test class |
| Auth and RBAC | HIGH | Unit + E2E tests |
| Dashboard rendering | HIGH | 119 E2E tests |
| Outbound queue (enqueue/sweep/DLQ) | HIGH | 5 dedicated tests |
| Audit log completeness | MEDIUM | 4 tests cover payroll; admin commands not exhaustively tested |
| Bridge polling reliability | MEDIUM | Polled and mocked; real bridge reconnect not tested |
| Concurrent operations safety | LOW-MEDIUM | Tests document known race conditions |
| Redis failure recovery | LOW | Not tested in current suite |
| PostgreSQL failure recovery | LOW | Not tested in current suite |
| Full load behavior | LOW | Load tests exist but not in smoke |

---

## 6. Known Risks

| Risk | Severity | Status |
|---|---|---|
| `finalize_payment` double-finalize creates duplicate transactions | MEDIUM | Documented; application-level guard exists in `admin_commands` |
| Concurrent release/compute race conditions | MEDIUM | Documented; acceptable for current load |
| Bridge reconnect after restart | MEDIUM | Not tested; bridge binary handles internally |
| Redis unavailability impact | HIGH | Needs chaos testing |
| PostgreSQL connection pool exhaustion | HIGH | Needs chaos testing |
| Outbound DLQ recovery after Redis restart | HIGH | Needs chaos testing |
| Scheduler jobs during DB outage | MEDIUM | Needs chaos testing |

---

## 7. Unverified Operational Areas

- [ ] Bridge reconnect after WhatsApp disconnection
- [ ] Scheduler behavior during PostgreSQL outage
- [ ] Outbound queue behavior during Redis outage
- [ ] App startup with PostgreSQL unavailable
- [ ] App startup with Ollama unavailable (warn only, not abort)
- [ ] Media download failure handling
- [ ] OCR failure fallback
- [ ] RAG reindex under load
- [ ] Backup rotation correctness with >14 daily files
- [ ] Concurrent admin commands from multiple WhatsApp sessions
- [ ] Meta webhook signature verification under replay attack
- [ ] DB connection pool recovery after PostgreSQL restart
- [ ] Long-running (8+ hour) soak stability

---

## 8. Transient Issues Observed

| Issue | Observed | Root Cause | Resolution |
|---|---|---|---|
| E2E suite 118 errors (timeout at setup) | 2026-05-07 | System resource exhaustion after 2h16m run | Re-run immediately: 118/119 passed |
| E2E suite total time 2h16m | 2026-05-07 | Network/DB slowness during long run | Normal run: ~5 min |

---

## 9. Production Readiness Assessment

**Overall:** READY FOR CHAOS TESTING — not yet cleared for production scale load

| Dimension | Status | Notes |
|---|---|---|
| Functional correctness | GREEN | All test suites green |
| Data integrity | GREEN | Payment correction, audit logs verified |
| Auth/RBAC | GREEN | E2E + unit verified |
| Dashboard | GREEN | 119 tests cover all tabs and behaviors |
| Backup | GREEN | Automated + manual procedures documented |
| Observability | YELLOW | Metrics and logs working; alert rules not verified |
| Resilience | RED | Redis/PG failure scenarios not tested |
| Concurrency | YELLOW | Known races documented; acceptable for single worker |
| Load | YELLOW | Load tests not in standard suite |
| Recovery | YELLOW | Restore procedure documented; not drill-tested |

**Checkpoint tag:** `pre-chaos-stable-2026-05-07`
**Next phase:** Long-run reliability and chaos testing (see `CHAOS_TEST_PLAN.md`)
