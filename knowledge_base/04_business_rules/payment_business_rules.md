# Payment Business Rules

## Source Of Truth
- Admin manually sends all payment messages to accountant WhatsApp.
- If admin message reaches accountant, payment is considered complete.
- Ledger/database updates immediately after accountant handoff.
- Future system-mediated approval may exist, but accountant handoff remains completion trigger.

## Verification Rules
- Confirm identity.
- Confirm bKash/Nagad/cash method and number every time.
- Require duty selfie, duty slip, or release slip if needed.
- Apply duplicate prevention.

## Advance Rule
৳500-৳1,000 is a normal guideline, not a hard cap. Admin/management may approve exceptions.

## Cross References
- ../02_admin_knowledge/admin_payment_handling.md
- ../05_workflows/payment_workflow.md

---

## Advance Trigger Keywords

### Purpose
When an inbound message contains any of the following 18 phrases, the system detects it as an advance request and forces the response to draft (never auto-sends).

### Trigger Phrases (18 total)

**Standard advance requests:**
- অ্যাডভান্স চাই
- অ্যাডভান্স দরকার
- অ্যাডভান্স লাগবে
- অগ্রিম চাই
- advance চাই
- অগ্রিম টাকা দরকার

**Emergency / urgency variants:**
- জরুরি টাকা
- জরুরি অর্থ
- ইমার্জেন্সি টাকা
- emergency money

**Medical / personal crisis:**
- অসুস্থ
- ডাক্তার
- হাসপাতাল
- চিকিৎসা
- চিকিৎসার জন্য

**Family emergency:**
- পারিবারিক সমস্যা
- পরিবারে সমস্যা
- বাড়িতে সমস্যা

**Business Rule:** All 18 phrases force the response to admin draft regardless of the auto-send intent gate. Advance requests always require admin approval.

**Source Module:** `modules/payment_workflow`
**Source Function:** `ADVANCE_KEYWORDS`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-43)
**Management Authority:** Production evidence; documented 2026-06-22

---

## Advance Request Force-Draft Phrases (Short Form)

In addition to the 18 advance keywords, the bridge_poller separately checks these 5 short-form Bangla phrases in any message:

- অ্যাডভান্স চাই
- অ্যাডভান্স দরকার
- অ্যাডভান্স লাগবে
- অগ্রিম চাই
- advance চাই

**Business Rule:** These 5 phrases trigger the `_ADVANCE_REQUEST_PHRASES` force-draft check at the bridge_poller level, before any intent classification. They act as an early gate.

**Source Module:** `app/bridge_poller`
**Source Function:** `_ADVANCE_REQUEST_PHRASES`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-11)

---

## Payment Draft Lifecycle

### Purpose
Document the lifecycle of a payment/advance draft and when it expires.

### Draft Expiry Rules

| Draft Type | Expiry | Cleanup Job |
|---|---|---|
| Payment draft (`fazle_payment_drafts`) | 24 hours after creation | `combined_draft_cleanup` (runs hourly) |
| General draft (`fazle_draft_replies`) | Configurable via `draft_ttl_hours` | `draft_ttl_cleanup` (runs every 30 min) |

**Business Rule:** A payment draft that has not been approved or rejected within 24 hours is automatically expired by the scheduler. Admin must re-request the payment if the draft expires.

**Validation:** Expired drafts are marked with `status='expired'`, not deleted. The record remains for audit purposes.

**Source Module:** `modules/scheduler`, `modules/outbound`
**Source Function:** `job_combined_draft_cleanup()`, `expire_stale_drafts()`
**PKCA Report:** 07_scheduler_coverage_report.md, 09_state_machine_coverage_report.md

---

## Payment Draft States

| State | Meaning |
|---|---|
| `pending` | Draft created, awaiting admin action |
| `sent` | Admin ran PAID/ADVANCE command; payment finalized; accountant notified |
| `rejected` | Admin ran REJECT command |
| `expired` | 24-hour TTL passed without admin action |

**Source Module:** `modules/payment_workflow`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-03)
