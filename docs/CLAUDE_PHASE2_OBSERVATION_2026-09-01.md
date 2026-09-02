# CLAUDE PHASE-2 — 24–48 h OBSERVATION WINDOW

**`OBSERVATION BASELINE RESET 2026-09-02 — see below`**

> ### Baseline reset (2026-09-02)
>
> Two operational restarts happened during the window; per owner instruction the
> **observation baseline now begins from the successful Meta-secret core restart**:
>
> | # | Event | UTC | Core PID | Notes |
> |---|---|---|---|---|
> | 1 | Original Phase-2 deploy | 2026-09-01 19:20:33Z | 2981971 | Phase-2 P1 fixes live, flag OFF |
> | — | Meta secret rotation (`.env` edited by owner) | ~2026-09-01 19:43Z | — | value only; not loaded yet |
> | **2** | **Meta-secret activation restart** (`restart_gate --restart`, all 10 PASS) — **NEW BASELINE** | **2026-09-01 23:30:00Z** (01:30 CEST 09-02) | **3470442** | `feat/phase2-inbound-identity-2026-09-01 @ 351324f`; Phase-2 still OFF; Bridge1/2 PIDs untouched; Bridge3 stopped; `META_APP_SECRET` new value loaded + HMAC self-test PASS |
> | 3 | Bridge3 fix deploy (`restart_gate --restart`) | _(recorded at deploy time)_ | _(recorded)_ | `fix/bridge3-history-sync-quarantine-20260902`; F1–F4 + pairing-state machine (bridge3 FAIL-CLOSED) + F6; Phase-2 still OFF; **Bridge3 stays stopped, `bridge3.pairing_state=HISTORY_SYNC_QUARANTINE`, no pairing** |
>
> `PHASE2_IDENTITY_RESOLVER_ENABLED` has been **absent / OFF through every one of
> these restarts** and stays OFF. The Phase-2 enable decision (§14 of the
> deployment checkpoint) is unchanged and still needs a separate explicit owner
> instruction.
>
> **New earliest review: +24 h from event 2 → 2026-09-02T23:30Z.**
> **Preferred final review: +48 h → 2026-09-03T23:30Z.**
> If event 3 (the Bridge3 fix deploy) materially changes the picture, note it and
> consider extending.

| Field | Value |
|---|---|
| Original deploy | 2026-09-01 21:20:33 CEST (19:20:33Z) — `fazle-core.service` PID **2981971** on `feat/phase2-inbound-identity-2026-09-01 @ 8070bfb` |
| Feature flag | `PHASE2_IDENTITY_RESOLVER_ENABLED` **absent from `.env` → OFF**. Phase-2 enrichment is DARK this whole window. |
| What went live | P1 identity fixes (always-on): deterministic accountant tier · `platform='whatsapp'` scoping · inactive-employee resolution · `canonical_phone`/`phone_last10` write-time normalization · Meta `canonical_phone` populated · new read-only endpoints. |
| Resume command | **`RESUME CLAUDE PHASE2 OBSERVATION REVIEW`** |
| Rollback | `git checkout feat/owner-accountant-payment-relay-20260830` (→ `5e62ed5`) then `scripts/restart_gate.sh --restart` |

Pre-deploy baseline (for comparison at review): `/health` ok · DLQ 102 / actionable 6 · pollers age <25s / queue 0 · `NRestarts` 0 · `sender_name_snapshot`/`canonical_message_key`/`resolved_phone` populated rows = 0 · latest payment-relay txn `id 4510` key `payment-relay-send:7747:0`.

---

## Observation checklist (all READ-ONLY)

Run at +24 h and again at +48 h. Compare each to the pre-deploy baseline above.

### A. Service stability
- [ ] `systemctl show fazle-core.service -p MainPID -p NRestarts -p ActiveState` — PID still **2981971**, `NRestarts` still **0** (any increase = crash-loop → investigate `logs/fazle-core-error.log`)
- [ ] `systemctl show fazle-core -p NRestarts` over the window — 0
- [ ] `/health` overall — `ok` (or `degraded` **only** on ambient `cpu`/`disk`/`mem`; a CORE probe degraded ≥2 consecutive checks = real)
- [ ] `grep -cE "Traceback|CRITICAL" logs/fazle-core-error.log` scoped to `>= 2026-09-01 21:20` — compare to a same-length pre-deploy window; a material increase is the signal

