# CLAUDE PHASE-2 AUTONOMOUS SESSION — CHECKPOINT

**Recovery authority.** If the session is interrupted, resume with `RESUME FROM CLAUDE PHASE2 CHECKPOINT`.
No secrets or full sensitive phone numbers in this file.

---

## Status header

| Field | Value |
|---|---|
| Last updated | 2026-09-01 (Phases A–G code complete + tested; committing) |
| Working branch | `feat/phase2-inbound-identity-2026-09-01` (created from `5e62ed5`) |
| Base / production branch | `feat/owner-accountant-payment-relay-20260830` @ `5e62ed5` (== origin, untouched) |
| HEAD | `5e62ed5` (no local commits yet) |
| Production process | PID 3115745, started 2026-08-31 05:39:30 — **NOT restarted this session** |
| Production mutated? | **NO** (no restart, no deploy, no `.env` change, no DB write; migrations 072+078 were already applied before this session) |
| Hot-reload risk | None — `DEBUG=false` → uvicorn `reload=False`; file edits do not reach the running process |

---

## Phase plan & progress

| Phase | Scope | State |
|---|---|---|
| A | Security remediation + worktree cleanup | **DONE** |
| B | Phase-2 branch isolation | **DONE** |
| C | P1 running-code fixes (phone storage, platform filter, inactive employee, accountant) | **code DONE** (tests pending) |
| D | Phase-2 module fixes (resolver perf/correctness, scope gating, no-clobber, push-name, schema-tolerance, remove dead module, canonical_key) | **code DONE** (tests pending) |
| E | Meta persistence consistency + dedup-race analysis | consistency DONE; dedup migration pending |
| F | Searchability (query filters, MCP tool, name search, contact summary) | pending |
| G | `PHASE2_IDENTITY_RESOLVER_ENABLED` flag + restart-gate migration check | flag DONE; restart-gate check pending |
| H | Tests + self-review + local commits + final report | pending |

### Phase C/D code changes done

**Commit-2 candidates (P1, always-on, files NOT Phase-2-wired):**
- `modules/identity_brain/__init__.py` — `_check_accountant_settings` + Step 1b (deterministic accountant identity, priority 190); `_lookup_seed` + `_lookup_contact` get `platform='whatsapp'`; FPE-linked fallback in `_lookup_employee` no longer filters `e.status='active'` (inactive employees stay resolvable), active row still wins tie-break.
- `modules/user_role/__init__.py` — `_lookup_contact` gets `platform='whatsapp'`.
- `modules/message_router/__init__.py` — `_should_silent_skip`: accountant compare normalizes both sides; contact lookup gets `platform='whatsapp'`.
- `modules/phone_normalizer/__init__.py` — new `storage_canonical_phone()` / `storage_phone_last10()` (BD→8801, foreign kept digits-only, LID→None, JID stripped).

**Commit-3 candidates (Phase-2, flag-gated by `PHASE2_IDENTITY_RESOLVER_ENABLED`, default OFF):**
- `modules/identity_resolver.py` — `is_phase2_identity_enabled()` + `WHATSAPP_PLATFORMS`; `available_columns` now process-cached (`reset_column_cache()` test hook); tier chain fetched ONCE and reused by `_best_name` (now a pure function taking pre-fetched rows — ~13→~5 DB round-trips); `resolve_inbound_identity` computes `identity_phone` + `canonical_message_key` OUTSIDE the try so a DB error can't discard them; `_lookup_contact_role` deterministic `id` tie-break (kept `is_active` — documented: role authority vs identity existence); `compute_canonical_message_key` reduced to transport-ID-only (removed the content+60s heuristic) with a FORENSIC-ONLY docstring; `search_identity_by_name` contact/fcr queries get `platform='whatsapp'` + deterministic order; removed dead helpers (`_time_bucket`, `_content_fingerprint`, `_digits_only_last10`, `hashlib` import).
- `modules/bridge_poller/__init__.py` — `_save_raw_core_copy` writes `canonical_phone`/`phone_last10` via `storage_*` helpers (ALWAYS-ON P1 fix); Phase-2 columns filled by a NEW best-effort follow-up UPDATE `_phase2_enrich_raw_copy()` (flag-gated, schema-tolerant, cannot break the raw INSERT); `resolved_phone`/`resolved_at` set only when the raw sender was a LID we resolved; `_load_contact_names()` reads `whatsmeow_contacts` once per poll cycle (only when flag ON) and stashes push/full/business name hints on `msg`.
- `app/main.py` — `_save_message` gains optional `canonical_phone`/`phone_last10` (defaulted from `sender` for WhatsApp platforms — ALWAYS-ON, fixes Meta `canonical_phone=NULL`) + Phase-2 columns (schema-tolerant); `_handle_meta_message` parses `contacts[].profile.name` and (flag-gated) runs the resolver; `_handle_bridge_event` (dormant webhook) gets a `unresolved:` routing skip + flag-gated NO-CLOBBER enrichment (only fills role when identity_brain said "unknown"); stale "Meta is not in auto_reply_sources" comment corrected.

