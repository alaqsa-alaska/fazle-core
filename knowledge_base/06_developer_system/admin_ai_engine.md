# Admin AI Engine — Developer Notes

**See also:** `docs/ADMIN_AI_ENGINE.md` (architecture/rollback/ops reference), `docs/DEVELOPER_GUIDE.md` §H (message parser/AI/RAG flow this system builds on), `CANONICAL_BUSINESS_RULES.md` §Roles for the RBAC hierarchy.

This article explains, for a developer working on this codebase, how the WhatsApp admin AI commands, the per-number auto-reply gate, admin directive storage, and browser TTS actually work under the hood — not just what they do.

---

## How WhatsApp admin AI commands work

An inbound WhatsApp message from an identified `admin`-role sender reaches `modules.message_router.process_message()`'s admin branch (`role_str == "admin"`). The checks run **in this exact order**, and each one that matches returns immediately:

1. `is_completed_escort_draft(text)` — escort finalization
2. `is_admin_command(text)` — the large deterministic-regex command set (`PAID`, `APPROVE`, etc.)
3. `is_nl_admin_query(text)` — `nl_router`'s two no-LLM NL queries (`chat_history`, `last_contact`)
4. **`is_admin_ai_command(text)` — the new 5-command set this article covers**
5. Three bare-substring fallbacks (`draft`/`payment`/`attendance`)
6. Hardcoded help text

`is_admin_ai_command()` (`modules/admin_directives/router.py`) is a synchronous, exception-swallowing regex match against 5 patterns: `AI`/`এআই` (question), `AUTOREPLY ON|OFF|STATUS <phone>`, `REMEMBER <text>`, `FORGET <id>`, `SEND <phone> | <message>`. If it matches, `process_admin_ai_command()` dispatches to one of 5 async handlers.

**Every handler calls `modules.rbac.check_permission()` first**, via a shared `_authorize()` helper that fails closed on any exception (never fails open) and audits every attempt via `rbac.record_audit()`, whether allowed or denied. None of the 5 command names are registered in `rbac.COMMAND_ROLE` — they fall back to `DEFAULT_REQUIRED_ROLE = "admin"`, which is exactly the tier every one of them needs, so `modules/rbac/__init__.py` itself was never touched.

**Only `AI <question>` reaches an LLM.** It calls `modules.ai_console.orchestrator.orchestrate_console_request()` — the exact same function the web `/chat/message` endpoint uses — with `include_provider_response=True` (without this, the orchestrator assembles evidence but never actually calls a provider; this is easy to miss, since the parameter defaults to `False`). The reply text is extracted the same way the web endpoint does: `provider_result.get("response") or evidence.get("response") or "উত্তর পাওয়া যায়নি।"`.

**`AUTOREPLY`/`REMEMBER`/`FORGET`/`SEND` never touch an LLM at all** — they're plain Python dispatch to `modules.wa_chat_frontend`'s runtime-setting helpers, `modules.admin_directives`'s CRUD functions, and `shared.draft_reply.create_draft_reply()` respectively. This split is deliberate: a prior incident (tagged `B25 (H4)` in `message_router.py`'s comments) found that letting unmatched admin text reach an LLM produced garbage replies that got silently queued as drafts. The fix at the time was to cut admin text off from the LLM entirely and return static help instead. Reopening a WhatsApp-to-LLM path required guaranteeing that failure mode couldn't recur — achieved by (a) requiring an explicit keyword trigger, never a fallthrough, and (b) never letting the LLM's own output cause a write; only the admin's own typed `SEND` command can create a draft, never the AI's free-text response to a question.

**Output sanitization:** `_sanitize_ai_reply()` prefix-warns (never strips) if the LLM's reply text happens to start with a command-like keyword (`PAID `, `APPROVE `, etc.), so a human skimming WhatsApp can't mistake a quoted/discussed command for an actual instruction, and truncates at 3500 chars with a spillover note pointing to the web chat for the full answer.

