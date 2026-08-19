# System-Generated Message Root-Cause + Hermes Agentic Behavior Audit — 2026-08-19

Read-only audit. No production code, config, or data changed. Every claim
below is traced to a source file, a live production row, or an exact
message from the real Hermes conversation transcript — no phone+timestamp
inference presented as certain evidence.

---

## A. Incident A root cause — "🚨 AI Console ALERT [vmi3117764]"

**Generator**: `core/scripts/ai_console_monitor.sh` — a standalone bash
script, **not** part of the Python app. Registered via user crontab:
`*/5 * * * * /home/azim/core/scripts/ai_console_monitor.sh >> .../logs/ai-console-monitor.log 2>&1`
(confirmed via `crontab -l`).

**Exact function**: `send_wa_alert()` (`:38-59`) builds the message and
`POST`s it directly to `${BRIDGE_URL}/api/send` (`BRIDGE_URL="http://localhost:8081"`,
confirmed live-listening, owned by process `whatsapp-bridge`) — **this
never touches Fazle-Core's own outbound pipeline**: not
`fazle_outbound_queue`, not `wbom_whatsapp_messages`, not
`hermes_decision_audit_log`. Live-verified: zero rows in
`fazle_outbound_queue` whose body starts with `🚨 AI Console ALERT` exist,
ever.

**Trigger condition** (`:87-112`): `curl -sf --max-time 5 -H "X-Internal-Key: $INTERNAL_API_KEY" http://127.0.0.1:8200/chat/models`.
If the body is empty (curl failed/timed out) → `CRITS+=("AI Console
/chat/models unreachable (${MODELS_MS}ms)")`.

**Health endpoint**: `GET /chat/models` (`app/main.py:3773`) —
`chat_models()` → `ProviderRegistry().list_models()`
(`modules/ai_console/provider_registry.py:46`) → `app.llm.check_health()`
(`app/llm.py:575-607`).

**Root cause of the timeout, pinpointed live**: `check_health()` awaits
**four provider health checks sequentially** — `omniroute_provider`,
`github_models`, `groq_provider`, `ollama` (`:584-606`), not in parallel.
Live-timed each individually just now:

| Provider | Live latency |
|---|---|
| `omniroute_provider.check_health()` | **>10,000ms — did not return within a 10s wait** |
| `github_models.check_health()` | 0ms (disabled) |
| `groq_provider.check_health()` | 87ms (fast error) |
| `ollama.check_ollama_health()` | 60ms (ok) |

`omniroute_provider.check_health()` (`app/omniroute_provider.py:151-169`)
does a **real chat-completion call** (`client.chat.completions.create(...,
messages=[{"role":"user","content":"ping"}])`) with its own internal
`asyncio.wait_for(..., timeout=10.0)` — already longer than the monitor
script's 5-second outer `curl --max-time`. Since `omniroute` is checked
**first** in the sequential chain, its hang alone is sufficient to blow
the 5s budget regardless of the other three providers being healthy.

**Retry/timeout logic**: monitor script does a **single** curl attempt,
zero retries, 5s hard timeout — no tolerance for one slow provider inside
a 4-provider sequential chain with a 10s worst-case leg.

**Recipient-selection logic**: hardcoded constant,
`OWNER_JID="8801880446111@s.whatsapp.net"` (`:20`) — not looked up from
any identity/contact/RBAC table, always the same fixed number.

**Source bridge**: port 8081 (`whatsapp-bridge` process) — confirmed via
Hermes's own 2026-08-16 investigation (below) this is delivered as
`bridge1`.

**Intended infra alert, not a misrouted business message** — this is a
genuine, deliberately-built cron health monitor, working exactly as
designed at the mechanical level. It mixes ops paging into the same
WhatsApp number/channel used for business conversations, which is a
design choice worth the Owner's attention, not a routing bug.

**READ-ONLY policy line**: same script, same string literal (`:137`),
hardcoded in the same f-string as the rest of the alert. Not generated
by any other module.