### B. Bridges / pollers
- [ ] bridge1/2/3 services — PIDs / `ActiveEnterTimestamp` unchanged (2026-08-29); `NRestarts` 0
- [ ] `/health` `bridge_poller_b1/b2/b3` — `ok`, `age_s` < ~120, `queue_depth` 0 (b3 `quiet_but_poller_healthy` is fine — it's logged out)
- [ ] `bridge{1,2,3}_db` `mtime_age_s` — b1/b2 fresh (live traffic); b3 stale-but-quiet expected

### C. Outbound / queue
- [ ] `/health` `outbound` — `pending` near 0; `dlq` not materially above 102 (a jump of tens = investigate); `actionable_dlq` ~6
- [ ] `SELECT source, count(*) FROM fazle_message_queue WHERE created_at >= '2026-09-01 21:20' GROUP BY 1` — no unexpected duplication (e.g. two rows for one `bridge_poller:<src>:<msg_id>` idempotency key)
- [ ] `SELECT count(*) FROM wbom_whatsapp_messages w WHERE received_at >= '2026-09-01 21:20' GROUP BY message_hash HAVING count(*) > 1` — 0 (per-bridge `message_hash` UNIQUE still holds)

### D. Canonical-phone / storage (the always-on change)
- [ ] `SELECT platform, count(*), count(*) FILTER (WHERE canonical_phone ~ '^8801[0-9]{9}$'), count(*) FILTER (WHERE canonical_phone ~ '^01[3-9]'), count(*) FILTER (WHERE canonical_phone LIKE 'unresolved:%' OR canonical_phone LIKE '%@%' OR canonical_phone LIKE '+%') FROM wbom_whatsapp_messages WHERE received_at >= '2026-09-01 21:20' GROUP BY 1` — **0** `01…`-local, **0** junk on new rows
- [ ] `SELECT count(canonical_phone) FROM wbom_whatsapp_messages WHERE platform='meta' AND direction='inbound' AND received_at >= '2026-09-01 21:20'` — should equal the meta-inbound count (the Meta `canonical_phone=NULL` fix)
- [ ] `SELECT count(*) FROM wbom_whatsapp_messages WHERE sender_number LIKE 'unresolved:%' AND received_at >= '2026-09-01 21:20' AND (canonical_phone IS NOT NULL OR phone_last10 IS NOT NULL)` — **0** (LID sentinel never a phone; no junk last-10)
- [ ] `SELECT count(*) FROM wbom_whatsapp_messages WHERE (source_context = 'raw_received' OR is_processed = false) AND received_at >= '2026-09-01 21:20'` vs the count that later reach `is_processed=true` — no rise in un-finalized raw rows (would indicate the enrich follow-up is somehow interfering — should be impossible with the flag OFF)

### E. Phase-2 enrichment stays DARK
- [ ] `SELECT count(*) FILTER (WHERE sender_name_snapshot IS NOT NULL), count(*) FILTER (WHERE canonical_message_key IS NOT NULL), count(*) FILTER (WHERE resolved_phone IS NOT NULL) FROM wbom_whatsapp_messages` — **still 0 / 0 / 0**
- [ ] `cat /proc/$(systemctl show fazle-core -p MainPID --value)/environ | tr '\0' '\n' | grep PHASE2` — absent
- [ ] no `[core_copy] Phase-2 enrich` / `[META] Phase-2` / `identity_resolver` lines in `logs/fazle-core.log` (the code paths are flag-gated off)

### F. Identity resolution (P1, always-on)
- [ ] `curl -s -H "X-Internal-Key: <key>" "localhost:8200/api/identity/resolve?phone=<ACCOUNTANT_PHONE>"` → `identity_role=accountant`, `identity_source=settings`
- [ ] a known **active** employee number → correct role, `source=db_employee` (not clobbered / downgraded)
- [ ] a known **escort_worker** / **escort_client** number → still the fine-grained role (identity_brain, not "escort")
- [ ] `SELECT count(*) FROM wbom_whatsapp_messages WHERE identity_role='unknown' AND received_at >= '2026-09-01 21:20'` vs a pre-deploy window of the same length — no rise in `unknown` (would indicate a resolution regression)
- [ ] spot-check `logs/fazle-core.log` for any `[identity]` line resolving a known person to `unknown` post-deploy

### G. Business routing (via logs / DB, NOT by sending)
- [ ] recruitment: new `wbom_candidates` / `fazle_recruitment_sessions` rows advancing normally; `test_bridge3_recruitment_golden_baseline.py` still green if re-run
- [ ] attendance: `wbom_attendance` drafts still being created for supervisor/employee attendance messages
- [ ] escort: `wbom_escort_programs` / roster activity normal; no escort order misrouted to generic AI reply (`grep` logs for `handle_escort_client_message`)
- [ ] employee / client: `grep` `logs/fazle-core.log` for routing decisions — employees still reach the employee branch, clients the client path
- [ ] admin / accountant: admin commands + accountant reports still routing correctly (`grep` for `process_admin_command` / `is_report_command`)

### H. Payment / FPE (FROZEN — read-only evidence only, NEVER create a transaction)
- [ ] `SELECT id, txn_ref, source_channel, source_message_id, created_by, created_at FROM fpe_cash_transactions WHERE created_at >= '2026-09-01 21:20' ORDER BY id DESC` — any new `owner-accountant-payment-relay` row has a `payment-relay-send:<qid>:<idx>` `source_message_id` and `created_by='payment_relay_accounting'`
- [ ] no duplicate relay txn for one queue id; no reversal storm
- [ ] `fpe_employee_ledger` period totals move only by the amount of any genuine new relay payment
- [ ] employee (re)activation on a successful relay payment still fires; inactive-employee match still works
- [ ] the exact-stanza-ID echo-suppression + Rule-B reconciliation link still behave (no double-book, no missed link)

### I. Query endpoints (the new read tools)
- [ ] `GET /api/whatsapp/messages/query?phone=<n>` latency reasonable (<1s), result bounded, cursor works
- [ ] `GET /api/identity/search-by-name?name=<x>` — ambiguous → `resolved:null`; unique → the number; none → `count 0`
- [ ] `GET /api/whatsapp/contact/summary?phone=<n>` — platforms + counts + ≤ recent_limit rows, provenance on each
- [ ] no 5xx from any of the three in `logs/fazle-core.log`

### J. DB load
- [ ] `SELECT count(*) FROM pg_stat_activity WHERE datname='postgres' AND state='active'` — no sustained rise vs baseline
- [ ] pg slow-query / `fazle-core.log` DB-timeout lines — none new
- [ ] `_existing_columns` / `information_schema` query volume — the `/query` schema-tolerance adds one such query per admin call; should be negligible

### K. MCP (once the owner reloads the MCP server — see the deployment checkpoint Stage 9)
- [ ] MCP server restarted onto `8a3f7f0`; `search_identity_by_name` + `get_whatsapp_contact_summary` callable
- [ ] outputs PII-masked, bounded
- [ ] Stage D8/E9 (`stash@{0}`) still NOT deployed unless separately reviewed + committed

---

## At review time (`RESUME CLAUDE PHASE2 OBSERVATION REVIEW`)

1. Read `docs/CLAUDE_PHASE2_DEPLOYMENT_CHECKPOINT_2026-09-01.md` + this file.
2. Run the full checklist; compare to the pre-deploy baseline.
3. Inspect the full window in `logs/fazle-core.log` / `logs/fazle-core-error.log`.
4. Recommend exactly one of: **`ENABLE`** / **`EXTEND OBSERVATION`** / **`FIX BEFORE ENABLE`** / **`ROLL BACK`**, with evidence.
5. **Do NOT set `PHASE2_IDENTITY_RESOLVER_ENABLED=true`** — that needs a separate explicit owner instruction after the recommendation (§14).
