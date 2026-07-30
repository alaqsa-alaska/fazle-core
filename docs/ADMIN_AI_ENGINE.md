# Unified Admin AI Engine

**Status:** Implemented and merged (Phase 3, Steps 1-7). Not yet deployed/restarted in production as of this writing — see `docs/DEVELOPER_GUIDE.md` §K / `OPERATIONS.md` for the deploy gate.
**Companion docs:** `docs/DEVELOPER_GUIDE.md` (module reference), `docs/MODULE_MAP.md` (module → route → table map), `knowledge_base/06_developer_system/admin_ai_engine.md` (developer-facing internals).

---

## Architecture overview

The goal was to make the already-live **AI Console** (`modules.ai_console`, `ai_console_chat_enabled=true` since 2026-07-19) the single intelligence core reachable from both the web admin console (`/open-chat`) and WhatsApp admin messages, without rebuilding anything already working and without ever letting an LLM take a side-effecting action directly.

```
open-chat.html (web)                    WhatsApp admin message
      │                                          │
      ▼                                          ▼
POST /chat/message                    modules.message_router
      │  (existing, unchanged)               (role_str == "admin")
      │                                          │
      │                              is_admin_command() / is_nl_admin_query()
      │                                  (existing, unchanged — always win)
      │                                          │
      │                              is_admin_ai_command()  ← NEW (Step 6)
      │                              modules.admin_directives.router
      │                                          │
      └──────────────┬───────────────────────────┘
                      ▼
      modules.ai_console.orchestrator.orchestrate_console_request()
                      │
        ┌─────────────┼─────────────┬──────────────┐
        ▼             ▼             ▼              ▼
  ReadonlyRuntime  ReadonlyBusiness  AIMemory   Ollama → Groq →
     Adapter          Adapter        Adapter    GitHub Models
  (RT-001..015)   (BUS-001..016)                (LLM chain)
```

**Key design decision:** every WhatsApp write action (`AUTOREPLY`, `REMEMBER`, `FORGET`, `SEND`) is a **deterministic regex command**, never an LLM tool-call. The LLM is only ever reached for the read-only `AI <question>` path. This is a deliberate, permanent architectural boundary — see "RBAC model" below.

---

## WhatsApp command reference

```
=== WhatsApp Admin AI — Command Reference ===

Read / Ask AI:
  AI: <your question>
  এআই: <আপনার প্রশ্ন>
  Example: AI: who received payment this week?

Auto-reply control:
  AUTOREPLY OFF 01712345678
  AUTOREPLY ON 01712345678
  AUTOREPLY STATUS 01712345678

Remember an instruction:
  REMEMBER <instruction text>
  Example: REMEMBER always draft messages to VIP clients

Forget a saved instruction:
  FORGET <id>
  Example: FORGET 42

Send a message (creates draft, not sent directly):
  SEND 01712345678 | <message text>
  Example: SEND 01712345678 | আপনার কাগজপত্র রেডি আছে।
  (Admin must approve the draft before it is sent)

Rate limit: 1 AI query per 5 seconds per admin number.
All commands require admin role. Unauthorized use is denied and logged.
```

Implementation: `modules/admin_directives/router.py` — `is_admin_ai_command()`/`process_admin_ai_command()`, wired into `modules/message_router/__init__.py` after `is_admin_command()`/`is_nl_admin_query()`, before the hardcoded-help fallback (so unmatched text still gets help, never the LLM — the B25(H4) fix stays intact by construction).

---

## Web `/open-chat` features

All pre-existing features (chat, model list, memory stats/history, file upload/OCR, KB search, read-only business tools, voice transcription, LLM usage stats, KB admin) are unchanged. Two additions:

- **Voice input** (Stage A-C, already live since 2026-07-19, re-verified in Step 7A): click 🎤 → record → click stop → transcript appears in an editable box → **Send is always manual**, never auto-triggered.
- **Voice reply (TTS)** (new, Step 7B, browser-native `window.speechSynthesis`, Option A): every bot reply bubble gets a 🔊 button. Click to hear it spoken; click again (now showing ⏹) to stop; auto-reverts to 🔊 when speech ends naturally. Language auto-detected (Bengali Unicode range → `bn-BD`, else `en-US`). Purely client-side — no backend call, no new dependency.

---

## Auto-reply gate

**Before this work:** `POST /api/wa/contacts/{phone}/block|unblock` (pre-existing) wrote a `phone_block.{phone}` flag into `fazle_runtime_settings`, but nothing read it — a dead control.

