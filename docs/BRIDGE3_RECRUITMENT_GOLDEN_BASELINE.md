# Bridge3 Recruitment — Golden Baseline & Post-Completion Lifecycle

**Status:** verified against live production code + real production data, 2026-08-25.
**Known-good baseline commit:** `b7a302d` (P0 fix — cooldown silently dropped
active-session recruitment answers; see its own commit message for the full
incident). This is a diagnostic reference, **not** permission to
`git reset --hard` to it — later legitimate improvements must be preserved.

This doc has two parts: what currently happens after a candidate completes
intake (Task A, discovery only — no business policy was changed), and the
permanent regression protection around today's proven behavior (Task B).

---

## Part 1 — Current post-completion lifecycle (as-is, not as-designed)

```
Bridge3 candidate
  → name → age → area → job_preference → experience → phone_confirm
  → collection_step='complete', funnel_stage='scored'   [IMPLEMENTED, ACTIVE]
  → candidate receives INTAKE_COMPLETE_MSG               [IMPLEMENTED, ACTIVE]
  → admin/Owner notification                              [NOT IMPLEMENTED]
  → recruiter assignment / interview / hired-rejected     [NOT IMPLEMENTED — dead code only]
```

### A1 — What "scored" means

`modules/recruitment_flow._compute_score()`:
- Experience: 6+ years → +60, 3-5 → +40, 1-2 → +20, 0 → +0
- `job_preference` in `("Escort / Survey Scout", "Security Guard")` → +20
- All of `full_name, age, area, job_preference` present → +20
- Capped at 100; bucket: `hot` (≥70), `warm` (≥40), `cold` (<40)

Stored on both `fazle_recruitment_sessions` (score, score_bucket) and
`wbom_candidates` (same, via `_finalize_candidate()`). No later workflow in
the currently active code path reads or acts on the score or bucket —
confirmed by grep: zero live callers consume `score`/`score_bucket` beyond
writing them.

### A2 — Is there a candidate list? **YES**, two read surfaces, both pull-only

1. `GET /admin/recruitment` (`app/main.py:3140`) — raw listing over
   `fazle_recruitment_sessions` (id, phone, full_name, age, area,
   job_preference, score, score_bucket, funnel_stage, created_at).
2. `GET /api/recruitment/interested-candidates` (`app/main.py:1273`) — richer
   view joining inbound-message-detected interest with `wbom_candidates`,
   returns `missing_information`, `conversation_status`, per-candidate
   evidence. RBAC-gated (`require_api_key`).

Both require an admin to actively query them. **Neither is pushed anywhere.**

### A3 — Field map (verified against live schema + code, not assumed)

| Field | Source | Stored where | Required? | Available after complete? |
|---|---|---|---|---|
| full_name | candidate text, `_looks_like_name()` | `fazle_recruitment_sessions`, `wbom_candidates` | yes | yes |
| age | `_parse_age()` (Bengali+ASCII, DOB-rejecting) | both | yes | yes |
| area | free text (100 char cap) | both | yes | yes |
| job_preference | `_parse_job_preference()` | both | yes | yes |
| experience_years | `_parse_experience()` | both | no (defaults 0) | yes |
| join_date / confirmed_phone | later `COLLECTION_STEPS` entries | `fazle_recruitment_sessions` only | varies | yes (session), **not copied to `wbom_candidates`** — `_finalize_candidate()`'s UPDATE list does not include them |
| score / score_bucket | `_compute_score()` | both | n/a | yes |
| source bridge | ingestion (`source_bridge`) | `fazle_recruitment_sessions` | n/a | yes |
| conversation/thread | `fazle_candidate_conversations` (audit trail) | separate table | n/a | yes, via `_audit_candidate_conversation()` |
| documents / salary expectation | — | — | — | **not a tracked field anywhere in the live schema** |
| assigned_recruiter / assigned_at / next_follow_up_at | schema exists on `wbom_candidates` | `wbom_candidates` columns | — | **column exists, never populated by any live code path** (0 of 15 scored rows have a non-null value, confirmed live) |

### A4 — Candidate completion message

`INTAKE_COMPLETE_MSG` (`modules/recruitment_flow/__init__.py`), sent verbatim,
every time, no variation:

> আপনার তথ্য সফলভাবে সংগ্রহ করা হয়েছে।
> আমাদের টিম শীঘ্রই আপনার সাথে যোগাযোগ করবে।
> আরো জানতে: 01958 122300
> আবেদনের জন্য ধন্যবাদ।

This **promises the team will contact the candidate soon**. Given A5/A6
below, nothing in the live system currently acts on that promise
automatically — it is only true if an admin manually checks one of the two
read surfaces in A2. Flagging this gap; not changing the message or the
promise it makes — that is a business-policy decision for the Owner.

### A5 — Admin notification: **NO ACTIVE ADMIN COMPLETION NOTIFICATION**

