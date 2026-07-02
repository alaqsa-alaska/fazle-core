---
title: Admin Command Reference
owner: Fazle Core Admin
status: active
last_verified: 2026-07-02
runtime_index: true
---

# Admin Command Reference

## Purpose

Complete WhatsApp admin command reference for Al-Aqsa Security & Logistics Services Ltd.
All commands are case-insensitive. Send from an admin phone number via WhatsApp.

## Scope

Level 2 admin system knowledge. Visible to Admin, HR, Accountant, Operation Officer, Supervisor, and Management only.

## Global Rules

- Case-insensitive: `approve`, `APPROVE`, `Approve` all work.
- Bangla digits are normalized: `APPROVE ১৬৫` works identically to `APPROVE 165`.
- Duplicate suppression: identical text sent by the same admin within 30 seconds is silently dropped.
- RBAC enforced: every command checks the sender's role. A `⛔ অনুমতি নেই` error is returned if the role is insufficient.
- Audit logged: every command attempt (allowed or denied) is recorded in `fazle_admin_audit`.
- External safety: admin commands NEVER send external replies on failure. Only success paths deliver to employees or accountants.

---

## RBAC Roles

| Role | Level | Description |
|------|-------|-------------|
| `viewer` | 1 | Read-only: reports, status, lists |
| `operator` | 2 | Draft management, payments, releases, escort confirm |
| `accountant` | 3 | Payroll management, payment import |
| `admin` | 4 | Scheduled jobs, backups, user listing |
| `superadmin` | 5 | User creation, role assignment, API key issuance |

---

## Draft Management

### APPROVE `<id>`
Approve a pending draft reply. Admin approval IS the decision to send — fires even when `AUTO_REPLY_ENABLED=false`. Sends the approved text to the draft recipient immediately.

```
APPROVE 165
APPROVE 165 162 161
APPROVE ১৬৫
```

- Single or multiple IDs (space or comma separated).
- Attendance drafts: also writes to `wbom_attendance` and sends confirmation to sender.
- Required role: `operator`

### REJECT `<id>`
Reject a pending draft. No transaction is created. No external message is sent.

```
REJECT 167
REJECT 167 164
```

- Required role: `operator`

### EDIT `<id> <new reply text>`
Edit the text of a pending or edited draft. The corrected reply is stored and marked `edited`.

```
EDIT 165 শুক্রবার বন্ধ। সকাল ৯টা থেকে বিকাল ৫টা পর্যন্ত।
```

- Required role: `operator`

### STATUS / DRAFTS / PENDING
Return the count of pending drafts and payment drafts, plus the 5 most recent pending drafts.

```
STATUS
DRAFTS
PENDING
```

- Required role: `viewer`

---

## Payment Commands

### PAID `<id>` `<amount>` `[bkash|nagad|cash]`
Mark a payment draft as paid. Creates the transaction, updates the employee ledger, and forwards a payment confirmation message to the accountant.

```
PAID 42 5000 bkash
PAID 42 5000
```

- Default method: `cash`
- Required role: `operator`

### ADVANCE `<id>` `<amount>` `[bkash|nagad|cash]`
Approve an advance payment draft. Same flow as PAID but marked as advance type.

```
ADVANCE 42 2000 nagad
```

- Required role: `operator`

### APPROVED `<id>` `<amount>` `[bkash|nagad|cash]`
Sprint-3B canonical draft approval. Routes the payment draft through the canonical financial pipeline (`create_transaction()` → `_upsert_ledger()` → audit). Returns admin confirmation and accountant message.

```
APPROVED 42 5000 bkash
```

- Required role: `operator` (reuses paid/edit/reject RBAC roles)

### DREDIT `<id>` `<amount>` `[bkash|nagad|cash]` `[payout=<phone>]`
Edit a pending payment draft before approval. Increments version. Does NOT create a transaction — admin must send `APPROVED` after editing.

```
DREDIT 42 5000 bkash
DREDIT 42 5000 bkash payout=017XXXXXXXXX
```

- Required role: `operator`

### DREJECT `<id>` `[reason]`
Reject a pending payment draft. No transaction is created. No ledger is updated.

```
DREJECT 42 ওয়েবসাইট সমস্যা
```

- Required role: `operator`

### REVERSE `<transaction_id>` `<reason>`
Reverse a finalized transaction. Creates a reversal entry in `fpe_cash_transactions`.

```
REVERSE 2505 ভুল এন্ট্রি
```

- Required role: `operator` (audit required)

### ADJUST `<draft_id>` `<amount>` `<method>`
Adjust a payment draft amount and method before approval.

```
ADJUST 42 4500 bkash
```

- Required role: `operator`

