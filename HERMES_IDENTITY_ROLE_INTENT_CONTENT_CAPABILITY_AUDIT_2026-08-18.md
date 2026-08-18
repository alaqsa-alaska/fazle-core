# Hermes — Identity / Role / Intent / Content Capability Audit

**Date:** 2026-08-18
**Type:** Read-only investigation. No code, config, flags, DB, or service state changed.
**Requested scope:** Can Hermes reliably determine Identity → Role → Intent → Content for an
incoming message using its current MCP tools, or must it consume fazle-core's existing
classification pipeline? Where existing tool results were checked against code/DB/docs, and
mismatches found, each is root-caused with file/line/table evidence — not left as "doesn't match."

**Builds on same-week work:** `HERMES_PAYROLL_WORKFLOW_GAP_AUDIT_2026-08-18.md` (same method:
read code first, verify against KB, verify against Hermes's actual tool surface, don't assume).
This audit covers the identity/role/intent/content layer instead of the payment-draft layer.

---

## 1. Current Architecture Relevant to Identity/Role/Intent/Content

There are **three separate identity/role resolvers** in this codebase, not one:

| System | File | Status | Table(s) | Confidence scale |
|---|---|---|---|---|
| `identity_brain.detect_identity(phone, text)` | `modules/identity_brain/__init__.py:161` | **Live, canonical** — called from `message_router`, `bridge_poller`, `app/main.py`'s recruitment gate, `social_auto_reply` | `fazle_contact_roles`, `wbom_employees`, `fpe_employees`, `fpe_cash_transactions`, `wbom_attendance`, `wbom_escort_programs`, `wbom_contacts`, env `ESCORT_CLIENT_PHONES` | integer 0–100 |
| `user_role.detect_role(phone)` | `modules/user_role/__init__.py:79` | **Dead code — zero callers anywhere in the repo** (verified by repo-wide grep, see §8) | `wbom_employees`, `wbom_contacts` (joined to `wbom_relation_types`) | float 0.0–1.0 |
| `role_classifier.get_user_context(phone)` | `modules/role_classifier/__init__.py:45` | Live but scoped to one admin web endpoint only (`/api/users/{phone}`, `app/main.py:4246`) — not part of WhatsApp message processing | `user_profiles` (a fourth, unrelated identity table) | n/a |

`identity_brain.detect_identity()` is a 9-step priority cascade (admin settings →
`fazle_contact_roles` seed row, ordered by `priority DESC` → `wbom_employees`/`fpe_employees` →
gated cash/attendance operational evidence → escort roster → env escort-phone allowlist →
contact book → text-hint candidate detection → escort-content regex → `unknown`). It never
raises; always falls back to `_unknown()`.

**Intent:** `modules/intent/__init__.py::classify(text)` — a pure, stateless function (regex →
direct-substring → `rapidfuzz` fuzzy match → `unknown`), 11 categories, no DB access, no I/O.
Trivial to call in isolation.

**Content/Role fusion point:** `modules/message_router/__init__.py::process_message()`
(`__init__.py:352`–`1187`, ~830 lines, 15 numbered stages) is the only place all four —
Identity (`detect_identity()` call at line 428), Role (hardcoded stage-by-stage role branching),
Intent (`intent.classify()` call at stage 5, line 716), and Content (KB retrieval / reviewed-reply
memory / AI fallback, stages 12–15) — are fused into one orchestration run. There is no smaller
public function that returns "just the role" or "just the content answer" without running the
full message-processing side effects (DB writes, draft creation, sends).

**Persisted decision log:** `modules/bridge_poller/__init__.py` writes the *actual* per-message
result of this pipeline into `wbom_whatsapp_messages.identity_role`,
`.identity_confidence`, `.intent_detected`, `.workflow_triggered` (`_save_message()` line 1828,
`_finalize`-style update line ~1767). This is the one place a caller could read back "what did the
live pipeline decide for this message" without re-deriving it — see §6/§9 for why Hermes's one
tool that reads this table doesn't actually surface these columns.