**KB/RAG involvement**: **none whatsoever** — pure bash, hardcoded
strings, zero LLM/KB/RAG code path anywhere in this script.

**Not a one-off — this is an ongoing, ~24h+ sustained issue.** Grepped
the monitor's own log for every real `ALERT_SENT` (the cooldown means
only the first CRITICAL per hour actually reaches WhatsApp):

```
2026-08-18 08:15:02  [OK]           <- last known-good check
...gap, first failure sometime after this...
2026-08-19 06:05:02  [ALERT_SENT]
2026-08-19 07:10:01  [ALERT_SENT]
2026-08-19 08:15:01  [ALERT_SENT]
2026-08-19 09:20:02  [ALERT_SENT]
2026-08-19 10:25:01  [ALERT_SENT]   <- the exact message quoted in this task
```
And it is still firing every 5-minute tick as of this audit (11:15+ CEST),
cooldown-suppressed between the hourly sends.

**This is not the first time the Admin has asked about it.** A real
Hermes session from **2026-08-16 07:48-07:57 UTC** (`fazle_outbound_queue`
ids 2034/2036, `hermes_decision_audit_log` trace_ids `5db8de4d…`/`2d3a67f7…`)
shows the Admin already asked about this exact alert three days ago.
Hermes correctly identified `ai_console_monitor.sh` as the generator that
time too — but guessed the timeout cause as "Ollama cold-start," which is
now disproven by today's direct timing (Ollama responds in 60ms; the real
culprit, OmniRoute, was never checked in that investigation). The
underlying cause was never fixed, so it recurred and has now been
recurring continuously for over a day. **That same 2026-08-16 session also
contains Hermes's own admission of an 8x-repeated-identical-tool-call loop
that its own guardrail had to stop** — a separate, earlier instance of the
same general tool-call-reliability problem class analyzed in Section D
below, already tracked in `[[project_hermes_toolcall_reason_bug_audit_20260811]]`.

---

## B. Incident B root cause — "পরিমাণ বুঝতে পারিনি" to +8801817201596

**Source file/function**: `modules/employee_conversation/__init__.py`,
the `STEP_AMOUNT` branch (`:857-866`) of the module's session-based
multi-turn state machine. `_extract_amount(text)` fails to parse a
number from the inbound text → returns the fixed clarification string,
verbatim, no template engine, no LLM, no KB.

