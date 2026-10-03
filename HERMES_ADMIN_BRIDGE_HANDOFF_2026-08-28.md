# Hermes Admin↔Bridge Recovery — Handoff, 2026-08-28

**Written:** end of a long production session (2026-08-27/28) that traced
and fixed the Super Admin WhatsApp→Hermes chain end-to-end, then diagnosed
and repaired a live Bridge1 WhatsApp session outage. Session is pausing
here to move to BrainV. This document is the resume point — read it before
touching any of the areas below.

No secret values are recorded anywhere in this file. Where a credential or
token is referenced, only its name/location is given, never its value.

---

## 0. Update — continuation same evening, still 2026-08-28

Three things happened after this document's original commit
(`fca9a19ca258b22e7e7cb67d63241631a1e0d934`) and before this second pause.
All verified live; §12's machine-readable block reflects the current state.

**Bridge1/Bridge2 port swap — FIXED, VERIFIED, LIVE.** Owner clarified the
intended mapping is Bridge1=8081, Bridge2=8082, Bridge3=8083; the running
system had Bridge1 and Bridge2 swapped (Bridge1=8082, Bridge2=8081) from
earlier development. Corrected:
- `/etc/systemd/system/whatsapp-bridge.service` (Bridge1): `WHATSAPP_PORT`
  8082 → 8081
- `/etc/systemd/system/whatsapp-bridge2.service` (Bridge2): `WHATSAPP_PORT`
  8081 → 8082 (edited by Owner/root directly — I don't have write access to
  this file, only to `whatsapp-bridge.service`)
- `/home/azim/core/.env`: `BRIDGE1_URL=http://localhost:8081`,
  `BRIDGE2_URL=http://localhost:8082` (was reversed)
- Restarted in the safe order (Bridge2 first would have collided since
  Bridge1 hadn't moved off 8082 yet — actual safe sequence used: Bridge1
  first to free 8082, then Bridge2 to claim it), then `fazle-core.service`
  via the normal restart-gate. **Both WhatsApp sessions/accounts were
  untouched by this — this was a pure port-label correction, verified via
  clean re-authentication logs with no QR prompt on either bridge.** Live
  confirmed: `ss -tlnp` shows 8081→Bridge1, 8082→Bridge2, 8083→Bridge3
  unchanged; both `/api/send-status` return `{"allowed":true}`; the
  running `fazle-core` process's own environment confirmed
  `BRIDGE1_URL=http://localhost:8081` / `BRIDGE2_URL=http://localhost:8082`.
- **One real permission gap worth keeping in mind for future sessions:**
  I have passwordless sudo for `systemctl restart` on 4 named units and
  write access to `whatsapp-bridge.service` specifically (`azim`-owned),
  but not `whatsapp-bridge2.service` (`root`-owned) and not
  `systemctl daemon-reload` — any future edit to `whatsapp-bridge2.service`
  or any unit-file edit that needs a fresh `daemon-reload` needs the
  Owner's direct help, same as this time.

**A better, zero-code-change path to close P0 was found and verified.**
Instead of the same-Bridge2-self-chat-thread reply design discussed and
explicitly **not implemented** (see the paragraph below), a plain
cross-account flow was traced read-only and confirmed to already work with
the current, unmodified code:
```
Owner's real phone (number == admin_bridge2_number)
  → sends a normal WhatsApp DM directly to Bridge1's number
  → genuine inbound (is_from_me=0) message on Bridge1
  → modules.message_router.process_message(phone, text, "bridge1")
  → process_admin_natural_query's existing bridge1 bypass
    (sender_phone == admin_bridge2_number AND source_bridge in
    _HERMES_CONTROL_CHANNELS -- bridge1 has been valid since 2026-08-13)
  → _handle_hermes_relay → Hermes → MiniMax-M3
  → _deliver_hermes_reply always sends via bridge1, to sender_phone
  → reply lands back on the Owner's phone from Bridge1's number --
    same two-party WhatsApp thread, no self-chat involved
```
Confirmed: Bridge1's own number never needs (and never gets) Super Admin
role — the identity check is purely on `sender_phone`, evaluated
identically regardless of which bridge received the message. Confirmed:
the reply landing back on Bridge2's own linked-device history (since the
Owner's phone is also Bridge2's account) is exactly the shape the
*existing, already-deployed* cross-bridge echo guard in
`modules/message_router/__init__.py` was built to suppress — no new loop
risk, nothing to add. **Zero code changes were made or are needed for
this path.** This is now the recommended way to finally close P0 — see
the updated §11.

