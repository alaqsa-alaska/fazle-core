# Hermes Message-Context & Reply-Execution Gap Audit — 2026-08-19

Read-only audit + minimal implementation plan. Sister/follow-on to
`HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md`. No
production code changed, no service restarted, no message sent. One real
incident occurred during this audit — see below, disclosed first per this
project's standing practice.

## ⚠️ Real incident this session: secret re-exposed

Investigating Section 3 (who consumes fazle-mcp), I ran
`grep -n "mcp_servers:" -A 40 ~/.hermes/config.yaml` to read the MCP block's
structure. `-A 40` pulled in line 241, `FAZLE_CORE_API_KEY: fk_...`, printing
the live key into tool output — full value, in this session's transcript.
This is the same file, same line as **Incident 4** in
[[feedback_grep_context_near_secrets]] (2026-08-18, one day ago), and that
incident's own rotation procedure updates `config.yaml` in place — so the
value just exposed is very likely the **already-once-rotated key exposed a
second time**, not a fresh secret. Memory updated with this as Incident 5
(new failure mode: collateral exposure while reading unrelated structural
content in a file already known to hold secrets — the existing "never
`-A/-B/-C`" rule already covered this, I just didn't recognize the file as
secret-bearing at the moment because the query wasn't secret-related).

**Recommend re-rotating `FAZLE_CORE_API_KEY` now**, same procedure as
yesterday (`core/scripts/create_hermes_mcp_admin.py --rotate`, output
redirected to a file never read, `config.yaml` updated programmatically,
temp file shredded) — not executed, needs your go-ahead since it's a
production credential action.

---

## A. Current verified state (Section 1)

All live-verified today, not just re-read from memory:

| Item | Status |
|---|---|
| `/api/whatsapp/messages/lookup` returns `message_body`/`extracted_text` | ✅ code correct (`app/main.py:1166-1173`), test-proven (`test_whatsapp_messages_lookup_route.py`, 2/2 pass) — **not live**, service not restarted since the edit |
| `/api/hermes/decisions/lookup` | ✅ code correct (`app/main.py:1213-1274`), test-proven (7/7 in `test_kernel_lookup_routes.py`) — **not live**, confirmed via live `curl` → `404` (route doesn't exist in the running process) |
| `/api/hermes/kernel/events` | ✅ same as above, also `404` live — **not live** |
| `fazle-mcp/kernel_tools.py` thin pass-throughs | ✅ code correct, 6/6 unit tests pass, zero DB/business logic in the file itself |
| `resolve_identity()` still exposes canonical identity | ✅ unchanged, re-read `app/main.py:1192-1201` this session — still calls `identity_brain.detect_identity()` directly, still strips only `bkash_number`/`basic_salary` |
| `classify_intent()` docstring fixed | ✅ both copies (`fazle-mcp/identity_tools.py` and the `@mcp.tool()` wrapper in `server.py`) now say "approximation, not exact replay of live LLM-first routing" |
| No duplicate business logic | ✅ confirmed — every new/changed function is either a bound-parameter SQL `SELECT` against an already-existing table, or a `core.get()` pass-through; no new classification, scoring, or routing logic anywhere in the diff |

**Deployment state (Section 9) — verified live, not assumed:**

- `core`: `git status` clean except the intended diff (`app/main.py`,
  `tests/unit/test_whatsapp_messages_lookup_route.py`, new
  `tests/unit/test_kernel_lookup_routes.py`); **not committed**; local `main`
  is already 1 commit ahead of `origin/main`'s last-known state only insofar
  as `1a9385e` — no new commits made this session.
- `fazle-mcp`: `git status` shows the intended diff (`identity_tools.py`,
  `server.py`, new `kernel_tools.py`, `tests/test_kernel_tools.py`) plus
  **pre-existing unrelated uncommitted changes** from before this session
  (`audit_tools.py`, `domain_reports.py`, `fazle_core_client.py`,
  `operational_tools.py`, `send_whatsapp_tools.py`, 2 test files,
  `test_get_settings_status.py`) — not touched by me, already `ahead 8` of
  `origin/main` before this session started. **Not committed.**
- `fazle-core.service`: active, uptime since `2026-08-19 00:05:37` —
  **predates** this session's `app/main.py` edits. Confirmed stale via live
  `curl`: the two new routes return `404` (don't exist in the running
  process); `/api/whatsapp/messages/lookup` returns `403` (route exists, but
  the widened SELECT isn't loaded).
- `hermes-dashboard.service` (owns the stdio `fazle-mcp` child per
  yesterday's finding): active since `00:23:26`, also predates this
  session — the running MCP server does **not** have `kernel_tools`
  registered.
- `hermes-runner.service`: **inactive** (unrelated to this work — noting
  for completeness since it's the other historically-relevant service name;
  the live admin relay is served by `hermes-dashboard.service`).
- **Restart required** to go live: `fazle-core.service` (via
  `scripts/restart_gate.sh --restart`) for the 3 route changes, and
  `hermes-dashboard.service` restart (or its own reload mechanism) to pick
  up `kernel_tools.py`/`identity_tools.py` — matches the exact two-service
  pattern from yesterday's deployment.
- `scripts/restart_gate.sh --check-only` not run this session (no restart
  requested) — do that first when you're ready to go live.
- Current Hermes mode: `READ` (`~/hermes-runner/current_mode.txt`, set
  `2026-08-15T04:43:56Z`, no expiry) — `send_whatsapp_message` and
  `approve_draft` are both hard-blocked at the mode gate right now
  regardless of anything else in this report.

---

## B. Remaining gaps (only real, evidence-backed ones)

1. **`/admin/send-whatsapp`'s server-side `source_bridge` default was never
   actually removed** — see Section E, this is the headline finding.
2. **No structured provenance** from an inbound message to the
   payment/salary/complaint/attendance draft it produced — see Section F.
3. **No explicit "reply permission" tool-facing signal** — `chosen_action`
   exists only in a table Hermes couldn't read until this session's
   (not-yet-deployed) fix, and even once readable it answers a different
   question than "can Hermes send" — see Section D/G.
4. Optional: no single aggregated "message context" call — Hermes must
   currently chain 3 separate tool calls to assemble identity+intent+
   content+decision for one message. Not a defect, a convenience gap —
   see Section G item 4.

---

## C. Super Admin masking verdict (Section 2)

**Verdict: `_HERMES_IS_ADMIN_CONTEXT = True` already correctly delivers
unmasked operational identifiers to Hermes, end-to-end, for every tool that
matters — verified by tracing every caller, not just the flag.**

Grepped every file for `pii_mask`/`mask_pii`/`_HERMES_IS_ADMIN_CONTEXT`/
`TEXT_SCAN_FIELDS`/`PII_FIELDS` in `fazle-mcp/`:

- Only **2 files** ever call `pii_mask.mask_pii()`: `audit_tools.py`
  (`audit_lookup_whatsapp_messages`, `audit_get_drafts`) and
  `metrics_tools.py` (2 call sites). Both pass `_HERMES_IS_ADMIN_CONTEXT`
  (hardcoded `True` in each file), and `mask_pii()`'s own first line is
  `if is_admin: return value` — so masking is a **provable no-op** for
  every call site that has it wired in.
- Every other tool file — `operational_tools.py`, `domain_reports.py`,
  `send_whatsapp_tools.py`, `identity_tools.py`, this session's new
  `kernel_tools.py` — **never imports `pii_mask` at all**. There was never
  any masking to disable for these; `resolve_identity`, `classify_intent`,
  `lookup_decisions`, `lookup_kernel_events` all return full, unmasked
  phone numbers by construction.
- **Why hardcoding `True` is safe, not a blanket bypass**: traced who can
  even reach `fazle-mcp/server.py`. Exactly one consumer exists —
  `~/.hermes/config.yaml`'s single `mcp_servers.fazle-core` entry spawns
  `fazle-mcp/server.py` as a stdio child of the `hermes` CLI process
  (confirmed by name-only `grep` on the file, no `-A/-B/-C`, per the
  incident above). Grepped `assistant-platform/` repo-wide for any
  reference to `fazle-mcp`/`fazle_mcp`: zero — the only hits are policy
  docs and the **separate** JS-side `backend/src/lib/piiMask.js`, which
  serves the web dashboard (a genuinely different, role-aware consumer)
  and was not touched. So there is no code path by which a non-admin
  context ever calls into `fazle-mcp`'s tools — `_HERMES_IS_ADMIN_CONTEXT`
  isn't disabling a real check for anyone else, because no one else is
  behind it.
- **Do not invent a second masking subsystem.** The existing mechanism
  (`mask_pii(value, is_admin, fields=, text_fields=)`) already supports
  per-call-site field overrides if a future non-admin fazle-mcp consumer
  is ever added — no new design needed, just wire a real `is_admin` value
  through if that day comes.
- **External-facing masking is untouched and out of scope** — confirmed
  `backend/src/lib/piiMask.js` (the web dashboard's own masking) was not
  read or modified this session or last.

Concrete example from the prompt: `01897998877` sending
"মামুন ভাইয়ের সাথে কথা বলেছি..." — once the not-yet-deployed
`/api/whatsapp/messages/lookup` widening is live, `resolve_identity`,
`lookup_decisions`, and the message lookup all return the **full**
`01897998877`/`8801897998877` unmasked to Hermes today, with zero
additional change needed.

---

## D. Recipient-resolution verdict (Section 3)

**Verdict: the existing identifiers are sufficient. No new identity/session
system is needed — but this depends on a behavioral rule for Hermes, not
a code guarantee, and cold-start last-4-digit references cannot be
resolved safely by design (correctly, not as a defect).**

What's already available, confirmed by re-reading the actual routes/schema:

| Identifier | Where it comes from | Stable? |
|---|---|---|
| `message_id` | `wbom_whatsapp_messages.message_id` (PK), returned by `/api/whatsapp/messages/lookup` | Yes, permanent |
| `sender_number` / `canonical_phone` | Same table/route | Yes — `canonical_phone` is the normalized form |
| `trace_id` | `hermes_decision_audit_log` (UUID, one per real turn), returned by the new `/api/hermes/decisions/lookup` | Yes, per-turn |
| `employee_id` / `contact_id` | `resolve_identity()` | Yes, when matched |
| `source` / `source_bridge` | Both tables (`platform` on messages, `source` on decisions) | Yes |
| A dedicated conversation/session/thread id | **Does not exist** for this purpose | — |

I grepped `hermes-runner/` and `fazle-mcp/` for any conversation-memory or
last-resolved-contact tracking mechanism (`current_conversation`,
`last_message`, `session_context`, etc.) — **zero hits**. There is no
purpose-built "remember who we were just talking about" store.

This is not actually a gap for the target workflow, because Hermes runs as
a conversational LLM agent: within one live chat turn sequence, the model's
own context window already retains the full, unmasked identifiers returned
by any earlier tool call in that same conversation (exactly the same
mechanism by which I, auditing this repo across many turns in this same
session, still remember file:line evidence from turns ago). Given Section C
confirms full phone numbers are never masked for Hermes, the moment any
tool call surfaces `01897998877` in-context, "ওই লোকটাকে reply দাও" can be
resolved deterministically by re-using that exact string — no re-query, no
digit-matching, no new infrastructure.

**The one thing this depends on that isn't a code fact**: Hermes must be
instructed (system-prompt / operating-procedure level, not a new tool) to
always prefer the strongest identifier already in its own conversation
context over re-deriving one from a fragment like "৮৮৭৭". This is a
prompt/procedure recommendation, not a code change (Section H explicitly
says don't build a new identity system).

**Cold-start "last 4 digits" case** (no prior lookup this conversation):
traced `/api/whatsapp/messages/lookup`'s fallback path
(`app/main.py:1145-1152`) — when `normalize_phone()` fails, it falls back to
`phone_last10 = <last 10 digits of input>`. `phone_last10` is a full
10-digit column (migration 006). A 4-digit fragment like `"8877"` produces
`digits[-10:] == "8877"` (shorter than 10), which can never equal a stored
10-digit value — so this **fails closed** (zero rows), not a false
ambiguous match. This is correct, safe behavior, not a bug: Hermes
genuinely cannot resolve a bare 4-digit fragment with no other context, and
the system doesn't guess — it should ask the admin to disambiguate (e.g. by
searching recent messages with `lookup_decisions`/`audit_lookup_whatsapp_messages`
and presenting candidates), never silently pick the "closest" match.

---

## E. Send/reply path verdict (Section 4)

**Verdict: mixed. The Python-tool-side fix is real and correct; the
server-side fix it claims is complete is not — a genuine, currently-live
gap. The 3 failing tests are confirmed stale, not a production risk.**

Traced the full path: `send_whatsapp_tools.send_whatsapp_message()` →
`POST /admin/send-whatsapp` (`app/main.py:3294`) →
`modules.outbound.enqueue()` → `fazle_outbound_queue` table.

- **Recipient format**: no normalization anywhere in this path — `recipient`
  flows from the tool call straight into the queue row as given. (Whatever
  normalizes it downstream, if anything, is in the queue *worker*, not
  audited this session — out of scope for this report but worth a
  follow-up read before relying on raw `01XXXXXXXXX` vs `880...` working
  interchangeably.)
- **`source_bridge` client-side (fazle-mcp)**: genuinely fixed. Reading
  `send_whatsapp_tools.py:100-107`, both `recipient` and `source_bridge`
  are validated non-empty *before* the mode/confirm checks even run — a
  call omitting `source_bridge` gets a clear `"source_bridge is required"`
  error, never silently defaults.
- **`source_bridge` server-side (fazle-core) — NOT actually fixed**: 
  `app/main.py:3299` — `source_bridge = (payload or {}).get("source_bridge") or "bridge2"`.
  This is the *exact* silent `"bridge2"` default the tool's own docstring
  (`send_whatsapp_tools.py:91-98`) says was "removed" on 2026-08-15. It was
  only removed from the Python tool's own validation, not from the HTTP
  route it calls. `modules/outbound/enqueue()`'s own signature
  (`source_bridge: str = "bridge2"`, `modules/outbound/__init__.py:40`)
  carries the same default a third time, though it's masked in practice
  here because the route always passes an explicit value (possibly the
  silent `"bridge2"` fallback itself) into it.
  **Practical exposure**: as long as every caller of `/admin/send-whatsapp`
  goes exclusively through the hardened `send_whatsapp_tools.py`, this is
  latent, not triggered — Hermes itself can no longer trigger the silent
  default. But the endpoint itself will still silently default for **any
  other caller** (a different script, a future second Hermes tool, a
  manual `curl`, anyone with the API key) that omits or empty-strings
  `source_bridge`. The specific risk named in the docstring — "a
  conversation that actually happened on bridge1/bridge3 would silently
  send via bridge2 instead" — is still real at the server layer.
- **Bridge preservation on reply**: `approve_draft(draft_id)` sidesteps
  this problem entirely for anything that already went through
  `create_draft_reply()` — the draft row itself carries `source` (the
  bridge it came in on), and approval reuses fazle-core's own
  `/api/drafts/{id}/approve`, which resolves recipient+bridge from the
  stored draft, not from anything Hermes passes. This is the *safer* path
  when a draft already exists for the message in question.
- **Draft table mismatch, reconfirmed**: `approve_draft()`'s own docstring
  (`send_whatsapp_tools.py:138-162`) and its call to
  `/api/drafts/{id}/approve` operate on `fazle_draft_replies` —
  matches the earlier finding
  ([[project_hermes_payroll_gap_audit_20260818]]) that Hermes has no
  approval tool for `fazle_payment_drafts` at all. Confirmed again by
  reading the live migration schemas
  (`db/migrations/001_safe_mode_and_escort_extractor.sql:5`,
  `db/migrations/002_align_tables.sql:53`) — two structurally different
  tables, only one has a Hermes-reachable approve path.
- **3 pre-existing failing tests — classified `test-only/stale`, not a
  production risk.** Read `tests/test_send_whatsapp_tools.py:29-56`
  directly: all 3 call `send_whatsapp_message("8801700000000", "hi",
  confirm=...)` with **no `source_bridge` argument at all** — written
  before the 2026-08-15 fix that made it mandatory. They now correctly get
  `"source_bridge is required..."` instead of the mode/confirm error they
  originally asserted. This is proof the code got *stricter and safer*
  after the tests were written, and nobody updated the tests to match —
  the opposite of a regression. Fixing them is a real but low-risk,
  test-only cleanup (update each call to pass `source_bridge="bridge1"`),
  **not a blocker** for the requested workflow.
- **RBAC layer, separate from all of the above**: `/admin/send-whatsapp`
  is also gated by `require_command("send_whatsapp_message")`, which looks
  up `COMMAND_ROLE["send_whatsapp_message"] = "superadmin"`
  (`modules/rbac/__init__.py:84`) — enforced independently of the mode
  gate and independently of `source_bridge`. See Section D/G for why this
  matters for the next section.

---

## F. Entity-provenance findings (Section 7)

**Verdict: linkage exists in exactly one narrow place; everywhere else,
none — confirmed by reading actual table schemas and INSERT call sites,
not inferred from migration 060's comment alone.**

- **Where real provenance exists**: `wbom_unmatched_confirmations`
  (escort admin-confirmation dedup queue, `modules/escort/__init__.py:2536-2571`)
  has `source_bridge` + `source_message_id` columns with a partial unique
  index on `(source_bridge, source_message_id)` specifically to make
  re-processing the same inbound message idempotent. This is real,
  structured, queried linkage — but scoped narrowly to one escort
  confirmation-matching flow, not a general mechanism.
- **`fazle_draft_replies`**: schema (`db/migrations/001...sql:5-14`, plus
  later `meta jsonb` column — present live per
  `shared/draft_reply.py:124-131`'s own INSERT, though not tracked by any
  migration file; matches this repo's already-disclosed
  "~150 tables, ~88 columns" undocumented drift category noted in
  `030_reconcile_drift_columns_payment_drafts_replies.sql:15-17`, out of
  scope to fix here) has no `message_id`/`trace_id` column. The `meta`
  JSONB *could* hold a reference, but the one call site I traced
  (`modules/message_router/__init__.py:899-907`, complaint drafts) passes
  a hardcoded static string, `context='{"draft_type": "complaint"}'` —
  never the originating message's id.
- **`fazle_payment_drafts`**: schema
  (`db/migrations/002_align_tables.sql:53-74`) has `employee_id`,
  `escort_program_id`, `source_bridge`, `admin_phone` — no
  `source_message_id`/`trace_id`/`conversation_id`.
- **The only usable fallback**: phone + timestamp proximity. Both
  `hermes_decision_audit_log` and every draft table carry `sender_phone`/
  `recipient`/`employee_mobile` + a timestamp, and `create_draft_reply()`'s
  own 5-minute collision window (`shared/draft_reply.py:97-104`) confirms
  drafts are, in practice, created within seconds of their triggering
  message — so "same phone, created within ~1 minute of a decision-log
  row for that phone" is a workable *heuristic* join for investigation
  purposes, not a reliable FK, and should never be presented to Hermes as
  certain.

**Per your explicit instruction, no linkage table implemented this
session** — this is a genuine data-model gap, correctly flagged, not
fixed.

---

## G. Minimal implementation plan

Ordered by what's actually required for the target workflow vs.
nice-to-have.

### G1. Fix `/admin/send-whatsapp`'s server-side `source_bridge` default (required — closes a real, currently-live gap)

- **File**: `core/app/main.py:3294-3327`
- **Current**: `source_bridge = (payload or {}).get("source_bridge") or "bridge2"` — silently defaults.
- **Change**: reject the request (`HTTPException(400, "source_bridge is required")`) when `source_bridge` is missing/empty, mirroring exactly what `send_whatsapp_tools.py` already enforces client-side. Do not touch `modules/outbound/enqueue()`'s own default — it's dead in practice once the route always passes an explicit value, and changing a shared queue-primitive's default is a larger blast radius than this fix needs.
- **Why**: this is the one item in the whole audit that's an actual live discrepancy between documented/claimed behavior and real behavior — every other "gap" found is either already correctly handled or a known, disclosed limitation.
- **Tests**: one new case in a route-level test (mirror `test_kernel_lookup_routes.py`'s pattern) asserting `POST /admin/send-whatsapp` without `source_bridge` returns `400`, not a silent `bridge2` enqueue.
- **Risk**: low — this only *rejects* previously-silently-accepted requests; any caller relying on the silent default was already flagged as a latent bug by the tool's own docstring.
- **Migration**: none.
- **Restart**: yes, `fazle-core.service` (bundle with G3/G4 below into one restart).

### G2. Update the 3 stale tests (optional cleanup, not a blocker)

- **File**: `fazle-mcp/tests/test_send_whatsapp_tools.py:29-56`
- **Change**: add `source_bridge="bridge1"` to the 3 failing calls so they exercise the mode/confirm logic they were actually written to test.
- **Why**: removes false-negative noise from the suite; zero behavior risk since it's test-only.
- **Tests**: is the test.
- **Risk**: none.
- **Migration/restart**: none.

### G3. Deploy this session's already-implemented identity/kernel work (required to realize A/C/D at all — nothing in this report is live yet)

- Run `scripts/restart_gate.sh --check-only` first; then `--restart` for
  `fazle-core.service`; separately restart `hermes-dashboard.service` for
  the `fazle-mcp` child to pick up `kernel_tools.py`/updated
  `identity_tools.py` — same two-service pattern as yesterday's deploy.
- **Not done this session** — explicitly your call, per your instruction not to restart/commit/push during audit/planning.

### G4. Optional: thin, non-interpretive message-context aggregator (nice-to-have, only if repeated tool calls become a real friction point)

- **New route**: e.g. `GET /api/hermes/context/message` — takes `phone`
  and/or `trace_id`, internally calls the *same three existing queries*
  (`wbom_whatsapp_messages`, `hermes_decision_audit_log`,
  `identity_brain.detect_identity()`) and returns them as three clearly
  separate, clearly labeled sub-objects in one response — no joining, no
  new heuristic, no interpretation. Reduces 3 round-trips to 1 without
  adding any logic a reviewer would need to re-audit.
- **Why optional, not required**: Hermes can already assemble the same
  picture today by calling `resolve_identity` + `lookup_decisions` +
  `audit_lookup_whatsapp_messages` in sequence within one turn — this is
  a convenience/latency improvement, not a capability gap.
- **Explicitly do not** make this route perform the phone+timestamp join
  described in Section F — that would be exactly the "parallel
  interpretation logic" you said not to build.
- **Tests/risk/migration/restart**: same low-risk pattern as G3's siblings if built; no migration; needs the same restart.

### G5. Not planned, flagged only: entity-provenance linkage (Section F)

- Would need a new column (e.g. `source_message_id`/`trace_id` on
  `fazle_payment_drafts`/`fazle_draft_replies`) and updating every
  draft-creation call site to pass it through. **Explicitly out of scope**
  per your instruction ("do NOT implement a new linkage table in this task
  unless absolutely required for the Super Admin reply workflow") — and
  it isn't required, because `approve_draft(draft_id)` already resolves
  recipient+bridge from the draft row itself without needing this
  linkage. Documented here only so it isn't silently forgotten.

---

## H. Explicit non-goals (confirmed respected, not just stated)

- No duplicate identity logic — `resolve_identity` still calls
  `identity_brain.detect_identity()` directly, verified unchanged this
  session.
- No duplicate role logic — same function, same source.
- No duplicate intent classifier — `classify_intent` still calls
  `modules.intent.classify()` directly; its docstring now correctly says
  it's the fallback, not a second implementation of the LLM-first path.
- No parallel policy engine — `chosen_action`/RBAC/mode-gate are read
  where they already live (`hermes_decision_audit_log`,
  `modules/rbac.COMMAND_ROLE`, `~/hermes-runner/current_mode.txt`); none
  reimplemented.
- No global PII masking disable — traced to confirm it was never global
  in the first place; the one context it applies to (Hermes via
  `fazle-mcp`) is provably the only consumer.
- No unnecessary database redesign — zero migrations this session or
  proposed as required in this plan; G5 is explicitly deferred.
- No new business logic inside MCP — every fazle-mcp file touched or
  proposed is a `core.get()`/`core.post()` pass-through.
- No production send during this audit — mode confirmed `READ`
  (hard-blocked at the gate even if attempted), nothing sent, nothing
  restarted.

---

## I. Acceptance criteria (PASS/FAIL, for the full workflow once G1 and G3 are done)

1. **Canonical facts reachable** — PASS if, given only a phone number,
   Hermes can obtain (in ≤3 tool calls): full unmasked sender number, raw
   message text, identity/role, intent + method, `chosen_action` +
   `explanation`, and `source`/bridge — all traced back to
   `identity_brain`/`intent`/`hermes_kernel`, none re-derived.
2. **Masking** — PASS if a fresh `resolve_identity("01897998877")` and
   `lookup_decisions(phone="01897998877")` both return the full,
   un-redacted number in their response bodies (live check once
   restarted).
3. **Recipient resolution** — PASS if, within one Hermes conversation
   turn, after any tool call has surfaced a full phone number, a
   follow-up "reply to that person" resolves to the exact same number
   with no re-query and no digit-fragment guess. FAIL if Hermes ever
   substitutes a `phone_last10`/partial match for a number already known
   in full.
4. **Cold-start ambiguity** — PASS if a bare last-4-digit reference with
   no prior context in the conversation returns zero candidates (fails
   closed) rather than picking the nearest match.
5. **Send authorization is independent of `chosen_action`** — PASS if
   Hermes never treats `chosen_action="reply"` from `lookup_decisions` as
   sufficient to call `send_whatsapp_message`; every actual send still
   requires `RUN` mode + `confirm=True` + superadmin-tier API key,
   checked at call time, regardless of what any historical decision
   record says.
6. **Bridge correctness** — PASS if `POST /admin/send-whatsapp` (after
   G1) rejects a missing `source_bridge` with `400` rather than
   defaulting to `bridge2`. FAIL if any code path can still silently
   select a bridge the admin's message didn't actually arrive on.
7. **No accidental cross-contact match** — PASS if resolving "8877" (or
   any partial fragment) either returns the one exact match already
   confirmed by an earlier tool call in-context, or returns zero/multiple
   candidates for the admin to disambiguate — never a silent
   best-guess pick among several contacts sharing those digits.
8. **Dry-run evidence, not a real send** — PASS if the full path up to
   (but not including) the actual `send_whatsapp_message(confirm=True)`
   call can be demonstrated with real tool outputs; the final send step
   itself requires separate, explicit authorization every time, and mode
   `READ` blocks it by default.