**Live occurrence, fully traced (source_message_id newly deterministic
thanks to today's Task 1 migration — no phone+timestamp guessing used)**:

```
inbound message_id  3A15902990C0C32B7A19  (bridge2)
        ↓
trace_id             44a38fc0-9b54-4294-bd9c-d102a2112fa1
        ↓ 2026-08-19 07:58:17 UTC
identity_role        internal_coordinator   (confidence 1.0 -- NOT unknown)
intent               payment_due  (method: rule/deterministic fallback)
chosen_action        reply
explanation          catchall-sprint3a-session
raw_input_text        "Ashbo, ektu pore"    ("I'll come in a bit" -- not an amount at all)
        ↓
session               fazle_draft_replies id=3032, intent='employee_conversation',
                       status='ec_amount' (STEP_AMOUNT)
        ↓
generator             modules/employee_conversation/__init__.py:857-866
```

**Root cause: a stale session with no expiry check, not an identity
failure.** The identity resolver worked correctly (`internal_coordinator`,
confidence 1.0) — the actual bug is in
`get_conversation_session(phone)` (`:346-371`):

```sql
SELECT id, status, reply_text, source FROM fazle_draft_replies
 WHERE recipient = $1 AND intent = 'employee_conversation'
   AND status NOT IN ($2 /*ec_done*/, 'rejected', 'cancelled')
 ORDER BY created_at DESC LIMIT 1
```

**No time-based expiry clause of any kind.** Queried the actual session
row: **id=3032, created 2026-08-18 06:28:18 UTC — over 25 hours before**
the unrelated "Ashbo, ektu pore" message arrived and was silently
absorbed into it. `_create_conversation_session()` (`:374-`) does close
stale sessions, but only when a *new* session is about to be created —
`get_conversation_session()` itself has no such guard, so any session
left open (never reaching `ec_done`/`rejected`/`cancelled`) will
intercept the *next* message from that phone indefinitely, regardless of
whether that message has anything to do with the original request.

**Originating trigger for the stale session**: session 3032's own stored
context: `purpose:"advance", employee_id:695, employee_name:"Unknown",
source_message:"Sumir advance chacche 1k\nAbdus Sama..."` — a third
party ("Abdus Sama[d]") relaying an advance request on behalf of
someone named "Sumir," which the parser attached to `employee_id=695`.

**Why `employee_name` is "Unknown" — a real, separate data-quality gap,
independently confirmed live today**: `GET /api/fpe/employees/695`
itself returns `404 {"detail":"Employee not found"}` — `employee_id=695`
does not resolve to any real FPE employee record at all. (This is the
exact same employee_id that also appears, also as "Unknown", on an
unrelated old payment draft #58 discovered during this session's earlier
deployment-acceptance testing — the same systemic employee-linking gap
surfacing in two independent places.)

**Identity/role validation before the clarification reply**: happens,
and resolves correctly (`internal_coordinator`) — but that check gates
*routing to the right workflow branch*, not *whether an already-open
session should still be trusted*. Once `get_conversation_session()`
finds an open row, the session's own stored state (not the current
message's fresh identity resolution) drives the reply.

**"Delayed/backlogged old outbound item"?** No — this is not a delayed
send of an old queued message. It's a fresh reply (`sent_at` matches the
inbound timestamp) generated fresh from stale *session state*, which is
a different (and in this case, worse) failure mode: a live, correctly-
identified sender got a nonsensical reply because of month-old
unrelated conversation residue.

**Legacy/alternate identity**: not applicable to *this* specific decision
— `internal_coordinator` was resolved fresh and correctly. The
"Unknown"-employee-name issue is real but belongs to the earlier
session-creation step (2026-08-18), not to this decision.

---

## C. Outbound provenance — deterministic vs. missing

| Hop | Incident A | Incident B |
|---|---|---|
| generated outbound message | ✅ (bash script) | ✅ |
| outbound message id | ❌ **never created** — bypasses `fazle_outbound_queue` entirely | ✅ (standard outbound path, same as every other `chosen_action=reply`) |
| recipient | ✅ hardcoded constant | ✅ `8801817201596` |
| source_bridge | ✅ bridge1 (confirmed via Hermes's own 2026-08-16 investigation) | ✅ bridge2 |
| triggering inbound/event | ⚠️ a cron tick, not an inbound message — no `wbom_whatsapp_messages` row exists for it | ✅ `message_id 3A15902990C0C32B7A19` |
| trace_id | ❌ **none — never enters `hermes_decision_audit_log`** | ✅ `44a38fc0-9b54-4294-bd9c-d102a2112fa1` |
| intent | ❌ not applicable (not an intent-classified flow) | ✅ `payment_due` (rule fallback) |
| workflow | ✅ known from source (the monitor script itself) | ✅ `employee_conversation` STEP_AMOUNT |
| generator function/module | ✅ `ai_console_monitor.sh::send_wa_alert()` | ✅ `employee_conversation/__init__.py:857-866` |
| template/KB source | ✅ hardcoded bash string, no KB | ✅ hardcoded Python string, no KB |

**Incident A's central finding: it is structurally invisible to every
one of Hermes's own tools** — `get_recent_messages`,
`audit_lookup_whatsapp_messages`, `lookup_decisions`, `get_dlq_status`,
`get_bridge_diagnostics` would all show *nothing* for it, because it
never touches any table those tools query. Confirmed by direct query:
zero `fazle_outbound_queue` rows, zero `hermes_decision_audit_log` rows
for the actual alert text (the only 2 decision-log hits found were
Hermes's *own* replies quoting the alert back, correctly marked
`chosen_action=no_action` by the existing cross-bridge echo guard — not
a new bug, working as designed).

**Incident B is fully deterministic end-to-end**, made possible
specifically by today's Task 1 provenance fix (`source_message_id`) —
this is a concrete, immediate real-world payoff of that change.

---

## D. Hermes agentic-behavior audit

Located and read the **exact real conversation** (session
`20260819_100105_e757da`, `~/.hermes/state.db`, 2026-08-19 08:01-08:10
UTC) — every claim below is a direct quote or a direct tool-call/result
pair from that transcript, not a reconstruction.

### What actually happened, turn by turn

1. Admin: "তুমি আলিম নামে কাউকে জান কিনা?" — Hermes answers from memory
   ("Alim, last-4 **1596**"), explicitly flags it as **unverified memory**,
   offers to check live, asks what it's needed for. *(Good instinct —
   correctly labeled memory as memory.)*
