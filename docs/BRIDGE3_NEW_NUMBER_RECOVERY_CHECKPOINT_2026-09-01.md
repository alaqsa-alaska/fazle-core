# BRIDGE3 NEW-NUMBER RECOVERY — INITIAL CHECKPOINT

**Status:** `READ-ONLY VERIFICATION COMPLETE — PRESERVATION + SAFETY HOLD IN PROGRESS — AWAITING OWNER PAIRING`
**Session:** owner-authorized operational recovery, 2026-09-01
**Author:** Claude (VS Code agent)
**Scope:** pair a NEW WhatsApp number onto Bridge3 (recruitment channel); preserve all
former-Bridge3 history/provenance; safely activate the owner's `META_APP_SECRET` rotation.
No business-routing / Payment / FPE / Bridge1 / Bridge2 change. Phase-2 stays OFF.

All phone numbers in this document are masked. No secrets, QR payloads, session keys, or
OTPs are recorded here or anywhere.

---

## 1. Current production state (verified read-only, 2026-09-01 ~19:45Z)

### 1.1 fazle-core

| Item | Value |
|---|---|
| Service | `fazle-core.service` **active/running**, `MainPID=2981971`, `NRestarts=0`, up since 2026-09-01 21:20:33 CEST |
| Branch / HEAD | `feat/phase2-inbound-identity-2026-09-01` @ `351324f` (deployed revision `8070bfb`; `8070bfb..351324f` = **docs-only**, established safe in the Phase-2 deploy session) |
| Worktree | 1 modified file: `docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md` — **already an approved cleanliness exception** in `db/.restart_gate_manifest`. Otherwise clean. |
| `PHASE2_IDENTITY_RESOLVER_ENABLED` | **absent from `.env` → OFF**; 0 occurrences in `/proc/2981971/environ`. Phase-2 enrichment stays dark this whole session. |
| `/health` | overall **`ok`**; db ok; disk 52% / 98.6 GB free; mem 8.9 GB free; cpu load1 ~1.6 |
| Observation window | Phase-2 24–48 h window still open (started 2026-09-01T19:26Z; +24 h 09-02, +48 h 09-03). This session does not close or alter it. |

### 1.2 Bridges

| Bridge | systemd | PID / since | WhatsApp account | Store dir |
|---|---|---|---|---|
| **Bridge1** (HR, :8081) | `whatsapp-bridge.service` active, NRestarts 0 | 3640163 / 2026-08-29 19:50 | **authenticated** (`whatsmeow_device` = 1 row, `8801885…` masked) | `/home/azim/whatsapp-mcp/whatsapp-bridge/store/` |
| **Bridge2** (OPS/Super-Admin, :8082) | `whatsapp-bridge2.service` active, NRestarts 0 | 3688844 / 2026-08-29 20:19 | **authenticated** (`whatsmeow_device` = 1 row, `8801886…` masked) | `/home/azim/whatsapp2/store/` |
| **Bridge3** (REC2 / recruitment, :8083) | `whatsapp-bridge3.service` active, NRestarts 0 | 2589884 / 2026-08-29 08:51 | **LOGGED OUT** — `whatsmeow_device` = **0 rows** | `/home/azim/whatsapp3/store/` |

**Bridge3 is a WhatsApp-account failure, not a process/DB failure.** The Go bridge process
is healthy and listening on :8083; only its WhatsApp session is gone. Root cause is in the
bridge log (`/home/azim/bridges/mcp/logs/bridge3.log`), 2026-08-31:

```
14:31:50  [Client INFO] Got 403: primary device was logged out connect failure,
                         sending LoggedOut event and deleting session
14:31:50  [Client WARN] Device logged out, please scan QR code to log in again
```

`403 primary device logged out` = the primary phone / account removed the linked device.
Consistent with the owner's confirmation that the former number is permanently
banned/revoked. whatsmeow then deleted the local session (device rows → 0). The store
files are frozen at 2026-08-31 14:31 (no writes since).

