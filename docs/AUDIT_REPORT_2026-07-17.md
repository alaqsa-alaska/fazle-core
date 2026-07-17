# Fazle Core — Full Audit, Safety Report & Implementation Planning (2026-07-17)

**Read-only audit. No code, schema, or production data was modified while producing this report.** Scope: git history from commit `3582821` (Session 18, 2026-07-16) to `HEAD` (identical commit — no new commits since), full `modules/`/`app/`/`app/static/` tree, and live production DB (read-only `SELECT`s only, via a throwaway Python/asyncpg script since no `psql` client is installed locally).

**Housekeeping note, disclosed for transparency:** early in this session a `bash` command intended to redact `.env` secrets only redacted keys named literally PASSWORD or PASS, missed the differently-named `DATABASE_URL_TEMPLATE` key, and the live Postgres password was echoed once into this session's transcript. No production changes resulted from that mistake, but if this transcript is retained anywhere, treat that password as exposed and consider rotating it again out of caution — same class of issue as the already-remediated 2026-07-12 incident, just self-inflicted this time and caught immediately.

---

## 🔴🟡🟢 Flag legend
🔴 CRITICAL — will/can break production or already has (confirmed). 🟡 WARNING — real risk, not yet manifesting or not yet confirmed live. 🟢 INFO — safe, noted for completeness.

---

# PART 1 — Full Codebase Audit

## 1A. Out-of-queue commits (Sessions 12→13 governance gap)

`git log --oneline 3582821..HEAD` is empty — HEAD **is** `3582821`, the last documented session. The real question was whether earlier commits (2026-06-24 → 2026-07-16) that touch critical paths are captured in `management_decisions.md` / `docs/NEXT_IMPLEMENTATION_QUEUE.md` (or its archive). Answer: **mostly yes, with a real ~3-week gap between Session 12 (2026-06-24) and Session 13 (2026-07-15).**

🟡 **Commits touching critical paths with no governance-doc record anywhere** (not in either live doc, not in the archive):

| Hash | Date | Files | What it does | Relation to known issues |
|---|---|---|---|---|
| `aea5679` | 2026-07-11 | `db/migrations/043_office_assistant_role.sql`, `modules/attendance/*`, `modules/admin_transactions/__init__.py`, `modules/rbac/__init__.py`, `role_permissions.md` | Formalizes `office_assistant` as a real RBAC role — a schema-changing migration with zero session record. | Neutral to known issues, but a migration with no governance trail is itself notable given this repo's own rule ("no DB schema change unless a task explicitly asks for a migration"). |
| `a0b2c38` | 2026-07-11 | `app/main.py`, `modules/rag/__init__.py`, `modules/message_router/__init__.py` | RAG stability: timeout wrapper + rebuild guard — only vaguely bundled as "RAG stability fixes" in the archive, never itemized. | Distinct from the separately-documented `f77368c` RAG-lock fix. |
| `06556b5` | 2026-07-15 | `modules/escort/__init__.py`, `modules/bridge_poller/__init__.py`, `escort_business_rules.md` | Fixes escort-order intake gate (MV-label regex), adds a Bangladesh-mobile-number pattern as a routing signal in `bridge_poller`. | Touches the exact file (`modules/escort/__init__.py`) already flagged for the destination-leak bug (§1C below) and the exact file (`modules/bridge_poller/__init__.py`) flagged for the webhook/poller gate divergence (§1C) — worth a closer read to confirm it didn't unintentionally interact with either. |
| `43ffcdc`/`7174e5b` | 2026-07-13 | `payroll.html`, `attendance/crud.py`, `payment_ingest`, `draft_approval` + 4 KB files | Payment-method parsing + attendance-lookup fix, KB synced. | Neutral, payments-adjacent, no session record. |
| `a66b37c`/`b960e34` | 2026-07-13/14 | `payroll.html`, `escort-roster.html` | Nav restructure, SSO, icon restyle. | Neutral, UI-only. |
| `690478c` | 2026-07-15 | `modules/social_auto_reply/{conversation_history,state_tracker}.py` | Excludes pre-activation backlog from planner sweeps. | Same subsystem as S13's selective-draft policy but not named in that session's writeup — confirm it wasn't meant to be folded into S13. |
| `fbf1204` | 2026-07-14 | `payroll.html` (373 lines), `db/migrations/046_employee_edit_requests.sql`, `admin_employees/__init__.py`, `fazle_payroll_engine/routes.py` | Unified 7-field edit modal, edit-review queue, admin-role gate, net-balance/tx-history — **a schema-changing migration + new RBAC gate with zero session record.** | Same file (`admin_employees/__init__.py`) that S15 (2026-07-16, documented) later touches for Salary/Designation/Joining-Date sync — worth confirming S15 doesn't silently build on an uncredited prior change. |
| `99e37fd` | 2026-07-14 | `fazle_payroll_engine/routes.py` | Route-ordering bug fix (`/employees/edit-requests` shadowed by `/employees/{emp_id}`). | Neutral, real bugfix, no record. |

🟢 Confirmed documented, just only in the archive (not a live-doc gap): `5b9f2bc` (migration-collision re-audit), the `contact_id`/`UnboundLocalError` fix, `5397e9b` (concurrent `compute_run()` race).

