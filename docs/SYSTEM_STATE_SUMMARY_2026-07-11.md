---
title: Fazle Core — Full System State Summary
owner: Fazle Core Admin
status: active
effective_from: 2026-07-11
runtime_index: true
---

# Fazle Core — Full System State Summary (2026-07-11)

This is a grounded, verified snapshot of what actually runs in production today —
not the aspiration, not the original design doc. Every claim below was checked
against real code (file:line) or real DB state on the date above. Where a
feature is partial, dead, or aspirational rather than live, that is stated
explicitly rather than glossed over — this document is only useful if it's
honest about gaps.

---

## 1. App Workflow — One Message, Start to Finish

```
WhatsApp message arrives (bridge1 / bridge2 / Meta webhook)
  → bridge_poller reads it (SQLite bridge DB → Postgres wbom_whatsapp_messages)
  → media_type check: text | image | audio/ptt | document/pdf
      → non-text: OCR / ASR / doc-extraction (see §2), result becomes text
  → message_router.process_message(sender, text, source)
      → silent-skip check (accountant-phone routing, draft-always-name contacts)
      → identity_brain.detect_identity(phone, text) → role + confidence
      → intent classification (LLM first, keyword rules as fallback)
      → route to the matching handler: payment, attendance, escort order,
        recruitment, general chat, admin command, etc.
  → handler either:
      (a) auto-sends a safe reply directly, or
      (b) creates a draft in fazle_draft_replies for admin review, or
      (c) (bridge2/admin-authored messages only) writes directly to the
          ledger/roster as an authoritative action
  → admin reviews pending drafts via payroll.html's Review tab or WhatsApp
    APPROVE <id>, both routed through the same shared finalize services
```

The single most important structural fact about this system: **almost every
inbound path that isn't from the admin/bridge2 number itself ends in a draft,
not a direct write.** The exceptions (escort confirmation from bridge2, manual
admin CRUD, WhatsApp `APPROVE`) are all deliberately gated to admin-controlled
identities.

---

## 2. Media Conversion — Real, Wired, Running

All three converters below hit a live sidecar process (`shared/media/media-processor/server.py`, confirmed running on `127.0.0.1:8090` at time of writing) — none of this is scaffolding.

- **Escort release-slip OCR** (`modules/escort_slip_extractor/__init__.py`, 944 lines): 3-pass OCR merge (media-processor `/ocr` + direct tesseract CLI `eng+ben` + ImageMagick-preprocessed pass), then structured field extraction (vessel names, phones, dates, signatures, a confidence score) for both printed-template and handwritten slips. Wired from `bridge_poller/__init__.py:838-846`, after an eligibility pre-filter. Results save to **`escort_slip_extractions`** — a standalone table, **not FK-linked** to `wbom_escort_programs`. Program matching happens live, per-message, in `modules/escort_lifecycle/__init__.py::handle_ocr_release_slip()` (phone match first, fuzzy vessel/escort-name match as fallback) — and the result always becomes a **draft for admin review**, never an automatic program close.
- **PDF / document extraction**: real, via PyMuPDF/python-docx on the sidecar (`modules/ocr_processor/__init__.py:118-145`). Classifies candidate-document type (CV/NID/certificate/passport) and auto-acks safe types. No structured-field table — only a 300-char text snippet is stored back on the message row.
- **Audio transcription**: real, via faster-whisper on the sidecar (`modules/voice_processor/__init__.py:34-81`), gated on a minimum word-count confidence, falls through to normal text-intent classification.

---

## 3. Role & Intent Routing

**Identity resolution** (`modules/identity_brain/__init__.py`) is an 11-step, phone-only, first-match-wins lookup:

```
admin (env ADMIN_NUMBERS/bridge numbers, checked first, always wins)
→ seed rules (fazle_contact_roles: family/accountant/vip_client/client_escort_buyer/vendor/supervisor)
→ repeat_client (payment history) → employee (wbom_employees, 3 phone variants + 4 secondary evidence sources:
  cash transactions, attendance, escort roster, contact DB) → contact DB → text-hint candidate → unknown
```

