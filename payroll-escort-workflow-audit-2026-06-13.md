# Payroll Engine, Employee Finance & Escort Operations Audit

**Audit date:** 2026-06-13  
**Mode:** Read-only code and live database inspection  
**Scope:** Employee onboarding, payroll, salary/advance requests, accountant cashbook, escort orders, roster, release, food/conveyance, media processing, AI reply, and learning

---

## 1. Executive Summary

বর্তমান system-এ প্রয়োজনীয় business capability-এর অধিকাংশই আছে, কিন্তু এগুলো একটি সরল application হিসেবে কাজ করছে না। একই domain-এর data এবং workflow একাধিক parallel subsystem-এ বিভক্ত:

- `wbom_*`: operational employee, escort, attendance, cash, and monthly payroll
- `fpe_*`: immutable WhatsApp-derived accounting, employee matching, and ledger
- `escort_roster_entries`: roster UI and calculated escort pay
- `fazle_payment_drafts`: advance and escort-payment approval queue
- `fazle_draft_replies`: WhatsApp reply drafts and employee verification sessions

সবচেয়ে বড় সমস্যা হলো write-path consistency। কিছু workflow শুধু `wbom_*` update করে, কিছু শুধু `fpe_*` update করে, এবং roster sync অধিকাংশ operational write-এর পরে স্বয়ংক্রিয়ভাবে চলে না।

### Current live snapshot

| Area | Live state |
|---|---:|
| `wbom_employees` | 171 rows, 170 active |
| `fpe_employees` | 351 rows, 350 active |
| FPE employees linked to WBOM | 98 of 351 |
| FPE unresolved employees | 349 |
| `wbom_escort_programs` | 319 |
| `escort_roster_entries` | 140 |
| Unsynced escort programs | 179 |
| `wbom_attendance` | 0 |
| `fazle_payment_drafts` | 47, all expired escort-payment drafts |
| `fpe_cash_transactions` | 2,282 total rows |
| `fpe_employee_ledger` | 336 rows |
| FPE parser/accounting messages | 2,078 done, 6 failed, 6,463 skipped |

### Overall assessment

**Architecture maturity:** Feature-rich but fragmented  
**Accounting reliability:** Medium  
**Escort order handling:** Partially functional  
**Release-to-payment workflow:** Currently broken in important paths  
**Frontend coverage:** Good for FPE and roster; weak for operational workflow control  
**Recommended direction:** One lightweight operational core with adapters for WhatsApp, media, AI, and accounting

---

## 2. Current Architecture

```text
WhatsApp Bridge 1 / Bridge 2
        |
        +--> bridge_poller / webhook
        |       |
        |       +--> message_router
        |       |       +--> employee verification
        |       |       +--> escort order
        |       |       +--> release lifecycle
        |       |       +--> admin/accountant commands
        |       |
        |       +--> media processor / OCR
        |
        +--> SQLite history sync
                |
                +--> FPE ingestion
                        +--> parser worker
                        +--> employee match/auto-create
                        +--> accounting worker
                        +--> fpe_cash_transactions
                        +--> fpe_employee_ledger

Operational data:
  wbom_employees
  wbom_escort_programs
  wbom_cash_transactions
  wbom_attendance
  wbom_payroll_runs

Secondary/canonical accounting:
  fpe_employees
  fpe_cash_transactions
  fpe_employee_ledger

Roster projection:
  wbom_escort_programs --> manual/scheduled sync --> escort_roster_entries
```

---

## 3. Employee Creation and Data Display

### 3.1 How new employees are added

#### Method A: Payroll frontend / Admin Employee API

`POST /api/admin/employees`:

1. Validates and normalizes employee mobile.
2. Inserts a full operational employee into `wbom_employees`.
3. Calls FPE `match_or_create_employee()`.
4. Creates or matches a corresponding `fpe_employees` record.

This is the safest current employee creation path because it creates the operational record first.

#### Method B: FPE WhatsApp accounting auto-create

When an owner/accountant WhatsApp payment message is parsed:

1. FPE extracts employee name, payout phone, ID phone, amount, and method.
2. FPE tries exact phone, ID-phone, alias, WBOM phone, exact name, and fuzzy-name matching.
3. If phone evidence exists but no employee matches, FPE immediately creates `fpe_employees`.
4. The new employee is marked `created_source='whatsapp_auto_create'`.

This path does **not** create a `wbom_employees` operational profile. Therefore the employee may appear in payroll history but cannot reliably participate in escort assignment, release, attendance, or WBOM payroll.

#### Method C: Income-command auto-create

Authorized WhatsApp income commands can create an FPE employee before writing an income record.

### 3.2 Where employee data appears

| Data | Frontend / API |
|---|---|
| FPE employee list, aliases, transaction totals, ledger | `/payroll/employees` |
| FPE transaction and employee history | `/payroll/transactions`, `/payroll/search` |
| WBOM operational employee add/edit/deactivate | Embedded in `/payroll` through `/api/admin/employees` |
| Escort assignment/person data | `/escort-roster` |
| Attendance | Dashboard/API only; no complete employee attendance operations page found |