**Same-Bridge2-self-chat-thread reply design — investigated, NOT
implemented, left for a future decision.** Owner asked whether a Hermes
reply could be delivered into the exact same Bridge2 self-chat thread the
instruction was typed in (rather than via Bridge1). Read-only trace found:
this is technically possible with one small, low-risk addition (an
in-memory provenance marker in `_deliver_hermes_reply`/
`_handle_hermes_relay`, following the existing `_hermes_cooldown` pattern
in `modules/admin_directives/router.py` — no DB/schema change), but it was
**deliberately not implemented** once the zero-code-change Bridge1-inbound
path above was confirmed to already satisfy "reply appears without
switching accounts." Revisit only if the Owner specifically still wants
same-thread delivery over the now-proven Bridge1↔Bridge2 two-party
conversation.

---

## 1. Proven target architecture — Super Admin path

```
Super Admin
  → Bridge2 (self-chat)
  → Fazle Core (modules/bridge_poller, modules/admin_directives/router.py)
  → Hermes Admin relay (_handle_hermes_relay)
  → hermes-runner (readonly_key="readonly:whatsapp_relay")
  → Hermes RUN toolset
  → MiniMax-M3 / minimax
  → Fazle Core (_deliver_hermes_reply → outbound queue)
  → Bridge1 (hardcoded primary reply channel, 2026-08-27 topology unification)
  → Super Admin
```

- **Bridge1 is the intended, hardcoded primary reply bridge** for every
  Hermes Admin relay turn, regardless of whether the request arrived via
  Bridge1 or Bridge2 (`_handle_hermes_relay`'s call to
  `_deliver_hermes_reply` always passes `source_bridge="bridge1"`).
- **Near-simultaneous Bridge2 Admin requests are already protected** by an
  existing per-admin-phone cooldown gate inside `_handle_hermes_relay()`
  (`_HERMES_COOLDOWN_SECONDS`). This was confirmed live: two genuinely
  distinct Admin messages sent 23 seconds apart were correctly serialized —
  the first became the primary Hermes request, the second received the
  Bangla "Hermes is already processing" ack, generated *before* ever
  reaching hermes-runner. This is not a bug and was not touched.

---

## 2. Already fixed and proven — do not modify without new evidence

### 2a. Bridge2 natural-language Admin routing — DEPLOYED, LIVE-PROVEN

**Problem:** the Bridge2 self-chat outgoing scanner
(`modules/bridge_poller/__init__.py`) only ever recognized the explicit
`hermes,`/`hermes:` prefix. An unprefixed natural-language Super Admin
self-chat message fell through every check and was silently dropped — this
was the real cause of 3 missing Admin replies traced earlier in this
session.

**Fix (deployed):** a new fallback branch, reached only after every
deterministic pattern above it (explicit prefix, release confirmation,
escort completion, trusted-group order) has had first refusal — bridge2
only, never inside the Trusted Group — routes remaining unprefixed text
through the existing `process_admin_natural_query()` → `_handle_hermes_relay()`
path already used for genuinely-inbound messages. No new authorization
logic was added.

- **Commit:** `53ddc241c34885642e926563f00447517f6b25ad`
  ("fix: route bridge2 natural admin self-chat to Hermes")
- **Files:** `modules/bridge_poller/__init__.py`,
  `tests/unit/test_bridge2_selfchat_natural_admin_routing.py` (15
  structural tests)
- **Deployed** via `scripts/restart_gate.sh --restart` on 2026-08-27,
  PASS with 0 warnings.
- **Live-proven twice** with real Bridge2 self-chat messages, most
  recently at 2026-08-28 00:33:49/00:34:12 (see §7 for why full E2E is
  still pending, not this routing step specifically — routing itself
  fired correctly both times, confirmed via
  `[bridge2] outgoing unprefixed self-chat → routing to Hermes
  natural-query` log lines).

**Do not modify this routing again without new evidence of a regression.**

### 2b. Hermes Admin execution / RUN toolset — VERIFIED INTACT