---

## 2. Existing Code Paths (does Hermes need to build new logic?)

No. Identity, Role, and Intent determination already exist, are live, and are exercised
continuously in production:

- `modules/identity_brain` — canonical identity/role resolver (confirmed live via caller grep).
- `modules/intent` — canonical intent classifier (pure function).
- `modules/message_router` — canonical orchestrator that ties identity+role+intent+content together for a real WhatsApp turn.
- `modules/recruitment_detector`, `modules/recruitment_flow` — recruitment-vertical intent/session layer built on top of the above.
- `modules/contact_roles/routes.py` — admin CRUD over `fazle_contact_roles`, the seed table `identity_brain` reads first.
- `modules/number_identity`, `modules/phone_normalizer` — phone canonicalization `identity_brain` delegates to.

Two systems exist that look like alternatives but are **not** what's live: `modules/user_role`
(dead, 0 callers) and `modules/role_classifier` (live but for an unrelated admin feature, a
different table, a single caller). Building new Hermes-side classification logic would create a
**fourth** parallel system in a codebase that already has three, two of which have already drifted
apart from each other and from the KB doc that describes them (§8–§9). This is a real, repeatable
failure pattern here — not hypothetical.

---

## 3. Hermes Tools Currently Available

Full tool list read directly from `/home/azim/fazle-mcp/server.py` (confirmed via
`list_my_capabilities()`'s own introspection mechanism at line 53, and by reading every
`@mcp.tool()` decorator in the file — 45 tools total). Relevant subset:

| Tool | Backing path | Reaches identity/role/intent code? |
|---|---|---|
| `get_contacts(limit)` | assistant-backend `fazleBridge.js` → `ai_read_contacts` view → `wbom_contacts` | No — unfiltered list, no phone param |
| `get_employees(status)` | → `ai_read_employees` view → `wbom_employees` only | No — unfiltered list, no phone param, **and misses `fpe_employees`-linked employees** (§9) |
| `get_recent_messages(limit)` | → `ai_read_recent_messages` view → `wbom_whatsapp_messages` + `wbom_contacts` join | No — exact-string join, no phone-variant matching (§9) |
| `audit_lookup_whatsapp_messages(phone, ...)` | `fazle_core_client` → `/api/whatsapp/messages/lookup` (`app/main.py:1160`) → `wbom_whatsapp_messages` | Partially — phone-parameterized and correctly normalized, but **selects the wrong columns** (§9) |
| `get_knowledge_base(category)` | → `ai_read_kb_articles` | Category-only, not query+role+intent aware like the real KB stage |
| `draft_whatsapp_reply`, `send_whatsapp_message`, `approve_draft` | write tools | Downstream of identity/role, don't determine it |

**No tool in fazle-mcp calls `identity_brain.detect_identity()`, `intent.classify()`,
`user_role.detect_role()`, `role_classifier.get_user_context()`, or
`message_router.process_message()` — directly or indirectly.** Verified by grepping every
`@mcp.tool()` function body in `server.py`/`audit_tools.py`/`domain_reports.py` for these names:
zero matches.

---

## 4. Required Tools (gap list)

| Needed capability | What it should wrap | Why it doesn't exist yet is a real gap |
|---|---|---|
| `resolve_identity(phone, text)` | `identity_brain.detect_identity()` directly | Already read-only, already safe (never raises), already the canonical logic — a thin wrapper is the entire implementation cost |
| `classify_intent(text)` | `intent.classify()` directly | Pure function, zero DB/network access, zero risk |
| Corrected message lookup | Fix `/api/whatsapp/messages/lookup`'s SELECT to include `identity_role`, `identity_confidence`, `intent_detected` (not just the dead `classification` column) | The route and the MCP tool already exist — only the column list is wrong |
| `get_contact_by_phone(phone)` / `get_employee_by_phone(phone)` | Parameterized, phone-variant-aware single lookups | Current tools are unfiltered listings; Hermes cannot ask "who is this number" directly today |
| Schema/permission inspection tool | `\d`, `has_table_privilege()`-style read | Needed for exactly the kind of investigation this report just did — not available to Hermes itself today (I had to use direct `psql` outside any Hermes tool) |