### 3.3 Employee data problems

1. `wbom_employees` has 171 rows while `fpe_employees` has 351 rows.
2. Only 98 FPE rows are linked to WBOM.
3. 349 FPE employee rows remain unresolved.
4. FPE auto-created employees may not exist operationally.
5. WBOM update does not generally update FPE profile fields.
6. FPE profile update does not generally update WBOM.
7. WBOM stores status as `Active/Inactive`; some FPE cross-lookup code compares against lowercase `inactive`, which can accidentally match inactive WBOM employees.

---

## 4. Employee Salary and Advance Requests Through WhatsApp

### 4.1 Salary question

When a recognized employee asks about salary/payment:

1. Phone identity maps the sender to `wbom_employees`.
2. AI classifies intent as `salary_query` or `payment_due`.
3. `payroll_logic.get_payroll_summary()` reads:
   - `wbom_employees`
   - `wbom_cash_transactions`
   - `wbom_escort_programs`
   - `wbom_salary_records`
4. The summary is passed to the general AI reply generator.

**Limitation:** The response reads WBOM finance data, while the richer live accounting history is mostly in FPE. Therefore an employee may receive an incomplete or incorrect salary/payment summary.

### 4.2 Employee advance request

Current verification workflow:

```text
Employee: "advance দরকার"
  -> detect employee / advance keywords
  -> create verification session in fazle_draft_replies
  -> request duty-location selfie
  -> request duty/release slip image
  -> request bKash/Nagad number
  -> persist payment number in wbom_employees
  -> create fazle_payment_drafts row
  -> create admin WhatsApp notification
  -> admin approves with ADVANCE/PAID command
  -> write wbom_cash_transactions
  -> notify accountant and employee
```

### 4.3 Advance workflow problems

1. `AUTO_REPLY_ENABLED=false` causes generated `admin_note` notifications to be suppressed. Verification may finish, but the admin may never receive the payment draft on WhatsApp.
2. Requested amount is not reliably extracted from the original employee message; the final draft can contain amount `0` or “unspecified”.
3. Verification sessions are stored in the generic `fazle_draft_replies` table rather than a dedicated workflow/session table.
4. Image verification checks media markers, not actual selfie identity/location authenticity.
5. Persisting a submitted bKash/Nagad number directly to `wbom_employees` has no admin approval step.
6. The approved advance writes to `wbom_cash_transactions`, not directly to FPE immutable accounting.
7. `wbom_fpe_sync` exists, but no automatic call was found after `finalize_payment()` or direct admin advance insertion.
8. Admin natural-language advance records bypass the FPE ledger and write only WBOM.

---

## 5. Daily Cash Ledger and Accountant Conversation

### 5.1 Current pathways

#### FPE historical conversation sync

FPE continuously reads the configured owner-to-accountant WhatsApp chat from bridge SQLite history:

```text
WhatsApp conversation
  -> fpe_wa_messages
  -> fpe_message_processing_state
  -> parser result
  -> employee match/create
  -> fpe_cash_transactions
  -> fpe_employee_ledger
```

It supports:

- payment lines
- authorized `Cash` commands
- authorized income commands
- multi-entry escort payment messages
- parser review queue for uncertain records

#### Accountant message router

Recognized accountant messages can:

- record direct advance via natural-language command
- import bKash/Nagad/payment SMS
- parse shorthand cash entries
- acknowledge daily accounting summaries

### 5.2 Cashbook problems

1. Daily accounting summary messages are acknowledged but intentionally **not stored** anywhere.
2. WBOM cash writes and FPE cash writes are not guaranteed to remain synchronized.
3. `wbom_fpe_sync` appears to use an outdated `create_transaction()` calling contract and is not automatically invoked from important WBOM write paths.
4. Direct admin/employee payment approval records WBOM cash but may not update the FPE ledger.
5. FPE standard payment parser records parsed owner-to-accountant messages as salary by default, even when business meaning may be advance, reimbursement, food, conveyance, or another category.
6. FPE has strong immutable transaction and reversal design; WBOM cash transactions are comparatively weaker and remain the source for several operational calculations.
7. The same payment can conceptually exist in both stores without a durable cross-reference.

---

## 6. Escort Client Order Through WhatsApp

### 6.1 Current order flow

```text
Escort buyer/client sends WhatsApp order
  -> role detection based on normalized mobile number
     (AI reads message context to decide: escort-client / escort-client-buyer / other)
  -> parse_escort_message()
  -> extract mother vessel, lighter(s) name, lighter master mobile,
     importer, cargo, destination, capacity
  -> save one wbom_escort_programs row per lighter, status=draft
  -> build admin draft message, send from Bridge1 to Bridge2 (super-admin)
  -> admin fills Escort Name + Escort Mobile
  -> admin sends completed assignment slip
  -> outgoing poller on both Bridge1 and Bridge2 detects completed slip
  -> match or create confirmed escort program
  -> resolve escort_employee_id from escort mobile
  -> auto-sync to escort_roster_entries
  -> send final assignment slip to original client
     (no forwarding label, no admin phone, no AI attribution — clean slip only)
```

