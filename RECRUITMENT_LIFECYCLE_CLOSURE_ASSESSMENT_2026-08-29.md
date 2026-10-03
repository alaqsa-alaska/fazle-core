# RECRUITMENT LIFECYCLE CLOSURE — ASSESSMENT & MINIMAL-GAP PLAN
**Date:** 2026-08-29 · **Mode:** read-only trace + test-suite verification · **No production changes made**

## Method
Traced the existing recruitment/identity/employee code against the 26-point closure spec.
Ran the recruitment + lifecycle unit suite against the `fazle-test` DB.
No schema change, no deploy, no bridge action. `git status` clean. Bridges + fazle-core verified unchanged.

## Headline
**The lifecycle is ~80% already closed** by prior Owner-directed passes (migration 072 "post_intake_status"
2026-08-27; the whole `recruitment_turn_decision` module; `conversation_state.py`; canonical-history
migrations 062/073–076 through 2026-08-28). **595 recruitment/lifecycle tests pass** (4 "failures" are
test-ordering pollution — every one passes run in isolation). The remaining gaps are small and specific,
and two of them touch a vocabulary the Owner explicitly froze — so they need an Owner decision, not a
unilateral edit.

---

## Test result
```
595 passed, 4 failed (ordering-pollution: all 4 pass in isolation), 2764 deselected
```
Failing file `test_recruitment_interested_candidates_route.py` — 5/5 pass alone; a preceding test leaves
rows in the shared test DB. **Test-hygiene bug, not a lifecycle bug.**
Also found: `tests/conftest.py` needs `TEST_DB_PASSWORD=contactsyncfix2026` / host `127.0.0.1:5433`
(the running `fazle-test-postgres`) — the default in conftest drifted. Non-production; documented here.

---

## Section-26 report

