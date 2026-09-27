> Governance control only: this file defines authority and reconciliation rules; it is not one of the two canonical Recruitment knowledge documents.

# Recruitment lifecycle and fact authority

Status: living reconciliation narrative; candidate-facing values are not
approved merely because an older source calls itself authoritative.

This document describes Recruitment as an employee lifecycle rather than a
question-and-answer catalogue. A person may first express interest, be
identified as an applicant, select a role, provide eligibility and intake
information, submit documents, undergo interview or review, receive a staff
selection decision, complete role-appropriate training or practical learning,
pass probation/readiness review, join, receive an assignment or deployment,
and then become an active employee. The existing `modules/recruitment_flow`
session and `modules/recruitment_turn_decision` state machine remain the
operational intake path; a candidate claim does not itself establish selection,
verification, joining, or active employment.

## What the current evidence supports

The current register (`knowledge_base/00_governance/recruitment_fact_authority.json`)
contains the fact-level decision for each subject. Only `current_approved`
facts have an answer value. `historical`, `unverified`, `conflicting`, and
`superseded` records remain evidence for audit or context and are not current
candidate-facing policy.

The lifecycle model and the rule that selection/joining requires office review
are current approved process semantics. The approved Recruitment brand, office
address, office hours/contact, role taxonomy, eligibility, required documents,
training/probation, salary role/stage values, fees, and duty hours/shifts are
recorded in the register. The current vacancy list, Escort/Survey post-training
range, fee deduction/refund calendar, overtime rate, and other role- or
employment-scoped benefits remain unresolved or narrowly pending. Approved
facts do not authorize automatic wage deductions, formal verification, or
hiring.

The applicant-facing conversation should therefore explain the next lifecycle
step naturally: clarify role interest, collect only the next needed intake
field, preserve identity and session state, and direct the person to office
review when a decision or verification is required. It may state current
approved facts with their conditions, but must not promise a current vacancy,
the conflicting Escort/Survey post-training range, the fee schedule, overtime
rate, unapproved benefits, selection, or joining date.

## Source reconciliation

`resources/ops/recruitment_source_of_truth.txt` remains valuable source
evidence for the existing route, office/address wording, role descriptions,
intake sequence, and owner directives. It is no longer treated as a single
undivided authority block. `master_recruitment_employee_information.txt` and
`employee_policy_rules_joining_form.txt` provide policy/form and lifecycle
evidence. `salary_structure.txt`, `joining_fee_policy.txt`, the migration and
seed SQL, generated KB rows, and older reply texts are retained as historical
or conflicting evidence where their values disagree.

Exact KB, BM25, SentenceTransformer, and Qdrant retrieval remain retrieval
mechanisms, not policy authorities. A relevant passage can establish that
evidence exists, while the fact register determines whether its value is
approved, historical, conflicting, unverified, or superseded.

## Owner Decision Required

The following subjects remain unresolved or narrowly pending and deliberately
have no exact approved candidate-facing value for the listed detail:

| Subject | Competing evidence | Existing sources | Why automatic resolution is unsafe |
|---|---|---|---|
| Current vacancies | Nine-role list and historical vacancy replies | resources/ops/recruitment_source_of_truth.txt, seed/migration SQL | The approved nine-role taxonomy is not a current vacancy decision. |
| Escort/Survey post-training salary | 12,000–24,000 vs 12,000–17,000 | resources/ops/salary_structure.txt, source/seed files | Prior K2 salary values are retained; only this range conflicts. |
| Fee deduction/refund calendar | BDT 500 monthly proposal and refund arrangement; exact calendar absent | resources/ops/joining_fee_policy.txt, owner decision | The owner approved the principle and amounts, not the precise schedule. |
| Role-duty detail beyond approved hours/shifts | Role-specific operational variants | source, employee policy, salary/seed files | Do not turn operational evidence into a complete public duty promise. |
| Overtime rate, leave, food/accommodation, other benefits | Role and employment-condition variants | source, employee policy, salary/seed files | These conditions remain role- or employment-scoped without a current owner decision. |

The owner should update the register through a future approved editing path with
fact key, value, source, approver, effective date, conditions, and superseded
fact-level authority columns, so Step 2 does not pretend that editing a reply
row activates policy. No new frontend or production schema migration is added
here.

## Retrieval and Hermes boundary

The authority annotator adds fact-level markers to existing source/RAG
evidence. `shared.reply_policy` converts those markers into
`ContextEvidence` blocks inside the existing `ProviderContextPackage`.
Approved values may be used naturally; unresolved records retain provenance,
competing evidence, and a safe confirmation action but no answer value. This
same metadata is attached to Exact KB/BM25/hybrid RAG result records. Similarity
only ranks evidence; it cannot promote a conflicting passage to current policy.
history. The existing `fazle_knowledge_base` admin editing path has no


## Admin-approved policy updates

The current operational value of a mutable Recruitment fact is selected by
latest valid approval, not by the newest timestamp. An authorized Admin first
saves a draft. Only a valid Approve action may make the proposed value
`current_approved`; the prior approved value is retained as
`superseded`/historical with provenance. Drafts, failed saves, imported legacy
files, applicant statements, and unverified evidence do not override the
current approved value.

The Fazle-Core Admin Control Plane is the controlled update path for this
mechanism. Its first implemented slice manages Recruitment authority through
the existing JSON register with locked atomic writes and revision checks.
Role/relationship classifications remain distinct from business/job roles: a
contact may have multiple relationship/access classifications and operational
roles, and those concepts must not be collapsed into one field.