**Sample order messages handled:**

```
(1) $ MV MARIMYR A , 
    Mv. Lichu shah-7
    O/a to Narayanganj
    Capacity: 1100
    Master: 01940125166

(2) MV MARIMYR A , SOYBEAN MEAL t
    Mv. Labonno
    O/a to Narayanganj
    Capacity: 1100
    Master: +880 1729-455965

(3) Mv.bos brook/Nabil /y.peas
    1.glory of Srinagar 4-01711202423 n.gonj 1500 m.t

(4) Mv.Bos Brook/Nabil /y.peas
    2.Lily 7-01738076669 n.para 1200 m.t

(5) Mv MARIMYR A , SOYBEAN MEAL
    Mv. Fazlay Khoda
    O/a to Narayanganj
    Capacity: 1100
    Master: +880 1811-505325

    Mv. Ahsan Habib-1
    O/a to Narayanganj
    Capacity: 1200
    Master: +880 1880-850771
    (example 5: single message with two lighters → two rows saved)
```

### 6.2 Extraction rules

The extractor supports three strategies (tried in order):

**Strategy 1 — Labeled blocks** (`Lighter:` / `Lighter Vessel:`):
- Mother vessel labels: `MV`, `M.V.`, `Mother Vessel`, `At-O/A`
- Lighter blocks with explicit `Lighter:` or `Lighter Vessel:` prefix

**Strategy 2 — Numbered inline format**:
- `1. Name-01712345678 destination 1500 m.t`
- `15. Name, PHONE: 01745377025`

**Strategy 3 — `Mv.` block format** *(added 2026-06-13)*:
- Mother vessel: first `Mv`/`MV` line with no phone in its immediate lookahead
- Lighter vessels: subsequent `Mv.` blocks followed by `O/a to` / `Capacity:` / `Master:` lines
- Handles formatted phone numbers: `+880 1729-455965`, `+880 1811-505325` etc.
- Normalizes all phone formats to `01XXXXXXXXX`

**Common extraction rules:**
- **Mother vessel:** identified by `MV/M.V./Mv.` prefix, no associated phone, usually with cargo keyword or importer. Trailing comma after name is handled correctly.
- **Cargo keywords:** wheat, corn, soybean/soya meal, coal, sugar, rice, salt, y.peas / yellow peas
- **Destination:** extracted from `O/a to <place>` or `Destination:` label; abbreviations (n.gonj, n.para, a.gonj, Rupsi) preserved
- **Capacity:** `900`–`1500` MT formats like `1100 m.t` or `Capacity: 1100`
- **Mobile normalization:** plain `01XXXXXXXXX`, `8801XXXXXXXXX`, or `+880 1729-455965` with spaces/dashes — all normalized to `01XXXXXXXXX`

### 6.3 Who provides escort name and mobile

The **admin/operations person** (Bridge2 super-admin) provides escort name and escort mobile by completing the generated assignment draft. The system does not automatically choose an available employee from the roster.

Admin template sent from Bridge1 to Bridge2:

```text
Notun escort order:

[A/c: <importer> | Cargo: <cargo>]
Client: <client-phone>

Mother Vessel: <mother-vessel>
Lighter Vessel: <lighter-vessel>
Master Mobile: <master-mobile>
Destination: <destination>
Escort Name:
Escort Mobile:
<date> (D/N)
Al-Aqsa Security & Logistics Services Ltd
```

After admin completes the blank fields:

1. The outgoing Bridge1 or Bridge2 message is detected.
2. The matching draft program becomes `confirmed`.
3. Escort name/mobile are written to `wbom_escort_programs`.
4. `escort_employee_id` is resolved from the escort mobile against `wbom_employees`.
5. Program is auto-synced to `escort_roster_entries`.
6. Final slip is built and sent to the original client — no forwarding label, no admin info, clean slip only:

```text
Mother Vessel: MV. Eastern Mongolia
Lighter Vessel: MV. HADIZ
Master Mobile:  01863191301
Destination: Rupshi
Escort Name: Tiplu Das Gupta
Escort Mobile: 01806730694
Start Date: 11.06.2026 (D)
Al-Aqsa Security Service
```

### 6.4 Escort order problems — status after 2026-06-13 fixes