Two important, verified facts about this: (1) **the "blocked" role no longer exists** — removed entirely on 2026-07-11 (migration 042), a sender is now strictly "known role" or "unknown," nothing else; (2) **employee lookup is phone-only, no name fallback** — an employee texting from a genuinely new number is treated exactly like a stranger.

**Bug found and fixed, same day**: step 4 (contact DB lookup, `_lookup_contact()`) was querying two columns that don't exist on `wbom_contacts` (`contact_name`/`contact_type` — real columns are `display_name`/`relation`), raising an uncaught `asyncpg.UndefinedColumnError` for any sender who isn't an admin/employee/escort-roster match but does have a `wbom_contacts` row (2,086 such rows at time of fix — clients, vendors, VIPs). Zero occurrences were found in the error log before the fix, so this was verified as a real, dormant bug rather than one observed actively firing in production. Fixed as a 2-column rename, nothing else touched.

**Intent classification**: LLM-first (`ai.classify_intent_llm`), falling back to keyword-rule classification (`modules/intent`) only if the LLM returns `"unknown"`.

**RBAC (dashboard/API access, distinct from the above)**: `fazle_roles` hierarchy —
```
viewer(10) < office_assistant(20) < operator(30) < accountant(50) < admin(70) ≤ superadmin(100)
```
`office_assistant` was formalized 2026-07-11 (view attendance list + submit draft entries only, no edit/approve/salary). As of the same date, all 45 `fazle_payroll_engine` API endpoints are individually tiered by this hierarchy (not a blanket "any admin account" check) — see git history for the full audit.

---

## 4. Data Layer