**Now (Step 3):** `modules.bridge_poller._is_draft_always()` checks `phone_block.{phone}` first, before its existing static `DRAFT_ALWAYS_PHONES`/role/name-prefix checks. If `"true"`, that phone's messages are always drafted for admin review, never auto-sent.

Three ways to toggle it, all converging on the same `fazle_runtime_settings` key:
1. Web: the existing block/unblock button in `wa_chat.html`.
2. WhatsApp: `AUTOREPLY OFF/ON/STATUS <phone>` (new).
3. Directly: `POST /api/wa/contacts/{phone}/block|unblock` (pre-existing API).

This is layered *underneath* — not instead of — the existing global `AUTO_REPLY_ENABLED` env-var SAFE MODE switch, which is untouched.

**A second call site existed** (`modules/social_auto_reply/planner_worker.py`) reusing the same `_is_draft_always()` function — see "Known limitations / lessons" below for the regression this caused and how it was fixed.

---

## Admin directive memory

A new, narrow table — `fazle_admin_directives` (`db/migrations/059_admin_directives.sql`) — deliberately separate from `modules.ollama_memory` (which is the AI's own isolated self-memory, not an admin-authored-instruction log).

Stores: `directive_text`, `category` (`general`/`autoreply_policy`/`contact_note`/`escalation`, DB-CHECK-constrained), `subject_type`/`subject_key` (optional scoping, e.g. `phone`/a number), `created_by_phone`/`created_by_channel` (`whatsapp`/`web`), `status` (`active`/`revoked`, CHECK-constrained), soft-revoke fields.

Read/write via `modules.admin_directives`: `save_directive()`, `list_directives()`, `get_directive()`, `revoke_directive()`. Reachable from:
- WhatsApp: `REMEMBER`/`FORGET`
- AI Console tools: `BUS-015` (read, `min_role=admin`), `BUS-016` (write, `min_role=admin`) — reachable via the LLM's tool selection inside `orchestrate_console_request()`, not a raw write path
- Web REST: `GET/POST /admin/directives`, `POST /admin/directives/{id}/revoke`

---

## RBAC model

- Every new WhatsApp command calls `modules.rbac.check_permission()` before doing anything, **fails closed** on any exception (never fails open), and every attempt — allowed or denied — is audited via `modules.rbac.record_audit()`.
- None of the 5 new WhatsApp command names, and neither new HTTP route's command names, are registered in `modules.rbac.COMMAND_ROLE` — they all fall back to `DEFAULT_REQUIRED_ROLE = "admin"`, which already matches the required tier for every one of them. **Zero changes to `modules/rbac/__init__.py` were needed.**
- **The LLM is never given a write tool.** `AI <question>` only ever reaches `ReadonlyBusinessAdapter`/`ReadonlyRuntimeAdapter` (read-only, RBAC-gated per tool via `min_role` in registry metadata) — `AUTOREPLY`/`REMEMBER`/`FORGET`/`SEND` are Python-regex-dispatched, decided by code, never by model output. This is the direct mitigation for the prior B25(H4) incident (unmatched admin text used to reach the LLM and produce garbage drafts).
- `SEND` never sends directly — it only calls `shared.draft_reply.create_draft_reply()`, landing in the existing `fazle_draft_replies` human-approval queue, identical to every other AI-drafted reply in this system.

---

## Known limitations

1. **`RT-015`'s `min_role` is descriptive only** — `ReadonlyRuntimeAdapter.fetch()` has no per-tool RBAC check at all (confirmed by signature inspection: no `actor_role_level` parameter), unlike `ReadonlyBusinessAdapter`. Future work: add the same RBAC-check pattern to `ReadonlyRuntimeAdapter.fetch()` and thread `actor_role_level` through `orchestrator.py`'s RT-dispatch call site.
2. **`test_phase8c_structured_evidence.py` has a pre-existing stale-mock bug**, unrelated to this work — its `fake_console_request()` mock doesn't accept `actor_role_level`, a parameter that predates this work (added in Stage E, 2026-07-19). Future work: update that test's mock signature.
3. **Browser TTS quality/voice availability depends on the admin's OS/browser** — Bengali voice is not guaranteed on every device. Future Option B (not built): a backend TTS endpoint.
4. **`SEND`'s target bridge is a hardcoded default** (`bridge2`, matching `modules.outbound.enqueue()`'s own default), not detected from the target's actual conversation context. Future work: resolve the correct bridge from the target's contact/history if cross-bridge accuracy becomes an issue.
5. **No `session_id`/conversation continuity for WhatsApp AI queries** — `orchestrate_console_request()` has no `session_id`/`channel` parameter at all (confirmed by direct signature read); each `AI <question>` is stateless. Future work: add session/history support to the orchestrator if needed.
6. **The `/admin/directives` REST endpoints were a later addition** (implemented after this document's first draft) — see "Files changed" below for their final status.

---

## Rollback procedures

**Component 1 — WhatsApp admin AI commands:** revert `modules/message_router/__init__.py`'s Step 6 diff (14 lines: 1 import + 1 if-block). Every other admin command (`PAID`, `APPROVE`, `chat_history`, etc.) is on a different, unaffected code path.

**Component 2 — Auto-reply gate (`bridge_poller` + `planner_worker`):** disable per-number without any code change via `POST /api/wa/contacts/{phone}/unblock` or `AUTOREPLY ON <phone>`. To revert the code entirely, revert **both** `modules/bridge_poller/__init__.py` (Step 3) **and** `modules/social_auto_reply/planner_worker.py` (Step 7 fix) together — they are now interdependent (`_is_draft_always` is `async`, awaited in both call sites); reverting only one half reintroduces a bug.

**Component 3 — Admin directives DB table:** revert `modules/admin_directives/*` and the `/admin/directives` routes in `app/main.py` first; `DROP TABLE fazle_admin_directives;` is safe afterward (confirmed zero FK dependencies in either direction).

**Component 4 — New AI Console tools (`RT-015`, `BUS-015`, `BUS-016`):** revert `modules/ai_console/registry.py` and `modules/ai_console/live_adapters.py`'s additive diffs — every insertion was additive-only (0 deletions), so this is a clean, isolated revert with zero impact on `RT-001..014`/`BUS-001..014`.

**Component 5 — TTS button:** revert `app/static/open-chat.html`'s Step 7B diff. Text chat, voice input, file upload are completely unaffected.

**Component 6 — `planner_worker.py` fix:** **must not be rolled back independently.** It is a required correctness fix for Component 2, not a separable feature — see Component 2 above.

---

## Feature flags

```
Name: ai_console.chat_enabled
Current value: documented as true since 2026-07-19 — not independently re-verified live in this pass
Effect if changed: false forces /chat/message onto the legacy admin_chat_lab path (untouched, still fully intact)
Where set: fazle_runtime_settings DB table, key "ai_console.chat_enabled"

Name: AUTO_REPLY_ENABLED
Current value: documented as true per management_decisions.md S11-02 (2026-06-23) — not independently re-verified live
Effect if changed: false = global SAFE MODE, blocks all auto-sends except admin approvals (coarse, existing, untouched)
Where set: .env

Name: phone_block.{phone}   (per-number, now live as of Step 3)
Current value: unset per phone by default (defaults to "false"/not-blocked)
Effect if changed: "true" forces that number's messages into draft-only; "false"/absent = normal eligibility
Where set: fazle_runtime_settings DB table
```

No new standalone `.env` variables were introduced — every new control uses the pre-existing `fazle_runtime_settings` key-value mechanism.

---

## Files changed

| File | Action | Purpose |
|---|---|---|
| `db/migrations/059_admin_directives.sql` | Created | `fazle_admin_directives` schema |
| `modules/admin_directives/__init__.py` | Created | Directive CRUD |
| `modules/admin_directives/router.py` | Created | WhatsApp command dispatch |
| `modules/bridge_poller/__init__.py` | Modified | Wire `phone_block.{phone}` into `_is_draft_always()`, made async |
| `modules/ai_console/registry.py` | Modified | `RT-015`, `BUS-015`, `BUS-016` |
| `modules/ai_console/live_adapters.py` | Modified | 3 new handlers |
| `modules/observability/db_queries.py` | Created | `get_recent_errors()` |
| `modules/message_router/__init__.py` | Modified | WhatsApp AI trigger wiring |
| `modules/social_auto_reply/planner_worker.py` | Modified | Regression fix: missing `await` on second `_is_draft_always()` call site |
| `app/static/open-chat.html` | Modified | Browser-native TTS |
| `app/main.py` | Modified | `GET/POST /admin/directives`, `POST /admin/directives/{id}/revoke` |
| `tests/unit/test_bridge_poller_phone_block.py` | Created | 4 tests |
| `tests/unit/test_ai_console_registry_new_tools.py` | Created | 12 tests |
| `tests/unit/test_admin_ai_router.py` | Created | 41 tests |
| `tests/unit/test_admin_directives_routes.py` | Created | REST endpoint tests |