| # | Problem | Status |
|---|---|---|
| 1 | Safe mode suppresses admin notification | ✅ Fixed — `internal_notifications_enabled=True` (independent of `AUTO_REPLY_ENABLED`); admin notes sent via `_notify_admin_bridge()` regardless of safe mode |
| 2 | Dedup has no date limit — repeat operations ignored | ✅ Fixed — dedup now only blocks duplicates within the **last 30 days** |
| 3 | No lighter extracted → partial empty program silently created | ✅ Handled — partial row created with placeholder name; admin template explicitly marks it for admin to complete |
| 4 | Regex extraction English-heavy; `Mv.` block format and `+880` phone numbers not supported | ✅ Fixed — new `_parse_mv_block_lighters()` strategy handles all 5 sample formats; `_MOBILE_FMT_RE` + `_normalize_mobile()` handles `+880 1729-455965` style |
| 5 | No confidence score or mandatory admin review | ⚠️ Not addressed — out of scope for this fix; admin fills the draft which serves as the review step |
| 6 | No automatic availability check | ⚠️ By design — admin assigns manually; auto-recommendation is a future feature |
| 7 | Confirmed program does not set `escort_employee_id` | ✅ Fixed — `_resolve_escort_employee_id()` looks up `wbom_employees` by escort mobile after confirmation |
| 8 | Without `escort_employee_id`, release/attendance/payroll broken | ✅ Partially fixed — `escort_employee_id` now set on confirmation; existing unlinked programs require backfill |
| 9 | No automatic `sync_program_to_roster()` after confirmation | ✅ Fixed — `_sync_roster_after_confirm()` called automatically after every confirmation |
| 10 | Only Bridge 2 outgoing messages scanned for completions | ✅ Already fixed in bridge_poller — both Bridge1 and Bridge2 outgoing messages are scanned |

**Remaining gaps (not addressed in this fix):**
- Extraction confidence score / mandatory admin review queue (P1 from section 11)
- Automatic escort availability check / recommendation
- Backfill of `escort_employee_id` on 319 existing programs
- `_MV_LABEL_RE` cannot match vessel names ending mid-line with cargo info on same line without a comma separator; the comma-lookahead fix (`[,\n/]`) handles the common case

---

## 7. Escort Roster Updates

### 7.1 Current roster model

`wbom_escort_programs` is the operational source. `escort_roster_entries` is a secondary projection used by `/escort-roster`.

Roster sync:

1. Reads a program.
2. Looks up the employee name using `escort_employee_id`.
3. Calculates shifts, salary, conveyance, and total.
4. Upserts `escort_roster_entries`.
5. Writes `escort_roster_audit_logs`.

### 7.2 How roster can be updated

- Manual `Sync All` from frontend
- Manual single-program sync API
- Historical import/rebuild
- Direct roster create/edit from frontend
- Recalculation endpoint

### 7.3 Roster problems

1. Only 140 of 319 programs currently exist in roster entries.
2. Direct roster edits update `escort_roster_entries`, not necessarily `wbom_escort_programs`.
3. Manual roster creation creates a roster-only synthetic program ID without creating an operational program.
4. Operational program changes do not automatically update roster.
5. Confirmed status maps to roster `draft`, because status mapping does not include `confirmed`.
6. Two-way edits create source-of-truth ambiguity.
7. Roster supports hard-delete of draft programs, which is risky for operational audit history.

---

## 8. Release, Food Bill, Conveyance, and Final Payment

### 8.1 Text release flow

If a recognized employee sends “release”, “duty done”, or related text:

1. Find latest non-completed escort program using `escort_employee_id`.
2. Mark program `Completed`.
3. Backfill attendance for each duty date.
4. Create escort-payment draft.
5. Employee receives a completion acknowledgment.

### 8.2 OCR release-slip flow

If media processor detects a release slip:

1. OCR extracts employee, lighter, date, release location, and amount.
2. A draft-only admin message is generated.
3. Food estimate = duty days × BDT 150.
4. Conveyance estimate uses a hardcoded location-rate table.
5. Admin must correct and send `[RELEASE CONFIRMED]`.
6. Outgoing Bridge 2 poller detects the confirmation and closes the program.

### 8.3 Confirmed breakages

1. `create_escort_payment_draft()` selects `shift_type`, but live `wbom_escort_programs` has no `shift_type` column. Release can close a program and then fail to create its payment draft.
2. Release-confirmation lookup joins `wbom_employees.contact_id`, but live `wbom_employees` has no `contact_id` column.
3. Closing the program, attendance backfill, and payment-draft creation are not one transaction. Partial completion is possible.
4. `wbom_attendance` currently has zero rows, indicating release backfill is not operating successfully or no linked releases completed.
5. Food estimate is shown only in OCR draft and is not persisted into a structured table or payment draft.
6. Conveyance parsed from admin confirmation is not written by `close_program()`.
7. OCR conveyance uses hardcoded rates while roster uses `escort_calculation_config`; comments explicitly acknowledge rate disagreement.
8. Payment draft calculates amount from `basic_salary / 30`, while roster calculates shift-based salary. These can produce different payable amounts.
9. Previous advances are deducted from the last 60 days, not from a specific duty/program or payroll period.
10. All 47 live escort-payment drafts are expired; no pending/approved payment draft exists.

---

## 9. Monthly Payroll

The WBOM payroll module computes:

```text
basic salary
+ completed escort program days × per-program daily rate
- WBOM advances within payroll month
= net salary
```