- **Backend**: FastAPI + asyncpg, no ORM, no build step (`run.py` → uvicorn, restart loads whatever's on disk).
- **Frontend**: static HTML/JS pages served directly (`app/static/*.html`) — `payroll.html` (main admin dashboard, tabs gated by RBAC), `drafts.html` (review/approve/edit/delete queue), `open-chat.html` (AI console).
- **DB**: single governed migration path, `db/migrations/` (43 files as of this document), scratch-validated before production application per project convention.

---

## 5. Contact Book (`modules/contact_sync`) — ⚠️ Partially Dead Code

The docstring claims merging bridge1 + bridge2 + Meta into `wbom_contacts` as "the central table." Verified reality: **Meta has no reader implemented**; the hourly sync loop (`start_contact_sync_loop`) and the on-message upsert hook (`upsert_contact_from_message`) are both **defined but never called anywhere** — dead code. The only live trigger is a manual admin API call (`POST /api/wa/contacts/sync`). Practical implication: `wbom_contacts` is likely stale relative to live traffic unless someone is manually triggering sync. Dedup is phone+platform only — there's no join key to `wbom_employees`/`fpe_employees`, so the same real person can legitimately exist as unrelated rows across these tables today.

---

## 6. Employee Identity — the `employee_id_phone` Question

You asked that all search/lookup be linked to `employee_id_phone`, one employee_id_phone = one unique employee. **Current reality is more fragmented than that goal:**

- There are **two employee tables**: `wbom_employees` (the original, escort/attendance-facing) and `fpe_employees` (the payroll-engine's own, with `employee_id_phone`, alias table, fuzzy-name matching, canonical-duplicate soft-links). A cross-lookup bridges them by phone (`modules/fazle_payroll_engine/employee.py`'s step 3b), but they are not one unified table.
- Within `fpe_employees`, `employee_id_phone` **is** the intended unique identity key, and as of the 2026-07-10/11 investigation this session, the matching priority was fixed so `employee_id_phone` is checked *before* payout phone (previously backwards — a real misattribution bug, since fixed).
- `wbom_contacts` (the general contact book) has no join key to either employee table beyond raw phone string matching.

**Honest assessment**: the "one employee_id_phone, one employee" invariant is enforced *within* `fpe_employees`, but not yet as a single cross-system identity — `wbom_employees`/`wbom_contacts`/`fpe_employees` are three tables reconciled by phone-matching heuristics at read time, not by a shared foreign key. Unifying this fully would be a real, scoped project, not a quick fix.

---

## 7. Cash Transactions, Ledger, Employee Creation

- **Ledger table**: `fpe_employee_ledger` (running per-period totals), updated by `modules/fazle_payroll_engine/accounting.py::create_transaction()` — the single, idempotent (via `txn_ref` hash) entry point that also writes the immutable `fpe_cash_transactions` row.
- **Auto-create-on-payment**: `match_or_create_employee()` (`modules/fazle_payroll_engine/employee.py`) creates a new `fpe_employees` row when no existing match is found *and* at least one phone is present — never on name alone (a phone-less, name-less match returns `None` rather than creating a phantom record).
- **entry_source governance**: `wbom_attendance.entry_source` is a hard CHECK-constrained vocabulary (`manual_office | message_parser | whatsapp_escort | escort_release | system | legacy`) — this is a payroll-*eligibility* category, not a full provenance log. Full provenance (which exact draft, which module) is preserved via `source_draft_id → fazle_draft_replies.meta`.

---

## 8. Escort Client Order — Full Lifecycle

```
Intake: modules/escort/__init__.py::handle_escort_client_message()
  (gated to trusted sources — bridge1/bridge2/meta whitelist)
  → parse_escort_message() → wbom_escort_programs (draft) + roster draft sync

Confirmation — TWO paths:
  (a) Normal: draft sits in fazle_draft_replies for admin review/approve.
  (b) Direct/trusted bridge2 path (real, wired): if the message comes from
      ANY configured admin number, _create_confirmed_from_admin() writes
      status='confirmed' straight into wbom_escort_programs, bypassing the
      review queue entirely — "Admin reply is the source of truth"
      (modules/escort/__init__.py, logged explicitly as such).

confirmed → running: modules/escort_lifecycle::mark_program_running_on_attendance()
  fires automatically on the FIRST admin-approved attendance record for that escort.

running → released/closed: modules/escort_lifecycle::close_program(),
  triggered by release-intent detection in the conversation.

Release → payment: create_escort_payment_draft() (modules/payment_workflow)
  → admin approval → finalize_payment() → match_or_create_employee()
  → create_transaction() → fpe_cash_transactions + fpe_employee_ledger.
```

---

## 9. Advance / Salary Requests, "Employee Verification"

`modules/employee_verification/__init__.py` is **not** an identity-reverification-by-AI flow (no LLM call in it at all) — it's a deterministic, regex/state-machine gated **financial-request verification**: selfie → duty slip → payment method confirmation, for advance/salary payout requests. There is no separate AI-driven "is this really employee X" conversation anywhere in the codebase.

---

## 10. Draft Creation & Admin Approval — the Unifying Pattern

`fazle_draft_replies` is the single queue every non-authoritative action funnels through: attendance (message-parser or office_assistant drafts), payment reviews, escort confirmations, general AI replies. As of 2026-07-11, attendance specifically has one **shared finalization service** (`modules.attendance.finalize_attendance_draft`) used identically by both the WhatsApp `APPROVE <id>` command and the frontend Review Queue button — atomic, idempotent, never auto-creates an employee.

---

## 11. Recruitment / Job-Inquiry Conversation

**Updated same day (2026-07-11, later pass):** trigger detection is no longer keyword-only. The original flat Bangla/English keyword list (`is_recruitment_trigger`) still runs first and still auto-sends on an explicit match, but `modules/recruitment_flow::recruitment_eligibility()` now also calls a new 3-layer detector (`modules/recruitment_detector`) for phrasing the keyword list misses:
- **L1 entity signals** — scored Bangla+English phrase categories (decisive categories like "seeking_work"/"opportunity_ask" trigger alone; supporting categories need 2+ combined; weak categories never trigger alone). Draft-only (not auto-send) unless decisive.
- **L2 LLM zero-shot** — reuses the `intent` value `message_router` already computed via `ai.classify_intent_llm()` earlier in the same request; does **not** make a second LLM call.
- **L3 conversation context** — reuses the existing `get_active_session()`/`get_recent_history()` helpers, no new SQL.

`modules/recruitment_ai` is now **fully dead** outside its own isolated test file — its last two live symbols (`_QUESTION_HINTS`, `looks_like_recruitment_followup()`) were moved into `recruitment_flow` (their only real caller) the same day. Reply generation is still LLM-driven via `modules/conversation_layer` (unchanged, still the live-wired path).

Funnel state (name → age → area → job preference → experience → phone confirm) is stored in `fazle_recruitment_sessions`.

**"Recruitment supervisor" does not exist** as a role or escalation mechanism anywhere in this codebase — "Security Supervisor"/"Ghat Supervisor" are just job titles a candidate can apply for; there is no human-handoff concept for recruitment specifically.

**Unknown number asking about jobs vs. an existing employee on an unrecognized number**: still phone-only matching, no name-based rescue — but as of 2026-07-11 an unknown-role sender's first reply now prepends a soft-ask ("আপনাকে চিনতে পারছি না। আপনি কি আমাদের পুরনো employee?") asking them to self-identify. This is a **prompt only, not an automated cross-reference** — there is still no code that checks the stated name/old-number against `cash_transactions`/`attendance`/`escort_roster`. An actual employee texting from a new SIM still needs an admin to manually link the number; this gap is documented inline at `modules/recruitment_flow/__init__.py` near `intake_message()`.

---

## 12. AI / LLM Layer — What's Real vs. the Stated Goal

**Providers**: Ollama (local) → Groq → GitHub Models, a **hardcoded fallback chain per call-site**, not dynamic quality-based routing. A separate task-class → specific-Ollama-model router exists only for the admin chat console (`modules/ai_console/model_router.py`).

**Bilingual handling**: hand-written prompt instructions ("reply in Bangla unless asked in English," in `shared/reply_policy.py` and repeated at each generation call-site) — not a dedicated language classifier. This is prompt-engineering, and it works, but it's not a separate NLU layer. **Updated 2026-07-11**: a shared `_BANGLA_NUANCE_NOTE` (Banglish spelling recognition — kaj/chai/lagbe/vai → কাজ/চাই/লাগবে/ভাই — plus "match the user's formality level") was added to both the recruitment and general rule sets. Conversation history is now also wired into all 3 `ai.generate_reply()` call sites in `message_router` (previously always passed empty, despite the prompt builder already having a ready "কথোপকথন" section for it).

**Admin AI chatbot** (`open-chat.html` → `/chat/message`): real and live by default via `modules.admin_chat_lab`, gated by a feature flag (`ai_console_chat_enabled`, default off, DB-overridable) that switches to a more elaborate `modules.ai_console` orchestrator when enabled. Tool access is **keyword-pattern tool-selection**, not LLM function-calling — but the tools themselves are real, reading live Postgres (`ai_read_*` views + direct tables): contacts, employees, recent messages, payroll runs, escort program status, attendance summaries, daily payments, recruitment leads, module/bridge health, KB articles, plus a safety-filtered external URL fetch. Note: `ai_console_chat_enabled` stays recommended-off pending 72h stability on this session's changes — see §13.

**RAG — updated 2026-07-11, this claim from earlier the same day is now stale and corrected**: the curated `knowledge_base/*.md` governance/business-rules tree (previously NOT indexed at all) is now indexed alongside `resources/*.txt` and the `fazle_knowledge_base` DB table — 8,878 total chunks (was 4,429), role-gated: `kb_file:` chunks are only retrievable by `role="admin"/"developer"` callers (the admin AI console), never by a candidate/employee-role search. Verified end-to-end (candidate search returns zero `kb_file:` hits, admin search returns real hits).

The hybrid Qdrant/embedding path's **code default is still `HYBRID_SEARCH_ENABLED=false`**, but the **live production `.env` has it set to `true`** — this was true before 2026-07-11 too, the "inert until enabled" framing above was accurate about the code default but did not reflect the actual deployed `.env` state. Practical consequence discovered this session: a full index rebuild now re-embeds all 8,878 chunks on a single CPU thread, taking **~32-36 minutes** (was ~16 min at half the corpus size) and holding an internal lock for that whole duration. Two mitigations were added the same day: (1) `rag.safe_search()`/`safe_answer()` timeout wrappers (4s) on the three customer-facing call sites (recruitment reply, general AI-fallback, KB semantic-fallback), so a customer reply never blocks on a rebuild race; (2) an idempotency guard on `POST /api/rag/rebuild` (skips if already rebuilding, skips if built <10min ago) — a previous same-day live-verification run without this guard triggered two full redundant rebuilds back to back (~60min wasted). The underlying "long CPU-bound single-thread embed holds a lock for the whole rebuild" design itself was **not** re-architected — flagged as a known operational cost of the larger corpus, not fixed at the root.

**Self-improving memory** (`modules/reviewed_reply_memory`): real and live by default. When an admin edits/approves a draft, the corrected reply is cached (keyed by intent/role/draft-type/phone) and looked up *before* generating a fresh LLM reply for similar future messages — this is genuine learn-from-correction behavior, though it's template reuse, not model fine-tuning.

**Gap vs. your stated goal** ("must be an agent power... read/fetch/lookup/search/summarise data from all db and write its own db table... improve its own memory by reading codebase, db, complete vps"): the current system is a solid foundation for this but is **not yet there**. Concretely missing today: (1) true LLM function-calling/agentic tool use (current tool-selection is keyword matching, not the model deciding which tools to call); (2) RAG over the full governance/KB tree, not just `resources/*.txt`; (3) any mechanism for the AI to read its own codebase or "the complete VPS" — no such capability exists; (4) a genuinely independent agent loop (today it's single-turn request→tools→reply, not multi-step autonomous reasoning). This is a real, scoped next-phase of work, not a small tweak.

---

## Summary Table — Real vs. Partial/Dead

| Feature | Status |
|---|---|
| WhatsApp media classification, routing, drafts | ✅ Real, live |
| Escort release-slip OCR + extraction | ✅ Real, live (not FK-linked to programs) |
| PDF/audio conversion | ✅ Real, live |
| Identity resolution (11-step, phone-only) | ✅ Real, live |
| "Blocked number" concept | ❌ Removed entirely (2026-07-11) |
| RBAC hierarchy incl. office_assistant | ✅ Real, live, freshly audited |
| Contact sync (`contact_sync`) | ⚠️ Manual-trigger only; scheduled + on-message hooks are dead code |
| Employee identity unification (`employee_id_phone`) | ⚠️ Enforced within `fpe_employees` only, not cross-table |
| Escort order intake/confirm/release/payment | ✅ Real, live, including the bridge2-direct-confirm bypass |
| Employee "verification" (financial, not identity) | ✅ Real, live — not an AI identity-recheck |
| Recruitment funnel + AI reply | ✅ Real, live (keyword trigger + 3-layer detector as of 2026-07-11, LLM reply) |
| "Recruitment supervisor" | ❌ Does not exist |
| Reviewed-reply learning memory | ✅ Real, live |
| Admin AI console + read-only tools | ✅ Real, live (tool-selection is keyword-based, not agentic) |
| RAG over full `knowledge_base/` tree | ✅ Wired as of 2026-07-11 — 8,878 chunks, role-gated admin/developer only |
| identity_brain `_lookup_contact()` bug | ✅ Fixed 2026-07-11 (was querying nonexistent columns, dormant) |
| Unknown-sender soft-ask (recruitment) | ✅ Real, live — prompt only, no automated employee cross-reference (gap documented in code) |
| `modules.recruitment_ai` | ❌ Fully dead as of 2026-07-11 (last live symbols moved to `recruitment_flow`) |
| RAG rebuild time/lock cost | ⚠️ ~32-36min per full rebuild (was ~16min), holds a lock the whole time — timeout wrappers + rebuild idempotency guard added, root design not re-architected |
| Fully autonomous, self-improving AI agent | ❌ Aspirational — meaningful gap from today's single-turn tool-use pattern |

---

## 13. Same-Day Addendum (2026-07-11, later pass) — What Changed After This Document Was First Compiled

A follow-up task ("Complete System Unification") touched recruitment detection, RAG indexing, and Bangla reply quality; the sections above have been corrected in place rather than left stale. Net changes, in 3 commits:

1. **Bug fix** — `identity_brain._lookup_contact()` wrong column names (§3).
2. **RAG operational stability** — `rag.safe_search()`/`safe_answer()` timeout wrappers on the 3 customer-facing call sites, and an idempotency guard on `POST /api/rag/rebuild` (§12).
3. **Feature work** — `recruitment_ai` deprecation completed, 3-layer recruitment detector added, unknown-sender soft-ask added, `knowledge_base/*.md` indexed into RAG (role-gated), `admin_chat_lab` RAG role bug fixed, Bangla nuance + conversation history wired into reply generation (§11, §12).

Test suite after all three commits: 583 passed, 330 skipped, 14 xfailed, 4 xpassed, 0 failed (`tests/e2e` excluded — unrelated Playwright suite).

**Still open, by design (not fixed this pass):**
- Phase 3 automated employee-cross-reference gap (§11) — soft-ask only, tracked as a separate task.
- `ai_console_chat_enabled` recommended to stay `false` until this batch is stable for 72h — pure `.env`/DB flag flip when ready, no code change needed.
- The RAG rebuild lock's root design (single-thread CPU-bound embed holding a lock for the full rebuild) was mitigated at the edges, not re-architected.

---

## 14. Correction Addendum (2026-07-12, morning pass) — Two Claims Above Were No Longer Accurate

Re-verified against the live host on 2026-07-12 04:47 CEST. The code and DB-side facts in §1–§13 were still correct as *committed/indexed* state, but two runtime claims needed correcting at the time:

**(a) The process serving production traffic had not actually loaded the 22:12–22:39 commits from §13.** `fazle-core.service` (PID 2449634) had been running continuously since **2026-07-11 20:31:03** with no restart since (`systemctl show -p ExecMainStartTimestamp`; `DEBUG=false` in `.env` means `uvicorn`'s `reload=s.debug` is off, so there is no hot-reload). But the three feature/fix commits this document's §13 describes as "live" were made *after* that restart: `47a9c8e` (identity_brain fix, 22:30:33), `a0b2c38` (RAG stability fixes, 22:33:39), `07687b1` (recruitment detector/Bangla/conversation-history, 22:39:43) — confirmed by file mtimes on `modules/identity_brain/__init__.py` (22:12), `modules/rag/__init__.py` and `modules/message_router/__init__.py` (22:34), `modules/recruitment_flow/__init__.py` (22:12), all later than the 20:31:02 process start. **Resolved 2026-07-12 21:26:32 CEST**: service restarted (Owner-directed, `sudo systemctl restart fazle-core.service`), new `ExecMainStartTimestamp` confirmed postdating all three commits. Post-restart `/health` confirmed all probes `ok` including `qdrant.points_count: 8878` (RAG index intact across the restart, as expected since it's external DB state). §1–§13's "live" claims for those three commits are now actually true of the running process.

**(b) `AI_CONSOLE_CHAT_ENABLED=true` had been live in production `.env` since at least 2026-07-09**, confirmed via `.env.bak.pre-rotation-20260709_073455` and `.env.bak.pre-hrofficer-cleanup-20260710_100008` (both show `true`), with no `fazle_runtime_settings` row for `ai_console.chat_enabled` existing at the time to make that explicit. This meant §12/§13's "recommended to stay `false` until this batch is stable for 72h" was never actually the deployed reality — the effective flag had been `true` since before 2026-07-11, not off-by-default as described. **Resolved 2026-07-12: Owner confirmed this is intentional**, not stale drift — the `.env` value is the Owner's actual decision, not an accident (contrast with the earlier genuine `AUTO_REPLY_ENABLED` doc-vs-`.env` mismatch, which *was* accidental). An explicit `fazle_runtime_settings` row (`key='ai_console.chat_enabled', value='true'`) was added the same day so the decision is DB-governed going forward rather than an implicit `.env`-fallback default — `_is_ai_console_chat_enabled()` in `app/main.py` checks the DB row first, so this row is now authoritative. **The AI Console orchestrator (`modules.ai_console`, not the simpler `admin_chat_lab` default) is confirmed the live `/chat/message` handler as of the restart in (a).** §12's "gated by a feature flag ... default off" framing is now stale — the flag is on, by Owner decision, both in `.env` and explicitly in the DB.

---

*Compiled 2026-07-11 from direct code inspection (file:line citations available on request per section) and live production DB queries; §13 added later the same day after a follow-up task; §14 added 2026-07-12 after re-verifying runtime state against the live host, then updated the same day once the Owner restarted the service and confirmed the AI Console decision. This is a snapshot — re-verify before relying on any specific claim in a future session, per standard practice for point-in-time audit documents.*
