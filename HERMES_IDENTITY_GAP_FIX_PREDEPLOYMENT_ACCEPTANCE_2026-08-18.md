# Pre-Deployment Acceptance & Credential-Remediation Pass

**Date:** 2026-08-18
**Scope:** Read-only verification only. No gated action taken — no restart, no migration apply,
no commit, no push, no secret value printed or written anywhere in this report.
**Precedes:** deployment of the 5-item fix from `HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md`,
tracked in `project_hermes_identity_gap_fix_implemented_20260818` (memory).

---

## 1. Credential Compromise — `FAZLE_CORE_API_KEY`

**Status: compromised, must be treated as such.** Exposed in full in this session's terminal
output on 2026-08-18 (a `sed` redaction pattern assumed `.env`-style `KEY=value` syntax against a
YAML file's `KEY: value` syntax and silently failed to match). No value is reproduced anywhere in
this document.

### 1a. Every consumer identified (name/path only, no values read or printed)

Systematic search across every `.env`/config file on the box (`grep -q` existence checks only,
never `-A`/`-C`/`cat`/`source`):

| Location | Role |
|---|---|
| `~/.hermes/config.yaml` → `mcp_servers.fazle-core.env.FAZLE_CORE_API_KEY` | **The only place the live value is configured.** Confirmed via a Python `yaml.safe_load()` pass that printed key *paths* only, never values. |
| `fazle-mcp/fazle_core_client.py` | Reads it via `os.environ.get("FAZLE_CORE_API_KEY", "")` at call time — the sole code-level consumer. |
| `fazle-mcp/audit_tools.py`, `fazle-mcp/identity_tools.py` (new, this session), `fazle-mcp/monitoring_tools.py`, `fazle-mcp/metrics_tools.py`, `fazle-mcp/send_whatsapp_tools.py` | Import `fazle_core_client` — indirect consumers, not separate holders of the value. |
| `fazle-core` DB (`fazle_admins.api_key_hash` for the `hermes-mcp-service` identity, phone `01958122304`, role `viewer`) | The **issuer/verifier** side — `require_api_key` checks incoming requests against this hash via `modules.rbac.get_admin_by_api_key()`. This is what actually needs to change to invalidate the old key. |

No `.env` file anywhere on the box (`core/.env`, `fazle-mcp` has none, `hermes-runner/.env`,
`assistant-platform/.env`, `assistant-platform/backend/.env`, `secure-env-backup/runtime-services.env`,
nor any of the 4 backup/archive `.env` copies found) contains this key — confirmed by
existence-only `grep -q` against each, zero matches outside `~/.hermes/config.yaml`. **One
consumer process, one config location, one DB-side identity to rotate.**

### 1b. Existing, already-built rotation mechanism (found, not written)

`core/scripts/create_hermes_mcp_admin.py` already has a `--rotate` flag, and its own docstring
explicitly names this exact scenario: *"Pass --rotate to explicitly generate and store a
brand-new key for the existing identity (invalidates the old one immediately — only do this if
you actually intend to rotate it, e.g. suspected exposure)."* No new tooling needed.

Mechanics confirmed by reading the script: `rbac.issue_api_key(PHONE)` **unconditionally
overwrites** `fazle_admins.api_key_hash` (a single column, not a list) — the old key stops
verifying the instant this runs, before `~/.hermes/config.yaml` is updated. The new plaintext key
is printed to stdout **exactly once** — the script's own docstring warns not to let that output
linger in shell history/scrollback/logs.

### 1c. Safe rotation procedure (documented here, not executed)

Ordering matters because of the "invalidates immediately" behavior above — a naive
run-then-copy-then-restart sequence leaves fazle-mcp calling with a dead key for however long the
human takes to copy-paste it. Safest sequence:

1. **Issue the new key without it ever reaching this session's output or any shell history file.**
   Redirect stdout directly to a file the agent never reads, e.g.:
   ```
   cd /home/azim/core && ./venv/bin/python3 scripts/create_hermes_mcp_admin.py --rotate > /tmp/rotate_out.$$ 2>&1
   ```
   This one command both invalidates the old key (DB write) *and* captures the new one — so steps
   2 onward must follow immediately.
2. **Write the new value into `~/.hermes/config.yaml` programmatically**, in the same breath,
   without any intermediate step displaying it — e.g. a small Python snippet that extracts the
   `API key (copy this...):` line from `/tmp/rotate_out.$$`, loads the YAML, sets
   `mcp_servers['fazle-core']['env']['FAZLE_CORE_API_KEY']`, and writes it back with `yaml.safe_dump`
   — all inside one non-echoing script, so the value transits memory only, never a terminal.
3. **Shred the temp file immediately**: `shred -u /tmp/rotate_out.$$` (or equivalent) — do not
   leave it for a later cleanup step.
4. **Restart the fazle-mcp MCP connection** (however Hermes reconnects to `mcp_servers.fazle-core`
   — confirm the exact mechanism before executing; per `config.yaml`, the process is spawned as
   `/home/azim/fazle-mcp/venv/bin/python3 /home/azim/fazle-mcp/server.py`) so the new env value is
   picked up. The old key is already dead from step 1, so every fazle-mcp call fails closed
   (visible `403`, not silent) between step 1 and step 4 — acceptable, brief, and safe (fails
   closed, not open).
5. **Verify**: one read-only call through the reconnected MCP session (e.g. `list_my_capabilities()`)
   succeeds. If it 403s, the config.yaml write in step 2 didn't take — re-check before assuming
   rotation is complete.
6. Confirm no other process still holds the old key cached in memory (only one consumer process
   exists per §1a, so restarting it in step 4 is sufficient — no second process to hunt down).

**Not executed. This is the procedure for when rotation is authorized**, kept separate from this
session's implementation work per the "no gated action" instruction.

---

## 2. Re-audit of the six changed files

### `knowledge_base/03_ai_identity/identity_overview.md`
Doc-only. Diff reviewed line-by-line: every corrected claim (function name, dual confidence
scale, step structure, removed fabricated step) is traceable to a specific `identity_brain`
line number cited in the same edit. No claim in the new text lacks a citation. Zero code risk by
construction (markdown only).

### `modules/user_role/__init__.py`
Diff is exactly one inserted docstring block (8 lines) above the existing module docstring. No
function body, signature, import, or logic line touched — confirmed via `git diff`, which shows
only `+` lines, zero `-` lines beyond the diff context. `detect_role()`'s zero-caller status
re-confirmed by re-running the repo-wide caller grep this session — unchanged from the original
audit.

### `app/main.py` (three hunks)
1. `whatsapp_messages_lookup`'s SELECT: one line added (`identity_role, identity_confidence,
   intent_detected,`), zero lines removed. The `where_clause`/`params`/filter logic above it is
   untouched — the fix is scoped to exactly the column list, nothing else in that ~60-line
   function was touched.
2. `api_resolve_identity`: 6 lines of route body. Traced line-by-line — see §3.
3. `api_classify_intent`: 3 lines of route body. Traced line-by-line — see §3.

No other route, dependency, or helper in `app/main.py` was touched. `python -c "import ast;
ast.parse(...)"` syntax check passes.

### `fazle-mcp/identity_tools.py` (new file)
40 lines total, two functions, each a single `return core.get(...)` call. Zero conditionals, zero
data transformation, zero state. See §3.

### `fazle-mcp/server.py`
Diff isolated via `git diff server.py`: one new import line + two new `@mcp.tool()` wrapper
functions (18 lines), each body a single `return identity_tools.X(...)` call — no logic added at
the registration layer either. (The surrounding diff noise — `audit_get_auto_reply_settings`,
`get_settings_status`, `approve_draft`, etc. — predates this session; confirmed these functions
already existed in the working tree before today's changes, not introduced now — see §6.)

### `db/migrations/066_ai_read_employees_fpe_fallback.sql`
Full line-by-line review — see §5. **Not applied.**

---

## 3. `resolve_identity`/`classify_intent` — thin-exposure verification

Traced the complete call chain for both, end to end:

```
identity_tools.resolve_identity(phone, text)
  → core.get("/api/identity/resolve", {"phone": phone, "text": text})   [identity_tools.py:30]
    → GET /api/identity/resolve  (app/main.py:1234)
      → result = await detect_identity(phone, text)                     [app/main.py:1241, calls modules/identity_brain/__init__.py:161 UNCHANGED]
      → return {k: v for k, v in result.items() if k not in (...)}      [app/main.py:1242, a filter, not a transform]
```

```
identity_tools.classify_intent(text)
  → core.get("/api/intent/classify", {"text": text})                    [identity_tools.py:38]
    → GET /api/intent/classify  (app/main.py:1246)
      → return {"intent": classify(text)}                               [app/main.py:1250, calls modules/intent/__init__.py:113 UNCHANGED]
```

**No new classification/decision logic exists anywhere in this chain.** Every role, confidence
score, source, and intent label returned still originates from `identity_brain.detect_identity()`
and `modules.intent.classify()` exactly as they existed before this session — confirmed by `git
diff` showing **zero changes** to `modules/identity_brain/__init__.py` or `modules/intent/__init__.py`
(neither file appears in `git status --porcelain` at all). The only operations added anywhere in
the chain are: an HTTP GET, an `await`, and a dict-comprehension filter (see §4).

---

## 4. PII-stripping boundary verification

The strip is exactly one line: `app/main.py:1242` —
`{k: v for k, v in result.items() if k not in ("bkash_number", "basic_salary")}`.

- This is a **dict comprehension**, which builds a brand-new `dict` object. It performs no `del`,
  no `.pop()`, no mutation of `result` or of anything `result` references.
- `result` is itself a fresh dict returned fresh by `await detect_identity(...)` on every call
  (confirmed: `identity_brain._build()`/`_build_contact()` construct and return a new dict literal
  each invocation — `identity_brain/__init__.py:565-628` — nothing cached or shared across calls).
- `identity_brain/__init__.py` has **zero diff** this session (confirmed above) — every other
  caller of `detect_identity()` (`message_router`, `bridge_poller`, `app.main`'s recruitment gate,
  `social_auto_reply`) still receives the complete, unmodified dict including `bkash_number`/
  `basic_salary`, exactly as before.
- The filter exists **only inside `api_resolve_identity()`'s return statement** — it is not a
  module-level function, not imported anywhere else, and not reachable from any other code path.

**Verdict: the strip is scoped correctly — output-boundary only, zero internal data effect.**

---

## 5. `/api/whatsapp/messages/lookup` consumer check

Every reference to this route path across `core`, `fazle-mcp`, `hermes-runner`, and
`assistant-platform` (`grep -rl`, all four repos):

| Consumer | Contract dependency | Broken by this change? |
|---|---|---|
| `fazle-mcp/audit_tools.py::audit_lookup_whatsapp_messages` | Reads `result.get("messages", [])`, passes through `pii_mask.mask_pii()` | **No.** `mask_pii()`'s `PII_FIELDS` allowlist (`whatsapp_number`, `sender_number`, `escort_mobile`, `phone`, `target_id`, `canonical_phone` — read directly from `fazle-mcp/pii_mask.py:20`) does not include `identity_role`/`identity_confidence`/`intent_detected`; masking is a name-allowlist pass-through, so the new fields flow through untouched and unmasked (correctly — they're classification labels, not identifiers). |
| `knowledge_base/00_governance/management_decisions.md` | Documents the route's *creation* (bound filters, PII-masked) — no column-list contract asserted | Not applicable — historical log entry, not a live consumer. |
| `tests/unit/test_whatsapp_messages_lookup_route.py` (new, this session) | Asserts the new columns are present | N/A — this is the verification, not a pre-existing consumer. |

No other route, script, or frontend calls this endpoint. The change is strictly additive (3 new
keys in the response dict; all 5 previously-present keys — `classification`, `workflow_triggered`,
plus the unchanged filter/pagination params — are untouched), so no existing consumer's expected
keys disappear or change type.

---

## 6. Migration 066 — line-by-line review

**Not applied. Static/manual review only** (see also the environment note at the end of this
section on why an execution-based dry run wasn't possible here).

| Line(s) | What it does | Privilege/scope assessment |
|---|---|---|
| `CREATE OR REPLACE VIEW ai_read_employees AS` | Redefines an existing view, same name | **No new object created.** Per Postgres semantics, `CREATE OR REPLACE VIEW` preserves the view's OID, owner, and existing ACL (grants) as long as the output column list is compatible (same names/types/order, or new columns appended at the end) — confirmed this view's column list is byte-for-byte identical to the pre-066 version (7 columns, same names, same order, same types); only the *row set* changes via the added `UNION ALL`. |
| First `SELECT ... FROM wbom_employees e` | Unchanged from the pre-066 view (migration 012) — byte-identical to the original, no `WHERE` clause, same as before | No behavior change to this branch. |
| `UNION ALL SELECT ... FROM fpe_employees fe LEFT JOIN wbom_employees w ...` | Adds rows for `fpe_employees` records with no matching `wbom_employees` row | **Read-only.** `SELECT`/`LEFT JOIN` only — no `INSERT`/`UPDATE`/`DELETE`, no `ALTER TABLE`, no trigger, no function call with side effects. |
| `WHERE fe.status = 'active'` | Restricts the new branch to active payroll-engine employees | Mirrors `identity_brain._lookup_employee()`'s own fallback filter exactly (`identity_brain/__init__.py:395`) — **not broader** than what the live identity resolver already reads. |
| `AND NOT EXISTS (SELECT 1 FROM wbom_employees w2 WHERE w2.employee_id = fe.wbom_employee_id)` | Prevents double-counting an `fpe_employees` row already linked to a `wbom_employees` row (which the first `SELECT` already returns) | Correctness guard, not a privilege concern. |
| Column list in both branches | `employee_id, employee_name, employee_mobile, designation, joining_date, status, basic_salary` | **`bkash_number` deliberately excluded** (same field the new `/api/identity/resolve` route also strips) — no new sensitive column exposed beyond what migration 012 already exposed (`basic_salary` was already in the pre-066 view). |
| Grants | **None issued in this file.** | Correct — the existing `GRANT SELECT ON ai_read_employees TO fazle_ai_reader` from migration 012's dynamic grant-loop (`012_ai_readonly_views.sql`'s `DO $$ ... GRANT SELECT ...` block) still applies after a `CREATE OR REPLACE`, because the view's OID is unchanged. **No write capability is granted anywhere in this migration — it is SELECT-only, on both branches, to both source tables, exactly as the resolver it mirrors already does.** |

**Explicit rollback SQL** (inlined in the migration file's own header comment, self-contained —
does not require cross-referencing migration 012):
```sql
CREATE OR REPLACE VIEW ai_read_employees AS
SELECT
    e.employee_id,
    e.employee_name,
    e.employee_mobile,
    e.designation,
    e.joining_date,
    e.status,
    e.basic_salary
FROM wbom_employees e
ORDER BY e.employee_name;
```
Instant, no data impact (rollback is itself just another `CREATE OR REPLACE VIEW`; no `DROP`, no
re-`GRANT` needed).

**Consumer/contract sweep** (every reference to `ai_read_employees` across all four repos):
- `assistant-platform/backend/src/routes/fazleBridge.js`'s `/employees` route — `SELECT ... WHERE
  status ILIKE $1` — case-insensitive, compatible with the view's title-case `Active`/`Inactive`
  values (my new branch produces the same casing convention via the same `CASE WHEN ... THEN
  'Active' ELSE 'Inactive'` pattern `identity_brain` itself uses). **Compatible, no regression.**
- `assistant-platform/backend/src/tools/fazleTools.js`'s `get_employees` case — uses `WHERE status
  = $1` (case-**sensitive**) against a lowercase literal. **Pre-existing, unrelated bug** (same
  class already fixed once in `fazleBridge.js` 2026-08-10 and in `ai_readonly_tools.py` 2026-08-17,
  but never ported here) — this consumer returns zero rows today, before and after migration 066,
  for a reason that has nothing to do with this migration. Flagged for awareness; **out of scope,
  not touched.**
- `modules/ai_readonly_tools/__init__.py::get_employee_list()` (Ollama's separate AI subsystem,
  not Hermes) — uses `WHERE status ILIKE $1`, same case-insensitive pattern as `fazleBridge.js`.
  Compatible.
- `tests/integration/test_ai_readonly_tools_employee_status_casing.py` — asserts exact casing
  behavior against a real Postgres test schema. **Could not execute in this environment** — the
  local test-DB (`172.20.0.3:5432`, per `Makefile`'s `TEST_DB_URL`) is unreachable/unconfigured
  here (`password authentication failed for user "postgres"`, confirmed pre-existing and
  unrelated to this session — see §7). This test schema is rebuilt from the real migration files
  (`tests/conftest.py::_setup_test_schema`), so it **will** pick up migration 066 automatically
  once a reachable test DB exists — recommend running it as part of migration readiness (§8)
  before applying to production.
- No hardcoded row-count assumption found anywhere for this view (checked `docs/MODULE_MAP.md`,
  `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md`, `knowledge_base/module_registry.yaml`
  — none assert a row count or column-order dependency beyond what's preserved above).

**One tooling note, checked and dismissed:** the IDE's inline SQL diagnostics flagged
`CREATE OR REPLACE` and the `::date` cast as syntax errors ("Expecting ALTER", "Expecting
CONVERSATION" — classic T-SQL/SQL-Server parser messages). Confirmed false positive: migration
012 — already applied and live in production — uses the identical `CREATE OR REPLACE VIEW` and
`::DATE`/`::INT` cast syntax throughout (`012_ai_readonly_views.sql:14,24,31,45,...,117`). The
IDE's SQL linter is evaluating against the wrong dialect; not a real defect in this file.

---

## 7. Test results

### Targeted (this session's new tests)
| Suite | Result |
|---|---|
| `core/tests/unit/test_identity_intent_routes.py` | 5/5 pass |
| `core/tests/unit/test_whatsapp_messages_lookup_route.py` | 2/2 pass |
| `fazle-mcp/tests/test_identity_tools.py` | 5/5 pass |

### Regression — fazle-mcp full suite
`./venv/bin/python -m pytest tests/` → **207 passed, 3 failed.**

**The 3 failures, proven pre-existing and unrelated:**
- All three are in `tests/test_send_whatsapp_tools.py::TestModeAndConfirmGate`.
- That test file's only import is `import send_whatsapp_tools` (`tests/test_send_whatsapp_tools.py:11`)
  — it does not import `server`, `identity_tools`, or anything touched this session.
- The failing assertions (`assertIn("confirmation", result["error"])`, etc.) fail because
  `send_whatsapp_tools.py:105` now returns `"source_bridge is required -- ..."` for the exact
  input these tests use — a validation check inside `send_whatsapp_tools.py` that predates this
  session (confirmed: `modules/identity_brain/`, `identity_tools.py` and this session's edits
  never touch `send_whatsapp_tools.py`; `git status` shows it was already modified before today —
  file mtime is ~3.7 days older than "now" in this environment, consistent with an earlier,
  separate session's uncommitted work, not today's).
- **Root cause: the test file wasn't updated when an earlier session added the `source_bridge`
  requirement to `send_whatsapp_tools.py`.** Unrelated to identity/role/intent, out of this
  session's scope, not fixed here.

### Regression — fazle-core, broader identity-adjacent sweep
Ran individually and in combination to check for interaction effects:

| File | Alone | Result |
|---|---|---|
| `test_identity_brain.py` | Yes | 23 pass (+2 pre-existing DB-unreachable errors, see below) |
| `test_escort_client_identity_convergence.py` | Yes | 37 pass (+2 pre-existing DB-unreachable errors) |
| `test_message_router_identity_seed.py` | Yes | 1 pass |
| `test_admin_directives_routes.py` | Yes | 10 pass |

**Environment-level, pre-existing, unrelated to this session:** every file shows
`[test-schema] FATAL: schema setup failed: password authentication failed for user "postgres"` at
collection time, and any test needing `test_db_pool` (real Postgres) errors out — the local
test-DB (`172.20.0.3:5432`) is not reachable/configured with credentials in this sandbox. This
affects `test_ai_readonly_tools_employee_status_casing.py` (§6) and 2 tests each in
`test_identity_brain.py`/`test_escort_client_identity_convergence.py`'s LID-resolution classes.
Not caused by, and not fixable from, this session's changes.

**One additional pre-existing issue found during this sweep (disclosed, not fixed, out of
scope):** running `test_identity_brain.py` and `test_escort_client_identity_convergence.py` in
the **same** pytest process — in that order — produces 3 assertion failures in the latter file
that do **not** occur when either file runs alone, or in combination with this session's 2 new
test files. Isolated via 4 controlled pairwise runs (see below); confirmed **not** connected to
anything changed this session — `modules/identity_brain/`, `modules/escort/` are both untouched
(`git status --porcelain` clean for both):

| Combination | Result |
|---|---|
| new tests + escort convergence | Clean (only the 2 DB-unreachable errors) |
| `test_identity_brain.py` + escort convergence | **3 new FAILED** (test-isolation leak between two pre-existing files) |
| `test_message_router_identity_seed.py` + escort convergence | Clean |
| `test_admin_directives_routes.py` + escort convergence | Clean |

This is a real, pre-existing test-isolation gap (likely an unrestored `unittest.mock.patch`/module
state leaking from `test_identity_brain.py`) between two files this session never touched — flagged
for a future session, not investigated further or fixed here (out of scope).

---

## 8. Deployment Readiness Verdict — 4 independent gates

| Gate | Status | Basis |
|---|---|---|
| **1. Credential rotation readiness** | 🔴 **NOT READY — action required first** | `FAZLE_CORE_API_KEY` is confirmed compromised (§1). Rotation procedure is fully specified (§1c) and reuses an already-built, already-idempotent script (`--rotate` flag) — no new tooling needed. Blocked only on execution, which needs explicit authorization per the "no gated action" instruction. **This gate should be closed first, independent of and before the other three** — the new `resolve_identity`/`classify_intent` tools depend on the same `FAZLE_CORE_API_KEY` fazle-mcp already uses for `audit_lookup_whatsapp_messages`, so deploying Step 4 while the key is known-compromised means deploying new capability on top of a credential that needs replacing anyway — wasteful ordering. |
| **2. Migration readiness** | 🟡 **Reviewed and safe by static analysis; one verification step outstanding** | Line-by-line review complete (§6): no write capability granted, no broader-than-necessary access, scope mirrors `identity_brain`'s own existing query exactly, rollback SQL is explicit and self-contained. **Not yet execution-tested** — the local integration test (`test_ai_readonly_tools_employee_status_casing.py`) that would prove no live regression couldn't run in this sandbox (test DB unreachable, pre-existing environment gap, §7). Recommend running that test against a reachable test/staging Postgres — or a manual `psql --single-transaction -f 066....sql` + `ROLLBACK` dry run — before applying to production. |
| **3. Restart/runtime readiness** | 🟢 **Ready, pending gates 1 and 4** | All code changes tested and passing (§7); no import/syntax errors (`ast.parse` clean); no route-path collisions (§2); PII-stripping boundary verified correct (§4); consumer contracts checked and compatible (§5). The only reason not to restart immediately is sequencing — restarting fazle-mcp now would put the new `resolve_identity`/`classify_intent` tools into service *using the compromised key* (gate 1) before it's rotated, and this repo's own restart discipline (`scripts/restart_gate.sh`) expects a deliberate, gated moment, not an incidental one. |
| **4. Git commit/push readiness** | 🟢 **Ready, no blockers found**, execution still requires the user's explicit go-ahead per standing instruction | All 6 changed files re-audited individually (§2), no unintended scope creep found, no secrets in any diff (checked — `git diff` output for every touched file was reviewed and contains no credential-shaped strings), 3 pre-existing test failures proven unrelated (§7) so won't be mistaken for a regression introduced by this commit. `fazle-mcp` is a public upstream repo per prior-session memory — recommend commit message/PR description explicitly note the migration file is included but **not applied**, so a reviewer doesn't assume it's already live. |

### Recommended exact execution order

1. **Rotate `FAZLE_CORE_API_KEY`** (§1c) — closes gate 1. Independent of everything else; do this
   regardless of whether/when the rest of this deployment proceeds, since the exposure already
   happened.
2. **Run the migration-readiness verification** (§6/§8 gate 2) — either the integration test
   against a reachable test DB, or a transaction-wrapped `psql` dry run against a non-production
   target. Do not apply to production yet.
3. **Restart `fazle-core.service`** via `scripts/restart_gate.sh --restart` (brings the new
   `/api/identity/resolve`/`/api/intent/classify` routes and the widened `/api/whatsapp/messages/lookup`
   live) — only after step 1, so these routes come up already using the rotated key.
4. **Reconnect/restart the `fazle-mcp` MCP process** (brings `resolve_identity`/`classify_intent`
   live for Hermes) — immediately after step 3, same reasoning.
5. **Apply migration 066** to production (`psql "$DATABASE_URL" -f
   db/migrations/066_ai_read_employees_fpe_fallback.sql`) — only after step 2's verification
   passes. This step is independent of steps 3-4 and could also run before them if preferred; it
   does not depend on the rotated key or the restarted services.
6. **Run the end-to-end verification checklist** from the original implementation plan (curl the
   new routes, confirm `list_my_capabilities()` lists the two new tools, re-check
   `audit_lookup_whatsapp_messages` returns populated `identity_role`/`intent_detected`, confirm
   an FPE-only employee now appears via `get_employees`).
7. **Commit and push**, once 1-6 are confirmed live and correct — last, not first, so the commit
   message can truthfully state "deployed and verified" rather than "should work."

**No step above has been executed. This section is the plan for when you authorize each gate.**