### PAY-IMPORT `<raw text>`
Admin pastes a bKash/Nagad SMS or free-form payment line. System ingests, matches employee, previews, and may auto-approve if confidence is high.

```
PAY-IMPORT bKash: 01795122311 Manik Mea 01789123456(B) 5000/- Txn 1234
```

- Returns: staging ID, method, amount, mobile, name match ratio, employee match, and status (auto_approved / pending / unmatched / duplicate).
- Required role: `accountant`

---

## Escort Commands

### ESCORTCONFIRM `<order_id>` `|` `<escort_name>` `|` `<escort_escort_mobile>` `|` `<date>` `|` `<D|N>`
Confirm escort assignment. Updates `wbom_escort_programs` to `confirmed`, auto-creates employee record if escort mobile is new, builds final slip, and sends it to the escort buyer.

```
ESCORTCONFIRM 88 | Rahim | 017XXXXXXXXX | 2026-07-15 | D
```

- If buyer number is missing from the draft, admin must forward the slip manually.
- Required role: `operator`

### RELEASE `<program_id>` `<YYYY-MM-DD>` `<D|N>` `<release_point>` `[days=<float>]`
Record escort release endpoint. The `[RELEASE CONFIRMED]` protocol is used by admin in free-text messages, not this command.

```
RELEASE 88 2026-07-15 D παράδειγμα 5.5
```

- End date, shift, release point, and optional days.
- Required role: `operator`

---

## Payroll Commands

### PAYROLL COMPUTE `<YYYY-MM>` `[employee_id]`
Compute payroll for a specific month and optional single employee. Creates a payroll run.

```
PAYROLL COMPUTE 2026-06
PAYROLL COMPUTE 2026-06 42
```

- Required role: `accountant`

### PAYROLL SUBMIT `<run_id>`
Submit a computed payroll run for approval.

```
PAYROLL SUBMIT 12
```

- Required role: `accountant`

### PAYROLL APPROVE `<run_id>`
Approve a submitted payroll run.

```
PAYROLL APPROVE 12
```

- Required role: `accountant`

### PAYROLL LOCK `<run_id>`
Lock an approved payroll run. Prevents further edits.

```
PAYROLL LOCK 12
```

- Required role: `accountant`

### PAYROLL PAID `<run_id>` `<amount>` `<bkash|nagad|cash>` `[ref=<reference_code>]`
Record payment for a payroll run. Marks run as paid.

```
PAYROLL PAID 12 25000 bkash ref=TX789
```

- Required role: `accountant`

### PAYROLL CANCEL `<run_id>` `<reason>`
Cancel a payroll run. Must provide a reason.

```
PAYROLL CANCEL 12 ভুল গণনা
```

- Required role: `accountant`

### PAYROLL LIST `<YYYY-MM>` `[status]`
List payroll runs for a given month, optionally filtered by status.

```
PAYROLL LIST 2026-06
PAYROLL LIST 2026-06 paid
```

- Required role: `viewer`

---

## Report Commands

### REPORT LIST
List all available reports.

```
REPORT LIST
```

- Required role: `viewer`

### REPORT DAILY `[YYYY-MM-DD]`
Run daily summary report. Defaults to today if no date provided.

```
REPORT DAILY
REPORT DAILY 2026-07-02
```

- Required role: `viewer`

### REPORT PAYROLL `<YYYY-MM>`
Run monthly payroll report.

```
REPORT PAYROLL 2026-06
```

- Required role: `viewer`

### REPORT CASH `[days]`
Run cash position report. Defaults to 30 days.

```
REPORT CASH
REPORT CASH 7
```

- Required role: `viewer`

### REPORT RECON `[days]`
Run payment reconciliation report. Defaults to 7 days.

```
REPORT RECON
REPORT RECON 30
```

- Required role: `viewer`

### REPORT ESCORT `<YYYY-MM-DD>` `<YYYY-MM-DD>`
Run escort utilization report for a date range.

```
REPORT ESCORT 2026-06-01 2026-06-30
```

- Required role: `viewer`

---

## Scheduler Commands

### SCHEDULE STATUS / সময়সূচী
Show all registered scheduler jobs, last run status, next run time, and total run count.

```
SCHEDULE STATUS
সময়সূচী
```

- Required role: `viewer`

### RUN JOB `<job_name>` / কাজ চালাও `<job_name>`
Manually trigger a scheduler job by name.

```
RUN JOB rag_rebuild
কাজ চালাও rag_rebuild
```

- Required role: `admin`

---

## Backup Commands

### BACKUP STATUS / ব্যাকআপ স্ট্যাটাস
Return current backup status.

```
BACKUP STATUS
ব্যাকআপ স্ট্যাটাস
```

