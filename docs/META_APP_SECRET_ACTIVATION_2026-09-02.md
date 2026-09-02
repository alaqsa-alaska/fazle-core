# META_APP_SECRET ROTATION — ACTIVATION RECORD

**`META_APP_SECRET ROTATION ACTIVATED`**

**Date:** 2026-09-02 · **Session:** owner-authorised · **Author:** Claude (VS Code agent)

No secret value — old or new — was displayed, copied, logged, written to any file, or
hashed into this record. HMAC signatures generated for the self-test are not reproduced.

---

## 1. What happened

The owner **externally** rotated the Meta App Secret:

- **Meta Dashboard** (Settings → Basic → App Secret): **reset** — owner-confirmed **YES**,
  the dashboard secret was regenerated *before* the `.env` value was replaced, and the
  `.env` now holds that exact new secret.
- **Production `.env`** (`/home/azim/core/.env`, line 81): the `META_APP_SECRET=` value was
  replaced by the owner (file mtime `2026-09-01 21:43`).

This session did **not** rotate, generate, restore, or copy any secret. Its role was
secret-safe validation → gated restart to load the new value → offline activation
verification.

## 2. Pre-restart `.env` validation (secret-safe, read-only)

| Check | Result |
|---|---|
| `META_APP_SECRET=` definitions in `.env` | exactly **1** (line 81) |
| Present / non-empty | yes (32 chars) |
| Surrounding quotes / trailing whitespace / trailing CR / CRLF / lone CR / missing final newline | **none** |
| `.env` permissions | `600 azim:azim` — unchanged |
| Second `EnvironmentFile` (`/home/azim/secure-env-backup/runtime-services.env`) | does **not** define `META_APP_SECRET`; it only templates DB/Redis URLs — `.env` is the sole source |
| Every other `.env` key vs the running process's loaded env | **only `META_APP_SECRET` differs.** `META_VERIFY_TOKEN`, `META_API_TOKEN`, `META_APP_ID` (`1149218…`), `META_WABA_ID` (`3119866…`), `META_PHONE_NUMBER_ID` (`1190102…`) all **UNCHANGED**. No key added or removed. |
| `META_WEBHOOK_SIGNATURE_ENFORCEMENT`, webhook subscriptions, templates, live-send flags | untouched by the owner's edit and by this session |
| `PHASE2_IDENTITY_RESOLVER_ENABLED` | absent (OFF) — unchanged |
| `.env` value vs the (pre-restart) running process | **differ** — confirms a real change landed after the last restart |
| In git / git history / tracked files / docs / logs | **not present anywhere**; `scripts/secret_scan.sh` = **PASS** |
| Plaintext `.env` backup created by this session | **none** |

**Fingerprint caveat (disclosed):** the "pre-rotation" salted fingerprint recorded on
2026-09-01 was taken at ~22:02 CEST — *after* the `.env` was already edited at 21:43. A
valid non-reversible *old→new* comparison was therefore not possible; that window had
closed. What is definitive: the pre-restart running process and the `.env` held **different**
`META_APP_SECRET` values, and it is a clean single-key change.

**Old-secret exposure surface (for separate owner cleanup, NOT touched here):** multiple
historical `/home/azim/core/.env.bak.*` snapshots exist (all `600`, `azim`-only, none in
git). The pre-rotation value may persist in the more recent ones and in
`/proc/2981971/environ` (the now-exited old process — gone after the restart). None were
read; none deleted. Recommend the owner review/prune the `.env.bak*` set separately.

## 3. Gated restart (Meta secret load ONLY — no Bridge3 fix, no Phase-2)

Pre-restart verification (all PASS):

- core working tree on the deployed branch `feat/phase2-inbound-identity-2026-09-01`;
  HEAD `351324f` (= deployed `8070bfb` + 2 **docs-only** commits `a8bd9fa`, `351324f` —
  no code delta);
- Bridge3 fix code **absent** from the working tree (`from_history_sync` /
  `_bridge_quarantine_until` grep = 0 — it is committed only on
  `fix/bridge3-history-sync-quarantine-20260902`, checked out away);
- worktree carried only the one pre-approved manifest exception
  (`docs/BRIDGE_MEDIA_ACQUISITION_INTEGRATION_PLAN_2026-08-29.md`);
- Bridge3 stopped; Bridge1/Bridge2 healthy (`whatsmeow_device` = 1 each); Phase-2 OFF;
  migrations clean; newest DB backup 2 h old.

`scripts/restart_gate.sh --check-only` → **OVERALL: PASS (0 warnings)** — all 10 checks.
`scripts/restart_gate.sh --restart` → all 10 checks **PASS**, then **RESTART: SUCCESS**.

