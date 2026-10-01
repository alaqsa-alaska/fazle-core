# SALARY QUALIFICATION + QUEUE-RBAC + 9ROUTER — VERIFIED CHECKPOINT

**Status:** `CHECKPOINT ESTABLISHED — BASELINE FOR UNCHANGED CODE`
**Date:** 2026-10-01
**Author:** Kilo (VS Code agent)
**Branch:** `feat/bridge1-2-conversation-continuity-20260902`
**HEAD:** `993c7f4f0f59e8143cb7348e8f5441ff13f09e9f`

This checkpoint records verified behavior, exact test results, and unresolved
provenance for three focused work streams: Queue-RBAC authorization snapshot
capture, 9Router integration (development-only), and salary document
reconciliation. It establishes the verified baseline for unchanged code.

All phone numbers are masked. No secrets, raw gateway keys, or credentials are
recorded in this document.

---

## 1. Changed files (complete working-tree diff)

### 1.1 Tracked modifications

| File | Change | Stream |
|---|---|---|
| `app/config.py` | 9Router settings block: `nine_router_url`, `nine_router_private_url`, `nine_router_data_path`, `nine_router_version`, `nine_router_credential_ref`, `nine_router_single_upstream_guaranteed=False` | 9Router |
| `app/unified_routing.py` | `RouteCandidate` validation for 9Router (physical model required, text-only, local VPS endpoint, dedicated credential ref); `_invoke_core_route` gates 9Router on `nine_router_single_upstream_guaranteed` | 9Router |
| `modules/outbound/__init__.py` | `_capture_customer_authorization_snapshot()` — auto-captures auth evidence for `customer-reply:*` purposes at enqueue; fail-closed to legacy on capture failure | Queue-RBAC |
| `modules/fazle_payroll_engine/routes.py` | Bridge-alias display-name enrichment on transaction list/detail endpoints | Queue-RBAC (alias) |
| `resources/ops/salary_structure.txt` | Provenance header, owner clarification (old short-letter historical), condition wording (`নিয়মিত উপস্থিতি`, `কর্মক্ষমতা অনুযায়ী`), `সর্বসাকুল্য` note, PF/insurance no-assumption note | Salary |
| `resources/ops/master_recruitment_employee_information.txt` | Salary table condition wording, `সর্বসাকুল্য` note, PF/insurance note, applicability + provenance notes beside salary section | Salary |
| `tests/db/test_outbound_authorization_revalidation.py` | 9 new customer-reply snapshot capture tests + helper refactor | Queue-RBAC |
| `tests/unit/test_bridge_poller_payment_relay_gate.py` | Monkeypatch style refactor (module attr vs string path) | Maintenance |
| `tests/unit/test_llm_provider_order.py` | Scope-isolation allowed-file list extended with new/changed files (incl. salary docs) | Maintenance |
| `tests/unit/test_payment_relay.py` | Monkeypatch style refactor | Maintenance |

### 1.2 New (untracked) files

| File | Purpose |
|---|---|
| `app/nine_router_provider.py` | 9Router chat-completion adapter (takes `route_api_key` as runtime param; never logs bodies/credentials) |
| `modules/fazle_payroll_engine/bridge_alias.py` | Bridge-alias phone → display-name lookup helper |
| `knowledge_base/06_developer_system/identity_role_agent/README.md` | Identity-Role Agent design-contract doc |
| `tests/unit/test_9router_adapter.py` | 9Router adapter unit tests |
| `tests/unit/test_9router_recruitment_qualification.py` | 9Router recruitment qualification tests (4 tests, incl. salary deterministic checks) |
| `tests/unit/test_fpe_bridge_alias.py` | Bridge-alias helper tests |

---

## 2. Verified behavior

### 2.1 Queue-RBAC snapshot capture (customer-reply purposes)

- `_capture_customer_authorization_snapshot()` captures current authorization
  evidence only for purposes prefixed `customer-reply:` (recruitment + employee
  AI replies); all other purposes (admin, protected, system, legacy) are
  unchanged and short-circuit to `None`.
- On capture failure (unverified/ambiguous identity, no matching scoped role,
  active hold, domain disabled, lookup error) the enqueue falls back to a legacy
  row, which sweep revalidation still denies — fail-closed no-send preserved.
- Tests cover: purpose scoping, authorized recruitment capture (snapshot rows
  populated, revalidation ALLOWED), authorized employee capture, unlinked
  fallback (legacy + denied), wrong-domain role denial (legacy context +
  CONTEXT_MISMATCH), internal-staff identity cannot enter customer flow.

### 2.2 9Router integration (development-only)

- `nine_router_single_upstream_guaranteed` defaults to `False`; with it `False`,
  `_invoke_core_route` returns `INVALID_REQUEST_OR_POLICY_REJECTION` — no Core
  9Router route can be used for customer-facing workloads until the guarantee is
  verified.
- `RouteCandidate` enforces: 9Router uses bounded physical-model transport,
  dedicated `nine_router_core_api_key` credential ref, explicit physical model ID
  (no general/coding/fast/auto aliases), text-only modalities, local VPS endpoint.
- No production flag/credential changes; `OUTBOUND_ENABLED=false` maintained.

### 2.3 Salary document reconciliation

- Provenance notes placed beside relevant salary sections (not EOF).
- Owner clarification recorded: old short-letter `9,000+500+500` structure is
  **historical, NOT current**; current Escort range is `10,000–15,000`
  (salary_policy.md Groups 1–3, 2026-06-23).
- Condition wording: Hajira bonus requires `নিয়মিত উপস্থিতি` (regular
  attendance); evaluation incentive is `কর্মক্ষমতা অনুযায়ী` (by performance).
