# CLAUDE PHASE-2 — DEPLOYMENT FINAL REPORT

**Session:** owner-authorized review → deploy (flag OFF) → observe
**Date:** 2026-09-01
**Companion files:** `docs/CLAUDE_PHASE2_DEPLOYMENT_CHECKPOINT_2026-09-01.md` (stage-by-stage evidence), `docs/CLAUDE_PHASE2_OBSERVATION_2026-09-01.md` (24–48 h checklist)

---

## Executive status

**`CORE DEPLOYED — PHASE-2 OFF — OBSERVATION STARTED`**

`fazle-core` was restarted through `scripts/restart_gate.sh --restart` onto the reviewed
Phase-2 branch with `PHASE2_IDENTITY_RESOLVER_ENABLED` **absent from `.env`** (enrichment
dark). All 10 gate checks passed; post-deploy smoke passed on every layer. A 24–48 h
observation window is running. **Phase-2 enrichment is NOT enabled and enabling it is not
authorized by this session.**

Deferred to the owner: `META_APP_SECRET` rotation (no Meta interface here); the fazle-mcp
**server reload** (no systemd unit; coupled to a live Hermes agent); the fazle-mcp Stage
D8/E9 prior-session work (stashed, preserved); the Phase-2 enablement decision.

---

## Meta secret status

| Item | Status |
|---|---|
| Exposure remediation | **COMPLETE on disk** — the 7 plaintext scripts were removed (commit `71bb3c0`, prior session). `META_APP_SECRET` value now exists ONLY in `/home/azim/core/.env` + gitignored `.env.bak.*` / `.env.save` / `/home/azim/secure-env-backup/env_live_*.bak`. **0** occurrences in any tracked file or in git history (`git log -S`, all branches). `scripts/secret_scan.sh` PASS. |
| Consumer | exactly one: `app/main.py:2487` — HMAC-SHA256 verification of `X-Hub-Signature-256` on inbound Meta webhooks. `META_WEBHOOK_SIGNATURE_ENFORCEMENT` default `True`. `META_VERIFY_TOKEN` is a **separate** value, unaffected. |
| Rotation | **`OWNER_ACTION_REQUIRED #1`** — needs Meta App Dashboard access (unavailable here). No secret value is in this report, chat, logs, git, or any command output. |

---

## Core review

Independently re-verified against code + the live DB; the earlier agent's conclusions were
checked, not trusted.

| Commit | Purpose | Review findings | Tests | Verdict |
|---|---|---|---|---|
| `71bb3c0` | security remediation | `.gitignore` +`venv`; `ARCHITECTURE.md` verify-token literal → `${…}` (value also in git history — noted); manifest phase7 block → RESOLVED note (the 7 files are gone). No code/schema/routing. | n/a | **PASS** |
| `5bc1c4f` | P1 identity correctness | accountant Step 1b: `.env` `ACCOUNTANT_PHONE` is `88…` form → compare sides identical; **`process_message` branch order unchanged** (accountant already routes via seed today — same output role). NOTE: Step 1b now populates `employee_id` on the accountant identity dict — **verified harmless** (the ACCOUNTANT branch and every branch before it key on `sender`, not `identity["employee_id"]`). `_lookup_seed`/`_lookup_contact` `platform='whatsapp'` = no-op on current data (all 38 fcr + 2899 contact rows are `whatsapp`). FPE-fallback drop of `e.status='active'`: **DB check → 0 rows would newly resolve today** (no-op + future-proofing). `_should_silent_skip` normalize = no-op for `88…` `.env`. | `test_phase2_p1_identity.py` (6), `test_phase2_storage_phone.py` (6) | **PASS** |
| `c44d80d` | Phase-2 resolver + wiring + read tools | **deep review — see below**. | `test_phase2_identity_resolver.py` (24), `test_phase2_bridge_poller_regression.py` (9), `test_phase2_read_tools.py` (6), `test_phase2_escort_worker_vs_client.py` (4, still green) | **PASS** (+ 1 LOW fixed, 1 LOW documented) |
| `642d8e1` | restart-gate check 5b | one read-only `SELECT name FROM schema_migrations`, 10s timeout, WARN (not BLOCK) on infra failure, no DDL. Ran live → `pending: []`. Inventory + doc updated. | n/a (ran live) | **PASS** |
| `78d8238` | tests | 5 files, +774, **zero non-test code**. No secret literals. Phone literals all obvious fixtures. | n/a | **PASS** |
| `486d3de` | session docs | prior-session report + checkpoint. Re-scanned clean. | n/a | **PASS** |