2. Admin: "তো তার কাছে অপ্রাসঙ্গিক ম্যাসেজ সিস্টেম থেকে যাচ্ছে কেন?" —
   Hermes says it can't verify yet, asks *which* system, proposes 5
   tools, asks "কোনটা দিয়ে শুরু করি?"
3. Admin: "Resolve করলে কি হবে" — Hermes explains `resolve_identity` is
   read-only (**and here misdescribes it — see below**), lists 6 tools
   in a table, asks "এখনই চালাই?"
4. Admin: **"চালাও, আমি এই ধরনের ম্যাসেজ আর যাক এটা যাতে না হয়"** — an
   explicit, unambiguous go-ahead.
5. Hermes fires 6 parallel tool calls. **Two fail immediately on a type
   error** (`resolve_identity`/`audit_lookup_whatsapp_messages` called
   with `phone: 1596` as a JSON **integer**, not a string — the schema
   requires a string). **One fails on a hallucinated parameter**
   (`lookup_kernel_events` called with `query: "Alim 1596"` — that
   parameter does not exist; the tool's actual schema is `trace_id`
   (required) + `limit`). Three succeed (`get_contacts`,
   `get_bridge_message_stats`, `audit_get_auto_reply_settings`).
6. Hermes retries — **makes the identical `int` mistake a second time**
   on both failed calls. The harness itself injects a
   `repeated_exact_failure_warning` telling Hermes this looks like a
   loop.
