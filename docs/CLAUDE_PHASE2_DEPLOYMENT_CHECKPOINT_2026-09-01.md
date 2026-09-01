# CLAUDE PHASE-2 — DEPLOYMENT & OBSERVATION CHECKPOINT

**Recovery authority for the owner-authorized review → deploy → observe workflow.**
Resume with `RESUME CLAUDE PHASE2 OBSERVATION REVIEW` (post-observation) or from current git + this file.
No secrets, no full customer phone numbers in this file.

---

## Stage 0 — initial state verification (2026-09-01T18:37Z)

| Field | Value | Evidence |
|---|---|---|
| Core repo | `/home/azim/core` | — |
| Core branch | `feat/phase2-inbound-identity-2026-09-01` | `git branch --show-current` |
| Core HEAD | `486d3de` | `git rev-parse HEAD` |
| Core upstream | none (branch not pushed) | `git status -sb` / `git ls-remote` empty |
| Core 6 commits | `71bb3c0` → `5bc1c4f` → `c44d80d` → `642d8e1` → `78d8238` → `486d3de`, linear off `5e62ed5` | `git log --format='%h parent=%p'` |
| Prod baseline branch | `feat/owner-accountant-payment-relay-20260830` @ `5e62ed5` == `origin` | `git rev-parse` |
| Core worktree | only `docs/BRIDGE_MEDIA_...` (M, deliberate) + 2 untracked audit docs | `git status --porcelain` |
| Core worktrees | single (`/home/azim/core`) | `git worktree list` |
| Running service PID | **3115745**, `NRestarts=0`, `active/running` | `systemctl show` |
| Service start | 2026-08-31 05:39:30 CEST | `ActiveEnterTimestamp` |
| Loaded revision | **`5e62ed5` (OLD code)** — CONFIRMED: `/api/identity/search-by-name` and `/api/whatsapp/contact/summary` both return **HTTP 404** on the running service (routes not registered); process start predates every Phase-2 file mtime; `identity_resolver` not in `/proc/3115745/maps` | curl + `systemctl` + `/proc` |
| Hot reload | none — `DEBUG=false` | `.env` |
| `PHASE2_IDENTITY_RESOLVER_ENABLED` | **absent from `.env`** (code default OFF) | `grep .env` |
| `/health` | **status: ok** — db, bridge1/2/3_db, poller_b1/2/3, outbound, scheduler, disk, mem, cpu, ollama, qdrant all `ok` | `curl localhost:8200/health` |
| Pollers | b1 age 19s, b2 22s, b3 6s, all queue_depth 0 | health probe |
| Outbound | pending 0, DLQ 102 (actionable 6) — pre-existing slow drift (98→102), unrelated | health probe |
| Bridges | PIDs 3640163 / 3688844 / 2589884, `NRestarts=0`, started 2026-08-29 — untouched; `whatsapp.db` inodes stable | `systemctl show` + `stat` |
| `schema_migrations` | 84 rows, max `078_*` — unchanged since session start (072+078 applied 2026-08-31 20:52, before this session) | `psql` |
| MCP repo | `/home/azim/fazle-mcp` | — |
| MCP branch / HEAD | `recovery-2026-08-25` @ `852043f` == `origin` (0/0) | `git` |
| MCP worktree | `M audit_tools.py`, `M draft_tools.py`, `M server.py` (mixed: mine + prior Stage D8/E9) | `git status --porcelain` |
| MCP `dbadaa4` | recoverable — `reflog HEAD@{1}` | `git reflog` |
| MCP process | PID 64399, owned by `mcp_stdio_watchdog.py` (PID 64397) ← hermes-agent (64377) | `ps` / `pstree` |

**⚠️ `RESTART WOULD DEPLOY PHASE-2 BRANCH`** — the core working tree is on `feat/phase2-inbound-identity-2026-09-01` while production runs `5e62ed5`. This is intended for the deployment stage. Emergency-restart rule: `git checkout feat/owner-accountant-payment-relay-20260830` first.