- `সর্বসাকুল্য` noted as not guaranteed take-home pay; PF/insurance
  contribution/deduction method explicitly not to be assumed.
- No business values changed; source text preserved.

---

## 3. Exact test commands and counts

Test interpreter: `./venv/bin/python` (documented test environment; system python lacks `openai`).
DB env: `TEST_DB_PASSWORD` from `fazle-test-postgres` container (127.0.0.1:5433).

### 3.1 Salary + qualification suites (venv interpreter)

| Command | Result |
|---|---|
| `python3 -m pytest tests/unit/test_9router_recruitment_qualification.py -v` | **4 passed** |
| `python3 -m pytest tests/unit/test_recruitment_salary_context_routing_fix.py -v` | **19 passed** |
| `python3 -m pytest tests/unit/test_second_rebrand_and_recruitment_rag.py -v` | **11 passed** |
| `python3 -m pytest tests/unit/test_recruitment_answer_first.py -v` | **34 passed** |
| `python3 -m pytest tests/unit/test_recruitment_factcheck_normalization.py -v` | **67 passed** |
| Combined salary suites | **135 passed, 1 warning** |

Per-suite counts: 19 + 11 + 34 + 67 = 131 (four regression suites) + 4
(qualification) = **135 total**.

### 3.2 Complete verified baseline (all affected suites)

`TEST_DB_PASSWORD=<container password> ./venv/bin/python -m pytest \
  tests/unit/test_9router_recruitment_qualification.py \
  tests/unit/test_9router_adapter.py \
  tests/unit/test_fpe_bridge_alias.py \
  tests/unit/test_llm_provider_order.py \
  tests/unit/test_recruitment_salary_context_routing_fix.py \
  tests/unit/test_second_rebrand_and_recruitment_rag.py \
  tests/unit/test_recruitment_answer_first.py \
  tests/unit/test_recruitment_factcheck_normalization.py \
  tests/db/test_outbound_authorization_revalidation.py \
  tests/unit/test_bridge_poller_payment_relay_gate.py \
  tests/unit/test_payment_relay.py -v`

**Result: 232 passed** (includes 39 provider-order + 9 DB revalidation + 4
qualification + salary regressions + 9Router adapter + bridge alias + relay
gate suites).

Note: system python (`python3`) lacks the `openai` module — 18 provider-order
tests fail under it for that dependency reason alone; the venv interpreter is
the documented test environment and passes them all.

---

## 4. Salary qualification status

**QUALIFICATION = PARTIAL**

The 9Router salary test (`test_9router_does_not_invent_ordinary_absent_facts_like_salary_or_benefits`)
now contains four deterministic checks:
1. Approved fact (Escort training Group-1 range) accepted for its role/stage.
2. Unsupported permanent range rejected for training role.
3. Historical old-letter structure not presented as current.
4. Conditional bonus/incentive not promised as guaranteed take-home pay.

**Limitations:** the checks are regex-based figure+keyword detection scoped to
the four cases. They do not implement a general role+stage+component+conditions
validation engine. Prompt/reply pairs from live 9Router calls were
**NOT CAPTURED** as static artifacts.

---

## 5. Resolved and unresolved provenance

### 5.1 Resolved

- Old short-letter `9,000+500+500` structure: **historical** (owner
  clarification recorded in `salary_structure.txt`).

### 5.2 Unresolved — approval provenance UNVERIFIED

- **All-role permanent range** `17,000–24,700`: the KB `salary_policy.md`
  (Group 3, "স্থায়ী কর্মী — সকল পদ") records it with the label
  "Management-approved (2026-06-23)", but **no independent owner-approval
  record outside the KB file was located**. KB labels alone are NOT proof of
  owner approval.
- **30-working-day rule** (`salary_policy.md` SR-06 / `salary_structure.txt`
  `শর্ত: সম্পূর্ণ প্যাকেজের জন্য মাসে সর্বনিম্ন ৩০ কর্মদিবস`): the canonical
  KB entry is the only record; no earlier decision in governance artifacts was
  found. **UNVERIFIED** until an original approval record is produced.
- **Single-upstream guarantee** and **physical-model-only enforcement** for
  9Router: `nine_router_single_upstream_guaranteed=False`; approval/provenance
  **UNVERIFIED** (not in governance docs).

### 5.3 Prompt/reply capture

**NOT CAPTURED** — No prompt/reply pair from a live 9Router inference call was
collected as a static artifact in this session.

---

## 6. Production activation

**NOT PERFORMED** — No production restart, deployment, activation, gateway
change, or message send was executed in this session. `OUTBOUND_ENABLED=false`.

---

## 7. Boundary (baseline contract)

This checkpoint is the **verified baseline for unchanged code**. Future work
must recheck only:
- relevant changed files (Section 1);
- failed checks (none — all verified suites pass);
- unresolved items (Section 5);
- changed runtime/configuration (e.g., any future flip of
  `nine_router_single_upstream_guaranteed` or outbound flags).

**Do not** turn unresolved claims (Section 5) into approved policy without an
independent owner approval record.

---

## 8. Precise next task

1. **Produce original approval records** for: all-role permanent range
   `17,000–24,700`, the 30-working-day rule, and the 9Router
   single-upstream guarantee (or keep UNVERIFIED).
2. **Capture a live 9Router prompt/reply pair** (sanitized) as a static test
   artifact so grounding can be verified from real output rather than regex
   presence/absence.
3. If/when the above are resolved, extend the salary test from the four
   deterministic cases toward role+stage+component+conditions validation, and
   re-run the full suite.

---

## 9. Commit & push status

Filled at commit time: commit SHAs, branch, checkpoint path, push status.
