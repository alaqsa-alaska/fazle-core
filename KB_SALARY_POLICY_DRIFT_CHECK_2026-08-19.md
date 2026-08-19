# KB vs. Live Payroll/Policy Consistency Check — 2026-08-19

Read-only Phase 0 of `HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_
AUDIT_2026-08-19.md`'s approved 4-task implementation plan. No code, KB,
or DB change made. Compared salary/policy figures in
`knowledge_base/04_business_rules/` and `knowledge_base/05_workflows/`
against the live runtime fallback text and grepped for any code-level
enforcement of the figures found.

## Finding: one real, evidence-backed drift — `salary_policy.md`'s joining-fee section is stale

**`knowledge_base/04_business_rules/salary_policy.md`** self-declares
(header, line 18): *"This schedule is the single authoritative source for
all salary figures across the AI system. Any hardcoded fallback, LLM
prompt, or KB article that quotes salary figures must match this
schedule exactly."* Its `last_verified` is **2026-06-24**.

Its **SR-05 — Joining Fee and Form Fee** section (`:70-74`) still reads:

> জয়েনিং ফি: ৳৩,৫০০ (ঘুষ বা জামানত নয়; ৬ মাস চাকুরি পর ফেরত দেওয়া হয়)
> ফর্ম ফি: ৳৩৩০ (এককালীন)
> জয়েনের সময় কমপক্ষে: ৳১,০০০ + ৳৩৩০ = ৳১,৩৩০
> বাকি টাকা মাসে ৳৫০০ করে বেতন থেকে কাটা হয়

— i.e. **at least ৳1,330 must be paid upfront at joining**, remainder
deducted at ৳500/month.

**`knowledge_base/04_business_rules/recruitment_business_rules.md`**
(`last_verified: 2026-07-21`, one month newer) has an explicit
"Approved Deposit/Fee canonical text" (`:171-179`) that says it
**"supersedes the older 'pay ~৳1,330 upfront, refund after 6 months'
framing everywhere it previously appeared"**:

> চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না। যদিও প্রসেসিং ফি,
> জয়েনিং ফি ইত্যাদি অন্যান্য প্রতিষ্ঠানের মত আমাদের এখানেও আছে। তবুও
> আমরা **অগ্রীম গ্রহনের পরিবর্তে** মাসিক বেতনের সাথে অর্থাৎ বেতন থেকে
> কিস্তিতে কাটার ব্যবস্থা রেখেছি।

— i.e. fees exist but are **never collected upfront**, only deducted
from salary in installments. This directly contradicts SR-05's "at least
৳1,330 at joining time."

**Which one is actually live**: grepped `modules/knowledge_base/__init__.py:90-97`
— the real runtime fallback template for this exact question (code
comment: *"2026-07-21: single canonical deposit/fee text (Owner-approved),
also the DB row `b11_no_fee`"*) matches `recruitment_business_rules.md`'s
newer no-upfront framing **verbatim**, not `salary_policy.md`'s SR-05.
Also grepped `modules/` and `shared/` repo-wide for `1330`/`১,৩৩০`/
`joining_fee` — every hit is an intent-detection keyword list (recognizing
that a candidate *asked about* a fee), never an actual payroll deduction
enforcing ৳1,330 or ৳500/month. **No code implements SR-05's figures at
all** — this is a documentation-only drift, not a live financial-logic
bug.

**Base salary range figures** (৳10,000–15,000 training,
৳12,000–17,000 escort/duty-based, ৳17,000–24,700 permanent) are
**consistent** across `salary_policy.md`, `salary_business_rules.md`, and
the live runtime fallback — no drift found there.

## Assessment

Low severity in practice (the customer-facing path already uses the
correct, newer text), but real: `salary_policy.md` is the one document
in this KB that explicitly claims to be *the* single authoritative
source, and one of its own sections is a month stale and describes a
policy the Owner-approved 2026-07-21 canonical text explicitly says was
superseded. Anyone or anything reading `salary_policy.md` directly
(rather than the fallback template/DB row) — including a future
RAG-based Hermes recruitment tool — would get the wrong answer on
joining-fee timing.

## Not done (per this task's read-only scope)

No edit to `salary_policy.md`, no DB/KB write. Per
`[[feedback_mutable_business_facts]]`: this is a business-fact
reconciliation, not a code fix — flagging for the Owner to confirm which
framing is current and, if `recruitment_business_rules.md`'s is correct
(the evidence above suggests it is, given it's newer, explicitly marked
as superseding, and matches what's actually live), update or remove
`salary_policy.md`'s SR-05 section accordingly.