---

## 5. Tool-by-Tool Capability Mapping

| Required Capability | Required Tool/Function | Hermes has it? | Tool Name | Existing Code Access? | Evidence | Gap |
|---|---|---|---|---|---|---|
| Resolve sender identity | `identity_brain.detect_identity()` | **No** | — | Exists in code, unexposed | `fazle-mcp/server.py` full tool list, no match | Missing entirely |
| Classify message intent | `intent.classify()` | **No** | — | Exists in code, unexposed | Same | Missing entirely |
| Read the pipeline's own identity/intent decision for a message | `wbom_whatsapp_messages.identity_role/.intent_detected` | **Available but insufficient** | `audit_lookup_whatsapp_messages` | Partial — reaches the table, wrong columns | `app/main.py:1210-1212` selects `classification` (dead) not `identity_role`/`intent_detected` (live, populated) | Available but insufficient |
| Look up a specific phone in the contact/employee registry | `identity_brain._lookup_contact/_lookup_employee` | **Available indirectly, incomplete** | `get_contacts`, `get_employees` | Indirect via `ai_read_*` views | `ai_read_employees` = `wbom_employees` only, omits `fpe_employees` fallback `identity_brain` checks (`identity_brain/__init__.py:386-401`) | Available indirectly / insufficient |
| KB / content retrieval with role+intent context | `modules/rag` | **Available but insufficient** | `get_knowledge_base` | Category filter only | `server.py:92-96` | Available but insufficient |
| Full end-to-end message processing (identity+role+intent+content in one call) | `message_router.process_message()` | **Exists in code, not exposed** | — | Has real side effects (writes, sends) — exposing it raw would be dangerous | `message_router/__init__.py:352` | Dangerous / should not be exposed as-is; a read-only "dry-run" variant would be the safe form |
| Schema/permission introspection | `\d`, `has_table_privilege()` | **Missing entirely** | — | Had to use direct `psql` outside Hermes's tool surface for this audit | This report's own method | Missing entirely |

---

## 6. Actual Runtime/Test Results

Live-verified against the running system today (`fazle-core.service` and the assistant-backend
bridge both returned HTTP 200 on health checks). Because this session has no MCP client wired to
`fazle-mcp` itself (only `claude.ai` connectors are available as deferred tools here — confirmed
via the tool list), I could not invoke Hermes's actual MCP tool calls turn-by-turn as Hermes would.
Instead I verified the **backing data layer directly** — the same layer every fazle-mcp tool
ultimately reads — using the repo's own designated read-only role (`FAZLE_AI_READER_DB_URL`,
the same credential fazle-mcp's `assistant-backend` bridge uses):

- **Permission boundary confirmed real, not just visible catalog metadata**: a direct `SELECT` against
  the raw table `wbom_whatsapp_messages` using the reader role returned
  `ERROR: permission denied for table wbom_whatsapp_messages` — confirming the "10 named views
  only, no raw tables" boundary documented in `assistant_bridge_client.py`'s docstring is actually
  enforced at the DB grant level, not just claimed in a comment. This is a positive finding —
  the security boundary matches its documentation.