**No differences from the owner-review package's expected values.** No other agent/user has changed the repos.

**Rollback point at Stage 0:** `feat/owner-accountant-payment-relay-20260830 @ 5e62ed5` (== running code).

---

## Stage log

### Stage 0 — state verification — DONE 2026-09-01T18:37Z
Evidence above. No production mutation.

### Stage 1 — META_APP_SECRET rotation assessment — DONE 2026-09-01T18:45Z — **EXTERNAL_BLOCKED**

| Check | Result |
|---|---|
| Former secret-bearing scripts (`scripts/phase7_*.py`, `restart_fazle7.sh`) | **ABSENT** (`ls` → No such file) |
| Value in tracked files | **NONE** (`git ls-files` scan) |
| Value in git history | **NONE** (`git log -S`, all branches) |
| Value in working tree (excl. `.env*`) | **NONE** |
| Value locations (authorized) | `/home/azim/core/.env` + gitignored `.env.bak.*` / `.env.save` + `/home/azim/secure-env-backup/env_live_*.bak` (June snapshots). All `-rw-------`, all gitignored. |
| `runtime-services.env` (2nd systemd EnvironmentFile) | does **NOT** contain it — source is solely `/home/azim/core/.env` |
| Consumer | **exactly one**: `app/main.py:2487-2493` — `POST /webhook/meta` HMAC-SHA256 verification of `X-Hub-Signature-256` on inbound Meta webhooks. `settings.meta_app_secret` (pydantic, env `META_APP_SECRET`, `@lru_cache`). |
| `META_WEBHOOK_SIGNATURE_ENFORCEMENT` | unset → config default **`True`** — mismatched/missing signature ⇒ HTTP 403 |
| Restart needed after rotation | **YES** — full `fazle-core.service` restart (Settings read at construction; cache-clear insufficient) |
| Webhook validation depends on this value | **YES** (the app secret). `META_VERIFY_TOKEN` is a **separate** value (GET-handshake) — NOT affected by app-secret rotation. |
| Meta grace period for App Secret | **none** — reset invalidates the old immediately; brief 403 window until fazle-core reloads. Meta retries failed webhook deliveries, so a short gap loses nothing. |
| Can Claude perform the rotation? | **NO** — no authenticated Meta Dashboard / browser capability in this environment |

→ Recorded as `OWNER_ACTION_REQUIRED #1` with exact steps (see final report). Continuing with all non-blocked work.

### Stage 2 — independent 6-commit review — DONE 2026-09-01T19:20Z