### 1.3 Former Bridge3 stored data (preservation baseline — must be unchanged at the end)

| Store | Path | Metric |
|---|---|---|
| whatsmeow device store | `/home/azim/whatsapp3/store/whatsapp.db` | 4,255,744 bytes, mtime 2026-08-31 14:31, `whatsmeow_device` 0 rows (session deleted on logout), contact/lid-map tables intact |
| bridge in-house log | `/home/azim/whatsapp3/store/messages.db` | 1,564,672 bytes, **4,196 messages**, 2026-05-25 → 2026-08-31 14:31:04 |
| per-chat media dirs | `/home/azim/whatsapp3/store/*@lid/…` | 29 subdirectories |
| pre-existing old backup | `/home/azim/whatsapp3/store-backup-20260726/` | left untouched |
| central Postgres (canonical) | `wbom_whatsapp_messages WHERE platform='bridge3'` | **5,820 rows** (3,472 inbound + 2,348 outbound), 2026-05-18 → 2026-08-31 12:39:32 |
| dedup ledger | `processed_bridge_messages WHERE bridge='bridge3'` | 3,374 rows, max 2026-08-31 12:31:12 |
| poller cursor | `bridge_poller_cursor WHERE bridge='bridge3'` | `last_ts = 2026-08-31 12:31:04+00` |
| recruitment | `wbom_candidates` | 545 total (165 in last 7 d) — not platform-scoped, but recruitment intake is Bridge3-fed |

Provenance is stored per row in `wbom_whatsapp_messages.platform = 'bridge3'` (there is no
`source_bridge` column on that table — the bridge id lives in `platform`). Historical rows
are therefore already attributable to "the Bridge3 channel". See §4 for the new-number
identity design.

---

## 2. Outbound-safety analysis (the "could a send auto-fire on reconnect?" check)

### 2.1 Queues — clear

| Queue | Bridge3-relevant state | Verdict |
|---|---|---|
| `fazle_outbound_queue` | **0** non-terminal Bridge3 rows. 2,392 `sent`, 31 `dlq` (terminal; DLQ needs a manual `/admin/dlq/{id}/requeue`, never auto-fires). Latest Bridge3 DLQ 2026-09-01 07:11 (post-ban send attempts that correctly failed closed). | ✅ nothing will auto-flush |
| `fazle_message_queue` | 18 Bridge3 rows stuck `status='processing'` at `stage=raw_received` (Aug 23–31). This table is an **audit/observability trail only** — the poller drives off the bridge SQLite + `bridge_poller_cursor`, never re-reads these rows. Same benign pattern exists for Bridge1 (33 rows, Jul) and Bridge2 (23 rows). | ✅ audit debris, not reprocessed |
| `social_reply_queue` | 86 `sending` + 100 `pending_retry` + 9 `blocked` — **all `platform='messenger'`, `source_bridge=NULL`**. Pre-existing dormant Facebook-Messenger backlog (oldest May), `meta_messenger_live_send` flag is OFF. Not Bridge3, not in scope. | ℹ️ noted, untouched |

### 2.2 Poller replay — bounded, one small gap

`bridge_poller_cursor[bridge3].last_ts = 2026-08-31 12:31:04Z`. The poller only ingests
messages with `timestamp > cursor` and additionally dedups via `processed_bridge_messages`.
Historical messages therefore will **not** replay.

**Small gap:** the bridge kept receiving inbound between the cursor (12:31:04) and the
logout (14:31:50) on 2026-08-31 — those messages sit in `messages.db` with timestamps
*after* the cursor. If the poller runs against that old DB after a new pairing it could
treat up to ~2 h of pre-ban candidate messages as "new" and generate replies to them from
the new number. **Mitigation (applied at pairing time, see §6):** advance
`bridge_poller_cursor[bridge3]` to the pairing moment — this is exactly the system's own
documented "fresh bridge starts from NOW()" behaviour and matches the owner's "no auto
re-contact of old candidates" directive.