- `readonly_key="readonly:whatsapp_relay"` (the value
  `_call_hermes_readonly` always sends for the WhatsApp Admin relay) maps
  unconditionally to the RUN toolset in `hermes-runner/server.py`:
  `memory,web,todo,skills,fazle-core,file,code_execution,terminal`.
- Super Admin authorization (RBAC superadmin + exact
  `admin_bridge2_number` match + `source_bridge` in
  `_HERMES_CONTROL_CHANNELS`) — unchanged, all three gates independently
  verified still enforced.
- `task_action_policy` (the CLI plugin that gates actual tool-call
  execution — commit/push/restart/deploy/DB writes) — unchanged,
  file mtime confirmed to predate this whole session.
- **No approval policy was weakened anywhere in this session's work.**

### 2c. MiniMax-M3 root cause and fix — DURABLY CORRECTED, VERIFIED TWICE

**Root cause (two layers, both real):**
1. Hermes's built-in `minimax` provider profile
   (`~/.hermes/hermes-agent/plugins/model-providers/minimax/__init__.py`)
   correctly expects MiniMax's Anthropic-Messages-compatible endpoint,
   `https://api.minimax.io/anthropic`. A stale cached override had this
   pointed at `https://api.minimax.io/v1` instead — the OpenAI-compatible
   path, which 404s under the Anthropic client Hermes actually uses for
   this provider.
2. Editing `~/.hermes/auth.json`'s `credential_pool.minimax[0].base_url`
   directly did **not** persist — `~/.hermes/.env`'s
   `MINIMAX_BASE_URL=https://api.minimax.io/v1` is the true authoritative
   source, and every `hermes chat` startup re-syncs that value into
   `auth.json`, silently overwriting any direct edit.

**Durable fix applied (both backed up first, `.env`-only edit is the
authoritative one):**
```
~/.hermes/.env:  MINIMAX_BASE_URL=https://api.minimax.io/anthropic
```
Model (`MiniMax-M3`) and provider identifier (`minimax`) were never
changed — only the base URL.

**Verification (both passed, independently, twice):**
- Direct curl proof (bypassing Hermes): `https://api.minimax.io/anthropic/v1/messages`
  → HTTP 200, real reply `"OK"`, 1.03s.
- Minimal Hermes probe (`-t memory`, no other tools): PASS — actual
  endpoint `/anthropic/v1/messages`, HTTP success, stdout `OK`, return
  code 0, `fallback=False`.
- Admin-equivalent RUN probe (full RUN toolset, real `~/core` cwd): PASS —
  `fazle-core` MCP registered 129 tools, MiniMax succeeded on the first
  attempt (~2.3s), stdout present, return code 0, no fallback entered.
- **No hermes-runner or fazle-core restart was required** for either fix
  — each `hermes chat` subprocess reloads `.env` fresh on every
  invocation.

**Do not revisit this fix unless new evidence shows a regression** (e.g. a
fresh 404 or fallback cascade reappearing on a `readonly:whatsapp_relay`
call).

---

## 3. Bridge1 outage and recovery — RESOLVED (transport only — see §4 for the unresolved store-path issue)

**Outage, fully proven:**
- Expected HTTP endpoint: `http://localhost:8082`
- Bridge1's WhatsApp multi-device session had logged out (last real
  session activity: 2026-08-24 23:26)
- Symptoms: nothing listening on 8082; every `fazle-core` send attempt
  failed with `transport: All connection attempts failed`; `systemctl`
  showed the process `active`/`running` throughout (misleading —
  process-level health ≠ transport health); `NRestarts` had reached 655+
  in a continuous crash-restart loop; the dedicated log
  (`/home/azim/whatsapp-mcp/logs/bridge1.log`) showed thousands of
  QR-generation cycles, each ending in `Timeout waiting for QR code scan`.

**Recovery, completed live tonight:**
- Owner scanned the QR code directly from the already-running,
  systemd-managed instance's log (no service stop/manual-foreground
  handoff was possible — this account only has passwordless sudo for
  `systemctl restart`, not `stop`; using the live instance's own log was
  lower-risk and achieved the same result without any dual-process race).
- Login succeeded: `History sync complete. Stored 2319 messages.`
- Port 8082 came up listening.
- `send-status` → `{"allowed":true}`, HTTP 200.
- Crash loop stopped: `NRestarts` frozen at **657**, `ActiveEnterTimestamp`
  stable, no further QR/logout errors.
