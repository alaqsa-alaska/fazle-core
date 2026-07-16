---
title: Escort Program — Source-of-Truth Message Parser Audit & Fix Proposal
owner: Fazle Core Admin
status: proposal
last_verified: 2026-07-16
runtime_index: true
---

# Escort Program — Source-of-Truth Message Parser Audit & Fix Proposal

Requested by Owner following the S/N Provenance Report (garbage rows 869, 871, 873, 880
traced to sender phone 01958122302 / 8801958122303 — not a registered admin or
escort-client number). Owner's business rules (Rules 1-4 below) are authoritative and
override any conflicting code/doc statement found during this audit.

## Business Rules (as given, non-negotiable)

1. Draft creation only from a registered escort-client number, to a registered admin
   bridge number, with valid escort-order components. Otherwise reject, don't parse,
   don't save.
2. Confirmed-program creation only from a registered admin number, with valid
   confirmation components. Otherwise reject. No duplicates.
3. Unconfirmed drafts must be hard-deleted (not soft-expired) after 24 hours, no
   exceptions.
4. Only admin↔escort-client conversations are source of truth. Unregistered senders
   and component-incomplete messages are never source of truth.

---

## Task 1 — Full Discovery (read-only)

### 1.1 Admin bridge numbers

Stored in `.env` (loaded via `app/config.py` `Settings`, no DB table):

```
BRIDGE1_NUMBER=8801958122300          (HR/Recruitment)
BRIDGE2_NUMBER=8801880446111          (Operations/Escort)
ADMIN_NUMBERS=8801880446111,8801958122300
ADMIN_META_NUMBER=8801880446111
ADMIN_BRIDGE1_NUMBER=8801958122300
ADMIN_BRIDGE2_NUMBER=8801880446111
```

`app/config.py:224-226` exposes `Settings.admin_number_list` (parses `ADMIN_NUMBERS`).
`modules/escort/__init__.py:1793-1798` additionally unions in `admin_meta_number`,
`admin_bridge1_number`, `admin_bridge2_number` — so the escort module's admin check
covers all 4 config keys, not just `ADMIN_NUMBERS`.

### 1.2 Escort-client numbers

Two **independent** mechanisms, both DB/config-driven, both already enforced:

- **Role classification (DB-driven):** `modules/identity_brain` assigns every inbound
  contact an `identity_role` (e.g. `escort_client`, `client_escort_buyer`, `vip_client`,
  `repeat_client` — the set `_ESCORT_ROLES` at
  [message_router/__init__.py:77](../../modules/message_router/__init__.py#L77)).
  `detect_identity()` decides *whether this sender is even routed toward* the escort
  handler at all ([message_router/__init__.py:450](../../modules/message_router/__init__.py#L450)).
- **Phone allowlist (env-driven):** `ESCORT_CLIENT_PHONES` in `.env`
  (currently `8801670535255,8801757622300,8801836743754`), read via
  `_escort_client_phone_list()` / `_is_allowed_escort_client()` in
  [modules/escort/__init__.py:252-275](../../modules/escort/__init__.py#L252-L275). This
  is the second, independent gate actually enforced inside the escort handler itself.

### 1.3 Message parser entry points — full flow

```
WhatsApp bridge (bridge1/bridge2/meta webhook)
  -> modules/bridge_poller (or Meta webhook handler)
  -> modules/message_router.route_message()
       -> identity = await detect_identity(sender, text)   [role classification]
       -> if role in _ESCORT_ROLES and _looks_like_escort_order(text):
            -> modules.escort.handle_escort_client_message(text, sender, source)
                 -> _is_trusted_escort_source(source)        [gate: bridge trusted?]
                 -> _is_allowed_escort_client(sender_phone)   [gate: sender whitelisted?]
                 -> order = parse_escort_message(text)
                 -> is_minimum_escort_order(order)            [gate: MV + LV + master_mobile?]
                 -> save_escort_programs(order, sender_phone, source)  [INSERT status='draft']
       -> if role == "admin" and is_completed_escort_draft(text):
            -> modules.escort.handle_admin_escort_completion(text, admin_phone, source, recipient_phone)
                 -> _is_authoritative_bridge_confirmation(ctx) [gate: admin_phone registered? recipient a registered client?]
                 -> _find_pending_program(...) -> _update_program_confirmed(...)   [UPDATE, merges source_of_truth=bridge_confirmation]
                    or (no match) -> _create_confirmed_from_admin(...)            [INSERT, auto_created=true]
```

Only **3** call sites write to `wbom_escort_programs` codebase-wide (verified by
grepping every `INSERT INTO wbom_escort_programs` in `modules/`):
`escort/__init__.py:1121` (draft), `escort/__init__.py:1711` (admin-confirmed),
`escort_roster/history_sync.py:875` (one-time SQLite backfill, `is_historical=true`,
out of scope per Owner's constraint not to touch 940-type records).

### 1.4 Current sender-validation status per function

| Function | Validates sender? | Mechanism | Since |
|---|---|---|---|
| `handle_escort_client_message()` (caller of `save_escort_programs()`) | **Yes** | `_is_trusted_escort_source()` + `_is_allowed_escort_client()`, both called before parsing | commit `41916b0`, 2026-06-27 |
| `save_escort_programs()` itself | No direct check (relies on caller) | — | — |
| `handle_admin_escort_completion()` (caller of both confirm functions) | **Yes** | `_is_authoritative_bridge_confirmation()` — admin_phone must be in the 4 admin config keys; if a recipient is given, recipient must be a registered client phone | commit `41916b0`, 2026-06-27 |
| `_update_program_confirmed()` | No direct check (relies on caller) | — | — |
| `_create_confirmed_from_admin()` | No direct check (relies on caller) | — | — |

**Correction to the audit's opening premise:** the premise that "the pipeline currently
accepts and parses messages from ANY sender" is **not true of the code as it stands
today**. Both gates described in Rules 1 and 2 already exist and are already wired in
front of every write path, live since commit `41916b0` (2026-06-27, "Fix escort roster
source-of-truth and browser flows"). `_is_allowed_escort_client` and
`_is_authoritative_bridge_confirmation` do not exist anywhere in git history before that
commit (`git log -S` confirms both were introduced by it, never touched since).

**Component-validity gates also already exist:** `is_minimum_escort_order()`
([escort/__init__.py:804-811](../../modules/escort/__init__.py#L804-L811)) requires MV +
at least one lighter with both lighter_vessel and master_mobile before a draft is saved.
`is_completed_escort_draft()` gates the admin-confirmation content shape similarly.

**Duplicate prevention also already exists:** `save_escort_programs()` has a dedup
lookup by `(master_mobile, mother_vessel, program_date, contact_id/sender_phone)`
before inserting ([escort/__init__.py:1090-1117](../../modules/escort/__init__.py#L1090-L1117));
`_create_confirmed_from_admin()` matches by MV+lighter+master_mobile+date and a legacy
MV+escort_name fallback before creating; `handle_admin_escort_completion()` also calls
`reconcile_drafts_for_confirmation()` to delete stale duplicate drafts after a confirm
([escort/__init__.py:1856-1866](../../modules/escort/__init__.py#L1856-L1866)).

### 1.5 Draft deletion vs expiration

`modules/escort_roster/db.py:1184` `expire_stale_drafts()` — **already hard-deletes**,
not soft-expires. Docstring: *"Permanently delete stale draft entries and their source
draft programs... keep audit trail via escort_roster_audit_logs."* It logs a
`draft_ttl_deleted` audit entry, then `DELETE FROM escort_roster_entries` and
`DELETE FROM wbom_escort_programs WHERE status = 'draft'` for anything past
`draft_ttl_hours` (default 24, `app/config.py:207`). This behavior was added in commit
`82f2b33` ("escort-roster: enforce 24h draft TTL deletion and hide stale drafts"),
**2026-07-05**. `modules/scheduler/__init__.py:414-417` calls it on a schedule.

Live-verified just now (read-only queries):
- 0 rows with `status='draft'` older than 24h exist right now → the scheduler is
  actively sweeping on schedule, working as designed.
- 0 rows have transitioned to `status='expired'` since 2026-07-05 → nothing has fallen
  back to the old soft-expire behavior since the hard-delete fix landed.

**There is a second, unrelated `expire_stale_drafts` in `shared/draft.py`** used by the
scheduler for *payment* drafts (Batch-11-style), not escort programs — same function
name, different module, different table, soft-expire (`status='expired'`) by design for
that other feature. Not in scope here; flagging only so it isn't confused with the
escort-specific one during any future search.

### 1.6 KB documents

- [`knowledge_base/05_workflows/escort_source_of_truth_workflow.md`](escort_source_of_truth_workflow.md)
  (`last_verified: 2026-06-27` — same day as the `41916b0` fix). Documents the
  draft→bridge-confirmation flow and states *"Live non-destructive verification
  completed on 2026-06-27 confirmed: draft creation works, invalid client intake is
  ignored, bridge confirmation overwrites corrupted draft values..."* — consistent with
  what this audit independently re-verified from the code and live data.
- [`knowledge_base/06_developer_system/escort_roster_system.md`](../06_developer_system/escort_roster_system.md)
  (`last_verified: 2026-07-03`) documents source-of-truth rules ("client message draft"
  and "parser guess alone" are explicitly listed as **not** source of truth) and the
  program-status → roster-status mapping table. That table has no `expired` row, which
  is a minor doc gap (code has had `status='expired'` as a real transitional state since
  before this doc was last verified) — worth a one-line addition, not a conflict with
  Owner's Rules.
- **No conflict found** between these two KB docs and Rules 1-4 above. Neither document
  claims open/unauthenticated intake is acceptable; both frame drafts as provisional and
  bridge-confirmation as authoritative, matching Rules 1-2 in spirit. The one gap is
  operational, not doctrinal: KB doesn't mention the 24h-hard-delete detail explicitly
  (Rule 3) even though the code already does it — proposed fix includes updating this
  doc (Task 5 below).
- No KB article documents `ESCORT_CLIENT_PHONES` / `_is_allowed_escort_client()` /
  `_is_authoritative_bridge_confirmation()` by name. That's the actual doc gap this audit
  found — the enforcement mechanism exists in code but isn't named in the KB, so a
  future reader has no fast way to confirm Rules 1-2 are already enforced without
  reading source, which is exactly the confusion that produced this task.

### 1.7 Existing garbage data — full query, not just the 4 example rows

Queried `wbom_escort_programs` (348 total programs) for rows that are: not
`is_historical` (940-type rows excluded per Owner's constraint), not created via the
admin-confirmed path (`auto_created` or `source_of_truth=bridge_confirmation` absent
from remarks — i.e., true client-origin drafts), and whose `remarks.sender_phone` is
neither a registered admin number nor a registered escort-client number.

**18 rows found, program_id: 155, 213, 214, 219, 229, 230, 765, 769, 774, 779, 808, 840,
851, 860, 869, 871, 873, 880** — spanning 2026-04-26 through 2026-06-20. **All 18
predate the 2026-06-27 gate.** Zero such rows exist after that date (verified above).

Financial-impact check (read-only): all 18 have `escort_employee_id IS NULL`,
`total IS NULL`, `net_payable = 0.00` in `escort_roster_entries`, and **zero** rows in
`fpe_cash_transactions` reference any of these `program_id`s. They are financially
inert — no payroll run, ledger entry, or payment was ever computed against them.

13 of the 18 are `status='expired'` (already visually distinct/de-emphasized in the
roster UI, but still occupying real S/N slots and still queryable/exportable). **5 are
`status='confirmed'`** (213, 214 — same sender, 3h apart; 219, 229, 230 — same sender
`8801958122301`, a one-digit neighbor of the real Bridge1 number `8801958122300`, two of
the three created 22ms apart, consistent with the same kind of split-lighter batch seen
in 861-864). These 5 reached `confirmed` status without ever passing through the
post-06-27 gate, since they predate it — they warrant the same cleanup treatment as the
draft/expired ones given the financial-inertness check above, but I'm calling the status
difference out explicitly since "confirmed" implies more scrutiny than "expired" and I
did not want that distinction silently collapsed into one bucket.

Separately, **5 more `status='expired'` rows exist with a *known/valid* sender** (not in
the 18 above) — legitimate client orders that simply were never confirmed in time,
pre-dating the current 24h hard-delete behavior. These are a different, milder category
(not unauthorized-sender garbage) and are **not included in the Task 3E cleanup scope**
below unless Owner wants them swept too.

---

## Task 2 — Formal Proposal

**1. Current production behavior:** Sender-validation gates for both draft creation
(Rule 1) and confirmed-program creation (Rule 2), component-validity checks, duplicate
prevention, and 24h hard-delete of stale drafts (Rule 3) **already exist and are already
live** (commit `41916b0`, 2026-06-27, and commit `82f2b33`, 2026-07-05, respectively).
Verified via git history (`git log -S` on each gate function) and live data (zero
unauthorized-sender rows and zero soft-expired rows created after those dates).

**2. KB reference:** `escort_source_of_truth_workflow.md` and `escort_roster_system.md`
(§1.6 above) — no conflict with Owner's Rules 1-4; one doc gap (24h-delete detail and
the specific gate-function names aren't documented) proposed to be closed in Task 5.

**3. Gap analysis:** The only real, currently-live gap is **historical**: 18 rows
created *before* the 2026-06-27 gate was deployed were never retroactively cleaned up,
because the 2026-07-05 hard-delete fix only sweeps rows currently sitting in
`status='draft'` — it has no mechanism to catch rows that had already been soft-expired
under the pre-07-05 regime. That's why 869/871/873/880 (and 14 siblings) are still
visible in the roster today. Fixes 3A/3B/3C/3D as scoped in the original task are **not
needed as new code** — they already exist and have a 3-week clean track record in
production (2026-06-27 → 2026-07-16, zero unauthorized-sender rows created). Building a
second, redundant validation gate on top of the existing one would add maintenance
surface without closing any real gap.

**4. Proposed implementation:**
- **3E only, scoped down:** one-time `DELETE` of the 18 identified `program_id`s from
  `wbom_escort_programs` and their `escort_roster_entries` rows, logging an audit entry
  per row first (mirroring `expire_stale_drafts()`'s own pattern: log via
  `log_audit(action="manual_unauthorized_sender_cleanup", ...)` before each delete, so
  the S/N, sender, status, and created_at survive in `escort_roster_audit_logs` even
  after the row itself is gone).
- **Small defensive addition (optional, low-risk):** extend
  `expire_stale_drafts()`'s WHERE clause (or add a lightweight periodic sweep) to also
  catch any *future* `status='expired'` row that's financially inert and past TTL, so
  this exact "stuck forever in expired limbo" failure mode can't recur even in an edge
  case the 07-05 fix didn't anticipate. Not required by current evidence (nothing has
  hit this path in 3 weeks) but cheap insurance given Rule 3's "no exceptions" framing.
- **No changes** to `save_escort_programs()`, `handle_escort_client_message()`,
  `handle_admin_escort_completion()`, `_update_program_confirmed()`, or
  `_create_confirmed_from_admin()` — their validation is already correct.
- **Task 5 doc update:** add the gate-function names and the 24h-hard-delete detail to
  `escort_source_of_truth_workflow.md`, and add an `expired` row to the status-mapping
  table in `escort_roster_system.md`.

**5. Risk assessment:** Deleting 18 rows with zero payroll/ledger linkage and
`escort_employee_id IS NULL` across the board is low-risk to financial data. Residual
risk is entirely about audit-trail loss for a hard delete — mitigated by logging full
row contents to `escort_roster_audit_logs` before each delete (same pattern the
production TTL-delete path already uses, so this isn't a new precedent). The 5
`status='confirmed'` rows get slightly more explicit callout above so Owner can exclude
any of them from the delete list if a closer look is wanted before this ships. The
optional defensive-sweep addition touches only the scheduler's stale-draft path, purely
additive, same non-fatal pattern already used throughout this module.

**6. Rollback plan:** Code change (if the optional defensive sweep is approved) is a
single function in `modules/escort_roster/db.py`, revertible via `git checkout`. The
DELETE is not reversible by rollback — it's reversible only by re-inserting from the
`escort_roster_audit_logs.old_data` JSON captured immediately before each delete, which
is why that logging step is mandatory before any DELETE executes, not optional.

---

## Sign-off

Pending — see `management_decisions.md` for the authorization record once granted.