- **Schema confirmed**: `\d wbom_whatsapp_messages` (run via the DB owner path, since the reader
  role can't even see raw-table structure) shows `classification`, `identity_role`,
  `identity_confidence`, `intent_detected`, `workflow_triggered` all exist as real columns, with
  `classification` defaulting to `'unclassified'` and carrying its own index
  (`idx_wbom_messages_class`).
- **Write-path confirmed**: repo-wide grep for `classification\s*=` finds it written only for
  **`social_inbox_events`** (a completely different table, the social-media auto-reply pipeline —
  `modules/social_auto_reply/__init__.py:194-200`, `modules/social_auto_reply/planner_worker.py:347`).
  Zero writes to `wbom_whatsapp_messages.classification` exist anywhere in `modules/`, `app/`, or
  `scripts/`. The column that *is* written by `bridge_poller` for every WhatsApp message —
  `identity_role`/`identity_confidence`/`intent_detected`/`workflow_triggered`
  (`modules/bridge_poller/__init__.py:1771-1782`, `1848-1850`) — is not selected by the one
  endpoint (`/api/whatsapp/messages/lookup`) Hermes's `audit_lookup_whatsapp_messages` tool calls.

---

## 7. Documentation vs Code vs Tool Result Comparison

`knowledge_base/03_ai_identity/identity_overview.md` ("Identity Resolution Algorithm
(Production)", last verified 2026-06-22) claims to document `identity_brain`'s live behavior. Read
side-by-side with the actual code:

| Doc claim | Code reality | Verdict |
|---|---|---|
| "Source Function: `resolve_identity()`, `_ROLE_PRIORITY`" | The function is `detect_identity()` — there is no `resolve_identity` in the module | **Doc ≠ code** (function name) |
| Confidence scale 0.0–1.0 (e.g. "0.95", "0.7") | Code returns integers 0–100 (`confidence=100, 88, 86, 85, 80, 50, 40, 95`) | **Doc ≠ code** (scale, 2 orders of magnitude apart) |
| 11 rigid, hardcoded priority steps, one role each (admin, family, accountant, vip, escort-buyer, repeat_client, vendor, employee, supervisor, candidate, unknown) | Family/accountant/vip/escort-buyer/vendor/supervisor are **one single step** — a `fazle_contact_roles` lookup ordered by a data-driven `priority` column, not 6 separate hardcoded steps | **Doc ≠ code** (architecture) — relative ordering between these roles is whatever the seed table's `priority` values say today, not a fixed doc-listed sequence |
| Step 6: `repeat_client` ← "Repeat payment history" (`fpe_cash_transactions`) | The actual `_lookup_cash_identity()` cash-payment branch resolves to an **employee-designation role** (`_designation_to_role(...)`), never literally `"repeat_client"`. The real `role="repeat_client"` in code comes from two *different* paths: (a) `wbom_escort_programs.master_mobile` match without an `escort_mobile` match, or (b) an unverified escort-content keyword regex on an unknown sender's message text (confidence 40) | **Doc ≠ code** (this step describes a code path that doesn't exist) |
| Secondary C evidence table: `escort_roster_entries` | Actual query targets `wbom_escort_programs` (`identity_brain/__init__.py:515`) | **Doc ≠ code** (table name) |
| `05_workflows/whatsapp_reply_workflow.md:308` lists `classification` as a legitimate populated field of `wbom_whatsapp_messages`, alongside `identity_role`/`intent_detected` | `classification` is never written for this table (§6) — it is a dead/legacy column | **Doc ≠ runtime** |

---

## 8. Detected Mismatches

1. **Three parallel identity/role systems, one dead.** `user_role.detect_role()` has zero
   callers anywhere in the repo (`grep -rn "detect_role(" .` outside its own file: no results).
   `role_classifier.get_user_context()` has exactly one caller, an unrelated admin web endpoint
   operating on a fourth table (`user_profiles`) that message-processing never touches.
2. **KB doc (`identity_overview.md`) describes a function, confidence scale, and priority
   structure that don't match the live `identity_brain` code** (§7, five separate discrepancies).
3. **Hermes's only phone-parameterized message-lookup tool reads a dead column** instead of the
   live identity/intent columns the pipeline actually writes (§6).
4. **`get_employees` (Hermes's only employee-listing tool) cannot see FPE-linked employees**
   that `identity_brain` itself can resolve — `ai_read_employees` selects from `wbom_employees`
   only; `identity_brain._lookup_employee()`'s fallback (`__init__.py:386-401`) additionally
   joins `fpe_employees`, which is exactly the auto-create path flagged in the same-week
   `HERMES_PAYROLL_WORKFLOW_GAP_AUDIT_2026-08-18.md` as having no approval gate. An employee who
   exists only via that path is invisible to `get_employees` but real to `identity_brain`.
5. **`get_recent_messages`/`ai_read_recent_messages` resolves sender name via a single exact
   string match** (`wbom_contacts.whatsapp_number = m.sender_number`) with no phone-variant
   normalization, unlike every real identity-resolution code path in this system, which always
   tries 3 variants (`identity_brain._phone_variants()`, `number_identity.normalize_phone()`).

---

## 9. Root Cause of Each Mismatch

- **#1 (dead `user_role`)** — legacy code left in place after `identity_brain` superseded it;
  no removal ever happened, no doc ever pointed at it, so it's a latent trap (an agent grepping
  for "role detection" could easily find and reuse the wrong, unused, differently-scaled module).
- **#2 (KB doc drift)** — the doc is dated "last_verified: 2026-06-24" / "documented 2026-06-22."
  The code comment at `identity_brain/__init__.py:207-220` documents a real fix dated **2026-08-01**
  that specifically changed how cash-payment evidence is gated relative to escort-client phones —
  i.e., the code changed after the doc was last verified, and the doc was never re-synced. This is
  exactly the "Docs and KB must stay in sync with code" rule in this repo's own `CLAUDE.md` being
  violated by a change that predates this audit by over two weeks.
- **#3 (dead `classification` column selected)** — `classification` is a legacy column from the
  original schema bootstrap (`db/migrations/029_bootstrap_core_tables_schema_record.sql`). At some
  point the live pipeline was re-architected to write `identity_role`/`identity_confidence`/
  `intent_detected` instead (visible only in `bridge_poller`, added later), but
  `/api/whatsapp/messages/lookup`'s SELECT list (`app/main.py:1210-1212`) was never updated to
  match — a stale column reference in a working endpoint, not a broken query (it doesn't error,
  it just always returns `'unclassified'`).
- **#4 (`get_employees` view incompleteness)** — `ai_read_employees` (migration 012, 2026-06ish)
  predates the `fpe_employees` fallback branch in `identity_brain._lookup_employee()`; the view
  was never extended when the fallback was added. Same drift pattern as #3: a data-access
  boundary lagging behind a code change on the other side of it.
- **#5 (`ai_read_recent_messages` exact-match join)** — the view was written for a "recent
  activity feed" use case (display name is cosmetic there), not for identity resolution — it was
  never intended to substitute for `identity_brain`, but nothing in its tool docstring
  (`get_recent_messages`) warns a caller not to treat the returned `sender_name` as authoritative
  identity, which is how the gap becomes exploitable-by-mistake rather than by design.

None of the five are permission/RBAC failures, wrong-tool-selection-by-Hermes, or hallucination —
all five are genuine, verifiable, code/schema-level facts, independent of any particular Hermes
run.

---

## 10. Missing Hermes Capabilities

- No tool reaches `identity_brain.detect_identity()`.
- No tool reaches `intent.classify()`.
- No tool reaches a *correct* read of the pipeline's persisted identity/intent decision.
- No phone-parameterized single-record lookup for contacts or employees (only paged, unfiltered listings).
- No schema/permission introspection tool (this audit needed one and had to step outside Hermes's own tool surface to get it).
- No tool wrapping `message_router.process_message()` even in a safe, read-only "what would this route to" dry-run form.

## 11. Code Exists but Not Exposed to Hermes

- `modules/identity_brain.detect_identity()` — full, safe, exception-proof, already the canonical implementation.
- `modules/intent.classify()` — pure function, trivially safe to expose.
- `modules/recruitment_flow.recruitment_eligibility()` / `route_recruitment_message()` — recruitment-specific role+intent decisioning, unexposed.
- `bridge_poller`'s persisted `identity_role`/`identity_confidence`/`intent_detected`/`workflow_triggered` columns — reachable in principle through the existing `/api/whatsapp/messages/lookup` route, just not selected.

## 12. Architecture Risks

- **Duplicate-logic risk is not hypothetical here — it has already happened twice** (`user_role`
  dead-ended after `identity_brain` superseded it; `role_classifier`/`user_profiles` grew as an
  unrelated fourth system). Any new Hermes-side identity/intent classifier would be a fifth.
- **Doc-drift risk is active**, not just possible: the identity KB doc has already drifted from a
  code change over two weeks old, in the exact document meant to be the "runtime source of truth"
  for RAG (`runtime_index: true` in its own frontmatter) — meaning any AI answer path (including
  Hermes's own KB retrieval, or a customer-facing RAG answer) that cites this doc for "how does
  identity resolution work" would currently describe a superseded algorithm.
- **A raw exposure of `process_message()` would be genuinely dangerous** — it has write side
  effects (message logging, draft creation, auto-send under some role/intent combinations). Any
  future tool must wrap the read-only sub-pieces (`detect_identity`, `classify`) individually, not
  the orchestrator itself.

## 13. Recommended Minimal Changes

*(Not implemented — this is a read-only audit per the request. Each item below reuses an existing
function; none require new business logic.)*

1. Add two thin, read-only MCP tools: `resolve_identity(phone, text)` →
   `identity_brain.detect_identity()`, and `classify_intent(text)` → `intent.classify()`.
2. Fix `/api/whatsapp/messages/lookup`'s SELECT to include `identity_role`, `identity_confidence`,
   `intent_detected` (keep `workflow_triggered`; `classification` can stay or be dropped — it's inert either way).
3. Extend `ai_read_employees` (or add a second view) to include the `fpe_employees`-linked
   fallback `identity_brain` already checks, so `get_employees` stops silently missing a real
   category of employee.
4. Re-verify and correct `knowledge_base/03_ai_identity/identity_overview.md` against current
   `identity_brain` code (function name, confidence scale, step structure, table names) — flagged
   here, not fixed, per this audit's read-only scope.
5. Delete or clearly mark `modules/user_role` as unused/deprecated so it stops being a plausible-looking trap for future searches.

Each requires fresh Owner authorization before implementation, per the established pattern from
prior Hermes capability-expansion sessions (see memory `project_hermes_full_authority_phase1_20260813`).

## 14. Final Verdict

**Can Hermes reliably determine Identity → Role → Intent → Content on its own today?** **No.**
Under the current architecture, Hermes has zero tool-level access to the canonical resolvers
(`identity_brain.detect_identity()`, `intent.classify()`), and its one tool that comes closest
(`audit_lookup_whatsapp_messages`) reads a dead column instead of the live ones. Its remaining
tools (`get_contacts`, `get_employees`, `get_recent_messages`) are unfiltered, phone-variant-blind
listings that miss a real category of employee and use a weaker identity signal (exact-string
contact-name join) than the pipeline they're standing in for.

**Should Hermes build its own classification logic to fill the gap?** **No.** This codebase has
already produced two abandoned/orphaned identity systems from exactly that instinct
(`user_role`, `role_classifier`+`user_profiles`), and its own identity-domain KB documentation has
already drifted from the canonical code without anyone noticing for over two weeks. The correct
fix is exposure, not reimplementation: thin, read-only MCP wrappers around the two functions that
already do this correctly (`detect_identity()`, `classify()`), plus the two small data-layer
corrections in §13. This is a **capability-exposure gap**, not a missing-capability gap — the
hard part (the actual classification logic) is already built, tested-by-production-traffic, and
correct; it is simply not reachable from Hermes's tool layer today.