- **Live PID observed at repair time: 2908850 — do not assume this PID
  persists into a future session; re-verify with `systemctl show
  whatsapp-bridge.service -p MainPID` before relying on it.**

---

## 4. Bridge1 store-path mismatch — PROVEN, NOT FIXED

This is real and unresolved. Do not assume Bridge1's transport being
healthy means its data path is correct — it is not.

**Root cause (fully proven, not inferred):**
- The compiled `whatsapp-bridge` binary (used identically by all three
  bridge instances) **never reads `WHATSAPP_STORE_DIR`** — confirmed via
  a full `strings` scan of the binary: the literal string
  `WHATSAPP_STORE_DIR`/`STORE_DIR` does not appear anywhere in it.
- It always opens a store at a path relative to its own
  `WorkingDirectory`: `./store`.
- Bridge2 (`WorkingDirectory=/home/azim/whatsapp2`) and Bridge3
  (`WorkingDirectory=/home/azim/whatsapp3`) "work correctly" purely by
  directory-naming coincidence — each `WorkingDirectory` happens to
  contain its own real `store/` subdirectory matching the (otherwise
  unread) `WHATSAPP_STORE_DIR` value. Verified live via each process's own
  open file descriptors (`/proc/<pid>/fd`), not assumed.
- **Bridge1's `WorkingDirectory` is `/home/azim/whatsapp-mcp/whatsapp-bridge`**
  (the shared source/binary directory, never migrated to a dedicated
  `/home/azim/whatsapp1` folder like its siblings). Its `./store` is an
  old symlink, dated **2026-05-01**:
  `/home/azim/whatsapp-mcp/whatsapp-bridge/store → /home/azim/bridges/bridge1/store`.

**Consequence:** Fazle Core's `bridge1_db_path`/`bridge1_whatsapp_db_path`
(`app/config.py` defaults) point at `/home/azim/whatsapp1/store/*.db` —
which the live Bridge1 process has **never** written to, before or after
tonight's re-pair. Fazle Core has been reading stale Bridge1 data.

**Split-history evidence (audit-time snapshot — recheck counts before
acting):**
| | Fazle Core's configured path | Bridge1's actual live path |
|---|---|---|
| Path | `/home/azim/whatsapp1/store/messages.db` | `/home/azim/bridges/bridge1/store/messages.db` |
| Messages (at audit time) | ~14,358 | ~5,356 |
| Range | 2025-06-15 → 2026-08-24 23:26:53 | 2026-04-21 → present, actively growing |

Bridge2 and Bridge3: configured path, actual live path, and Fazle Core's
poller path are all effectively the same (`/home/azim/whatsapp2/store` and
`/home/azim/whatsapp3/store` respectively) — no mismatch, confirmed live.

**Recommended next fix (NOT implemented):** point Fazle Core's Bridge1 DB
config (`bridge1_db_path`/`bridge1_whatsapp_db_path`, prefer an
environment/config override over hardcoding) at the proven live path:
`/home/azim/bridges/bridge1/store/{messages,whatsapp}.db`. Expected
restart: `fazle-core.service` only, through the normal restart-gate
process. Do **not** move/copy/delete either live SQLite file, and do
**not** alter Bridge1's `WorkingDirectory`/symlink as part of this minimal
fix — that's separate, riskier, longer-term normalization work (see §11,
P4).

---

## 5. DLQ row 5750 — leave exactly as-is

Outbound row `id=5750` was a **real, successfully generated** Hermes Admin
reply (MiniMax succeeded, Hermes stdout existed, content was genuine).
Delivery failed purely because Bridge1 was logged out at the time — 3
attempts (00:34:35, 00:36:36, 00:40:37, exponential backoff per
`modules/outbound/__init__.py`), all before the repair (~00:54:42), then
`status='dlq'` per the `max_attempts=3` default
(`db/migrations/029_bootstrap_core_tables_schema_record.sql`).

**Decision: leave row 5750 unchanged as historical/audit evidence.** Do
not requeue it, change its status/attempts, manually resend its content,
or create a duplicate row from it. A fresh, clean E2E test (§6) is the
intended functional proof instead — not a replay of this row.

---

## 6. Part A — clean post-repair E2E test: STILL PENDING (two sessions now)