State machine:

```text
draft -> reviewed -> approved -> locked -> paid
                    \-> cancelled
```

### Payroll problems

1. Payroll uses WBOM cash advances, while much of the accounting history lives in FPE.
2. Payroll uses a fixed default program rate; roster uses shift-rate configuration.
3. Food and conveyance are not clearly integrated into monthly payroll.
4. Marking a payroll run `paid` does not clearly create a canonical FPE transaction.
5. `wbom_payroll_runs` contains 510 rows, suggesting runs exist, but operational employee and attendance linkage remains incomplete.

---

## 10. Frontend Coverage

### Existing payroll frontend

`/payroll` currently provides:

- Overview
- Transactions
- Search
- Employees
- Unmatched messages
- Review queue
- Sync
- Manual transaction
- Cash
- Income
- Employee details and ledger
- Add/edit/deactivate operational employee
- Reversal and audit-oriented transaction operations

### Existing escort frontend

`/escort-roster` currently provides:

- Summary
- Search/filter/sort/date range
- CSV export
- Detail view
- Add/edit
- Recalculate
- Sync all / history sync
- Conveyance configuration

### Missing operational frontend

- Incoming escort order review queue with extraction confidence
- Employee availability and assignment board
- Advance verification/session tracker
- Release-slip review and approval queue
- Payment draft approval page linked to employee/program/roster
- WBOM-to-FPE reconciliation dashboard
- Program lifecycle timeline
- Daily accountant cashbook summary and reconciliation page

---

## 11. Priority Problems and Remediation

### P0: Prevent partial release completion

Use one database transaction for:

```text
validate program and employee
-> close program
-> write release fields and conveyance
-> backfill attendance
-> calculate structured settlement
-> create payment draft
-> sync roster
```

Fix invalid `shift_type` and `contact_id` queries before enabling the workflow.

### P0: Define canonical sources

Recommended:

- Employee identity/operations: `employees`
- Escort lifecycle: `escort_programs`
- Immutable money events: `fpe_cash_transactions`
- Derived monthly ledger: `fpe_employee_ledger`
- UI projections: roster/payroll views, not independent writable copies

During migration, retain WBOM tables but write through one service that synchronizes both stores transactionally.

### P0: Restore admin notifications safely

Admin/accountant operational notifications must not depend on customer auto-reply safe mode. Add a separate setting:

```text
INTERNAL_OPERATION_NOTIFICATIONS_ENABLED=true
CUSTOMER_AUTO_REPLY_ENABLED=false
```

### P1: Make employee linkage mandatory

- New employee frontend creation should create/link both operational and FPE identities.
- FPE auto-created employee should enter a review queue before becoming operational.
- Confirmed escort assignment should resolve and write `escort_employee_id`.
- Add uniqueness and normalized-phone constraints.

### P1: Unify settlement calculation

Create one settlement calculator with structured components:

```text
duty_salary
food_allowance
conveyance
other_allowance
advance_deduction
other_deduction
net_payable
```

All rates must come from versioned DB configuration, never hardcoded release-flow values.

### P1: Automatic roster projection

Call roster sync automatically after:

- order creation
- assignment confirmation
- release confirmation
- program correction/cancellation

Roster should be read-only projection except for actions that call the operational program service.

### P1: Canonical payment posting

Every approved advance, salary, escort settlement, food bill, or conveyance payment should:

1. Create one immutable FPE transaction.
2. Reference employee, program, settlement, approval, and source WhatsApp message.
3. Update ledger.
4. Send notification through persistent outbound queue.
5. Preserve delivery status separately from accounting status.

---

## 12. Recommended Lightweight App Structure

```text
app/
  api/
    employees.py
    escort_orders.py
    escort_programs.py
    settlements.py
    payroll.py
    accounting.py
    whatsapp.py

  domain/
    employees/
      service.py
      repository.py
    escort/
      extractor.py
      assignment.py
      lifecycle.py
      settlement.py
    accounting/
      transactions.py
      ledger.py
      payroll.py
    messaging/
      router.py
      templates.py
      outbound_queue.py

  integrations/
    whatsapp_bridge.py
    meta_whatsapp.py
    media_processor.py
    ai_provider.py

  learning/
    extraction_feedback.py
    reply_feedback.py
    reviewed_examples.py
```

### Lightweight event flow

```text
Inbound WhatsApp
  -> normalize phone
  -> identify contact/employee/client
  -> deterministic business-event classifier
  -> AI extraction only when needed
  -> validate extracted structured data
  -> save event and create review task
  -> human approval for financial/assignment actions
  -> domain service transaction
  -> persistent outbound queue
  -> delivery receipt and audit log
```

### Media processor role

Media processor should only:

- identify document type
- OCR text
- extract candidate fields with confidence
- preserve original media reference

It must not close duties or calculate final money. The domain service should validate OCR output and create an admin review task.

### AI role

AI should:

- classify free-form messages into business events
- extract vessel/order/release fields into strict JSON
- generate user-friendly replies from approved facts