| | Before | After |
|---|---|---|
| `fazle-core.service` PID | 2981971 | **3470442** |
| `NRestarts` | 0 | 0 |
| Started | 2026-09-01 21:20:33 CEST | **2026-09-02 01:30:00 CEST** |
| Branch / HEAD | `feat/phase2-inbound-identity-2026-09-01` / `351324f` | unchanged |
| startup-complete / first healthy `/health` | — | 10 s / 10 s (`ok`) |
| `/health` overall | ok | **ok** (all probes ok) |
| `PHASE2_IDENTITY_RESOLVER_ENABLED` in `/proc/<pid>/environ` | absent | **absent** (0) |
| Bridge1 PID / NRestarts | 3640163 / 0 | **3640163 / 0** (untouched) |
| Bridge2 PID / NRestarts | 3688844 / 0 | **3688844 / 0** (untouched) |
| Bridge3 service | inactive | **inactive** |

New process env: `META_APP_SECRET` present, non-empty, and **byte-equal to the current
`.env` value** (verified in-memory; value not printed). The old process had a different
value — the restart is what loaded the rotation.

Restart log: graceful `Finished server process [2981971]` → `Started server process
[3470442]` → `Application startup complete`. No `ERROR` / `CRITICAL` / `Traceback` in
`logs/fazle-core.log` since 01:30. (`logs/fazle-core-error.log` carries only the
pre-existing benign `modules/outbound/__init__.py:76 RuntimeWarning: coroutine
'_was_recently_replied' was never awaited` — present on the Phase-2 branch before this
work, "informational, NOT blocking send"; tracked separately.)

## 4. Offline Meta HMAC activation verification (no Meta call, no message sent)

Local self-test against the running service, using the app's own verification path
(`POST /webhook/meta` → `X-Hub-Signature-256` HMAC-SHA256 with `settings.meta_app_secret`
→ `hmac.compare_digest`). Payload: a well-formed but **empty**
`whatsapp_business_account` delivery (`entry[].changes[].value = {}`) — the exact shape the
existing `tests/integration/test_webhooks.py::test_meta_webhook_empty_messages_ok` uses;
zero message processing, zero outbound.

| Test | Result |
|---|---|
| Valid signature computed with the **new** `.env` secret | **HTTP 200** — the new secret is loaded and verifies |
| Invalid signature (`sha256=` + 64×`0`) | **HTTP 403** `Invalid signature` — bad sigs rejected, **no fail-open** |
| `GET /webhook/meta?hub.verify_token=<wrong>` | **HTTP 403** — verify endpoint reachable and rejecting correctly |
| `meta_webhook_invalid_signature` events in the window | exactly **1** (`reason=mismatch`, from the invalid-sig test) — **0** from the valid-sig test |
| HMAC / signature errors or tracebacks post-restart | **none** |

Signature verification was **not** disabled and does **not** fail open.
No Meta / WhatsApp / Messenger / Instagram message was sent. No webhook subscription,
token, phone-number ID, business ID, page ID, app ID, WABA ID, template, or live-send
flag was changed.

## 5. Bridge3 containment — reconfirmed AFTER the Meta restart

| | State |
|---|---|
| `whatsapp-bridge3.service` | **inactive** (stopped) |
| `auto_reply.recruitment` | **false** |
| `recruitment_recovery_sweep_live_send` | kill-switched (`effective_enabled=false`) |
| `phone_block.447710173736` | **true** |
| `bridge3.quarantine_until` | unset (only set immediately before an approved pairing) |
| `bridge_poller_cursor[bridge3]` | `2026-09-01 21:12:23Z` — unchanged |
| `fazle_outbound_queue` bridge3 non-terminal rows | **0** — no queue flushed |
| `wbom_whatsapp_messages` platform=bridge3 since the restart | **0** new rows |
| `/home/azim/whatsapp3/store/*.db` mtime | frozen at the 2026-09-02 00:01 incident time — nothing writing |
| `whatsmeow_device` rows | **0** — still logged out |
| QR generated / message sent | **none** |

**The `META_APP_SECRET` rotation is unrelated to the WhatsApp temporary restriction and is
not a remedy for it.** The primary WhatsApp account restriction (≈ 6 h countdown) remains
an owner / mobile-app matter — see `BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02.md`.

## 6. Remaining owner actions

1. **Prune the old-secret exposure surface** — review `/home/azim/core/.env.bak.*` (and
   `/home/azim/archives/bak-files-20260612/.env.bak.meta.*`); delete the ones that hold the
   pre-rotation `META_APP_SECRET` once you're satisfied nothing needs them. Not done here
   (no approval to delete; not read).
2. **Bridge3 second pairing** stays fully gated — restriction countdown must expire and
   the account be re-verified, the F1–F4 history-sync/quarantine fix must be reviewed **and
   deployed**, and you must separately approve the pairing.
3. Decide the spurious `fazle_recruitment_sessions` id 571 / `wbom_candidates` id 548
   cleanup (from the incident) — can ride along with the F1–F4 deploy.

---

`META_APP_SECRET ROTATION ACTIVATED · CORE PID 3470442 · /health ok · PHASE-2 OFF · BRIDGE1/2 UNTOUCHED · BRIDGE3 STILL STOPPED & CONTAINED`