**Rate limiting:** a plain in-process `dict[str, float]` (`_ai_cooldown`), 5 seconds per admin phone, checked before RBAC (so a denied/unauthorized attempt doesn't consume the cooldown) and updated only after a successful, authorized call is about to be made. No new table — deliberately simple, single-process, GIL-safe.

**Voice notes:** no special handling needed. `modules.bridge_poller` already transcribes *any* inbound audio (admin or not) into `text` before role-based routing happens — an admin's voice note "AI, ..." arrives as already-transcribed text and hits the same `is_admin_ai_command()` check as typed text.

---

## How the auto-reply gate works

Two independent gates exist, layered:

1. **Global:** `AUTO_REPLY_ENABLED` env var — SAFE MODE, blocks all auto-sends system-wide. Untouched by this work.
2. **Per-number:** `fazle_runtime_settings` key `phone_block.{phone}`, read by `modules.bridge_poller._is_draft_always(phone, role, display_name)` — the function that decides whether a given inbound message must be drafted for admin review instead of auto-replied. This check runs *first*, before the pre-existing static `DRAFT_ALWAYS_PHONES`/role/name-prefix checks, so it can force draft-mode for a number that wouldn't otherwise match any static rule.

The flag itself was not new — `POST /api/wa/contacts/{phone}/block|unblock` already wrote it — but nothing read it until `_is_draft_always()` was extended. This is why the fix is described as "wiring a dead control live" rather than "building a new gate."

**Important implementation detail:** `_is_draft_always()` had to become `async def` (it now does a DB read via `get_runtime_setting()`), which meant finding and fixing *every* call site, not just the one inside `bridge_poller` itself. A second call site existed in `modules.social_auto_reply.planner_worker._is_draft_always_sender()` (deliberately reusing the same function rather than re-implementing the match rules, per that function's own docstring) — missed on the first pass because the initial search was file-local (`grep` scoped to `bridge_poller/__init__.py` only), not repo-wide. Calling an async function without `await` returns a truthy coroutine object rather than raising, so this bug was silent — it made `_is_draft_always_sender()` always return `True`, silently forcing every social-auto-reply message into draft mode. It was only caught by running the *full* test suite and diffing against a clean baseline, not by the smaller targeted regression suites used after earlier steps. **Lesson for any future async conversion in this codebase: grep the function name repo-wide, not file-local, before assuming you've found every caller.**

---

## How admin directives are stored

`fazle_admin_directives` (`db/migrations/059_admin_directives.sql`) — a narrow, purpose-built table, explicitly *not* a reuse of `modules.ollama_memory`'s tables (`ai_memory_facts`/`ai_memory_questions`/`ai_memory_tasks`), which are documented as the AI's own isolated self-memory, written only by the AI about facts it infers — a different concern from an admin explicitly saying "remember X."

Columns: `directive_text`, `category` (CHECK-constrained: `general`/`autoreply_policy`/`contact_note`/`escalation`), `subject_type`/`subject_key` (nullable — global directives have both null), `created_by_phone`/`created_by_channel` (CHECK: `whatsapp`/`web`), `status` (CHECK: `active`/`revoked`), soft-revoke audit fields. The CHECK-constraint choice deliberately follows `escort_roster_change_requests`' pattern (migration 026), which `CANONICAL_BUSINESS_RULES.md`'s own Approval section names as the model every other approval-style table in this codebase should have used but didn't (most status columns here are plain unconstrained TEXT, a documented drift problem).

`modules.admin_directives` exposes `save_directive()`/`list_directives()`/`get_directive()`/`revoke_directive()` — plain `app.database.fetch_val/fetch_all/fetch_one` calls, matching the codebase's dominant DB-access style (not `ollama_memory`'s raw-asyncpg-pool style, which is an intentional outlier for that module's isolation guarantee).

Three surfaces write/read the same table: WhatsApp `REMEMBER`/`FORGET`, the AI Console tools `BUS-015` (read)/`BUS-016` (write, both `min_role=admin`), and the web `GET/POST /admin/directives` + `POST /admin/directives/{id}/revoke` REST routes.

---

## How TTS works

Purely client-side, in `app/static/open-chat.html` — `window.speechSynthesis` (Web Speech API), no backend call, no new dependency. `appendMessage()` (the single function that renders both user and bot bubbles) gained one conditional call to `renderTTSButton()` when `role !== "user"`. `speakText()` cancels any in-flight speech first, detects Bengali via a Unicode-range regex on the reply text to pick `bn-BD` vs `en-US`, and toggles the clicked button's icon between 🔊 and ⏹ via the utterance's `onstart`/`onend`/`onerror` callbacks. Voice availability and quality depend entirely on the browser/OS — there is no server-side fallback (that would be "Option B," deliberately not built, to keep this addition zero-risk to the existing chat flow).

---

## Security model

- **The LLM never receives a write-capable tool.** `orchestrate_console_request()`'s adapters are all `Readonly*`/`AIMemoryAdapter` — confirmed by `modules.ai_console.adapters.py`'s own `prohibited_side_effects` declarations per adapter class, unchanged by this work.
- **Every write path in this system requires an explicit, code-decided (not model-decided) trigger.** `AUTOREPLY`/`REMEMBER`/`FORGET` write directly (to a runtime flag or the narrow directive table — never a business/financial table). `SEND` never sends — it only creates a `fazle_draft_replies` row, subject to the exact same human-approval queue every other AI-suggested reply already goes through.
- **RBAC fails closed everywhere in this system**, including the new surfaces — an exception during a permission check is treated as denial, matching the system-wide rule established after the 2026-07-09 fail-open fix in `modules.admin_commands`.
- **Rate limiting exists only for the LLM-calling path** (`AI <question>`), not the deterministic commands — those are cheap, local operations with no external call to protect against abuse of.