AI must not:

- directly create employees
- approve payment
- assign escort
- calculate authoritative settlement
- mutate ledger

### Learning system

Store:

- original message/media
- AI extraction
- admin-corrected extraction
- final approved action
- model/version/confidence

Use corrected examples to improve extraction prompts and tests. Never learn financial values or identities automatically without review.

---

## 13. Suggested Delivery Plan

### Phase 1: Stabilize current system

1. Fix release query/schema mismatches.
2. Separate internal notifications from customer auto-reply.
3. Add transactional release settlement.
4. Automatically sync roster after lifecycle changes.
5. Post every WBOM financial approval into FPE.
6. Add alerts for expired payment drafts and unsynced roster programs.

### Phase 2: Reconcile data

1. Resolve/link FPE employees to WBOM.
2. Review 179 unsynced escort programs.
3. Reconcile WBOM and FPE cash transactions.
4. Review all 47 expired escort-payment drafts.
5. Verify employee assignment links on active programs.

### Phase 3: Simplify architecture

1. Introduce employee, escort, settlement, and accounting services.
2. Make roster and payroll frontend read projections from canonical services.
3. Replace direct bridge sends with persistent outbound queue.
4. Add operational review pages.
5. Retire duplicate write paths after migration validation.

---

## 14. Final Assessment

বর্তমান system-এর শক্তি হলো প্রচুর বাস্তব workflow ইতোমধ্যে code-এ আছে: employee matching, immutable FPE ledger, WhatsApp parsing, escort extraction, roster UI, release OCR, verification sessions, payment drafts, and monthly payroll state machine।

কিন্তু system বর্তমানে “একটি payroll/escort application” নয়; এটি কয়েকটি overlapping subsystem-এর সমষ্টি। সবচেয়ে নিরাপদ পরবর্তী পদক্ষেপ হলো নতুন feature যোগ করার আগে release/payment breakage ঠিক করা, employee identity reconcile করা, এবং প্রতিটি business action-এর জন্য একটি canonical write path নির্ধারণ করা।

---

## 15. Current System Delta Review — 2026-06-25

**Review date:** 2026-06-25  
**Mode:** Read-only code comparison against the 2026-06-13 audit report  
**Live DB note:** This update did not re-run live database row counts. The snapshot counts in section 1 remain the original 2026-06-13 evidence until a fresh DB inspection is performed.

### 15.1 Summary of Changes Since the Original Audit

Yes — important changes happened after the original audit. The biggest improvements are in escort release settlement and admin-confirmed release handling:

- Release finalization now requires explicit admin confirmation by default.
- Release close, attendance backfill, payment draft creation, and roster projection now run inside one database transaction.
- The previous `shift_type` query breakage in escort payment draft creation is removed; current code reads `shift`, `end_shift`, `day_count`, `food_bill`, and `conveyance`.
- The previous `wbom_employees.contact_id` release-confirmation breakage is removed; current code identifies employees by normalized `employee_mobile`, with fallback to active program `escort_mobile`.
- Escort payment drafts now persist structured `food_bill`, `conveyance`, `advance_deduction`, `gross_amount`, and `expected_amount`.
- Release OCR drafts now include validation/warning behavior and remain draft-only until admin sends `[RELEASE CONFIRMED]`.
- Confirmed escort assignment still resolves `escort_employee_id` and auto-syncs roster, as already documented in the June 13 fixed-status section.

However, several original architectural gaps remain:

- Approved payment drafts still write only to `wbom_cash_transactions`; FPE sync exists as an adapter but is not called by `finalize_payment()`.
- Monthly payroll still uses WBOM cash advances and a fixed per-day escort rate, not the richer FPE ledger or the exact settlement draft components.
- Roster remains both a projection and a directly editable surface, so source-of-truth ambiguity still exists.
- Employee identity remains split between WBOM and FPE creation/matching paths.
- Operational review frontends for release slips, payment drafts, reconciliation, and lifecycle timelines are still not visible in the active app surface reviewed here.

### 15.2 Previous vs Current Workflow