**Post-review corrections (separate commits, no code):** `df2e530` — `db/migrations/078` comment
(SQL unchanged, already applied, skipped by basename): removed the `persist_inbound_central()`
reference + the false "process_bridge_inbound pre-check" consumer claim for
`canonical_message_key`. `2035641` — track the 2 prior-session audit docs.

### Deep review of `c44d80d`

- **Flag default OFF** verified: `os.getenv("PHASE2_IDENTITY_RESOLVER_ENABLED","false")` — absent ⇒ `false`. Confirmed live post-deploy (`/proc/<pid>/environ` has no `PHASE2*`).
- **Restricted to inbound WhatsApp/Meta**: `_save_message` normalizes `canonical_phone` only for `source ∈ {bridge1,bridge2,bridge3,meta,whatsapp}`; messenger/instagram/fb-comment (PSIDs) are excluded — verified.
- **PSIDs / IG / FB / groups / unresolved-LID cannot become canonical phones**: `compute_identity_phone` → `normalize_phone` (BD-only) → a PSID / group JID / 16-digit id returns `None`; `unresolved:*` → `None`; last-resort is `lid:<digits>` (prefix-tagged, never a phone, rejected by `storage_canonical_phone`).
- **`canonical_phone` normalization**: `01…`→`8801…`; `+880…`→`8801…`; JID suffix stripped; valid foreign number kept digits-only (NOT nulled); `unresolved:`/`lid:` → `None`; PSID (>15 digits) → `None`. `storage_phone_last10` returns `None` for a LID (never a 10-digit slice).
- **Names never override phone identity**: in `resolve_inbound_identity`, `identity_role`/`identity_source`/`trusted_identity_name`/`employee_id`/`contact_id` come **only** from the phone-keyed tiers; the name inputs (`bridge_*`/`meta_profile`/`social_inbox`) reach **only** `_best_name` → written **only** to `sender_name_snapshot/_source/_confidence`. `message_body` reaches only `compute_canonical_message_key`'s now-**ignored** `content` param.
- **Existing role not clobbered / escort worker-client authoritative**: NO-CLOBBER in `_handle_bridge_event` — `if (not _id_role or _id_role=="unknown")` — a meaningful `identity_brain` role (incl. `escort_worker`/`escort_client`) is never overwritten by the resolver's coarser taxonomy. `_handle_meta_message` never passes `identity_role` at all.
- **Accountant deterministic, routing order unchanged**: Step 1b (`5bc1c4f`) — verified live: `/api/identity/resolve?phone=<ACCOUNTANT>` → `role=accountant, source=settings`.
- **Inactive / fpe-only employees resolvable**: `_lookup_employee` / `_lookup_fpe_employee` have no status filter, return `identity_lifecycle`.
- **All WhatsApp contact lookups scoped `platform='whatsapp'`**: `_lookup_contact_role`, `_lookup_wbom_contact`, `search_identity_by_name` (contact + fcr).
- **Schema-tolerant enrichment cannot lose a raw message**: `_phase2_enrich_raw_copy` runs as a follow-up UPDATE **after** the raw INSERT, flag-gated, `available_columns`-checked, `try/except → log.debug`. `_save_message` filters Phase-2 columns through `_existing_columns`.
- **Fallback preserves a valid normalized phone**: `identity_phone` + `canonical_message_key` computed **outside** the try; a DB error in a tier lookup degrades only the role fields. Test: `test_fallback_preserves_identity_phone_on_db_error`.
- **DB round-trips bounded**: flag ON ⇒ ~5 queries/inbound message (tier sources fetched once, `_best_name` pure); `available_columns` process-cached. No N+1.
- **Cache safe**: module-level dict, only caches non-empty results, `reset_column_cache()` test hook; schema is static under a running process (migrations require a restart).
- **SQL parameterized**: every value is a bind param; column/table names come from `information_schema` or are hardcoded literals — no injection surface. `search_identity_by_name` passes `%needle%` as a bind param.
- **Query limits bounded**: `search_identity_by_name` `LIMIT $2` + `[:limit]`; endpoints clamp 1-25 / 1-20; `/query` cursor-paginated, 50-row ceiling.
- **`canonical_message_key` forensic-only**: **grep-confirmed ZERO readers** anywhere in `app/` `modules/` `shared/`. Transport-ID-only (`meta:wamid:*` / `bridge:msgid:*`); the content+60s heuristic is removed.
- **Meta persistence preserves `source_message_ref`/wamid**: `_handle_meta_message` still passes `source_message_ref=message_id` to `_save_message`; the dedup SELECT is unchanged.
- **Migration 078 additive + rollback-compatible with `5e62ed5`**: 6 nullable columns + 1 NULL-tolerant CHECK + 2 partial indexes. `5e62ed5` never references any of them; a code rollback needs **no** migration rollback.
- **Payment / FPE untouched**: `identity_resolver.py` has **zero** `INSERT`/`UPDATE`/`DELETE` (10× `fetch_one`/`fetch_all`). `git diff 5e62ed5..HEAD` touches **0** files under `payment_relay*` / `fazle_payroll_engine` / `modules/outbound` / `payment_workflow` / `payment_ingest`.