| Commit | Purpose | Independent review findings | Verdict |
|---|---|---|---|
| `71bb3c0` | security | Doc/config/manifest only. `.gitignore` +`venv`; `ARCHITECTURE.md` verify-token literal → `${…}` (real sanitization; value also in git history — noted); manifest phase7 block → RESOLVED note (harmless — the 7 files are gone from disk). The 7 script removals are working-tree actions, not git-visible (were untracked) — message explains this. No code/schema/routing. | **PASS** |
| `5bc1c4f` | P1 identity | `_check_accountant_settings` Step 1b: verified `.env` `ACCOUNTANT_PHONE` is `88…` form → both compare sides identical; **routing branch order in `process_message` unchanged** (accountant already hits its branch via seed today — same output role). NOTE: Step 1b now passes `emp=<lookup>` → accountant identity dict carries `employee_id` where the seed path gave `None` — **verified harmless**: the ACCOUNTANT branch (`process_message:1069`) and every branch before it use `sender` (phone), not `identity["employee_id"]`; `create_attendance_draft` takes `sender`. `_lookup_seed`/`_lookup_contact` platform filter = no-op on current data (all 38 fcr + 2899 contact rows are `whatsapp`). FPE-fallback drop of `e.status='active'`: **DB check → 0 rows would newly resolve today** (pure no-op + future-proofing). `_should_silent_skip` normalize both sides = no-op for `88…`-form `.env`. `storage_*` phone helpers pure, not yet called in this commit. | **PASS** (with the verified-harmless `employee_id` note) |
| `c44d80d` | Phase-2 resolver + wiring + read tools | `identity_resolver.py`: **zero writes** (10× `fetch_one`/`fetch_all` only) → Payment/FPE-safe; flag default **OFF** verified (`os.getenv(...,"false")`); `identity_phone`+`canonical_key` computed OUTSIDE the try (fallback preserves them — test covers); tier chain fetched once, `_best_name` pure → ~5 DB round-trips/msg (flag ON); `available_columns` process-cached, only caches non-empty; all SQL parameterized (column names from `information_schema` or literals); `canonical_message_key` = **transport-ID-only, ZERO readers grep-confirmed**; all 4 contact lookups scoped `platform='whatsapp'`. `bridge_poller`: always-on `canonical_phone` normalization = **effective no-op for current poller traffic** (whatsmeow `pn` is already `8801…`; foreign kept digits-only; `unresolved:`→NULL as before); `_phase2_enrich_raw_copy` runs AFTER the raw INSERT, flag-gated, schema-checked, `try/except`→`log.debug` — **cannot break the raw row or routing**; dynamic UPDATE `SET` uses only hardcoded column names + bound params. `app/main.py`: `_save_message` +7 optional kwargs, `_canon` defaulted from `sender` only for `_WHATSAPP_PLATFORMS` (**PSIDs/messenger/ig/fb NOT normalized** — verified); Phase-2 cols via `_existing_columns` filter (schema-tolerant, **cannot cause INSERT failure**); `_handle_meta_message` flag-gated, **never sets identity_role** (Meta clobber impossible), `source_message_ref`/wamid preserved, dedup SELECT unchanged; `_handle_bridge_event` (DORMANT path) NO-CLOBBER verified (`if (not _id_role or _id_role=="unknown")` — a meaningful identity_brain role incl. `escort_worker`/`escort_client` is NEVER overwritten) + new `unresolved:` routing skip (defensive, inert today). New endpoints auth-gated, read-only, bounded (limit clamps, cursor), never-guess on ambiguous name. **2 LOW items** ↓ | **PASS** |
| `642d8e1` | restart-gate 5b | Pure tooling+docs. `check5b` = one read-only `SELECT name FROM schema_migrations`, 10s timeout, WARN (not BLOCK) on infra failure, no DDL. Ran live → `pending: []` = would PASS. `MIGRATION_INVENTORY.md` 078 entry lets check5 pass. Doc updated. | **PASS** |
| `78d8238` | tests | 5 test files, **+774, zero non-test code**. No secret literals (`import secrets` = stdlib, for unique refs). Phone literals all obvious fixtures (`8801700000001`, `8801711111111`, …). Isolated-test-DB only (conftest guards). | **PASS** |
| `486d3de` | docs | Session checkpoint + final report. Re-scan clean (§ Stage 3). | **PASS** |

**c44d80d — 2 LOW items:**
1. `whatsapp_messages_query` (`?phone=`) and `/api/whatsapp/contact/summary` reference the migration-078 column `resolved_phone` in their WHERE without a schema guard → would 500 on a DB missing 078. **Mitigated:** production HAS 078; new check 5b BLOCKS a deploy without it; the whole feature depends on 078. Acceptable as-is; documented.
2. Migration `078` file comment named the removed `persist_inbound_central()` helper and claimed a non-existent `process_bridge_inbound pre-check` consumer for `idx_wbom_msg_canonical_key`. → **FIXED** in the working tree (comment only; migration is applied, `db/migrate.py` skips by basename so no re-run) — pending a "review corrections" commit.

**No critical/high defect. Earlier agent's conclusions independently re-verified against code + live DB.**

### Stage 3 — fazle-mcp mixed-work separation — DONE 2026-09-01T19:40Z

Non-destructive method: `git stash -u` → split the combined diff into MINE / THEIRS
by hunk (mapped each hunk to author by content + date markers) → `git apply` MINE →
commit → `git apply` THEIRS → `git stash drop`.