| Dimension | Status | Evidence / gap |
|---|---|---|
| **INTEREST/READINESS MODEL** | **PARTIAL** | `wbom_candidates.post_intake_status` (mig 072, Owner-locked) = `awaiting_agreement, agreed, joining_ready, salary_objection, not_ready_now, declined, future_contact`. Covers spec's `agreed`, `join_ready`(=joining_ready), `considering`(=awaiting_agreement), `deferred`(=not_ready_now/future_contact), `not_interested`(=declined). **Gaps:** no explicit *pre-intake* `engaging`/`interested` (pre-scored people tracked only by `funnel_stage` + `score_bucket`); no `inactive`; no `joined`/`converted` terminal. 3-concept separation IS clean — `_finalize_candidate` writes `score_bucket`, `funnel_stage`, `post_intake_status` independently; status is never inferred from score. |
| **NURTURE/MOTIVATION** | **READY** | `_POST_INTAKE_CLASSIFY_SYSTEM_PROMPT` + responsible-role experience gate + fee-policy directive guard (`SAFE_FEE_PENDING_OWNER_DECISION`) + KB-grounded reply (`conversation_layer.recruitment`, `build_recruitment_context_with_directives`). Fact-check blocks fabricated numbers. Aug-23 audit found fact-check *over*-restrictive (blocks legit public info) — tuning, not fabrication risk. |
| **RESPONSE-NEEDED LOGIC** | **READY** | `conversation_state.resolve_recruitment_state()` → `{state, next_actor, requires_action}`; `awaiting_agreement`/`not_ready_now` → `waiting_for_counterparty`, `requires_action=False`. `RecruitmentTurnDecision` has `FOLLOW_UP_CONTEXTUAL` + `AMBIGUOUS`→`state_write=NONE` + no auto office-contact on low confidence. |
| **CONVERSATION HISTORY AWARENESS** | **READY** | `conversation_canonical.canonical_history` (bounded, read-only annotation of `wbom_whatsapp_messages`, identity via mig 062); `_build_post_intake_state_block`/`_build_recruitment_session_state_block` feed collected facts to the model; TTL bounded (recruitment 24h). Not a second DB. |
| **REPEATED-QUESTION PREVENTION** | **READY** | `apply_state_rules` skips writes where `session[col]==value` (Phase-B field locking); post-intake path "never re-asks name/age/etc." (`_route_post_intake_message`); `test_recruitment_session_state_guard`, `test_recruitment_name_field_extraction_fix` pass. |
| **CONTACT/ADDRESS REPETITION CONTROL** | **PARTIAL** | Fact-check + `POST_INTAKE_FALLBACK_MSG` govern *when* the office number appears; Aug-23 audit found it mostly *blocked*, not over-repeated (0 observed repeated-info failures). But there is **no explicit "already sent address/phone this thread" guard** — relies on the reply model + bounded history. Works in practice, not a hard rule. |
| **PHONE-CALL CONTEXT RECORDING** | **PARTIAL** | `modules/admin_directives/` is the authenticated Owner business-context path; Hermes records only admin-supplied facts; `detect_identity` never fabricates. **Gap:** no first-class "candidate called office" event that flips `post_intake_status`→`joining_ready` from an Admin note; today = free-form directive + manual status set. |
| **SHORTLIST** | **READY** | `GET /api/recruitment/interested-candidates` (`app/main.py:1857`) — bounded ≤92d, unified store, reuses `is_recruitment_trigger`, RBAC Admin-only, returns name/mobile/role/last-interaction/evidence. `post_intake_status` gives honest interested-vs-agreed labels. Passes in isolation. |
| **FIRST-N RESOLUTION** | **PARTIAL / NOT DETERMINISTIC** | `interested-candidates` is `ORDER BY received_at ASC` (deterministic oldest-first) but does **not** rank `joining_ready` before `agreed` or tie-break on `candidate_id`. No dedicated first-N invitation resolver — a Hermes "call first 5 ready" currently depends on LLM ordering. |
| **BATCH INVITATION** | **PARTIAL** | Send primitives ready: `modules/outbound.enqueue` (idempotent, individual not group). Owner-authorised bounded-action gate exists (`hermes_action_approvals` mig 068 + business-action fields mig 076, 2026-08-28) + `is_trusted_direct_admin_channel`. **Gap:** no purpose-built "invite these exact N on date D" action with a frozen immutable recipient set + per-recipient send ledger — today it's a generic Hermes task. |
| **EXACT-ONCE SEND** | **PASS (mechanism)** | `outbound.enqueue` idempotent; escort path proves it (`test ...replay_of_same_message_id_creates_no_duplicates`, mig 067). Same primitive available to a recruitment batch. |
| **POST-INVITATION CONTINUITY** | **PARTIAL** | `get_active_session` `scored` branch keeps a scored non-terminal candidate routed into recruitment flow, so "আমি রওনা দিয়েছি" continues in context (not re-intake). **Gap:** no `invited`/`arrived`/`joined` event rows written anywhere; `wbom_candidate_conversations` *could* carry them (spec wants events, not columns) but nothing does today. |
| **CANDIDATE→EMPLOYEE LINKAGE** | **PARTIAL** | Continuity is **implicit via canonical phone** (`8801XXXXXXXXXX` = join key across `wbom_candidates.phone` / `wbom_employees.employee_mobile` / escort / attendance / payroll; `shared/identity_map.py` documents the map). **Gap:** no stored `candidate_id ↔ employee_id` link, no candidate-side "converted" marker — phone change post-join breaks the thread; reports can't show "applicant → employee #N" without a live phone match. |
| **EMPLOYEE LIFECYCLE CONTINUITY** | **READY** | `detect_identity` order: `wbom_employees` lookup (pri 88) beats `candidate` text-hint (pri 30) — a joined person routes as `employee` with `lifecycle` (active/inactive/released). `escort_lifecycle`, `wbom_attendance`, `fpe_transactions`, `fazle_payment_drafts` all key on `employee_id`; `test_escort_lifecycle` (release → 2 attendance rows + payment draft) passes. No destructive cleanup of former employees. |
| **LONG-TERM CANONICAL MEMORY** | **READY** | Canonical DB is source of truth; `build_*_context_with_directives` + `canonical_history` = server-side filtered retrieval → bounded facts → model. Not chat-memory. `conversation_ttl_*` bounds windows. |
| **PRIVACY/AUTHORITY** | **PASS** | `employee_privacy.py`, `authority_lanes.py`, RBAC on shortlist route, `wbom_group_identities` (mig 075) — group participants never gain Admin authority; `is_trusted_direct_admin_channel` needs the admin bridge2 number + control channel; phone-call facts only from authenticated directive. |
| **BRIDGE SAFETY** | **PRESERVED** | No restart, no QR/relink, no session/store/auth change, no DB write. Bridge PIDs `3037267 / 3037616 / 3597312`, NRestarts `0 / 0 / 0` — unchanged start→end. fazle-core `/health` 200. `git status` clean. Only read-only pytest (temp log dir). |
| **DEVELOPMENT FREEZE READY** | **NO** — pending the 4 small gap-closures below + Owner sign-off on vocabulary. |

---

## Same-person continuity — demonstrated (identity chain)

The one real person is followed by **canonical phone `8801XXXXXXXXXX`**, resolved at every stage:

| Stage | Table / mechanism | Keyed by | Verified |
|---|---|---|---|
| first recruitment message | `is_recruitment_trigger` → `fazle_recruitment_sessions.phone` | canonical phone | `test_route_b_handoff` (isolation) |
| interest / agreement | `wbom_candidates.post_intake_status` via `_classify_post_intake_turn` (evidence-only, fail-closed) | `candidate_id` (phone-seeded) | `test_recruitment_post_intake` (32 pass) |
| invitation | `outbound.enqueue` + business-action gate | candidate_id → phone | mechanism only (gap: no batch action) |
| arrival / travelling | `get_active_session` scored branch keeps context | phone → candidate_id | `test_recruitment_session_state_guard` |
| employee creation | `wbom_employees` row (phone = `employee_mobile`) | canonical phone | **gap: no stored candidate_id link** |
| routing flips to employee | `detect_identity` (employee pri 88 > candidate 30) | `employee_mobile` | traced `modules/identity_brain/__init__.py:301` |
| duty / escort / attendance | `wbom_escort_programs`, `wbom_attendance` | `employee_id` | `test_escort_lifecycle` pass |
| cash / salary / payroll | `fpe_transactions`, `wbom_cash_transactions`, `fazle_payment_drafts` | `employee_id` + validated `payout_phone` | `test_stage_h5_financial_lifecycle_repair` pass |
| later employee message | `canonical_history` + `build_employee_context_with_directives` | `employee_id` | `test_sprint3a` identity-resolution (needs test-DB fix) |
| release / inactive | `escort_lifecycle.handle_release_event`, `wbom_employees.status` | `employee_id` | `test_escort_lifecycle` pass |

**Continuity holds today for any person whose phone does not change between applicant and employee.**
The break risk is a phone change — closed by gap #1 below.

---

## Minimal gap-closure plan (smallest coherent change; Owner decision required)

**All four are additive. None is a new engine, table-family, or memory system.**

### GAP 1 — explicit candidate→employee link + `joined` status  *(the "critical" one, spec §16)*
- **Change:** `ALTER TABLE wbom_candidates ADD COLUMN linked_employee_id INTEGER REFERENCES wbom_employees(employee_id)` + add `'joined'` to the `post_intake_status` CHECK set.
- **Write site:** one hook in the employee-creation path (`modules/fazle_payroll_engine/employee.py` / admin employee-create) — on new employee, `UPDATE wbom_candidates SET linked_employee_id=$1, post_intake_status='joined' WHERE phone = <canonical> AND linked_employee_id IS NULL`.
- **Read site:** `detect_identity` and the employee context builder already resolve by phone; add candidate history to the employee context via `linked_employee_id` when present.
- **Owner input needed:** approve adding `'joined'` to the frozen status set + the FK column.

### GAP 2 — post-invitation events (`invited` / `arrived` / `joined`) *(spec §15 — events not columns)*
- **Change:** none to schema. Write event rows into the existing `wbom_candidate_conversations` (mig 062) with a `step` value of `invited` / `arrived_office` / `joined` at the three moments (batch-invite send, admin "arrived" note, employee creation).
- **Owner input needed:** confirm `wbom_candidate_conversations.step` is the right carrier (vs a small `wbom_candidate_events` table).

### GAP 3 — deterministic first-N + batch-invitation action *(spec §12–14)*
- **Change:** one new AI-write action `recruitment_invite_batch(candidate_ids[], date, message_template)` that (a) resolves the exact IDs → freezes an immutable recipient list, (b) orders `joining_ready` before `agreed`, then oldest `post_intake_status` change, then `candidate_id`, (c) sends each via `outbound.enqueue` with a per-recipient key, (d) records each send + failures without resending successes. Reuses `hermes_action_approvals` (mig 068/076) as the trusted-Admin gate — **no second human approval per message**.
- **Owner input needed:** approve the action + its authorisation path.

### GAP 4 — phone-call admin event → status *(spec §9)*
- **Change:** extend the existing admin-directive/note path so a trusted-Admin note like "Candidate 88017… called, will come" is recorded as business context **and** may set `post_intake_status='joining_ready'` — attributed to the Admin, never "Hermes heard the call".
- **Owner input needed:** approve the status write from an Admin note.

### GAP 5 (housekeeping, no Owner decision) — test hygiene
- Fix `test_recruitment_interested_candidates_route.py` ordering pollution (add per-test cleanup / unique fixtures).
- Update `tests/conftest.py` test-DB default to match the running `fazle-test-postgres` (or document the env vars in `TESTING_STATUS.md`).

---

## Recommended path
1. Owner approves the vocabulary/schema decisions in GAP 1–4 (or trims scope).
2. Implement GAP 1 + 2 first (identity continuity — highest value, ~1 migration + 2 write hooks + tests).
3. GAP 3 + 4 next (invitation determinism + phone-call event).
4. Full lifecycle test (spec §23 A–K) against `fazle-test`.
5. Deploy fazle-core via the existing safe restart gate only; verify bridge PIDs/NRestarts before+after.
6. Then freeze.

**Nothing is deployed or changed until Owner sign-off on the four vocabulary decisions.**