7. Hermes finally recognizes the type error correctly in prose ("phone
   কে string হিসেবে পাঠাতে হবে") — but then, instead of quoting the
   *same* known value (`"1596"`), **invents a placeholder string,
   `"0151...1596"`**, containing a literal ellipsis, and queries with
   that fabricated non-number.
8. Both calls "succeed" against the fabricated number and correctly
   return "not found" (of course — it isn't a real number). Hermes then
   states this as if it were real evidence: **"এই নম্বরটা DB/contact
   book/employee table কোথাওই Alim হিসেবে registered না... Memory
   আমার ভুল ছিল"** — drawing a conclusion from a query it invented, not
   from real data.
9. In the *same* message, Hermes partially self-corrects (recognizes
   "1596" was probably only the last-4 digits, proposes: (A) paginate
   `get_contacts` to find Alim's real number, (B) search `get_employees`),
   and separately reports a genuine, real finding it stumbled into along
   the way — a serious WhatsApp backlog (bridge1: 33 stuck >1h, bridge2:
   22 stuck >1h, `whatsapp`: 290 stuck >1h) — correctly refusing to touch
   any of it without instruction.
10. **But it never executes (A) or (B).** It stops and asks: **"আগে একটা
    confirm দরকার: Alim এর actual WhatsApp number কী?"** — despite the
    Admin having already said "চালাও" once, and despite Hermes itself
    having just proposed exactly this as the next safe step.

### Verified claims about specific tool capabilities

All six, checked against the real transcript and/or live source —
**five of six were wrong or unsupported by the tool's actual schema**:

| Claim | Verdict | Evidence |
|---|---|---|
| `resolve_identity` can return bridge/view information | **FALSE** | Hermes's own turn-3 description said it returns "কোন bridge, কোন view" — the real result (turn 19) has fields `role, employee_id, employee_name, designation, contact_id, display_name, company_name, relation_name, confidence, identity_role, identity_confidence, identity_source, identity_priority, identity_notes, identity_lifecycle`. No `bridge`, no `view`, anywhere. `identity_source` sounds similar but means the *identity-resolution method* (e.g. "seed_rule"), not the message's WhatsApp channel. |
| `lookup_kernel_events` searchable by phone/name | **FALSE** | Tool error, verbatim: `"missing required argument(s): trace_id... required: [trace_id]"`. No phone/name param exists in the schema at all. |
| `get_bridge_message_stats` answers recipient-specific traffic | **FALSE** | Called with zero params; returned only 5 rows of per-bridge aggregate totals (`bridge1/bridge2/meta/whatsapp/bridge3`), no per-recipient breakdown possible — the function signature (`hours: int = 24`) has no phone param at all. |
| `get_contacts` supports search or pagination | **FALSE (both)** | No `search`/`name` param exists; Hermes itself said "সেটা paginated, আরো page আনতে হবে" (it's paginated, need more pages) — but the tool's actual signature is `get_contacts(limit: int = 30)`, **no offset/cursor param exists at all**. There is no way to page past the first `limit` contacts. Hermes's belief that pagination was even possible was itself inaccurate. |
| `get_employees` searchable by name/phone | Not tested live (proposed, never called) | Source confirms `get_employees(status: str = "active")` — no name/phone param. |
| `audit_lookup_whatsapp_messages` normalizes phone forms | **TRUE (from source; not meaningfully tested live)** | `app/main.py:1147-1150` — tries `normalize_phone()`, falls back to `phone_last10` substring match. Both real calls in this transcript used either the rejected integer or the fabricated placeholder, so the transcript itself doesn't exercise this — but the code is correct. |

### Category breakdown

- **Tool gap (real)**: `get_contacts`/`get_employees` genuinely cannot
  search by name or phone, and `get_contacts` has no pagination at all.
  Even a perfectly-behaving Hermes could only ever see the first N
  contacts/employees — Alim might never surface if outside that window.
- **Prompt/policy gap (real)**: the system prompt (identical, resent
  every turn) says every "destructive/state-changing action" needs a
  yes/no first, but never distinguishes Tier-A reads from that
  requirement — so the model defaults to asking permission for
  demonstrably safe reads three times in a row (turns 2, 4, and again
  after the Admin's explicit "চালাও"). **`HERMES_AUTONOMY_POLICY_
  2026-08-19.md` (written today, hours after this conversation) is not
  yet wired into Hermes's actual runtime system prompt** — it exists
  only as a repo doc right now.
- **Memory discipline gap (partial)**: Hermes correctly flagged its
  initial claim as unverified memory (good) — but later stated "memory
  আমার ভুল ছিল" as a conclusion drawn from a fabricated, meaningless
  query result, not real evidence. It never stated "1596 = Alim" as
  unqualified fact (matches the desired behavior), but its evidence
  handling around the invented placeholder number was sloppy.
- **Orchestration gap (the core finding)**: Hermes proposed its own
  next steps (A/B above) and then didn't execute them — stopping to ask
  the Admin instead, one turn after the Admin had already said "proceed."
  This is the direct, transcript-confirmed match for the reported
  behavior.
- **Data-quality gap**: `employee_id=695` unresolved (see Incident B) —
  unrelated to the Alim conversation specifically (different phone
  number, never established as connected), but a real, independently-
  confirmed systemic gap worth fixing on its own merits.
- **Permission/mode gap**: **none found** — mode/RBAC handling was
  correct throughout; Hermes never attempted a write, correctly refused
  to touch the backlog/auto-reply settings without instruction. This
  part of the system worked exactly as intended.

---

## E. Minimum fixes (not implemented — audit only)

Ordered by leverage, smallest change first.

1. **`ai_console_monitor.sh`: parallelize the 4 provider checks in
   `app/llm.py::check_health()`** (or, even smaller: move `omniroute`
   to run last / give it its own shorter timeout inside the existing
   `asyncio.wait_for`) so one slow provider can't block the whole
   `/chat/models` response past the monitor's 5s window. Existing-tool
   reuse: no new check needed, just reorder/parallelize what's already
   there (`asyncio.gather` instead of 4 sequential `await`s).
2. **`get_conversation_session()`: add a session-age guard** — e.g.
   `AND created_at > NOW() - INTERVAL '<N hours>'`, closing/ignoring
   anything older, matching the exact staleness-cleanup pattern
   `_create_conversation_session()` already implements elsewhere in the
   same file. Smallest possible fix: one added SQL clause, reusing the
   existing STEP_DONE/rejected/cancelled close-out mechanism as the
   template.
3. **System-prompt/operating-rule correction (no code change)**: state
   explicitly that Tier-A reads (per `HERMES_AUTONOMY_POLICY_2026-08-19.md`)
   never need a per-call yes/no, and that once the Admin has said
   "proceed"/"চালাও" once for an investigation, Hermes should keep
   pursuing every remaining safe read in that investigation until it
   hits a real wall (both search paths exhausted, not just its own
   tool-call mistake) before asking again.
4. **Fix the tool-call type/parameter bugs directly** (existing-tool
   reuse, no new tools): this is the model correctly *not* being told
   `phone` must be a JSON string vs int, and inventing a `query` param
   `lookup_kernel_events` never had. A short, explicit reminder in the
   relevant tool docstrings ("phone must be a quoted string, e.g.
   `\"1596\"`, never a bare number") plus removing any ambiguity about
   `lookup_kernel_events`'s single required param would reduce (not
   eliminate) this same class of error already tracked in
   `[[project_hermes_toolcall_reason_bug_audit_20260811]]`.
5. **`ai_console_monitor.sh`: route the alert through the existing
   outbound pipeline instead of a raw bridge POST** — smallest viable
   version: call fazle-core's own `/admin/send-whatsapp` (already
   `source_bridge`-validated, already queued/audited/traceable) instead
   of `curl POST localhost:8081/api/send` directly. This alone would
   make Incident A traceable by every existing Hermes read tool with
   zero new infrastructure.
6. **`employee_id=695` linking gap**: separate, smaller-priority fix —
   whatever created this dangling FPE employee reference (surfaced twice
   now, independently) should either resolve to a real employee or the
   session/draft creation path should decline to proceed past "Unknown."
   Not proposing a new classifier — this is existing employee-resolution
   logic returning a bad reference, worth its own narrow follow-up.

None of these were implemented this session — audit only, per the task's
explicit instruction.

---

## F. Acceptance tests (for when fixes are implemented)

1. Admin asks "আলিমের কাছে এই message কেন গেল?" → Hermes performs the
   full safe-read chain (`resolve_identity`, `get_contacts` with correct
   pagination/limit, `get_employees`, `audit_lookup_whatsapp_messages`)
   autonomously, without asking permission per tool, once given a single
   go-ahead for the investigation.
2. Hermes never states "1596 = Alim" (or any resolved identity) as fact
   before a canonical `resolve_identity`/`get_contacts` call actually
   confirms it with a real, non-fabricated identifier.
3. A stale `employee_conversation` session older than its staleness
   threshold cannot silently intercept an unrelated later message — it
   is either closed or the new message starts a fresh classification.
4. Every system-generated outbound message under `ai_console_monitor.sh`
   is traceable via `fazle_outbound_queue`/`hermes_decision_audit_log`
   like any other Hermes-visible send.
5. READ mode continues to block `approve_payment_draft`/`send_whatsapp_message`-class
   writes but never blocks `resolve_identity`/`get_contacts`/`get_employees`-class
   reads, even for several tool calls in a row within one investigation.