**c44d80d — 2 LOW items:** (1) `/query?phone=` and `/contact/summary` reference the 078 column
`resolved_phone` in WHERE without a schema guard — would 500 on a DB missing 078; **mitigated**:
production has 078, check 5b blocks a deploy without it, the whole feature depends on 078 —
accepted, documented. (2) migration 078 comment stale — **FIXED** (`df2e530`).

**No critical/high defect.**

### Two previously reported baseline test failures — reproduced at clean `5e62ed5` (git worktree)

| Failure | At `5e62ed5` | Verdict |
|---|---|---|
| `pytest test_identity_brain.py test_escort_client_phone_normalization_fix.py` (same process, that order) → 7 failures in the escort file (`_escort_client_phone_list()` reads empty) | **REPRODUCED** | pre-existing test-isolation bug (`test_identity_brain.py:47` `patch("app.config.get_settings")` leaking); the gate's check 4/4b never combine these two files |
| `test_route_b_handoff.py` → `test_known_employee_never_enters_route_b`, `test_ollama_failure_produces_safe_response_not_dropped` | **REPRODUCED** | pre-existing — stale `assert_awaited_once_with` not expecting `sender_name=`/`sender_message=` kwargs + a RESPX/Ollama mock miss |

Neither is in scope; documented for a separate cleanup (§ OWNER_ACTION #6).

---

## Core git and remote state

| | |
|---|---|
| Branch | `feat/phase2-inbound-identity-2026-09-01` — **pushed** (`origin` == local, verified `git ls-remote`) |
| HEAD | `a8bd9fa` |
| **Deployed commit** (loaded at the restart) | **`8070bfb`** — HEAD is `a8bd9fa` = `8070bfb` + 2 doc files (observation checklist + checkpoint text), **zero code delta** |
| Base / prod branch | `feat/owner-accountant-payment-relay-20260830 @ 5e62ed5` == `origin` — **untouched** |
| Not merged | `a8bd9fa` is contained only in this branch — `origin/main` still `0f5ef31` |
| 10 commits `5e62ed5..a8bd9fa` | 6 reviewed (`71bb3c0` `5bc1c4f` `c44d80d` `642d8e1` `78d8238` `486d3de`) + 4 post-review housekeeping (`df2e530` `2035641` `966e1c9` `8070bfb`) + 1 post-deploy doc (`a8bd9fa`) |
| Working tree | 1 dirty: `docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md` (manifest-approved, Owner directive: not committed by another session; masked 3 phone/LID values) |

---

## Audit-document decision

`docs/PRODUCTION_SYSTEM_STATUS_2026-08-31.md` + `docs/WEB_UI_WORKFLOW_AUDIT_2026-08-31.md`:
read-only prior-session audits, `secret_scan` PASS, WEB_UI 0 phones, STATUS 5 distinct
numbers all **internal infra** numbers already committed in `app/critical_numbers.py` /
`docs/MODULE_MAP.md` / `AUDIT_REPORT.md` (no customer PII, no sanitization needed).
→ **committed as-is** (`2035641`), which removed the sole restart-gate Check-1 blocker
without a manifest wildcard.

---

## Restart-gate result

**Attempt 1 (19:10Z): BLOCK on check 4b — `test_recruitment_salary_context_routing_fix.py::
test_recruitment_eligible_empty_reply_never_falls_to_employee_verification` → `Timeout >30.0s`
(pytest wall clock). Restart ABORTED, production UNTOUCHED.**

Root-caused as **CPU-contention artifact, not a code defect**:
- failing test in isolation → PASS, 3.5s CPU
- exact check-4b 6-suite batch at **`5e62ed5`** (worktree) → PASS (92 tests, 20s wall, 7.6s CPU)
- same batch on my branch → PASS (92 tests, 63s **wall** but **8.9s CPU** — identical CPU work; the gap is scheduler wait)
- box during Attempt 1 was `load1` ~8/8 (100%) — my acceptance-gate test runs + the gate's own check 4 + a live `hermes-agent` LLM call + persistent IDE tooling
- my changes add no `await`, no hot-path DB round-trip, no lock — a code hang shows as an assertion failure, not a 30s wall timeout
- `conftest.py:1093` already documents this exact fragility

The 30s per-test timeout was **not weakened or bypassed** (§7). Retried when the box was quieter.

**Attempt 2 (19:18:34Z, `load1` 4.51): OVERALL PASS — all 10 checks.**
```
[PASS] 1 cleanliness   [PASS] 2 secret scan   [PASS] 3 xfail 8/13   [PASS] 4 targeted
[PASS] 4b bridge3-golden   [PASS] 4c 14/14   [PASS] 5 inventory
[PASS] 5b migrations-applied   [PASS] 6 flags   [PASS] 7 backup 22h
Check 8 pre-restart health: ok
Check 9: startup-complete 8s, first healthy /health 8s (ok)  →  RESTART: SUCCESS
```

---

## Deployment evidence

| | |
|---|---|
| Old PID → New PID | **3115745 → 2981971** |
| `NRestarts` | **0** (clean restart, not a crash-loop) — still 0 at +12 min |
| `ActiveEnterTimestamp` | 2026-09-01 21:20:33 CEST |
| New code loaded | `/api/identity/search-by-name` + `/api/whatsapp/contact/summary` → **HTTP 200** (were **404** pre-restart) |
| Startup log | `Finished server process [3115745]` (clean exit) → `Started server process [2981971]` → `Application startup complete.` → `Uvicorn running` |
| `/health` at +12 min | **`ok`** (transient `degraded` on ambient `cpu` only, recovered) |
| Error log post-restart window | **0** Traceback / ERROR / CRITICAL |
| Bridges | **untouched** — PIDs 3640163 / 3688844 / 2589884, `NRestarts=0`, `ActiveEnterTimestamp` 2026-08-29 |
| `.env` | not modified · `schema_migrations` still 84 (no migration applied) |

---

## Feature-flag evidence

**`PHASE2_IDENTITY_RESOLVER_ENABLED` is OFF.** Absent from `/home/azim/core/.env`; absent from
`/proc/2981971/environ` (only `BRIDGE_MEDIA_ACQUISITION_ENABLED` / `PAYMENT_RELAY_ACCOUNTING_ENABLED`
/ `CONVERSATION_POLICY_SHADOW_ENABLED` are set — pre-existing). Code default is `"false"`.
Confirmed by data: `sender_name_snapshot` / `canonical_message_key` / `resolved_phone` populated
rows = **0 / 0 / 0** across all 22k+ rows, including post-deploy.

**Phase-2 identity enrichment is DARK.**

---

## Smoke verification

| Category | Result |
|---|---|
| **health** | `/health` `ok` — db, bridge1-3_db, poller_b1-3 (age ~22s, queue 0), outbound, scheduler, ollama (9 models), qdrant all `ok` |
| **bridges / pollers** | untouched; all 3 pollers healthy; b3 `quiet_but_poller_healthy` (logged out) |
| **DLQ / queue** | `pending 0`, `dlq 102`, `actionable_dlq 6` — **identical to pre-deploy** (102/6); no spike, no duplication |
| **error rate** | 0 post-restart vs 0 in a comparable pre-restart window |
| **recruitment** | pollers ingesting; `test_bridge3_recruitment_golden_baseline.py` green (gate check 4b) |
| **attendance** | routing suites green (`test_attendance` 39) |
| **escort** | `test_escort_lifecycle` (27), `test_escort_client_identity_convergence` (39), `test_phase2_escort_worker_vs_client` (4) green |
| **accountant identity** | live: `/api/identity/resolve?phone=<ACCOUNTANT>` → **`role=accountant, source=settings, confidence=100`** (Step 1b LIVE) |
| **existing role preservation** | live: known employee → `role=escort, source=db_employee` (not clobbered) |
| **canonical phone** | 0 new `01…`-local / 0 junk `canonical_phone` since deploy; `unresolved:` rows → 0 `canonical_phone` (298 rows) |
| **Payment / FPE** | frozen path intact — latest relay txn `id 4510` key `payment-relay-send:7747:0` `created_by=payment_relay_accounting`; **0** frozen-path files in the deployed diff; 0 new relay txns since restart (expected) |

Separate statements: **transport** verified (pollers/queue/DLQ). **routing** verified
(`/api/identity/resolve`, no error-rate change, regression suites). **persistence** verified
(canonical_phone shape, enrichment dark). **accounting** verified (frozen path evidence,
0 diff files, correct idempotency key on the last real booking).

**No smoke check failed. No rollback.**

---

## MCP separation

| | |
|---|---|
| **Mine** — committed `8a3f7f0` on `recovery-2026-08-25`, **pushed** | `audit_search_identity_by_name`, `audit_whatsapp_contact_summary` (audit_tools.py); `search_identity_by_name`, `get_whatsapp_contact_summary` @mcp.tool (server.py); `phone` + `message_body_contains` on `query_conversations` / `audit_query_conversations`; `get_contacts` docstring pointer. Read-only, PII-masked, bounded (1-25 / 1-20), cursor-paginated. `server.py` imports clean; both tools register. |
| **Prior session's Stage D8/E9** (`get_conversations_requiring_attention`, `business_inbox_overview`, `office_group_activity`, `propose_business_action`, `notify_admin_of_draft`, `draft_tools.py`, `from typing import Optional`, 2 `get_recent_messages` docstring rewrites) | **NOT mine, NOT reviewed, NOT committed, NOT deployed.** Preserved in `fazle-mcp` `stash@{0}` "Stage-D8E9-prior-session-work-PRESERVED-for-owner-review". |
| Split verification | **byte-perfect** — `git diff 852043f` (my commit + the stash) == the original combined diff, content-identical (only `@@` line numbers differ) |
| Prior bundling commit `dbadaa4` | reset; recoverable via reflog |

---

## MCP deployment status

**NOT deployed — `OWNER_ACTION_REQUIRED #2`.** The MCP stdio server (PID 64399, up since
2026-08-21) has **no systemd unit**; it is spawned by a manually-run `hermes` agent (PID 64377)
serving live admin/customer traffic, and its watchdog is parent-death-only (no respawn on
child kill). `kill 64399` risks dropping an in-flight Hermes conversation with no guaranteed
respawn — and there is zero urgency (additive read-only tools). `server.py` on disk is already
the reviewed `8a3f7f0` (Stage D8/E9 stashed), so whenever the agent next cycles the MCP server
it loads exactly that. Endpoint targets were verified live and correct (exact-phone query with
provenance; cursor pagination; `message_body_contains`; `search-by-name` ambiguous → `resolved:null`,
none → `count 0`; `contact/summary` bounded).

---

## Production mutations performed this session

1. `git push origin feat/phase2-inbound-identity-2026-09-01` (core) — new remote branch, no force.
2. `git push origin recovery-2026-08-25` (fazle-mcp) — `852043f..8a3f7f0`, no force.
3. `fazle-core.service` restart via `scripts/restart_gate.sh --restart` (Attempt 2). PID 3115745 → 2981971.

**Not done:** no `.env` change · no migration applied · no direct `systemctl restart` · no
secret rotation · no flag enable · no message sent (WhatsApp or Meta) · no bridge / session /
QR / pair touch · no MCP server reload · no merge to `main` · no force-push.

---

## Rollback status

**Not triggered. Not needed.** Rollback point: `feat/owner-accountant-payment-relay-20260830 @ 5e62ed5`
(== `origin`, == the code that ran before this session).
```
cd /home/azim/core && git checkout feat/owner-accountant-payment-relay-20260830
scripts/restart_gate.sh --restart && curl -s localhost:8200/health
```
Migration 078 stays applied (additive-nullable; `5e62ed5` ignores it — no migration rollback needed).
Softer option (if the flag were ever enabled): remove `PHASE2_IDENTITY_RESOLVER_ENABLED` from
`.env`, `scripts/restart_gate.sh --restart`.

---

## Observation window

`OBSERVATION WINDOW STARTED` — 2026-09-01T19:26Z.
- earliest review: **2026-09-02T19:26Z (+24 h)**
- preferred final review: **2026-09-03T19:26Z (+48 h)**
- checklist: `docs/CLAUDE_PHASE2_OBSERVATION_2026-09-01.md`
- No durable scheduled monitor was created (avoids duplicating the owner's monitoring / restart
  gate / `/health`, per §13). The owner may add a read-only daily snapshot cron.

**24–48 h have NOT elapsed. This session stops here.**

---

## OWNER_ACTION_REQUIRED

1. **Rotate `META_APP_SECRET`.** Why: it was in plaintext in 3 (now-deleted) scripts on disk.
   Steps: Meta App Dashboard → App Settings → Basic → App Secret → **Show** then **Reset** →
   copy the new value → edit `META_APP_SECRET=` in `/home/azim/core/.env` (do NOT commit — `.env`
   is gitignored) and in `/home/azim/secure-env-backup/` if a current snapshot is kept there →
   **while on the deployed branch** run `scripts/restart_gate.sh --restart` → confirm the next
   organic inbound Meta message is accepted (no `[WEBHOOK_AUTH_AUDIT]` / `meta_webhook_invalid_signature`
   in `logs/fazle-core.log`; new `platform='meta'` inbound rows appearing). Brief 403 window between
   the Meta reset and the restart — Meta retries, so a short gap loses nothing. Order: first.
   Risk if skipped: anyone who saw the disk could forge HMAC-signed inbound Meta webhooks.

2. **Reload the fazle-mcp MCP server** onto `8a3f7f0` so Hermes gets `search_identity_by_name`
   + `get_whatsapp_contact_summary`. Why: PID 64399 is long-lived and does not auto-cycle; the
   core endpoints it now needs are live. Steps (choose the one that matches your Hermes setup):
   restart the manually-run `hermes` agent (PID 64377 / its parent) at a quiet time, **or**
   `kill 64399` and let the agent respawn the MCP subprocess on its next request — first
   confirm no Hermes conversation is mid-turn. Then verify `search_identity_by_name` +
   `get_whatsapp_contact_summary` are callable and PII-masked. Do NOT `git stash pop` the
   Stage D8/E9 stash before this — that would deploy unreviewed tools.

3. **Decide the fazle-mcp Stage D8/E9 prior-session work** (`stash@{0}` in `/home/azim/fazle-mcp`):
   `get_conversations_requiring_attention`, `business_inbox_overview`, `office_group_activity`,
   `propose_business_action`, `notify_admin_of_draft`, `draft_tools.py`. Review it, then either
   `git stash pop` + commit with an accurate message (a separate session's work), or `git stash drop`.
   It is preserved byte-perfect and is not deployed.

4. **Optional — `META_VERIFY_TOKEN` in git history.** `ARCHITECTURE.md` (working tree now sanitized).
   Lower severity (only the webhook subscription handshake). Rotate alongside #1 and/or scrub history
   (`git filter-repo`, needs force-push).

5. **After the observation review** (`RESUME CLAUDE PHASE2 OBSERVATION REVIEW` at +24 h / +48 h):
   the session will recommend `ENABLE` / `EXTEND OBSERVATION` / `FIX BEFORE ENABLE` / `ROLL BACK`.
   **Enabling `PHASE2_IDENTITY_RESOLVER_ENABLED=true` needs a separate explicit owner instruction**
   — it is not authorized by this or the prior session.

6. **Optional — fix the 2 pre-existing test-isolation failures** (`test_identity_brain.py` /
   `test_escort_client_phone_normalization_fix.py` ordering; `test_route_b_handoff.py`). Pre-date
   this work; the restart gate is unaffected.

7. **Optional — reconcile deployed vs HEAD.** Deployed `8070bfb`, HEAD `a8bd9fa` (2 doc files,
   no code). Harmless; a future `restart_gate.sh --restart` deploys HEAD. Nothing to do unless
   you want them identical.

8. **When the Phase-2 observation window closes**, remove the
   `docs/CLAUDE_PHASE2_DEPLOYMENT_CHECKPOINT_2026-09-01.md` entry from `db/.restart_gate_manifest`
   and finalize the session docs.

---

## Resume instruction

`RESUME CLAUDE PHASE2 OBSERVATION REVIEW`