**Still the open item at this second pause too.** No new Bridge2 self-chat
Admin message and no new Bridge1-inbound Admin message from the Owner was
observed at any point across either the original session or this
continuation. `whatsapp2/store/messages.db` was re-checked read-only
immediately before this update — still nothing newer than the
`3EB0AD63D6C55168B546E9` message at 00:34:12 CEST.

**Do not claim full E2E completion until a new message is actually
observed and traced.**

### Two valid ways to close this on resume — either is acceptable

**Option A (original plan) — Bridge2 self-chat:**
```
Bridge2 self-chat
  → bridge_poller outgoing scanner
  → process_admin_natural_query
  → _handle_hermes_relay
  → hermes-runner (readonly_key="readonly:whatsapp_relay")
  → MiniMax-M3 / minimax
  → Hermes stdout
  → new outbound row, purpose=hermes_relay_reply
  → Bridge1 :8081  (port corrected since original write of this doc -- see §0)
  → WhatsApp send
  → Owner
```

**Option B (found this continuation, zero code changes, recommended) —
plain Bridge1-inbound DM, see §0 for the full trace:**
```
Owner's phone → normal DM to Bridge1's number (:8081)
  → genuine inbound message on Bridge1
  → process_admin_natural_query's existing bridge1 bypass
  → _handle_hermes_relay → Hermes → MiniMax-M3
  → reply via Bridge1 back to the Owner's number
  → same two-party WhatsApp thread
```
Both share the same required PASS criteria below and both were fully
read-only verified as ready — no additional code changes needed for
either.

**Required PASS criteria (all of them):**
- exactly one Admin request (no cooldown collision on this message)
- MiniMax succeeds, `fallback=False`
- Hermes stdout generated
- new outbound row created, `purpose=hermes_relay_reply`, `bridge=bridge1`
- Bridge1 HTTP send succeeds on port **8081** (no `transport: All
  connection attempts failed`)
- outbound row reaches `status='sent'`
- Owner visibly receives the reply

If this completes before/during a future session's read of this
checkpoint, update this section (and the machine-readable block in §12)
with the real message ID, outbound row ID, and PASS/FAIL evidence — don't
leave it stale.

---

## 7. PostgreSQL credential exposure — rotation pending, NOT performed

A production PostgreSQL credential (the `postgres` role — a **superuser**,
not a scoped app role) was accidentally printed unredacted during
diagnostics this session. Treat it as exposed. **No value is recorded in
this document.** Rotation was scoped and planned but **not executed** —
requires separate, explicit approval.

**Known live secret sources/consumers (mapped, not touched):**
1. `/home/azim/core/.env` — `DATABASE_URL_TEMPLATE` — consumed by
   `fazle-core.service`, auto-resolved on every start via
   `ExecStartPre=/home/azim/shared/scripts/generate_runtime_services_env.sh`
   → writes `/home/azim/secure-env-backup/runtime-services.env` (mode
   600, self-refreshing — no manual edit needed here on rotation).
2. `/home/azim/agent/.env` — `PG_DSN_TEMPLATE` — consumed by
   `fazle-agent.service`, resolved by its own app code at startup.
3. `/home/azim/chat_sync/.env` — a **static, already-resolved**
   `DATABASE_URL=...` — consumed by the systemd **user** unit
   `chat-sync.service`. Not auto-regenerated; needs manual editing on
   rotation.

**Historical duplicate (inert, not consumed by any live service):**
`/home/azim/backups/fazle-backup-20260531-2150/.env` — confirms this
exact password has been unchanged since at least 2026-05-31.

**Recommended rotation sequence (not yet performed):**
1. Generate a strong new credential (never print it).
2. `ALTER ROLE postgres WITH PASSWORD '<new>'` directly in Postgres — old
   sessions are unaffected, only new connections need the new value, so
   this step alone causes no outage.
3. Update all 3 live `.env` sources above (§7.1–7.3).
4. Restart + verify `fazle-core.service` (auto-regenerates
   `runtime-services.env` via its own `ExecStartPre`).
5. Restart + verify `fazle-agent.service`.
6. Restart + verify `chat-sync.service` (user unit).
7. Confirm the generated `runtime-services.env` reflects the new value.
8. Verify all DB consumers are healthy.
9. Separately decide how to sanitize/archive the historical backup
   containing the old credential.

Production restart approvals/gates apply to every step above — this is
a fully separate task from everything else in this document.