| Area | Previous system/workflow from 2026-06-13 audit | Current system/workflow observed on 2026-06-25 | Change status |
|---|---|---|---|
| Escort client order | Client message -> parse vessel/lighter -> save draft program -> admin fills escort name/mobile -> confirmed program -> roster sync -> clean slip to client. | Same broad flow. Current code still uses `handle_escort_client_message()`, `save_escort_programs()`, `handle_admin_escort_completion()`, `_resolve_escort_employee_id()`, and `_sync_roster_after_confirm()`. If no matching draft exists, admin completion can create a confirmed program directly. | Mostly unchanged; direct confirmed-from-admin fallback is an important resilience path. |
| Escort extraction | Labeled blocks, numbered inline format, and `Mv.` block format, with formatted BD phone normalization. | Same strategy chain remains active in `parse_escort_message()`: labeled -> inline -> `Mv.` block. `+880` formatted phone normalization remains present. | No regression found. |
| Assignment confirmation | Admin completed slip updates DB, resolves `escort_employee_id`, syncs roster, and sends clean final slip to client. | Same behavior remains. Current `_update_program_confirmed()` sets `status='confirmed'`, escort fields, optional date/shift/lighter fields, and `escort_employee_id`; roster sync follows. | Still fixed. |
| Text release flow | Employee release text could close the program, backfill attendance, and create draft; audit warned partial completion was possible. | `handle_release_event()` returns `admin_confirmation_required` unless `admin_confirmed=True`. The employee text path should no longer finalize money/actions directly. | Improved; financial release is admin-gated. |
| OCR release flow | OCR created an admin draft with estimates; admin sends `[RELEASE CONFIRMED]`; outgoing poller detects confirmation. | Same flow, with stronger draft warnings: missing required fields, future/old dates, implausible day counts, low OCR confidence, and draft-only estimate language. | Improved validation/review posture. |
| Release finalization | Audit identified broken queries and non-transactional close + attendance + payment draft creation. | `_handle_release_event_tx()` runs under `conn.transaction()`: active program lookup, shift/day calculation, `close_program()`, `backfill_attendance_for_program()`, `create_escort_payment_draft(conn=conn)`, and roster upsert happen in one transaction. | P0 partial-release risk significantly reduced. |
| Escort payment calculation | Audit said payment draft selected missing `shift_type`; amount used `basic_salary / 30`, with inconsistent food/conveyance treatment. | `create_escort_payment_draft()` now selects valid program fields: `shift`, `end_shift`, `day_count`, `food_bill`, `conveyance`. Net payable is `(basic_salary / 30 * duty_days) - food_bill - conveyance - advances`. | Query breakage fixed; formula is more structured, but rate unification is still incomplete. |
| Advance deduction | Audit said previous advances were deducted from last 60 days, not tied to program/period. | Current escort payment draft deducts advances only when `employee_id`, `program_id`, current payroll month, and non-reversed conditions match. | Improved specificity. |
| Payment approval | Admin `PAID`/`ADVANCE` finalizes draft, writes WBOM cash transaction, sends accountant message. | Same final approval shape remains. `finalize_payment()` is transactional and idempotent by `payment-draft:<draft_id>`, but it does not call `sync_wbom_transaction()`. | WBOM idempotency improved, FPE canonical posting still missing. |
| WBOM -> FPE sync | Audit noted `wbom_fpe_sync` existed but was outdated/not automatically invoked. | `modules/payment_ingest/wbom_fpe_sync.py` now has a current adapter using `match_or_create_employee()` and `create_transaction()`, plus date-range backfill. It is still not wired into `finalize_payment()` or payroll paid transitions. | Adapter improved; automatic write-path integration still pending. |
| Monthly payroll | WBOM payroll computes basic salary + completed escort days * fixed rate - WBOM advances; state machine draft -> reviewed -> approved -> locked -> paid. | Same model remains. `mark_paid()` updates payroll run state and audit log but does not create an FPE transaction. | Largely unchanged. |
| Roster projection | Audit said roster is a secondary projection but also directly editable, causing ambiguity. | `sync_program_to_roster()` projection remains, and release finalization now upserts roster transactionally. Direct roster editing paths still exist. | Projection sync improved; two-way source ambiguity remains. |
| Safe mode / internal operational sends | Audit identified admin notifications being suppressed when auto-reply was disabled, then noted a fix for internal notifications. | Current knowledge base indicates production activation changed `AUTO_REPLY_ENABLED=true` on 2026-06-23, and code has admin approval/draft paths that bypass the customer auto-reply gate. | Current runtime policy changed from the original snapshot; verify `.env` during ops audit. |

### 15.3 Changed Items After the Changed Block

#### Change 1 — Release Finalization Is Now Transactional

**Previous system/workflow:**  
`close_program()`, attendance backfill, and payment draft creation could happen as separate writes. If payment draft creation failed after program closure, the program could be marked completed without a payable draft.

**Current system/workflow:**  
`handle_release_event(admin_confirmed=True)` opens one transaction and calls `_handle_release_event_tx()`. Inside that transaction it:

1. Finds the active program.
2. Calculates duty shifts/days using roster shift logic.
3. Updates `wbom_escort_programs` to `Completed`.
4. Backfills `wbom_attendance`.
5. Creates or updates one `fazle_payment_drafts` escort-payment row.
6. Upserts `escort_roster_entries` with `roster_status='completed'`.

**Current assessment:**  
The original P0 partial-release finding is substantially addressed in code. Remaining risk is operational/data-level: existing old completed programs and drafts may still need backfill/reconciliation.

#### Change 2 — Release Requires Admin Confirmation

**Previous system/workflow:**  
Employee release text or OCR release processing could be interpreted as part of the close/payment workflow, and the audit recommended making media/OCR draft-only.