**Takeaway:** two commits carry real schema changes (`043_office_assistant_role.sql`, `046_employee_edit_requests.sql`) with no entry in `management_decisions.md` at all — worth a retroactive session-log entry so the "every migration needs a decision record" convention doesn't quietly erode.

## 1B. Double-call and gap detection

**1B-i — `escort_roster_entries` has more writers than the one already-documented conflict.** Besides `escort_lifecycle`'s raw INSERT (line 361-383) vs. `escort_roster/db.py`'s `sync_program_to_roster()` (the known Tier-2 issue), a **third** writer exists: `escort_roster/db.py::create_roster_entry()` (line 797, INSERT at 857), called from `escort_roster/routes.py:615` — a separate, admin-facing "manually add a roster entry" API, duplicate-guarded (vessel+lighter+date check) before insert. 🟢 This looks like a deliberate, distinct manual-entry feature (not part of the release-sync race), but it's a third surface touching a table already known to have a synchronization problem — worth being aware of if the dual-write fix is ever revisited, so the fix accounts for all three writers, not just the two currently discussed.

**1B-ii — Missing-import / NameError risk:** 🟢 **NONE FOUND.** Checked `modules/escort`, `escort_lifecycle`, `escort_roster`, `payroll`, `social_auto_reply`, `admin_commands` — all cross-module calls use either top-of-file or deliberate function-local imports (a repeated intentional style to dodge circular imports), not undeclared globals.