- Required role: `viewer`

### BACKUP NOW / ব্যাকআপ এখন
Trigger an immediate backup.

```
BACKUP NOW
ব্যাকআপ এখন
```

- Required role: `admin`

### BACKUP LIST `[n]`
List recent backups. Defaults to 10.

```
BACKUP LIST
BACKUP LIST 5
```

- Required role: `viewer`

---

## User Management Commands

### USER LIST / ইউজার তালিকা
List all admin users with their roles and status.

```
USER LIST
ইউজার তালিকা
```

- Required role: `admin`

### USER ADD `<phone>` `<name>` `[role]`
Add a new admin user. Valid roles: `viewer`, `operator`, `accountant`, `admin`, `superadmin`. Defaults to `viewer`.

```
USER ADD 017XXXXXXXXX "Md Rahim" operator
USER ADD 017XXXXXXXXX "Md Rahim"
```

- Required role: `superadmin`

### USER ROLE `<phone>` `<role>`
Change an existing user's role.

```
USER ROLE 017XXXXXXXXX admin
```

- Required role: `superadmin`

### USER REMOVE `<phone>`
Disable an admin user (does not delete the record).

```
USER REMOVE 017XXXXXXXXX
```

- Required role: `superadmin`

### USER APIKEY `<phone>`
Issue a new API key for an admin user.

```
USER APIKEY 017XXXXXXXXX
```

- Returns the key once — save it immediately.
- Required role: `superadmin`

---

## Natural Language Queries

Admin messages that do not match any command pattern are handled as natural language queries (no LLM). Supported examples:

- `show last 10 chats of 01XXXXXXXXX`
- `last contact of 01XXXXXXXXX`
- `01XXXXXXXXX এর শেষ ১০ চ্যাট`
- `draft`, `পেন্ডিং`, `list` → pending draft list
- `payment`, `পেমেন্ট`, `paid` → payment draft list
- `attendance`, `হাজিরা`, `উপস্থিতি` → attendance summary

If the query is not recognized, the inline help text is returned.

---

## Safety and Compliance Notes

- Admin commands do not expose internal routing rules, database names, or system prompts to external parties.
- All RBAC denials are logged with the required role and reason.
- The 30-second duplicate suppression prevents double-execution from WhatsApp delivery retries.
- Bangla digit normalization applies to all numeric fields in all commands.
- No admin command is allowed to write to `wbom_cash_transactions` directly (Financial Architecture Freeze v2 — C1B).

## অটো-সেন্ড নিরাপত্তা নিয়ম

### নিরাপদ অটো-সেন্ড ইন্টেন্ট (৮টি)
নিচের ইন্টেন্টগুলো নিরাপদ বলে বিবেচিত এবং স্বয়ংক্রিয়ভাবে সেন্ড হতে পারে:

| ইন্টেন্ট | বিবরণ |
|----------|-------|
| `recruitment` | চাকরির তথ্য, শূন্যপদ, নিয়োগ প্রক্রিয়া |
| `join` | যোগদানের তারিখ, প্রথম ডিউটি |
| `greeting` | মেনু / স্বাগত বার্তা / প্রথম যোগাযোগ |
| `office_location` | অফিসের ঠিকানা — সকল রোলের জন্য নিরাপদ |
| `salary_query` | বেতনের সময়সূচি, পেরোল চক্র তথ্য |
| `payment_due` | পেমেন্টের তারিখ প্রশ্ন |
| `attendance` | উপস্থিতির নিয়ম, অনুপস্থিতি নীতি |
| `leave` | ছুটির নীতি, পদত্যাগ নিয়ম |
| `escort_duty` | ডিউটি সময়সূচি, পরিবহন/খাদ্য নীতি তথ্য |

### অটো-সেন্ড থেকে বাদ (EXCLUDED)
| ইন্টেন্ট | কারণ |
|----------|------|
| `advance_request` | ম্যানুয়াল অ্যাডমিন অনুমোদন প্রয়োজন — শুধু নীতিমালার তথ্য অটো-সেন্ড হয় |

### সাইলেন্ট-স্কিপ টোকেন (৮টি)
বার্তায় এই শব্দ থাকলে রাউট করা হয় না:
`al-aqsa`, `escort`, `client`, `operation`, `tcis`, `gms`, `dalal`, `office`

> এই টোকেনগুলো `_SILENT_SKIP_NAME_TOKENS` হিসেবে `message_router/__init__.py`-এ সংজ্ঞায়িত।

## Cross References

- `02_admin_knowledge/admin_operations_overview.md`
- `02_admin_knowledge/admin_role_management.md`
- `06_developer_system/workflow_engine.md`