---

## 8. Deferred resilience improvement — not implemented, future work only

`hermes_relay_reply` currently sends exclusively via Bridge1 (hardcoded,
per §1). Future desired behavior: **Bridge1 primary → Bridge2 fallback
only on a genuine Bridge1 transport failure** (mirroring the failover
some other outbound purposes, e.g. `social-draft-pending`, already have).
Must preserve the existing loop/cooldown protections described in §1.
**Do not implement this until the primary clean E2E path (§6) is proven.**

---

## 9. Do not regress these areas

Every item below is currently healthy/correct and should be preserved
unless new evidence specifically requires a change:

- Bridge2 Admin natural-language routing (§2a)
- Hermes as the Admin reasoning/execution brain
- MiniMax-M3/minimax as the healthy Admin provider (§2c)
- RUN toolset for the Admin relay
- Super Admin RBAC (three independent gates)
- `task_action_policy`
- approval gates (commit/push/restart/deploy)
- recruitment conversation behavior
- employee conversation behavior
- customer routing
- Bridge2/Bridge3 working stores (confirmed correct, no mismatch)
- existing production restart policy (`scripts/restart_gate.sh`, the
  limited passwordless-sudo scope for specific service restarts)

**No unnecessary architectural redesign.**

---

## 10. Related documents

- `HANDOFF_P2_TIMEOUT_FIX_2026-08-14.md` (repo root) — unrelated prior
  handoff, different topic (opencode_dispatch timeout chain), kept for
  reference only.
- `tests/unit/test_bridge2_selfchat_natural_admin_routing.py` — tests for
  §2a.
- `db/migrations/029_bootstrap_core_tables_schema_record.sql` — source of
  the `max_attempts=3` default referenced in §5.

---

## 11. Resume order

### P0 — Finish clean Admin E2E proof
Either a Bridge2 self-chat message or a plain Bridge1-inbound DM (both
proven ready, zero code changes needed) → Hermes/MiniMax → Bridge1 (now
correctly on **port 8081**, see §0) → Owner. See §6 for exact trace points
and PASS criteria for both options.

### P1 — Correct Fazle Core's Bridge1 store path
Point the poller/config to the proven live Bridge1 store
(`/home/azim/bridges/bridge1/store/*.db`) without moving/copying/deleting
any live SQLite file. See §4.

### P2 — Rotate the exposed PostgreSQL superuser password
Controlled, separate security task. Update every known consumer (§7) and
verify each sequentially before moving to the next.

### P3 — Add Hermes reply transport resilience
Bridge1 primary → Bridge2 fallback, on genuine transport failure only. See
§8. Do this only after P0 is proven.

### P4 — Long-term normalization
Consider making all three WhatsApp bridges' `WorkingDirectory`/store
conventions consistent, and eliminating the dead `WHATSAPP_STORE_DIR`
configuration entirely (it does nothing today). Separately planned
migration only — not a quick fix, touches a live Go binary/session.

---

## 12. Machine-readable resume block

```text
SESSION_STATE=PAUSED_FOR_BRAINV
DATE=2026-08-28
ADMIN_ROUTING=PROVEN
HERMES_RUN=PROVEN
MINIMAX_M3=HEALTHY
BRIDGE1_AUTH=RECOVERED
BRIDGE1_TRANSPORT=HEALTHY_AFTER_REPAIR
BRIDGE_PORT_MAPPING=CORRECTED_BRIDGE1_8081_BRIDGE2_8082_BRIDGE3_8083
BRIDGE1_INBOUND_ADMIN_PATH=VERIFIED_READY_ZERO_CODE_CHANGES
SAME_SELFCHAT_REPLY_DESIGN=INVESTIGATED_NOT_IMPLEMENTED
FINAL_POST_REPAIR_E2E=PENDING
BRIDGE1_STORE_PATH_MISMATCH=PROVEN_NOT_FIXED
OUTBOUND_5750=DLQ_LEAVE_UNCHANGED
POSTGRES_CREDENTIAL_STATUS=ROTATION_PENDING
HERMES_REPLY_FAILOVER=DEFERRED
NEXT_ACTION=FINISH_ONE_CLEAN_ADMIN_E2E_TEST_VIA_BRIDGE1_INBOUND_OR_BRIDGE2_SELFCHAT
```
