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
are current approved process semantics. The office address recorded in the
current Recruitment source is usable as a current address. The evidence also
supports that processing or joining fees may exist, that illegal bribes are not
allowed, and that financial difficulty or an instalment arrangement may be
discussed; this does not approve any exact fee amount or an absolute advance
requirement.

The applicant-facing conversation should therefore explain the next lifecycle
step naturally: clarify role interest, collect only the next needed intake
field, preserve identity and session state, and direct the person to office
review when a decision or verification is required. It should not promise a
vacancy, salary, training length, document list, duty schedule, benefit,
selection, or joining date unless that fact is later owner-approved.

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

The following subjects remain unresolved and deliberately have no approved
candidate-facing value in the register:

| Subject | Competing evidence | Existing sources | Why automatic resolution is unsafe |
|---|---|---|---|
| Available roles and current vacancies | Nine-role lists, seed vacancy replies, role-based historical flow | `resources/ops/recruitment_source_of_truth.txt`, `scripts/seed_recruitment_kb.sql`, `db/migrations/014_career_hr_dataset.sql` | A historical role list is not a current vacancy decision. |
| Salary by role and joining salary | Multiple role bands, package figures, training amounts | `resources/ops/recruitment_source_of_truth.txt`, `resources/ops/salary_structure.txt`, seed/migration SQL | Repetition does not establish owner approval and figures conflict. |
| Form/joining fee amount | 330, 1,000 minimum, 3,500, and instalment variants | `resources/ops/joining_fee_policy.txt`, `resources/ops/master_recruitment_employee_information.txt`, source/seed files | Fee existence is supported, exact amount and timing are not reconciled. |
| Recruitment contact and office hours/Friday | 01958-122300 vs 01958-122322; 09:00 vs 10:00; Friday variants | source, fee policy, seed/migration SQL | Candidate-facing contact and opening times need one current decision. |
| Duties, eligibility, documents, training, probation | Role-specific and general variants | source, employee/form documents, salary/seed/migration files | Applicability depends on role and current policy; legacy text conflicts. |
| Duty hours, weekly duty, overtime, leave, food/accommodation | Role and operational-condition variants | source, employee policy, salary/seed files | Conditions vary by assignment and lack current owner confirmation. |

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