**Current system/workflow:**  
`handle_release_event()` refuses to finalize unless `admin_confirmed=True`. OCR produces a draft for admin review; only an outgoing admin message containing `[RELEASE CONFIRMED]` reaches `handle_admin_release_confirmation()` and then finalizes.

**Current assessment:**  
This aligns better with the audit recommendation: media/OCR extracts candidate fields, while admin confirmation triggers the domain write.

#### Change 3 — Schema Breakages in Release/Payment Were Fixed

**Previous system/workflow:**  
The audit found two concrete breakages:

- `create_escort_payment_draft()` selected `shift_type`, but live `wbom_escort_programs` did not have that column.
- Release confirmation lookup joined `wbom_employees.contact_id`, but live `wbom_employees` did not have that column.

**Current system/workflow:**  
Current code no longer references `shift_type` in payment draft creation. It uses `shift`, `end_shift`, `day_count`, `food_bill`, and `conveyance`. Release confirmation lookup now uses normalized `employee_mobile`, with fallback to active program `escort_mobile`.

**Current assessment:**  
Both specific P0 schema/query issues appear fixed.

#### Change 4 — Settlement Draft Components Are More Structured

**Previous system/workflow:**  
Food and conveyance were mostly OCR draft estimates and not reliably persisted into the payment draft. Payment draft calculation could diverge from roster calculation.

**Current system/workflow:**  
`close_program()` writes `food_bill` and `conveyance` into `wbom_escort_programs`; `create_escort_payment_draft()` writes `gross_amount`, `food_bill`, `conveyance`, `advance_deduction`, and `expected_amount` into `fazle_payment_drafts`; release roster upsert copies those values into `escort_roster_entries`.

**Current assessment:**  
Structured settlement fields are now present in the release/payment path. Full unification is not complete because payroll still recomputes escort pay using a fixed `DEFAULT_PER_PROGRAM_RATE` and period-level WBOM advances.

#### Change 5 — WBOM-to-FPE Adapter Improved but Is Not Automatic

**Previous system/workflow:**  
The audit said `wbom_fpe_sync` existed, looked outdated, and was not automatically invoked from important WBOM write paths.

**Current system/workflow:**  
`sync_wbom_transaction()` now reads one `wbom_cash_transactions` row, resolves/creates an FPE employee, and calls FPE `create_transaction()` with a synthetic `wa_message_id` like `wbom:<transaction_id>`. `backfill_wbom_to_fpe()` can sync a recent range.

**Current assessment:**  
The adapter itself looks updated. The main gap remains: `finalize_payment()` inserts WBOM cash and updates the draft, but does not call `sync_wbom_transaction()`. Payroll `mark_paid()` also does not post to FPE.

#### Change 6 — Payroll Workflow Is Mostly Unchanged

**Previous system/workflow:**  
Monthly payroll used WBOM completed escort programs, fixed per-day program rate, and WBOM cash advances within the period.

**Current system/workflow:**  
`compute_run()` still computes:

```text
basic salary
+ completed escort day_count × DEFAULT_PER_PROGRAM_RATE
- WBOM advance transactions in month
= net salary
```

State transitions remain:

```text
draft -> reviewed -> approved -> locked -> paid
                    \-> cancelled
```

**Current assessment:**  
No major payroll unification change was found. The audit’s payroll recommendations still stand: integrate canonical FPE transactions, use unified settlement components/rates, and post paid payroll runs to the immutable ledger.

### 15.4 Current Priority List

| Priority | Item | Current recommendation |
|---|---|---|
| P0 | FPE canonical posting after payment approval | Call `sync_wbom_transaction()` or directly create FPE transaction inside `finalize_payment()` after WBOM insert, within a reliable idempotent flow. |
| P0 | Backfill old release/payment data | Reconcile old completed programs, expired payment drafts, missing `escort_employee_id`, missing attendance, and roster rows created before the transactional release fix. |
| P1 | Payroll settlement unification | Replace fixed payroll escort allowance with the same settlement components used by release/payment drafts, or make payroll read approved settlement rows. |
| P1 | Roster source-of-truth cleanup | Keep roster as a projection, or route edits through the operational escort program service. Avoid roster-only operational changes. |
| P1 | Employee identity reconciliation | Continue linking WBOM and FPE employees and prevent FPE auto-created employees from silently bypassing operational profile review. |
| P1 | Operational review UI | Add/review pages for release slip drafts, payment drafts, WBOM/FPE reconciliation, and program lifecycle timeline. |

### 15.5 Updated Final Assessment

The system is no longer in the exact same state as the 2026-06-13 audit. The most dangerous release workflow breakages have been addressed in the active code: release finalization is admin-gated, transactional, and writes structured settlement values into payment draft and roster records.

The broader architectural assessment remains mostly valid. Fazle Core still has overlapping WBOM, FPE, roster, payroll, draft, and WhatsApp subsystems. The next highest-value work is not another feature layer; it is wiring every approved money movement into the canonical FPE ledger, reconciling old data created before the fixes, and making payroll consume the same settlement truth that release/payment already writes.