**Byte-perfect verified:** `git diff 852043f` (current tree = MINE-commit + THEIRS-working)
vs `git stash show -p` (original combined), content-identical (only `@@` line numbers differ).

| | Result |
|---|---|
| MINE — **committed `8a3f7f0`** on `recovery-2026-08-25` | `audit_search_identity_by_name`, `audit_whatsapp_contact_summary` (audit_tools.py); `search_identity_by_name`, `get_whatsapp_contact_summary` @mcp.tool (server.py); `phone` + `message_body_contains` params on `query_conversations` / `audit_query_conversations`; `get_contacts` docstring pointer. All read-only, PII-masked, bounded (1-25 / 1-20), cursor-paginated. `server.py` imports clean; both tools register in `mcp._tool_manager._tools`. |
| THEIRS — **uncommitted, preserved** | prior session's Stage D8/E9: `get_conversations_requiring_attention`, `business_inbox_overview`, `office_group_activity`, `propose_business_action`, `notify_admin_of_draft` + `draft_tools.py` + `from typing import Optional` + 2 docstring rewrites (`get_recent_messages`). NOT reviewed, NOT committed, NOT to be deployed. Owner decides: review + commit, or discard. |
| `dbadaa4` (prior bundling commit) | already reset; reflog has it if the owner wants the exact pre-split state. |

### Stage 4 — the two untracked audit documents — DONE 2026-09-01T19:50Z

Both `docs/PRODUCTION_SYSTEM_STATUS_2026-08-31.md` + `docs/WEB_UI_WORKFLOW_AUDIT_2026-08-31.md`:
read-only prior-session audits, `secret_scan` PASS, WEB_UI has 0 phones, STATUS has
5 distinct internal infra numbers **already committed** in `app/critical_numbers.py` /
`docs/MODULE_MAP.md` / `AUDIT_REPORT.md` (no customer PII). → **committed as-is**
(`2035641`), removing the sole restart-gate Check-1 blocker without weakening the gate.

### Post-review corrections (on top of the 6 reviewed commits)

| Commit | What |
|---|---|
| `df2e530` | `db/migrations/078` comment-only fix (SQL unchanged, already applied, `db/migrate.py` skips by basename) — removes the `persist_inbound_central()` reference and the false "process_bridge_inbound pre-check" consumer claim for `canonical_message_key`. |
| `2035641` | track the 2 prior-session audit docs. |

Core branch `feat/phase2-inbound-identity-2026-09-01` is now at **`2035641`** (8 commits ahead of `5e62ed5`).

### Stage 5 — pre-push acceptance gate — IN PROGRESS 2026-09-01T19:50Z

| Gate item | Result |
|---|---|
| Independent commit review | DONE (Stage 2) — no critical/high defect; 1 LOW fixed, 1 LOW documented |
| Phase-2 test suite (isolated `fazle-test-postgres`) | **55 pass** |
| Payment / FPE regression | **98 pass** (`test_payment_relay` 25, `_accounting` 9+, `bridge_poller_payment_relay_gate`, `fpe_bridge_path_authority`, `fpe_rules`) |
| Recruitment / attendance / escort / admin / RBAC / identity_brain / bridge3-golden | **all pass** individually (11 suites) |
| Pre-existing failure #1 (`test_identity_brain`+`test_escort_client_phone_normalization_fix` same process) | **REPRODUCED at clean `5e62ed5`** (7 failures) — confirmed pre-existing |
| Pre-existing failure #2 (`test_route_b_handoff.py`) | **REPRODUCED at clean `5e62ed5`** — confirmed pre-existing |
| Default flag OFF | verified — `PHASE2_IDENTITY_RESOLVER_ENABLED` absent from `.env`, code default `false` |
| Rollback point | `feat/owner-accountant-payment-relay-20260830 @ 5e62ed5` (== running code) |
| secret scan / restart-gate check-only | ↓ next |