Traced the entire completion transition (`intake_message()` →
`_apply_audited_transition()` → `_finalize_candidate()`): no
`outbound.enqueue()`, no WhatsApp send, no email, no SMS, no scheduler job
fires on completion. `modules/scheduler` has no job watching for newly
`scored` candidates (only `job_recruitment_recovery_sweep`, which targets
*stale unanswered* conversations — a different concern). Confirmed, not
assumed.

### A6 — Interview / joining / hiring workflow: **NOT IMPLEMENTED** (dead code exists)

`resources/recruitment.py` ("Sprint-3 WhatsApp Candidate Funnel" — the
original, since-superseded prototype) contains a *complete* designed
pipeline: `score_candidate()` → `assign_recruiter()` (→ `funnel_stage=
'assigned'`, creates a 48h reminder) → `advance_stage()` (→ `'hired'` /
`'rejected'`) → `get_recruitment_metrics()` / `get_pending_reminders()`.
**Zero live callers anywhere in the codebase** — confirmed by repo-wide
import search. It uses a different DB helper module (`from database import
...`, not `app.database`) consistent with genuinely predating the current
architecture. Live data confirms this: `wbom_candidates.funnel_stage` has
only ever taken two values in production — `collecting` (339 rows) and
`scored` (15 rows). Zero rows have ever reached `assigned`/`hired`/
`rejected`.

**Not implementing or wiring any of this now** — this is a business-policy
decision (who assigns recruiters, what "hired" means operationally, whether
the dead prototype's design is even the one wanted) that belongs to the
Owner, not something to infer and silently build.

---

## Part 2 — Permanent Golden Regression Baseline

### B7 — Production invariants (tested, not just documented)

1. An active recruitment-session inbound must never be silently discarded
   solely because it arrived within the normal reply cooldown.
2. A successfully parsed/persisted intake field must advance state (or
   retain it with an explicit, traceable reason — e.g. field-lock re-entry).
3. A previously accepted field must not be repeatedly requested without a
   correction/validation reason.
4. A semantic/Hermes failure must not cause avoidable candidate silence
   when deterministic continuation exists.
5. Recruitment salary questions must not automatically become employee
   payroll queries merely because salary-related words appear —
   `recruitment_eligibility()`'s `active_session` check already wins over
   any keyword/intent classification (verified in code and by test).
6. A completed valid intake must remain capable of reaching
   `complete`/`scored`.
7. No recruitment semantic change (V2, Hermes, answer-first) may gain
   financial transaction authority — verified in WS5: zero recruitment/
   Hermes-attributed rows in `fpe_cash_transactions`, ever.

### Golden regression suite

`tests/unit/test_bridge3_recruitment_golden_baseline.py` — 19 tests covering:
Bengali+ASCII age parsing (incl. DOB-rejection), the exact `b7a302d`
cooldown/active-session regression, field-lock re-entry, step-never-regresses,
semantic-V2-failure fallback, mixed age+salary answer-first (the Owner's
explicit acceptance case, live-verified in production during the P0
incident), full completion → `scored`, and the ignored-new-visitor
counter-case. Sanitized from real production failure patterns — no real
phone numbers or candidate data in the fixtures.

Works alongside (not duplicating) existing coverage:
`test_recruitment_cooldown_lead_creation_fix.py`,
`test_recruitment_answer_first.py`,
`test_recruitment_salary_context_routing_fix.py`,
`test_recruitment_age_override.py`,
`test_recruitment_phone_confirm_bengali_digits.py`, and the offline 14-case
Phase C regression corpus
(`tests/unit/data/recruitment_regression_corpus/`).

### Deployment gate integration

`scripts/restart_gate.sh` — new **Check 4b** (golden suite + the five
directly-related existing suites) and **Check 4c** (Phase C 14/14 corpus),
both blocking, both run on every `--restart`/`--check-only` invocation
alongside the pre-existing Check 4. Scoped to recruitment-relevant suites
only, not the full test tree, so unrelated changes elsewhere don't pay this
cost — but any change touching `bridge_poller`, `message_router`,
`recruitment_flow`, `recruitment_turn_decision`, recruitment behavior in
`hermes_dispatch`, recruitment fact-checking, session state, or recruitment
outbound routing goes through the same restart gate as everything else in
this repo and cannot bypass this check.

### Runtime health signals (reused, not new)

Already-existing, already-sufficient log lines (no new logging added):
- `[bridge3] cooldown would have blocked ... proceeding to full processing`
  — active-session cooldown bypass firing (the exact fix in production).
- `[bridge3] cooldown suppressed reply but opened recruitment funnel`
  — the unchanged no-session path.
- `[recruit] {phone} scored={score} ({bucket})` — session reaching completion.
- `[recovery_sweep] ...` — stale/unanswered recovery cycles (separate concern).

### Development is not frozen

`NEW CAPABILITY >= CURRENT VERIFIED CAPABILITY`, not "never change anything."
Future work may improve wording, semantic understanding, field capture,
scoring, or add the admin/interview workflow found missing in Part 1 — it
must not regress any invariant in this doc or any test in the golden suite.
A failing golden test means a real, already-proven capability would be lost,
not that the change is forbidden outright.