**1B-iii — Blocking sync call inside `async def`:** 🟡 One real finding: `modules/bridge_poller/__init__.py:818-828`, inside `async def _poll_bridge()` — a raw `sqlite3.connect()` + `.execute().fetchall()` runs synchronously inline (not via `run_in_executor`, unlike the file's own `_fetch_new_messages`/`_load_lid_map` helpers which do this correctly). Blocks the event loop on every inbound image message during "STEP 1: lightweight context check." No `psycopg2`/bare-`requests` sync calls found anywhere else in `modules/`/`app/`/`shared/`.

**1B-iv — Duplicate/competing parser entry points:** 🔴 **Confirmed and this is the most operationally significant finding in Part 1.** Two live entry points process the same inbound message by design (webhook `_handle_bridge_event()` and poller `_poll_bridge()`), dedup-guarded via `processed_bridge_messages` (P15-03). But:
- The **poller** path applies `_requires_legacy_workflow()` (`modules/bridge_poller/__init__.py:658-678`) — a hard pre-filter (mobile-number regex + escort/vessel keywords + image/doc media) that routes operational messages away from `ingest_social_event()` and straight to the real order/booking pipeline. Its own comment documents a **prior incident**: a Bangladeshi mobile number previously caused valid orders to be silently dropped into the (then-disabled) social-reply pipeline.
- The **webhook** path (`app/main.py:_handle_bridge_event()`, lines 1616-1633) has **no equivalent gate** — it unconditionally calls `ingest_social_event()` whenever `SOCIAL_AUTO_REPLY_SINGLE_ENGINE` is on (default true), only falling through to the real pipeline if that call returns `None` (which only happens on a DB error or exact-duplicate `event_key`, never because the message was classified as an escort order).
- **Net effect:** an inbound escort order arriving over `/webhook/mcp1`/`/webhook/mcp2` can be classified `"escort_order"` by the social classifier, marked `reply_status='ignored'`, and **never reach booking creation at all** — reproducing the exact failure mode the poller-side fix was built to prevent, because the webhook path never got the same fix. `_requires_legacy_workflow` is referenced only inside `bridge_poller` and its own unit test — confirmed via grep it is never imported by `app/main.py`.
- Compounding fail-open risk (also found in 1B-iv's dedup check): if the `_is_processed`/`_mark_processed` dedup lookup itself throws, the webhook path proceeds **without** dedup protection rather than skipping — a DB hiccup on the dedup table turns into an actual double-processing event.

**This is a live, unconfirmed-in-production, previously-undocumented risk — not merely a documentation gap.** Recommend it be added to the queue's "Known Broken / Conflicting Workflows" section as a new, distinct item from the already-known escort-lifecycle dual-write.

## 1C. Message parser integrity

**Sender-validation gate (`41916b0`, 2026-06-27) and 24h hard-delete (`82f2b33`, 2026-07-05): 🟢 both confirmed still intact.** `_is_allowed_escort_client()`/`_is_trusted_escort_source()`/`_is_authoritative_bridge_confirmation()` are still called unconditionally at their original sites; the one substantive change since (`40293f1`, 2026-07-03) *widened* what counts as an authoritative confirmation, it did not weaken the gate. `expire_stale_drafts()` still does a real hard `DELETE` (not a soft expire) on the 24h TTL, still wired to the scheduler; Session 16's addition was a defensive second sweep, additive not weakening.

### 🔴 Destination field leak — CONFIRMED reproducible, blast radius corrected from 4 to 6 rows

This was tracked in `knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md` as "reproducibility unconfirmed — the 4 source messages could not be located." **I located them and confirmed the root cause with a live regex test against the real message text.**

**Blast radius (queried live DB):** 6 rows, not 4 — `escort_destination_field_leak_issue_2026-07-16.md` missed **902** and **938**:

| program_id | destination (as stored) | auto_created |
|---|---|---|
| 900 | `Escort Name: Sohedul Karim` | true |
| 901 | `Escort Name: Md Helal` | true |
| **902** | `Escort Name: Rejaul korim` | true *(previously uncounted)* |
| 905 | `Escort Name: Ferdous` | true |
| 906 | `Escort Name: Md Rubel` | true |
| **938** | `Escort Name: Anik Nath` | **false** *(previously uncounted, and NOT auto_created — see below)* |

**Source messages found** (`wbom_whatsapp_messages`), e.g. message_id 10678 (→ row 900):
```
Mother Vessel: MV Dubai Eco
Lighter Vessel: MV HABIBULLAH
Master Mobile:  01829984145
Destination:
Escort Name: Sohedul Karim
Escort Mobile: 01854919404
```
Same pattern confirmed for 901, 906, and 938 (938's source, message_id 11182/11493, is byte-identical in shape: `"Destination:\nEscort Name: Anik Nath"`). **In every case, the admin left `Destination:` blank.**

**Root cause, confirmed by direct test, not just static read** — `_CD_DEST_RE` (`modules/escort/__init__.py:144-147`):
```python
_CD_DEST_RE = re.compile(
    r"destination\s*:\s*(.+?)(?=\s*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
```
`\s` matches newlines. When the `Destination:` line is blank, `\s*` immediately after the colon consumes the trailing space **and the newline**, spilling onto the next line; the non-greedy `(.+?)` then has nothing to stop it until the *next* line's own end, capturing that entire line instead. Verified in a standalone interpreter:
```python
>>> _CD_DEST_RE.search("...Destination: \nEscort Name: Sohedul Karim\nEscort Mobile: ...").group(1)
'Escort Name: Sohedul Karim'
```
This exactly reproduces the corrupted value stored in production. The per-line label extractor used *first* (`destination_label = _extract_labeled_line_value(...)`, anchored per-line via `re.match` on pre-split lines) correctly returns `None` for a blank `Destination:` line — it's the *fallback* regex, `_CD_DEST_RE.search(text)` on the raw multi-line string, that has the cross-line bug. Confirmed **still present in current code at HEAD**, not fixed by any commit since.

**Note on the earlier static-only trace:** a parallel code-only trace (no DB access) concluded this pattern "cannot span into a subsequent line unless there is literally no newline between them" — that reasoning missed that `\s` itself matches `\n`, so the boundary is weaker than it looks. This is flagged so the KB doc's history doesn't get updated with the wrong conclusion.

**938 is a different code path — the bug is not confined to `_create_confirmed_from_admin()`.** Row 938's `remarks` show `"source": "sqlite_backfill"`, `"source_of_truth": "bridge_confirmation"` — a backfill/import path, not the live `auto_created=true` admin-confirm path the other 5 rows went through. Its `remarks.raw_block` already contains the pre-corrupted `"destination": "Escort Name: Anik Nath"` value, meaning the same `parse_completed_draft()`/`_CD_DEST_RE` function is shared and reused by more than one downstream writer — the fix, when scoped, needs to account for all callers of `parse_completed_draft()`, not just `_create_confirmed_from_admin()`.

**Recommended fix shape** (not applied — reporting only, per this task's constraints): change the whitespace after the colon in `_CD_DEST_RE` from `\s*` to `[ \t]*` (space/tab only, not newline) so the pattern can never cross into a following line; a blank destination should then correctly fall through to `None`/empty rather than capturing the next label's line. This is a one-line regex change with a clear, now-reproducible test case — a good, low-risk KB-first proposal candidate.

## 1D. Admin/bridge-number safety

🟢 All six task-listed vars (`ADMIN_NUMBERS`, `ACCOUNTANT_PHONE`, `FPE_CASH_AUTHORIZED_PHONES`, `FPE_INCOME_AUTHORIZED_PHONES`, `ESCORT_CLIENT_PHONES`, `DRAFT_ALWAYS_PHONES`) are real `Settings` fields, env-loaded, used consistently as routing filters — not hardcoded. No changes to `app/config.py` or the routing logic depending on these since 2026-07-13 (last touch: `3e692e6`, 2026-07-11, unrelated).

🟡 **Duplicate/divergent implementation of the escort-client phone allowlist.** Two independently-written `_escort_client_phone_list()`-style functions both derive from `settings.escort_client_phones` but normalize to *different* canonical formats: `modules/escort/__init__.py:252` → local `01XXXXXXXXX`; `modules/escort_roster/routes.py:275` (also reused by `modules/client_billing/profiles.py`) → international `880XXXXXXXXXX`. Each is internally consistent (no active bug found), but any future edge-case fix (dashes, `+` prefixes) applied to one copy won't propagate to the other.

🔴 **The same hardcoded literal admin/bridge number (`"8801880446111"`) appears in at least 5 separate production files instead of reading from `settings.admin_bridge2_number`:**
- `shared/bridge_orchestrator.py:53-54` (`ADMIN_SELF_NUMBER` constant)
- `app/main.py:1663` (`_AGENT_FORWARD_PHONES` — the actual filter deciding which senders' messages get forwarded to the background AI agent)
- `modules/fazle_payroll_engine/diagnostics.py:156-157` (stale-bridge admin alert)
- `modules/escort_roster/routes.py:538` (`/backfill-sqlite` route)
- `modules/fazle_payroll_engine/{historical_sync.py,replay.py}`, `modules/contact_sync/__init__.py` (one-time historical-sync/replay contexts, lower risk)

If this number is ever rotated in config, these silently drift out of sync with the config-driven parts of the system. Also found: `app/critical_numbers.py` — a 20-entry hardcoded phone list, apparently only consumed by a one-off recovery script and an already-archived module; likely dead, worth confirming before assuming it still matters.

## 1E. Frontend → backend route mapping

**Full per-page fetch-call inventory (11 pages, every call site, method, route-existence check, auth-header check, shared-utility check) is large — see the companion table in Part 5 below, where it's paired with the migration-risk assessment it's needed for.**

**Headline result: 🟢 zero `POSSIBLE 404`s found.** Every fetch target across all 9 functional pages (`ux-preview*.html` excluded, 0 fetch calls) resolves to a real FastAPI route once router mount-prefixes are accounted for, including `payroll.html`'s dual-prefix `adminApi()` helper (routes `/users*`/`/audit` to bare `/admin/...`, everything else to `/api/admin/...`) — a naive grep would flag these as mismatches; both prefixes were verified to hit real mounted routers.

**`escort-roster.html` — the one page already migrated to `shared-auth-fetch.js` — is itself inconsistent.** Its DELETE calls (`:1187`, `:1491`) and its Admin-Users-tab calls (via a page-local `adminApi()`) bypass the shared utility with raw inline `fetch()`, even though the rest of the page uses it. Worth a quick cleanup pass on its own, independent of the other 8 pages' migration.

## 1F. DB schema vs. code consistency

🟢 **No column mismatches found.** Checked live schema against every column referenced in raw SQL for `escort_roster_entries` (29 cols, including `calc_version`, `net_payable`, `advance_deduction` — all present), `wbom_whatsapp_messages` (36 cols, including `sender_number`, `canonical_phone`, `phone_last10` — all present, confirming the 2026-07-13 `sender_number`-vs-`phone` fix holds), `fpe_cash_transactions` (35 cols), `fpe_employees` (18 cols, including `joining_date`, `basic_salary`, `designation`, `wbom_employee_id` — all present, confirming S13-04/S15-01's fixes have matching live columns), `wbom_employees` (15 cols), and `chat_history` (14 cols — matches `sync.py`'s `INSERT_SQL` column list exactly). Last applied migration confirmed live: **`048_chat_history.sql`**; `047` correctly absent (rejected, per `db/MIGRATION_INVENTORY.md`).

---

# PART 2 — Safety Assessment for the 4 Selected Steps

## Step 1 — Dual-write concurrency hang

**Is the reverted (safe) code in place?** 🟢 Yes, confirmed by direct read of `modules/escort_lifecycle/__init__.py:359-387` — a single atomic `INSERT INTO escort_roster_entries (...) SELECT ... FROM wbom_escort_programs p JOIN wbom_employees e ... JOIN fazle_payment_drafts d ... WHERE p.program_id=$1 AND d.id=$4 ON CONFLICT (program_id) DO UPDATE SET ...` — exactly the pre-refactor shape the queue doc describes.

**Has any out-of-queue commit touched this file since the revert?** No — Part 1A's out-of-queue commit scan did not flag `modules/escort_lifecycle/__init__.py`.

**Diagnostic SQL to run during a live hang:**
```sql
-- Who is blocked, and by whom
SELECT
    blocked_locks.pid      AS blocked_pid,
    blocked_activity.query AS blocked_query,
    blocking_locks.pid     AS blocking_pid,
    blocking_activity.query AS blocking_query,
    now() - blocked_activity.query_start AS blocked_duration
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks
    ON blocking_locks.locktype = blocked_locks.locktype
   AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
   AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
   AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
   AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
   AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
   AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;

-- All current locks specifically on the contended table
SELECT pid, mode, granted FROM pg_locks WHERE relation = 'escort_roster_entries'::regclass;
```

**Minimum safe repro:** run the existing test in isolation with verbose output while polling the above query from a second `psql`/asyncpg session:
```bash
pytest tests/workflows/test_escort_payment_flow.py::test_concurrent_release_events -x -v
```
(`tests/workflows/test_escort_payment_flow.py:1427`.) Since the hang is transient during the test's ~10-concurrent-call burst, either instrument a short `asyncio.sleep()` into the candidate refactor to widen the observation window, or run the diagnostic query in a tight loop against the test DB for the duration of the test.

**Scope boundary:** 🟢 fixing this requires zero frontend/route changes — it's confined to `modules/escort_lifecycle/__init__.py` and, if the fix is to finally converge onto `escort_roster`'s own API, `modules/escort_roster/db.py::sync_program_to_roster()`. Recommend the fix also account for the third writer found in §1B-i (`create_roster_entry()`), even though that one isn't part of the concurrency bug itself.

## Step 2 — Destination field leak

Already fully investigated in §1C above — **confirmed reproducible, root cause identified, blast radius corrected to 6 rows.** This was done entirely via `SELECT`-only DB queries and a standalone regex test; the parser itself was not touched. Ready for a KB-first proposal (current behavior / KB reference / gap analysis / proposed one-line fix / risk / rollback) whenever that's wanted — not written here per this task's "report only" scope.

## Step 4 — `chat_history` sync deployment

**UPDATE (2026-07-17, later same day, recorded in `management_decisions.md` Session 19 Step 4 correction):** this section's premise — that the service was undeployed and blocked on sudo — turned out to be wrong. When the install commands below were run, `chat-sync.service` was discovered **already `active (running)` since 2026-07-15 23:28:28**, installed outside any recorded session, with `chat_history` already holding 9,156 healthy rows spanning the full ~2-day gap and zero errors in its journal. The commands below are kept as-written for historical accuracy (this is what the audit recommended at the time); see the governance doc for the corrected timeline and evidence.

**What `sync.py` does** (read in full, `~/chat_sync_staging/sync.py`): a standalone, non-fazle-core-importing daemon. Every 15-30s (`CHAT_SYNC_POLL_INTERVAL_S`, default 20s) it reads new rows from `wbom_whatsapp_messages` (inbound only, cursor = `MAX(source_id)` already synced) and `fazle_outbound_queue` (outbound, status `sent`/`failed`/`dlq` only — deliberately never reads outbound from `wbom_whatsapp_messages`, to avoid double-counting against the auto-send path), and `INSERT ... ON CONFLICT (source_table, source_id) DO NOTHING` into `chat_history`. Purely additive; the file's own docstring states it never writes anywhere else.

**Exact sudo commands to install:**
```bash
sudo mkdir -p /opt/chat_sync
sudo cp /home/azim/chat_sync_staging/sync.py /opt/chat_sync/sync.py
sudo cp /home/azim/chat_sync_staging/chat-sync.service /etc/systemd/system/chat-sync.service
sudo systemctl daemon-reload
sudo systemctl enable --now chat-sync.service
sudo systemctl status chat-sync.service
journalctl -u chat-sync.service -f   # watch first sync cycle
```

**Risk to existing payroll/escort/social-reply flows: 🟢 low.** It only ever `SELECT`s from `wbom_whatsapp_messages`/`fazle_outbound_queue` and `INSERT`s into `chat_history` — a table confirmed live (14 columns, matches `sync.py`'s insert list exactly, §1F) that no other running code path writes to or reads from (it exists solely as this sync's write target, per the file's own docstring and `db/MIGRATION_INVENTORY.md`'s note that "`chat_history`... is not read or written by any fazle-core application code"). No shared-table contention with the main app.

**Rollback:**
```bash
sudo systemctl disable --now chat-sync.service
sudo rm /etc/systemd/system/chat-sync.service
sudo systemctl daemon-reload
```
(`chat_history` itself needs no cleanup — it's purely additive and isolated; leaving already-synced rows in place is harmless if the service is later stopped.)

## Step 7 — `webhook/mcp1` always-200 behavior

**Exact code** (`app/main.py:1507-1517`):
```python
@app.post("/webhook/mcp1")
async def bridge1_webhook(request: Request):
    """Receive events from Bridge 1 (HR number)."""
    body = await request.body()
    _verify_bridge_webhook(body, request.headers.get("X-Fazle-Signature-256", ""), "bridge1")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")
    await _handle_bridge_event(payload, source="bridge1")
    return {"status": "ok"}
```
`_handle_bridge_event()` (`:1534` on) loops over each event in the payload; if `sender` is empty it does a plain `continue` (§ line 1554-1555) — the malformed/incomplete event is silently skipped, never raised. **The route can only ever return 400 (bad JSON) or 200 — never 422 for a structurally-valid-JSON-but-missing-field payload,** by construction, not by an oversight anyone would need to "add" a check to fix — every per-event error path already resolves to "log and skip," not "raise."

**Impact of adding real validation (Option B):** if the upstream bridge script relies on always getting `{"status":"ok"}` back regardless of payload shape (a common pattern to prevent a bridge-side retry-storm), returning 422 for a shape it currently sends routinely could cause the bridge to start retrying — this needs to be checked against the actual bridge client code (outside this repo, on the bridge script side) before flipping this, not inferred.

**Draft `management_decisions.md` entries for Owner sign-off (either one, not both):**

```markdown
## Session 19 — webhook/mcp1 Validation Decision (pending)

### Management Authorization (Session 19)

| ID | Decision | Details | Date |
|---|---|---|---|
| S19-01a | OPTION A: Document always-200 as intentional | `/webhook/mcp1`/`/webhook/mcp2` continue to ack every structurally-valid-JSON payload with `{"status":"ok"}` regardless of per-event field completeness, to prevent bridge-side retry-storms. `test_missing_sender_field_returns_422` updated to assert 200 (documenting intent, not a stale test). | 2026-XX-XX |
```
— or —
```markdown
| ID | Decision | Details | Date |
|---|---|---|---|
| S19-01b | OPTION B: Add 422 validation for missing sender | `_handle_bridge_event()` raises on a missing `sender` field instead of silently skipping the event. Requires confirming with the bridge-side client first that it can tolerate a non-200 response without retry-storming. | 2026-XX-XX |
```

---

# PART 3 — open-chat.html / Local Ollama AI Capability

**Route:** `POST /chat/message` (`app/main.py:3358`), gated by `require_api_key` (any active `fazle_admins` row or the shared internal key — **not** role-tiered within `fazle_roles`). Branches to `modules.ai_console.orchestrate_console_request` (active, per live `.env: AI_CONSOLE_CHAT_ENABLED=true`) or a legacy `admin_chat_lab` fallback.

**Model:** Ollama primary (`phi4-mini:latest`, confirmed both in `app/config.py:23` and live `.env:9`) → Groq (`llama-3.1-8b-instant`) → GitHub Models (`openai/gpt-4o-mini`) fallback chain, unchanged since the 2026-06-24 switch.

**Grounding: 🟢 genuinely business-grounded, not generic chat.** The active path assembles live read-only DB evidence (payroll/escort/attendance/cash-ledger/recruitment summary tools) plus a real `rag.search(question, k=5, role="admin")` call (`modules/ai_console/live_adapters.py:1074-1093`) into a single context block, injected into an explicit Bengali system prompt (`app/ollama.py:377-415`) that instructs the model to use only the given data. RAG **is** wired directly into `open-chat.html`'s query path (not a disconnected `kb.html`-only feature) whenever the KB toggle is on.

**Memory:** client-held only — last ~10 turns resent by the browser tab each call; no durable server-side per-thread memory (a reload/new tab starts cold). `llm_conversation_log` is write-only audit, never read back in.

**RAG:** `paraphrase-multilingual-MiniLM-L12-v2` embeddings, Qdrant collection `fazle_rag_chunks` (confirmed live config `QDRANT_HOST=172.20.0.2`), ~8,878 chunks as of 2026-07-11/12 (not re-verified live this session — no query tool used against Qdrant directly, only doc corroboration). `kb_file:` governance-tree chunks correctly role-gated to `admin`/`developer` only.

🟡 **Latency risk, not yet a confirmed complaint but structurally real:** tool evidence-gathering runs in a plain sequential loop (`modules/ai_console/orchestrator.py:210-230`, not `asyncio.gather`), and the **default fallback tool bundle for an unclassified query already includes a `systemctl list-units` subprocess shell-out** (`RT-001`, 6s timeout) plus 3 DB reads plus a memory lookup — all sequential, before the LLM call even starts. The active AI-console path also has no overall request timeout (unlike the legacy fallback, which has an explicit 240s ceiling). Extending business-context further without parallelizing this chain would compound latency in an easily-demonstrable way.

**Gap vs. Owner's stated target** ("AI understands full business context, assists with recruitment replies, escort queries, payroll queries, social-reply drafting"): payroll/escort/attendance/cash *read* queries are already wired in; **social-auto-reply drafting and recruitment-reply generation are not** — those pipelines (`modules/social_auto_reply`, `modules/recruitment_ai`) are entirely disconnected from the `ai_console` tool registry today. The Owner's "single NL query surface... with citations" is explicitly tracked in the queue file as blocked on the (separately gated, undesigned) Phase 2 dispatch/GL work.

**Safest next increment** (touches none of escort/payroll/social-parser code): extend the existing read-only `BUS-00x` tool set / improve citation surfacing on tools already in place — stays inside already-existing read-only surface area.

---

# PART 4 — Social Auto-Reply Review

**Trigger chain:**
```
Inbound message → _handle_bridge_event()/_poll_bridge()
  → ingest_social_event() [classify_message() → RISKY_INTENTS→needs_admin | not-recruiting→ignored | else pending]
  → daemon_worker._loop() (background poll)
  → planner_worker.process_due_threads() [re-check recruiting-intent, per-sender cooldown]
      → _is_draft_always_sender()? → YES: create_draft_reply() (admin approval queue)
                                    → NO:  enqueue_reply() → social_reply_queue → send_queue.sweep_once() → real send
```

**🟢 Confirmed: Selective-draft (not blanket) policy matches the documented S13-01 decision exactly** — quoted code (`planner_worker.py:111-131`) reuses `bridge_poller._is_draft_always()`, checked against `DRAFT_ALWAYS_ROLES`/`PHONES`/`NAMES` from `app/config.py`.

**Category exclusion is classifier-based, not identity-based.** `RISKY_INTENTS` (escort_order, transaction, payment_issue, legal_issue, abuse, etc.) never reach the send/draft fork at all; only `SAFE_AUTO_SEND_INTENTS` (greeting, job_details, salary, location, etc.) do. `unclear` (the classifier's default) is itself risky, so ambiguous text fails closed.

🟡 **This exclusion has no phone/role-based backstop inside `social_auto_reply` itself** — unlike the poller's `_requires_legacy_workflow()` gate (Part 1B-iv), which combines keyword + mobile-number-pattern + media-type checks independent of classification quality. A message that doesn't happen to contain a matched escort/financial keyword could in principle be misclassified into a safe-auto-send bucket. Combined with Part 1B-iv's finding (webhook path missing the poller's pre-filter), this compounds risk specifically for bridge1/bridge2 traffic.

**AI call exists**, gated and fenced: `_polish_with_ai()` (`intelligent_generator.py:202-234`) calls Ollama only (no Groq/GitHub fallback for this specific call), with a hard `_safe_polish()` guard (`:237-247`) that rejects AI output containing the fallback-apology phrase, any of a fixed poison-word list, or any un-allowlisted number — the AI can only be discarded, never override approved facts.

🟡 **Enforcement level: code (`if`), not DB.** The draft-vs-send decision is a ~10-line Python branch immediately before the fork — no DB constraint backs it up. A future "simplification" edit to that stretch could silently collapse selective-draft back into blanket auto-send with no structural safeguard to catch it.

---

# PART 5 — Frontend Shared-Fetch Migration Risk (Step 5, informational — not to be executed yet)

**8 pages, not the originally-estimated 5** (`wbom-create-link.html`/`wbom-link-review.html` shipped after the original count). Per-page findings (full route inventory omitted here for length — available via the agent transcript on request; every route was confirmed to exist, zero 404s found):

| Page | Custom behavior FazleAuthFetch won't cover | What breaks if swapped blind | Risk | Independent? |
|---|---|---|---|---|
| **payroll.html** | Two structurally different wrappers (`api()` single-prefix, `adminApi()` dual-prefix-by-path-string), boot-time retry/backoff loop, no-cache headers | Admin Users/Audit tabs 404 (wrong prefix), boot retry lost (one dropped packet strands a valid session), stale cached data on Admin/Audit tabs | 🔴 HIGH — **recommend deferring or splitting into a multi-PR, endpoint-group-by-group migration** | Yes, but shares `fpe_api_key` localStorage key with 3 other pages |
| **dashboard.html** | `api(path,{method,body})` call shape vs. FazleAuthFetch's `(path,apiKey,body,opts)`; auto-logout on any 403 | Every ~10 call site breaks (body never gets sent, not stringified into the right slot) — a full rewrite, not a wrapper swap | 🔴 HIGH | Partially — shares `fazle_api_key` with wa_chat/open-chat; its eager 403-logout is inconsistent with those two pages' softer 403 handling |
| **open-chat.html** | `apiFetch(path, method, body)` positional signature — **method and API key would land in swapped argument slots** | Every request sends the auth header as the literal string `"POST"` — total auth failure on every call | 🟡 MEDIUM (low call count, but catastrophic if swapped without review) | Yes, shares key group but no conflicting logout behavior |
| **wa_chat.html** | Distinguishes 403/429/5xx into different thrown error strings; drives an SSE stream authenticated via `?key=` query string (untouchable by a header-only utility) | Loses the "rate-limited" vs. "server down" UX distinction if anything depends on it (needs a follow-up grep) | 🟡 MEDIUM | Mostly — safer of the shared-key trio to migrate first (doesn't clear the key on 403) |
| **kb.html** | One `FormData` multipart upload relying on browser-set `Content-Type` | FazleAuthFetch force-sets `Content-Type: application/json`, breaking the upload button outright unless explicitly overridden | 🟡 MEDIUM | Yes, own key |
| **drafts.html** | Local `api()` never throws — returns `{ok:false}`, callers have no try/catch | Approve/Delete/Edit buttons go silently dead (unhandled promise rejection) instead of showing today's error toast | 🟡 MEDIUM | Yes, own key |
| **wbom-create-link.html** | Bespoke 409-as-success branch (already-linked = skip, not error) | Breaks the one-click-per-row workflow — 409 becomes a hard error, re-enabling buttons instead of auto-advancing through the 180-employee queue | 🟢 LOW-MEDIUM (one-time tool) | Yes |
| **wbom-link-review.html** | None — simplest fetch pattern of all 8, near-identical to FazleAuthFetch's own contract | Nothing | 🟢 LOW | Yes |

**Safe migration order:** `wbom-link-review` → `wbom-create-link` → `kb` → `drafts` → `wa_chat` → `open-chat` → `dashboard`, with **`payroll.html` treated as its own separate, high-caution project** rather than slotted into this sequence — not because it's technically harder per call, but because its blast radius (production payroll/payments system-of-record) is the highest of all 8 by a wide margin.

**Bonus finding:** `escort-roster.html` — the one page *already* migrated — is itself inconsistent (its DELETE calls and Admin-Users-tab calls still bypass the shared utility with raw inline `fetch()`). Worth a quick cleanup independent of the other 8.

---

# PART 6 — Current Source of Truth Summary

**1. Last applied migration:** `048_chat_history.sql` (2026-07-15). `047` correctly absent — rejected 2026-07-14, replaced by the reviewed create-and-link/link-fpe tools (see `docs/NEXT_IMPLEMENTATION_QUEUE.md`'s "Resolved/closed" section).

**2. Active routes:** see Part 1E / Part 5's route tables for the full per-page inventory; module-level router mounts are in the table at the top of Part 1E's source data (`fpe_router`→`/api/fpe`, `escort_roster_router`→`/api/escort-roster`, `client_billing_router`/`attendance_router`/`kb_upload_router`/`drafts_router`/`social_auto_reply_router`(`/api/social`)/`preflight_guard_router`/`wa_chat_router`(`/api/wa`) — all mounted with no extra prefix beyond what's already in each router file).

**3. Working features:**
- **Payroll:** office-attendance CRUD, eligibility/dry-run/reconciliation, client billing (Commit D, payment recording + audit log) all live and verified. Authoritative office payroll flag still off (0 real eligible employees). ~55 distinct frontend call sites in `payroll.html` alone.
- **Escort lifecycle:** intake→confirm→running→completed all live; sender-validation + 24h hard-delete gates intact; dual-write into `escort_roster_entries` still unresolved (now known to have a 3rd writer, §1B-i); destination-field leak now root-caused (§1C).
- **Social auto-reply:** Selective-draft policy confirmed matching spec; AI-polish call fenced by a hard fact-guard; 🔴 the webhook-path missing-gate risk (§1B-iv) is the most significant open item here.
- **open-chat/local AI:** genuinely business-grounded via RAG + read-only DB tools; social-reply/recruitment-reply generation not yet wired into the console.
- **KB/RAG:** ~8,878 chunks, role-gated, hybrid BM25+Qdrant, wired into open-chat directly.

**4. Known broken/incomplete items (code-evidence-based, consolidated across this whole audit):**
- 🔴 Webhook path missing the poller's `_requires_legacy_workflow` gate — escort orders over `/webhook/mcp1`/`mcp2` can be silently swallowed by social auto-reply (§1B-iv, **new finding, not in the queue doc yet**).
- 🔴 Destination-field leak — now confirmed reproducible, 6 rows not 4, root cause identified (§1C, **upgrades the KB doc's "unconfirmed" status**).
- 🟡 Escort-lifecycle dual-write concurrency hang — unresolved, now known to have a 3rd writer (§1B-i, §2 Step 1).
- 🟡 Fail-open dedup guard on the webhook path (§1B-iv) — a DB hiccup on `processed_bridge_messages` disables double-processing protection rather than failing safe.
- 🟡 Blocking synchronous SQLite call inside `_poll_bridge()`'s async loop (§1B-iii).
- 🟡 Duplicated escort-client-phone normalization logic in two places (§1D).
- 🟡 Hardcoded admin bridge number (`8801880446111`) in 5+ files instead of config (§1D).
- 🟡 3-week governance-log gap, Session 12→13, with 2 undocumented schema migrations (§1A).
- 🟢 8 (not 5) frontend pages still need the shared-fetch migration, with `payroll.html`/`dashboard.html` both HIGH risk for a careless swap (Part 5).
- 🟢 `open-webui` Groq API key still in plaintext (`/home/azim/docker-compose.yml`, previously flagged, still not rotated).

**5. Module boundary map (partial, evidence-based):**

| Table | Primary owner (writer) | Other writers | Readers-only |
|---|---|---|---|
| `escort_roster_entries` | `modules/escort_roster/db.py` (`sync_program_to_roster`, `create_roster_entry`) | `modules/escort_lifecycle/__init__.py` (raw duplicate INSERT) | `escort_roster/routes.py` (counts/summaries) |
| `wbom_whatsapp_messages` | `modules/bridge_poller` (`_save_message`) | — | `modules/admin_commands/nl_search.py`, `modules/recruitment_detector`, `chat_sync` (staged, not yet live) |
| `fpe_cash_transactions` | `modules/fazle_payroll_engine` | `modules/payroll` (`mark_paid()`, S13-02) | `modules/ai_readonly_tools` |
| `escort_roster_entries`/`wbom_escort_programs` | (see above) | one-off Session 16-18 cleanup scripts (governance-logged) | — |
| `chat_history` | `~/chat_sync_staging/sync.py` (staged, not yet deployed) | none | none (no fazle-core code reads it yet) |

**6. Config/env variables flagged:** `DATABASE_URL_TEMPLATE` (handle with care — this session accidentally displayed it once, see header note); admin/bridge numbers duplicated as literals outside `app/config.py` (§1D); `open-webui`'s Groq key still plaintext outside this repo.

## 7. RECOMMENDED NEXT SESSION PLAN

Ordered to minimize risk to the currently-working ~80% payroll/escort-roster functionality, cheapest/lowest-risk first:

1. **(0.5 session) Governance backfill:** add retroactive `management_decisions.md` entries for the two undocumented schema migrations (`043_office_assistant_role.sql`, `046_employee_edit_requests.sql`) found in §1A — pure documentation, zero code risk, closes the biggest process gap found.
2. **(0.5 session) Step 7 — webhook/mcp1 decision:** present the two draft options from Part 2 to the Owner, record whichever is chosen. Zero code required for Option A; a small, well-scoped change for Option B once the bridge-retry question is answered.
3. **(0.5-1 session) Step 2 — destination-field leak fix:** root cause is now fully confirmed with a reproducible test case (§1C) — this is now a normal KB-first proposal + one-line regex fix (`\s*`→`[ \t]*`) + a backfill decision for the 6 already-corrupted rows, not an open investigation anymore.
4. **(1 session) NEW — webhook/poller parser-gate parity (§1B-iv):** port `_requires_legacy_workflow()` (or an equivalent gate) into the webhook path, and fix the fail-open dedup-check exception handler to fail closed instead. This is higher real-world risk than the already-known dual-write hang, since it can silently drop live customer escort orders today, but it's also more contained (touching `app/main.py`'s webhook handler and/or importing the poller's existing gate function) than a concurrency debug session.
5. **(1+ session, open-ended) Step 1 — dual-write concurrency hang:** use the diagnostic SQL from Part 2 and the isolated repro command; budget real, uninterrupted debugging time, not a quick pass — this already burned one full attempt.
6. **(0.5 session, whenever sudo is available) Step 4 — chat_history deployment:** low-risk, mechanical, just needs a session with `sudo` access.
7. **(ongoing, low priority) Frontend fetch migration (Part 5):** start with the two LOW-risk one-time tools, defer `payroll.html` to its own dedicated multi-session effort whenever it's picked up at all.

Not recommended this cycle: anything in Phase 2 (dispatch/GL) — still correctly gated behind an Owner scope conversation per the existing roadmap, untouched by anything found in this audit.