### 2.3 Recruitment recovery sweep — **ACTIVE, this is the real risk**

- `assistant_feature_flags.recruitment_recovery_sweep_live_send` = **`is_active=true`,
  `kill_switch_active=false`** (enabled 2026-08-25).
- Scheduler job `recruitment_recovery_sweep` runs **every 30 min** (`IntervalTrigger`,
  `RECRUITMENT_RECOVERY_SWEEP_INTERVAL_MIN=30`).
- With live-send ON it finds "latest inbound per sender in the last 48 h with no outbound
  after it", generates a real reply for recruitment roles, and `outbound.enqueue(...,
  source_bridge=<row.platform>)` → **`bridge3`** for recruitment candidates. Bounded to 20
  candidates/run.
- **Right now: 11 distinct Bridge3 senders are stale-unanswered inside the 48 h window**
  (conversations that stalled when the ban hit). Recent sweep ticks show it trying and
  failing (`bridge not connected` → DLQ). **The moment Bridge3 can send again, the next
  sweep tick (≤30 min) would deliver catch-up recruitment messages to those candidates
  from the new, unknown number** — exactly what the owner said must not happen.
- The 48 h window naturally drains by 2026-09-02 ~14:31Z, but pairing may well happen
  before then, so a hold is required.

**Hold applied this session (see §3):** set `recruitment_recovery_sweep_live_send`
`kill_switch_active = true`. Per the module's own design this reverts it to
**dry-run/digest mode** — it still runs the full analysis and posts one admin digest to
Bridge1, but sends **nothing** to candidates/employees. This is the narrowest available
pause: there is no per-bridge toggle inside the sweep. It also pauses the low-volume
*employee* recovery path (Bridge1/Bridge2) for the pairing window — acceptable, fully
reversible, and the **interactive** live reply pipeline on all three bridges is
completely untouched. Restoration is a tracked step (§6 / OWNER_ACTION list).

### 2.4 Other automation — no Bridge3 auto-send

- `recruitment_recovery_sweep` digest → Bridge1 admin only (benign).
- `auto_reply.bridge3_full` runtime setting = `false`; `auto_reply.recruitment` = `true`
  (interactive path only; requires a live inbound, which a banned number cannot receive).
- No cron / scheduler job enqueues bulk Bridge3 outreach.
- Scheduler probe healthy (`age_s` ~240).

---

## 3. Actions taken this session (all reversible, none touching business routing)

| # | Action | Reversal |
|---|---|---|
| 3.1 | **Backup** former Bridge3 state — see §5 | n/a (additive) |
| 3.2 | **Outbound-safety hold:** `recruitment_recovery_sweep_live_send` → `kill_switch_active=true` (digest/dry-run mode) | set `kill_switch_active=false` (§6) |

Nothing else is changed until the owner completes pairing.

---

## 4. New-number Bridge3 identity design (smallest compatible change)

**Principle:** the bridge id stays `Bridge3` and `platform='bridge3'` stays the provenance
tag. The WhatsApp *account* behind Bridge3 changes; historical rows already carry
`platform='bridge3'` and are not touched. Canonical contact identity for any conversation
is the **external sender's normalized phone** (`8801XXXXXXXXX`), never the bridge account
number — so a change of the Bridge3 account number does not create or mutate any
employee / applicant / contact record, and does not reattribute any historical message.

- No schema change is required: `wbom_whatsapp_messages.platform` already distinguishes
  the channel; the *account* number is not stored per-row and does not need to be for
  routing.
- Optional, additive-only (decide with owner, not required for pairing): record the
  new Bridge3 account number + pairing timestamp in `fazle_runtime_settings`
  (`bridge3.account_number`, `bridge3.paired_at`) purely for operational/audit visibility.
  This does **not** overwrite any provenance.
- If, during validation, anything is found that would force *overwriting* existing
  Bridge3 provenance to distinguish old vs new account → **STOP** and document the
  smallest additive alternative before any mutation. (Not expected — nothing in the
  current schema requires it.)

---

## 5. Backup of former Bridge3 state

Location: `/home/azim/backups/fazle/bridge3-preban-20260901/` (access-restricted).
Method: `sqlite3 .backup` snapshots of both DBs (safe online copy) + `tar` of the whole
`/home/azim/whatsapp3/` tree + SHA-256 manifest. Excluded from git and secret scans
(outside the repo). Contents are **not** printed. See the "Backup evidence" section
appended below once complete.

---

## 6. Remaining plan (gated)

1. **[this session, now]** finish backup + verify readable + integrity hashes.
2. **[this session, now]** confirm outbound-safety hold is in effect (sweep digest-only).
3. **[this session, now]** target-process checks (port :8083 owner, disk, permissions,
   new number ≠ Bridge1/Bridge2 — the last needs the owner to supply the masked new
   number).
4. **[OWNER]** pair the new number onto Bridge3 (QR/OTP/device confirm on the physical
   phone). The agent pauses here with `OWNER ACTION REQUIRED — COMPLETE BRIDGE3 PAIRING NOW`
   and provides only: which account, which WhatsApp screen, where the QR is shown,
   pairing-window duration, expected confirmation. **No QR payload / OTP / token is ever
   copied into chat or docs.**
5. **[this session, after pairing]** validate new Bridge3 auth (connected account = the
   intended new number, masked; process stable; poller heartbeat fresh; no QR/logout
   loop; no Bridge1/2 collision; historical counts from §1.3 unchanged).
6. **[this session, after validation]** advance `bridge_poller_cursor[bridge3]` to the
   pairing moment (§2.2), then set `recruitment_recovery_sweep_live_send`
   `kill_switch_active=false` to restore it (§2.3).
7. **[this session]** trace the Bridge3 inbound recruitment path end-to-end; verify no
   backlog replay, no historical candidate re-messaged.
8. **[OWNER + this session]** `META_APP_SECRET` rotation handoff (§ separate) — owner
   edits only the `META_APP_SECRET=` line in `/home/azim/core/.env`; agent verifies
   secret-free before/after; then `scripts/restart_gate.sh --restart` (never a bypass).
9. **[this session]** post-change verification (core `/health`, webhook signature
   self-test without calling Meta and without exposing the secret, Phase-2 still OFF,
   Bridge1/2 healthy, no Meta message sent).
10. Update `docs/CLAUDE_PHASE2_OBSERVATION_2026-09-01.md` with the new baseline; write
    `docs/BRIDGE3_NEW_NUMBER_AND_META_SECRET_FINAL_2026-09-01.md`.

### Do NOT generate a QR until items 1–3 pass.

---

## Backup evidence (completed 2026-09-01 ~19:56Z)

Location: `/home/azim/backups/fazle/bridge3-preban-20260901/` — mode `700`, owner `azim`,
outside the git repo (excluded from restart-gate cleanliness + secret scans).

| Artifact | Notes |
|---|---|
| `whatsapp.db` | `sqlite3 .backup` online snapshot. `PRAGMA integrity_check` = **ok**. `whatsmeow_device` 0 rows (session was deleted on logout — expected), `whatsmeow_lid_map` **751 rows** preserved. |
| `messages.db` | `sqlite3 .backup` online snapshot. `integrity_check` = **ok**. **4,196** messages (parity with live: 4,196), range 2026-05-25 → 2026-08-31 14:31:04 — re-verified by reopening the snapshot read-only. |
| `whatsapp3-tree.tar.gz` | 34,078,944 B — byte-exact `tar` of the entire `/home/azim/whatsapp3/` tree (store, per-chat media dirs, the pre-existing `store-backup-20260726/`). |
| `SHA256SUMS` | SHA-256 of all three artifacts. |

Live source files remain **untouched** (frozen at 2026-08-31 14:31; independent SHA-256
recorded in the session log for later comparison). No session contents were printed.
`.backup`-snapshot hashes legitimately differ from the live files (SQLite rewrites page
layout on `.backup`); byte-identity is preserved in the tarball and proven for the DBs by
`integrity_check` + row-count parity.

---

## Target-process checks (2026-09-01 ~19:59Z) — PASS

| Check | Result |
|---|---|
| `:8083` listener | owned by `whatsapp-bridge` PID **2589884** (the `whatsapp-bridge3.service` process) — correct target, no stray listener |
| Bridge3 process | running as `azim`, ~85 h uptime, HTTP server responding (404 on unknown routes = up) |
| Bridge1 / Bridge2 | untouched — PIDs 3640163 / 3688844, `NRestarts=0`, both `whatsmeow_device` = 1 row (authenticated). Bridge1 acct ≈ `88019…`, Bridge2 acct ≈ `880188…` (masked). |
| Store dir | `/home/azim/whatsapp3/` + `store/` owned by `azim`, mode `775`, writable |
| Disk | 92 GB free (53% used) — ample |
| Outbound-safety hold | `recruitment_recovery_sweep_live_send` → `effective_enabled=false` (verified via `/api/assistant/ops/flags` **and** the DB row). In-process flag cache updated synchronously by the ops endpoint. |
| New number ≠ Bridge1/Bridge2 | **PENDING — owner to confirm** the masked new number differs from both accounts above |

### Restart mechanics (for the owner)

- whatsmeow only enters the QR-pairing routine **on process start** when no device is
  stored (`client.Store.ID == nil`). The currently-running Bridge3 process detected
  `LoggedOut` while running and is now idle — it will **not** produce a QR without a
  restart.
- The service unit redirects stdout/stderr to `/home/azim/whatsapp-mcp/logs/bridge3.log`
  (→ symlink → `/home/azim/bridges/mcp/logs/bridge3.log`). A restart therefore prints the
  QR **as ASCII half-blocks into that log file**, not to an interactive terminal.
- `sudo systemctl restart whatsapp-bridge3.service` requires the sudo password (only
  `fazle-core`, `fazle-agent`, `whatsapp-bridge`, `whatsapp-bridge2` are NOPASSWD for
  `azim`; Bridge3 is not) — so the **owner** performs the restart.
- `Restart=always`, `RestartSec=10`, `StartLimitBurst=5`: if the 3-minute QR window
  expires unscanned the process may exit and be re-spawned a few times before systemd
  backs off — so restart only when ready to scan immediately.

---

## OWNER ACTION REQUIRED — COMPLETE BRIDGE3 PAIRING NOW

**Do this only when you have the new number's physical phone in hand and ~3 minutes free.**

1. Confirm to the agent: the new Bridge3 number is **not** the Bridge1 (`88019…`) or
   Bridge2 (`880188…`) account.
2. Choose a QR method:
   - **A (recommended — direct terminal QR):**
     `sudo systemctl stop whatsapp-bridge3.service`
     then run the bridge in the foreground so the QR renders in your terminal:
     `cd /home/azim/whatsapp3 && WHATSAPP_PORT=8083 WHATSAPP_STORE_DIR=/home/azim/whatsapp3/store /home/azim/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge.candidate`
     Scan the QR with **WhatsApp → Settings → Linked Devices → Link a Device** on the new
     number's phone. Wait for `Successfully connected and authenticated!`, then `Ctrl-C`
     and `sudo systemctl start whatsapp-bridge3.service` (it will reuse the freshly paired
     session).
   - **B (service-managed):** `sudo systemctl restart whatsapp-bridge3.service`, then
     `tail -f /home/azim/whatsapp-mcp/logs/bridge3.log` and scan the ASCII QR from there.
3. WhatsApp screen: **Linked Devices → Link a Device** on the **new** number.
4. Pairing window: each QR code is valid ~20 s and auto-refreshes; the bridge gives up
   after **3 minutes** total — rescan-ready.
5. Expected success signal: `Successfully connected and authenticated!` in the log, then
   `Connected to WhatsApp`, and `whatsmeow_device` gains **1 row**.
6. Tell the agent when done (or if it failed). **Do not** send any test WhatsApp message.

Never paste the QR code, its payload, or any pairing token into chat or a document.

### After the owner confirms pairing, the agent will (same session):
- validate the new session (connected account = intended new number, masked; process
  stable; poller heartbeat fresh; no QR/logout loop; Bridge1/2 uncollided; §1.3 historical
  counts unchanged);
- advance `bridge_poller_cursor[bridge3]` to the pairing moment;
- restore `recruitment_recovery_sweep_live_send` (clear the kill-switch + re-activate);
- trace one inbound recruitment message end-to-end (no backlog replay);
- then move to the `META_APP_SECRET` rotation handoff and the gated `restart_gate.sh
  --restart`.

---

## META_APP_SECRET rotation — pre-edit checks (read-only, done 2026-09-01 ~20:02Z)

No secret value was printed, logged, or persisted. A session-local salted-SHA-256
fingerprint of the *current* value was taken in memory only, to prove afterward that the
value changed.

| Check | Result |
|---|---|
| `.env` path | `/home/azim/core/.env` (line **81**) — the single production env file the running service reads |
| `META_APP_SECRET=` definitions in `.env` | **exactly 1** |
| Permissions / owner | `-rw-------` (600) `azim:azim` — correct |
| Tracked by git / in history | **No.** `.gitignore` covers `.env`, `.env/`, `.env.*`. `git ls-files` does not match it. |
| Tracked files mentioning the name | only `db/.restart_gate_manifest` (incident comment) and `tests/conftest.py` (`"test_app_secret"` placeholder) — **no real value anywhere in the repo or its history** |
| Other copies on disk | 5 historical `env_live_2026-06-0X.bak` snapshots in `/home/azim/secure-env-backup/` (June, stale, not read by any running service). **Not touched** — the owner rotates only the live `.env` line. |
| Consuming service | `fazle-core` only (HMAC check at `app/main.py` `_verify_meta_signature` path) |
| Current value | length 32; fingerprint recorded in memory (session salt) |
| `scripts/secret_scan.sh` | present, will be run post-edit |

### OWNER ACTION — META_APP_SECRET rotation (do at the same time as / near the pairing)

1. In the **Meta App Dashboard → Settings → Basic**, reset the **App Secret**.
2. On the server, edit **only** the `META_APP_SECRET=` line (line 81) of
   `/home/azim/core/.env` — replace the value with the new one. Keep the same
   `KEY=value` form, no surrounding quotes, no trailing spaces, single line, LF newline.
3. Do **not** change `META_VERIFY_TOKEN`, any access token, phone-number ID, business ID,
   Page ID, webhook subscription, or template.
4. **Do not paste the new secret into chat or any file.** Tell the agent when the edit
   is saved.

### After the owner confirms the edit, the agent will (before any restart):
- verify `META_APP_SECRET` is present and non-empty **without printing it**;
- verify exactly one definition, no stray quotes/whitespace/duplicate/CRLF;
- verify the value **differs** from the pre-rotation fingerprint (non-reversible compare);
- verify `.env` is still `600 azim:azim`, still untracked, not added to any log/doc/backup;
- run `scripts/secret_scan.sh`;
- confirm `PHASE2_IDENTITY_RESOLVER_ENABLED` still absent.

### Single restart covers both changes
Once **(a)** Bridge3 is paired + validated + cursor advanced + sweep restored, **and**
**(b)** the new `META_APP_SECRET` is in place and verified, the agent runs
`scripts/restart_gate.sh --restart` **once** (never a bypass, never direct `systemctl`
for core), then the §9 post-change verification (core `/health`; a local webhook
signature self-test computed with the new secret **without calling Meta**; invalid
signature rejected; no Meta message sent; Phase-2 OFF; Bridge1/2 healthy).