**Removed:** `modules/inbound_persistence.py` + `tests/unit/test_phase2_inbound_persistence.py` (dead — zero non-test callers; latent `_compute_message_hash` bug).

**Reverted to HEAD then re-wired cleanly:** `app/main.py`, `modules/bridge_poller/__init__.py` (Hermes's staged draft had the audit-confirmed defects: role clobber, no flag, no schema-tolerance, ran on outbound/social, no push-names, dead computed fields).

---

## Phase A — completed actions

1. **Removed 7 unsafe untracked scripts** from the working tree after independent verification (untracked, not in git history, not imported by any app module / test / Makefile / CI; `db/.restart_gate_manifest` already recorded the Owner resolution "delete these files"):
   - `scripts/phase7_age_resend.py`, `scripts/phase7_second_webhook.py`, `scripts/phase7_live_test.py` — hard-coded the live `META_APP_SECRET` in plaintext and POSTed HMAC-signed forged webhooks at `127.0.0.1:8200/webhook/meta`.
   - `scripts/phase7_check_outbound.py`, `scripts/phase7_check_session.py`, `scripts/phase7_check_sessions.py` — debug scripts loading prod `.env` into the environment and connecting to the prod DB.
   - `scripts/restart_fazle7.sh` — `sudo systemctl restart fazle-core.service`, bypassing `scripts/restart_gate.sh`.
2. Verified the `META_APP_SECRET` value no longer exists anywhere in the repo working tree **except** legitimate config (`.env`) and its gitignored timestamped backups (`.env.bak.*`, `.env.save`). Those are expected homes; not a new exposure.
3. **`db/.restart_gate_manifest`**: replaced the now-obsolete 6-script + restart-script cleanliness-exception block with a RESOLVED note.
4. **`.gitignore`**: added a bare `venv` entry so the repo-root `venv → /home/azim/.venv` symlink is ignored (previously only `venv/` matched, which is a directory glob). Confirmed `git status` no longer lists it.
5. **`ARCHITECTURE.md:185`**: replaced a literal `META_VERIFY_TOKEN` value in a `curl` example with `${META_VERIFY_TOKEN}`. (The credentials table below it already used env-key references.) NOTE: this value is also present in git history — history scrub is out of autonomous scope; recorded for the Owner.
6. **`docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md`**: masked one genuine external customer phone, one internal bridge number, and one LID (kept country code + last 2 digits). Left **unstaged / uncommitted** — the `db/.restart_gate_manifest` records the Owner's directive that this file (belonging to the separate Bridge Media task) not be committed by another session.
7. `scripts/secret_scan.sh` — **PASS** (0 matches) on tracked files and on the two kept untracked docs.
8. Kept (scanned clean): `docs/PRODUCTION_SYSTEM_STATUS_2026-08-31.md`, `docs/WEB_UI_WORKFLOW_AUDIT_2026-08-31.md` — legitimate status write-ups, left untracked (their infra numbers are the org's own bridge/admin/relay numbers, already in `.env`; mask before tracking if ever committed).

## Phase B — completed

- Created `feat/phase2-inbound-identity-2026-09-01` from `5e62ed5` with `git checkout -b` (no reset, no stash — same commit, working tree carried intact). `feat/owner-accountant-payment-relay-20260830` and its origin remain at `5e62ed5`.

---

## Files changed so far (uncommitted)

| File | Change | Phase |
|---|---|---|
| `.gitignore` | +`venv` bare entry | A |
| `ARCHITECTURE.md` | sanitize `META_VERIFY_TOKEN` literal in curl example | A |
| `db/.restart_gate_manifest` | remove obsolete phase7 exception block | A |
| `docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md` | mask 3 phone/LID values (unstaged, do not commit) | A |
| `app/main.py` | (pre-existing staged Phase-2 work — to be revised in D) | — |
| `modules/bridge_poller/__init__.py` | (pre-existing staged Phase-2 work — to be revised in C/D) | — |
| `modules/identity_resolver.py` | (pre-existing staged Phase-2 work — to be revised in D) | — |
| `modules/inbound_persistence.py` | (pre-existing staged — SCHEDULED FOR REMOVAL in D) | — |
| `db/migrations/078_*.sql` | (pre-existing staged — KEEP; already applied to live DB) | — |

---

## Tests

**Test DB used: the isolated `fazle-test-postgres` container** (`127.0.0.1:5433/fazle_test`), password read from the container's own `POSTGRES_PASSWORD` at run time via `TEST_DATABASE_URL`. Never the production `postgres` DB. conftest rebuilds the schema from `db/migrations/*.sql` (96 files, incl. 078) and DROP-SCHEMAs only `fazle_test`.

New / rewritten Phase-2 test files — **all green (55 tests)**:
| File | n | Notes |
|---|---|---|
| `tests/unit/test_phase2_storage_phone.py` | 6 | pure — storage_canonical_phone / _last10 |
| `tests/unit/test_phase2_identity_resolver.py` | 24 | rewritten — pure helpers + DB resolver; precedence, LID rejection, platform filter (+/− case), push-name non-authority, ambiguous name, secret non-exposure, fallback preserves identity_phone, flag default OFF |
| `tests/unit/test_phase2_bridge_poller_regression.py` | 9 | rewritten — canonical_phone normalized flag-off; Phase-2 cols only when flag on; message_hash idempotency; group conv_key; autofill trigger |
| `tests/unit/test_phase2_p1_identity.py` | 6 | new — accountant Step 1b, platform filter, inactive fpe-only employee, no duplicate |
| `tests/unit/test_phase2_read_tools.py` (integration) | 6 | new — /messages/query phone + body filters; search-by-name single/ambiguous/none; contact/summary |
| `tests/unit/test_phase2_escort_worker_vs_client.py` (pre-existing) | 4 | still green — confirms escort disambiguation is NOT clobbered |

**Removed:** `tests/unit/test_phase2_inbound_persistence.py`, `tests/integration/test_phase2_hermes_read_contract.py` (their target module `inbound_persistence.py` was removed).

Regression suites run individually — **all green**: `test_admin_commands`, `test_drafts_router_mounted`, `test_rbac` (= restart-gate check4), `test_identity_brain`, `test_escort_client_phone_normalization_fix`, `test_message_router_no_reply_name_gate`, `test_message_router_identity_seed`, `test_payment_relay`, `test_payment_relay_accounting`, `test_bridge_poller_payment_relay_gate`, `test_fpe_bridge_path_authority`, `test_escort_client_identity_convergence`, `test_recruitment_cooldown_lead_creation_fix`.

### Pre-existing test-isolation flake (NOT caused by this session — documented per §33)

Running `pytest tests/unit/test_identity_brain.py tests/unit/test_escort_client_phone_normalization_fix.py` **in that order, in one process** fails 4–7 tests in the escort file (`_escort_client_phone_list()` reads empty). Reversed order passes; each file alone passes. **Reproduced on a fully pristine tree at commit `71bb3c0`** (which changes zero `.py`/test files) and via `git stash` at the production baseline — so it predates this session. Root cause is `test_identity_brain.py:47` `with patch("app.config.get_settings")` interacting with module-level settings caches (`modules/user_role._admin_phones` / `_accountant_phone`, or the `get_settings` lru_cache) and leaking into the next file. The restart-gate check4/check4b suites never combine these two files, so the gate is unaffected. Recommended (separate, out of this session's scope): give those two test files proper `get_settings.cache_clear()` + cache-reset teardown.

---

## OWNER_ACTION_REQUIRED (running list)

1. **Rotate `META_APP_SECRET`** — it was found in plaintext in 3 now-deleted untracked scripts (`scripts/phase7_*`). The scripts are removed from the working tree; the value was never committed to git, but was on disk (and possibly in host/VS Code backups). Exact action: generate a new App Secret in the Meta App dashboard, update `META_APP_SECRET` in `/home/azim/core/.env` + `/home/azim/secure-env-backup/`, restart via `scripts/restart_gate.sh --restart`. Risk if skipped: anyone who saw the disk could forge signed inbound Meta webhooks. Prerequisite: none. Order: first.
2. **`META_VERIFY_TOKEN` is in git history** (`ARCHITECTURE.md`, pre-existing, now sanitized in the working tree). Lower severity (only used in the Meta webhook subscription handshake). Optional: rotate it alongside #1 and/or scrub history (`git filter-repo`) — history scrub requires force-push, out of autonomous scope.

*(more will be appended as phases complete)*

---

## Next autonomous task

**NONE — session complete.** All phases A–H done. See `docs/CLAUDE_PHASE2_AUTONOMOUS_FINAL_2026-09-01.md` for the full report and the OWNER_ACTION_REQUIRED list.

Local commits (core, on `feat/phase2-inbound-identity-2026-09-01`):
`71bb3c0` security · `5bc1c4f` P1 identity · `c44d80d` Phase-2 resolver+wiring+read-tools · `642d8e1` restart-gate check 5b · `78d8238` tests.
fazle-mcp: my tool additions are UNCOMMITTED (interleaved with a prior session's Stage D8/E9 work in audit_tools.py/server.py) — owner to decide how to commit. The bundling commit dbadaa4 was reverted; HEAD back at 852043f.

Nothing pushed. Production not restarted/deployed. `.env` untouched. No migration applied this session (072+078 pre-dated it). Bridge sessions untouched. No messages sent.

If resuming: the remaining work is all owner-gated (rotate META_APP_SECRET, review diff, approve push, approve restart through the gate, decide the Meta UNIQUE index + Bridge3 outreach channel + whether to flip PHASE2_IDENTITY_RESOLVER_ENABLED). There is no further autonomous work.
