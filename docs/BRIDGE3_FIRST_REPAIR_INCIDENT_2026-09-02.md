# BRIDGE3 FIRST RE-PAIR — INCIDENT FORENSICS

**Status:** `CONTAINED · PRIMARY ACCOUNT TEMPORARILY RESTRICTED (~6 h countdown) · F1–F4 IMPLEMENTED, NOT DEPLOYED · NO PAIRING / NO DEPLOY WITHOUT SEPARATE OWNER APPROVAL`

> ### 2026-09-02 — MOBILE ACCOUNT STATUS (CORRECTION — this supersedes the earlier "normal" reading)
>
> **PRIMARY MOBILE ACCOUNT: TEMPORARILY RESTRICTED.** Current authoritative
> owner mobile-app evidence: WhatsApp shows **"Temporarily restricted"** with an
> **≈ 6-hour countdown** displayed. This is **not** confirmed as a permanent ban.
>
> **BRIDGE3 COMPANION SESSION: LOGGED OUT / REMOVED.**
>
> - Do **not** classify the primary account as normal.
> - Do **not** classify it as permanently banned unless the mobile app later shows an
>   explicit permanent-ban message.
> - **Restriction countdown:** ≈ 6 h from the owner's observation. Exact observation
>   time / screenshot timestamp and the derived expiry are **owner-provided — not
>   invented here.** _Owner to fill in:_ observed at `__________ (TZ)`, expiry ≈
>   `__________`.
>
> **During the countdown (in force now):** no QR; do not start or pair Bridge3; no
> repeated verification-code requests; send no message; Bridge3 stays stopped;
> `auto_reply.recruitment` stays `false`; `recruitment_recovery_sweep_live_send` stays
> disabled; the OTP/spam number stays blocked; **no production restart or deploy without
> separate owner approval.** Work continues on offline code, isolated tests, and this
> forensic documentation only.
>
> **After the countdown expires**, the owner must verify from the mobile app: the
> restriction banner is gone; normal chats open; no permanent-ban message; Linked
> Devices is available; the account number is the intended Bridge3 number. **Even then**,
> no second pairing until: history-sync quarantine is implemented; the 2,444-row replay
> regression passes; the fix is reviewed **and deployed**; Bridge3 starts in
> receive/persist-only quarantine; every outbound path stays blocked; and the owner
> **separately approves** the second pairing.
>
> _An earlier version of this document concluded "PRIMARY MOBILE ACCOUNT: ACTIVE /
> NORMAL" based on the owner's first status report. **That conclusion is SUPERSEDED**
> by the restriction evidence above._
**Incident window:** 2026-09-01 ~21:57Z → 22:03Z (2026-09-02 ~00:00 CEST)
**Author:** Claude (VS Code agent) · owner-directed incident response
**Sibling doc:** [BRIDGE3_NEW_NUMBER_RECOVERY_CHECKPOINT_2026-09-01.md](BRIDGE3_NEW_NUMBER_RECOVERY_CHECKPOINT_2026-09-01.md)

All phone numbers masked. No secrets, QR payloads, session keys, or OTP values recorded.
The two OTP *messages* involved are quoted only as evidence of message type; the codes
themselves are business-irrelevant third-party auth codes and are left as-is because they
were already delivered into `messages.db` by WhatsApp — they are not our secrets.

---

## 1. What happened (one paragraph)

The owner stopped `whatsapp-bridge3.service` and ran the bridge binary in the foreground to
pair a number onto Bridge3. Pairing succeeded and WhatsApp pushed a **history-sync** batch
of ~2,444 messages into `/home/azim/whatsapp3/store/messages.db`, **including the exact
pre-ban Bridge3 recruitment conversations from 2026-08-31 with their original timestamps.**
The fazle-core `bridge3` poller — whose persisted timestamp cursor was still at
**2026-08-31 12:31:04Z** and which has **no concept of history-sync vs. live delivery** —
saw 3 of those synced messages as brand-new inbound, routed them through the normal live
recruitment pipeline, generated replies, and **delivered one automated Bengali recruitment
message** to a long-standing OTP-spam contact (`447710…`, a UK number that has been sending
Facebook/Instagram confirmation codes to the Bridge3 number since May). A second reply was
enqueued and attempted. WhatsApp then removed the device (LoggedOut), the session was
deleted, and the bridge disconnected. **Bridge1 and Bridge2 were not affected in any way.**

The earlier assumption in the recovery checkpoint — that a stale cursor plus
`processed_bridge_messages` dedup made historical replay a non-issue — was **wrong for the
history-sync case**: history sync re-delivers old messages *above* a stale cursor, and the
planned cursor-advance was scheduled for **after** pairing, i.e. too late.

---

## 2. Timeline (UTC; local = +02:00)

| UTC | Event | Evidence |
|---|---|---|
| pre-incident | `bridge_poller_cursor[bridge3]` = `2026-08-31 12:31:04Z` (frozen at the original ban). `messages.db` = 4,196 rows. `wbom_whatsapp_messages platform='bridge3'` = 5,820. | earlier read-only checkpoint |
| 2026-09-01 19:57Z | Agent kill-switched `recruitment_recovery_sweep_live_send` (still in effect). | `assistant_feature_flags` |
| ~21:55–22:00Z | Owner `systemctl stop whatsapp-bridge3`, runs bridge binary in foreground, scans QR, **pairs**. whatsmeow `handleHistorySync` writes ~2,444 rows into `messages.db` (→ 6,640 total). Device row written to `whatsapp.db`. | `messages.db` count delta; owner's terminal (not in the service log — foreground stdout) |
| 22:01:12Z | `bridge3` poller: **`3 new message(s) to process`**. | `logs/fazle-core.log` |
| 22:01:12Z | msg `AC98916B…` — sender `88015…8973` ("Munna Master", **real escort employee, on_leave**), text `"Hi"`, true ts **2026-08-31 21:58:37Z**. Identity → `escort (db_employee)`. Reply generated (`role=escort intent=greeting`), then **`MANUAL_REPLY_GUARD: admin already replied … skipping`** → **not sent.** wbom msg 22794. | log + `wbom_whatsapp_messages` |
| 22:02:00Z | msg `173F8A…` — sender `447710…`, text `"Your WhatsApp account is successfully connected to your Instagram profile…"`, true ts **2026-09-01 20:57:56Z**. Identity → `unknown (conf 0)`. INTENT misclassified `salary_query`. semantic_gate → `no_match` conf 0.92 ("automated notification … candidate in technical-only bridge3 routing") — **advisory only, did not block.** `reply_policy … path=recruitment`. wbom msg 22795. | log |
| 22:02:30Z | `generate_recruitment_reply` → **`OUTBOUND_ENQUEUE id=7808 recipient=447710… bridge=bridge3 purpose=customer-reply:recruitment`**. Recruitment session **571** + candidate **548** created. | log + DB |
| 22:02:30Z | msg `66D0B0…` — sender `447710…`, text `"99015 is your Facebook confirmation code"`, true ts **2026-09-01 21:12:23Z**. `cooldown would have blocked … but an active recruitment session is a direct answer → proceeding`. Identity `unknown`. `recruitment_detector L3: active_session:name`. | log |
| **22:02:35Z** | **`BRIDGE_SEND_SUCCESS label=BR3 jid=447710…` — outbound 7808 DELIVERED.** Body: `"এটা একটা সিস্টেম মেসেজ মনে হচ্ছে — WhatsApp-Instagram সংযোগ …"`. | log + `fazle_outbound_queue.sent_at` |
| 22:02:43Z | msg `66D0B0…` routing: `[recruitment_flow] Phase C decide() failed, falling back to legacy: syntax error at or near "%"` (**pre-existing bug**, 24 prior occurrences today). Legacy path still produced `"আপনার বয়স কত বছর?"`. **`OUTBOUND_ENQUEUE id=7809 … bridge=bridge3`**. wbom msg 22798. | log |
| ~22:02:40Z | **WhatsApp removes the device** (between 7808 success and 7809 attempt). | inference from next line |
| 22:02:45Z | **`BRIDGE_SEND_FAIL label=BR3 … http=500 {"success":false,"message":"Not connected to WhatsApp"}`** — outbound 7809 fails; 2 retries; → **DLQ**. | log + `fazle_outbound_queue` |
| ~22:0x Z | Bridge foreground process: `LoggedOut`, session deleted, `Disconnecting…`, process exits. `whatsmeow_device` → 0 rows. Service left **stopped** by the owner. | owner terminal + `whatsapp.db` |
| 22:05Z | `bridge3` poller cursor now **`2026-09-01 21:12:23Z`** (advanced to the OTP msg ts). | `bridge_poller_cursor` |
| 2026-09-02 ~22:08Z→22:11Z | Agent: preserved all evidence; applied Bridge3 containment (§6). | this doc |

---

## 3. Messages delivered / attempted

| Queue id | Direction | Recipient | Bridge | Purpose | Result | Body (masked) |
|---|---|---|---|---|---|---|
| 7808 | outbound | `447710…` | bridge3 | `customer-reply:recruitment` | **DELIVERED 22:02:35Z** | `এটা একটা সিস্টেম মেসেজ মনে হচ্ছে — WhatsApp-Instagram সংযোগ …` ("this looks like a system message — a WhatsApp-Instagram link…") |
| 7809 | outbound | `447710…` | bridge3 | `customer-reply:recruitment` | **FAILED → DLQ** (device gone) | `আপনার বয়স কত বছর?` ("how old are you?") |
| — | (reply for Munna `88015…8973`) | — | bridge3 | escort greeting | **BLOCKED by MANUAL_REPLY_GUARD** — generated, never enqueued, never sent | — |

**Exactly one automated message was delivered** (7808) to exactly one recipient (`447710…`,
not a real candidate — an automated-OTP-relay number). One further reply (7809) was
attempted and failed. No other reply was queued, attempted, or delivered on any bridge.
No Bridge1/Bridge2 send resulted from this. No payment/FPE/escort/attendance side effect.

---

## 4. Root cause

**Primary:** WhatsApp **history sync** on a fresh device link re-delivered ~2,444 old
messages into `messages.db` with their original timestamps. The `bridge3` poller selects
work purely by `datetime(timestamp) > cursor` against `messages.db` and has **no
history-sync awareness** — the bridge's `messages` table has no history/live marker column,
and the bridge inserts history-sync rows into the same table as live `events.Message`.

**Enabling conditions (all had to be true):**
1. **Stale cursor.** `bridge_poller_cursor[bridge3]` was still `2026-08-31 12:31:04Z`.
   ~30 synced rows were newer than that; filtering (`is_from_me=0`, no groups/newsletters,
   `processed_bridge_messages` dedup) left **3** that reached routing. The recovery-checkpoint
   plan advanced the cursor **after** pairing/validation — one step too late.
2. **Bridge3 default = recruitment for unknown identity.** `447710…` resolved to
   `identity_role=unknown`; on bridge3 that routes to `path=recruitment`, which auto-sends
   for non-admin senders when `auto_reply.all=true` **and** `auto_reply.recruitment=true`
   (both were true). A single inbound opened recruitment session 571; subsequent messages
   (even the OTP) were then treated as "direct answers in an active session," bypassing the
   cooldown guard.
3. **Semantic gate is advisory only.** `hermes_dispatch.semantic_gate` correctly returned
   `no_match` ("automated notification / OTP, not recruitment") at confidence 0.92–0.97 for
   all three messages, but it logs as `task13_shadow … non-authoritative, no side effect`
   and does **not** block the deterministic recruitment fallback.
4. **No receive-only quarantine for a freshly paired bridge.** The moment pairing
   completed, the session was fully live for both inbound routing and outbound send.

**Contributory:** the pre-existing `recruitment_flow` Phase-C `syntax error at or near "%"`
bug (already tracked) — its legacy fallback still generated and enqueued reply 7809.

**Not a cause:** `processed_bridge_messages` dedup worked (only +3 rows). `MANUAL_REPLY_GUARD`
worked (blocked Munna). The recovery sweep was already kill-switched and did **not**
contribute (all its ticks from 19:57Z on show `live_send=False`).

**Why the device was removed:** `whatsmeow` reported device-removed/LoggedOut ~5 s after
outbound 7808 was acknowledged. **Device removal occurred *after* the outbound send
acknowledgement, not before.** Per the CORRECTED owner mobile-app evidence (2026-09-02, see
banner at top): the **primary account is TEMPORARILY RESTRICTED** (≈ 6 h countdown, not a
confirmed permanent ban), and the Bridge3 entry under Linked Devices shows "Logged out".
The most likely trigger is WhatsApp anti-automation enforcement: a fresh companion link
immediately followed by an outbound automated message to a number the account had not been
actively conversing with — the same behaviour class as the original 2026-08-31 logout.
The companion session was removed and the account rate-limited/restricted for the
countdown period. It is **not** (yet) an account ban; re-evaluate only from later mobile
evidence.

---

## 5. Affected rows / state (NOT modified — preserved for the remediation step)

| Object | Change | Location |
|---|---|---|
| `whatsapp3/store/messages.db` | 4,196 → **6,640** rows (+2,444 history-sync). **These rows are in `messages.db` only.** | SQLite |
| `wbom_whatsapp_messages` | +5 rows (`message_id` **22794, 22795, 22796, 22797, 22798**) — the 3 processed inbound + 2 outbound. `platform='bridge3'` 5,820 → 5,825. | Postgres |
| `fazle_recruitment_sessions` | +1 row **id 571** — `phone=447710173736`, `full_name='99015 is your Facebook confirmation code'`, `collection_step=age/name`, `source_bridge=bridge3`, `candidate_id=548`, created 22:02:30Z. | Postgres |
| `wbom_candidates` | +1 row **candidate_id 548** — `phone=447710173736`, `source_message='Your WhatsApp account is successfully connected…'`, `funnel_stage=collecting`, `score_bucket=cold`, created 22:02:30Z. | Postgres |
| `fazle_outbound_queue` | +2 rows — **7808** (`sent`), **7809** (`dlq`). | Postgres |
| `fazle_message_queue` | +3 audit rows (`source=bridge3`, all `done`). | Postgres |
| `processed_bridge_messages` | 3,374 → **3,377** (bridge3). | Postgres |
| `bridge_poller_cursor[bridge3]` | `2026-08-31 12:31:04Z` → **`2026-09-01 21:12:23Z`** (auto-advanced by the poller batch). | Postgres |
| `whatsapp3/store/whatsapp.db` | device row written at pairing, then **deleted** on LoggedOut. `whatsmeow_device` = 0. `whatsmeow_lid_map` still 751. Session remnants present. | SQLite |

Note: `447710173736` is **not** a new contact — it has 8 prior inbound rows in
`wbom_whatsapp_messages` dating to **2026-05-04** (all Instagram/Facebook confirmation
codes), plus one prior outbound (2026-07-26). It is an automated third-party
code-relay/spam number, not a job applicant. Recruitment session 571 / candidate 548 are
**spurious** and should be voided in the remediation step (owner-approved).

---

## 6. Containment applied this session (sanctioned controls only; reversible)

Bridge3 service was already `inactive/dead` (owner stopped it). On top of that:

| # | Control | Mechanism | Effect | Reversal |
|---|---|---|---|---|
| C1 | `recruitment_recovery_sweep_live_send` → kill-switch (from 19:57Z, pre-incident) | `POST /api/assistant/ops/kill-switch/…` | sweep is digest-only, no candidate/employee sends | `POST /api/assistant/ops/activate/…` |
| C2 | **`auto_reply.recruitment` → `false`** | `PATCH /api/wa/settings` (sanctioned UI toggle) | the recruitment auto-send path (`_recruit_gate`) can no longer fire on **any** bridge; recruitment traffic is Bridge3-only in practice, and every other `auto_reply.<role>` toggle was already `false`, so Bridge1/Bridge2 interactive behaviour is unchanged | `PATCH` back to `true` |
| C3 | **`phone_block.447710173736` → `true`** | `POST /api/wa/contacts/447710173736/block` (sanctioned) | that specific contact is draft-always, never auto-sent | `POST /api/wa/contacts/447710173736/unblock` |
| C4 | Bridge3 systemd service stays **stopped** | owner-controlled (agent has no sudo for `whatsapp-bridge3`) | no session → no inbound, no outbound | owner starts it only per §9 |

`auto_reply.all` (master) is left **`true`** — turning it off would suppress every future
Bridge1/Bridge2 role-based auto-reply too, which is broader than this incident warrants.
C2+C3+C4 together fully contain the Bridge3 path.

**Not touched:** Bridge1/Bridge2 sessions, auth, routing, cursors; Payment/FPE; Meta
webhook; `PHASE2_IDENTITY_RESOLVER_ENABLED` (still absent/OFF); core was **not** restarted;
no Bridge3 DB / history-sync row / cursor / queue was modified or deleted.

Post-containment: core `/health` = `ok`; Bridge1/Bridge2 `whatsmeow_device` = 1 each,
services `active`; `bridge_poller_b1/b2` cursors current.

---

## 7. Forensic determinations (the owner's checklist)

1. **Which history-sync message triggered the reply** — `173F8A529436427AD6`, text
   `"Your WhatsApp account is successfully connected to your Instagram profile and ad…"`.
   (The follow-up OTP `66D0B0D6BD389DA25D` triggered the second, failed reply.)
2. **Transport id / ts / chat JID / direction / source** — msg `173F8A529436427AD6`;
   original timestamp **2026-09-01 20:57:56Z**; `chat_jid = <sender>@lid` (masked);
   direction inbound; sender `447710173736`; arrived via **history sync**, not live
   delivery (core processed it 22:02:00Z, ~64 min after its own timestamp).
3. **Why it entered live inbound routing** — the `bridge3` poller reads `messages.db` by
   `timestamp > bridge_poller_cursor` with no history-sync flag; the cursor was stale
   (2026-08-31 12:31:04Z) so the synced row qualified as "new"; on bridge3 an
   `identity_role=unknown` sender defaults to `path=recruitment`, which auto-sends when
   `auto_reply.all` and `auto_reply.recruitment` are both true (they were).
4. **Were the 2,144 (actually ~2,444) synced messages inserted into messages.db, Postgres,
   or both** — **`messages.db` only.** `wbom_whatsapp_messages` gained just the 3 the
   poller processed (+2 outbound). Postgres was not bulk-polluted.
5. **How many were treated as new inbound** — **3** (`AC98916B…` Munna "Hi",
   `173F8A…` "WA connected", `66D0B0…` "FB code"). Of those, 1 blocked by
   `MANUAL_REPLY_GUARD`, 2 routed.
6. **Any other reply queued / attempted / delivered** — no. Only 7808 (delivered) and
   7809 (attempted, failed → DLQ). Nothing on Bridge1/Bridge2.
7. **Did `447710…` exist in the new account's imported history** — yes, as a chat, and
   **also** in our own prior Bridge3 history since 2026-05-04 (8 inbound OTP messages +
   1 outbound). It is an automated code-relay/spam number, not a candidate.
8. **Which system produced the response** — the **live interactive recruitment path**
   (`modules.bridge_poller.process_bridge_inbound` → `reply_policy path=recruitment` →
   `hermes_dispatch.generate_recruitment_reply`). **Not** Hermes/Harvest admin, **not**
   the recovery sweep (kill-switched, `live_send=False` throughout), **not** Meta.
9. **Exact queue row + delivery result for both sends** — 7808: `fazle_outbound_queue`
   `status=sent`, `sent_at=2026-09-01 22:02:35.82Z`, `attempts=1`, `source_bridge=bridge3`,
   `purpose=customer-reply:recruitment`. 7809: `status=dlq`, `attempts=2/3`,
   `last_error='http 500: {"success":false,"message":"Not connected to WhatsApp"}'`,
   `sent_at=NULL`.
10. **Did cursor advancement occur before or after history ingestion** — **after.** History
    sync wrote the rows first; the poller then processed 3 of them and advanced the cursor
    from `2026-08-31 12:31:04Z` to `2026-09-01 21:12:23Z` (the max processed timestamp).
    The planned pre-pairing cursor advance had not been done.
11. **Did duplicate / source-message protections fail** — no. `processed_bridge_messages`
    dedup worked (+3 only). `MANUAL_REPLY_GUARD` worked. The cooldown guard was
    *deliberately* overridden by the "active recruitment session = direct answer" rule —
    working as coded, but that rule is part of the design gap (§4.2).
12. **Was the device removed before or after the outbound send ack** — **after.** 7808
    `BRIDGE_SEND_SUCCESS` at 22:02:35Z; device-removed inferred ~22:02:40Z; 7809 failed
    `Not connected` at 22:02:45Z.
13. **Is the primary phone/account active, restricted, or banned** — **TEMPORARILY
    RESTRICTED** (corrected owner mobile-app evidence, 2026-09-02). WhatsApp shows
    "Temporarily restricted" with an ≈ 6 h countdown. **Not** confirmed as a permanent
    ban; not normal. Under Linked Devices the Bridge3 companion shows "Logged out".
    Classification: **PRIMARY MOBILE ACCOUNT: TEMPORARILY RESTRICTED** ·
    **BRIDGE3 COMPANION SESSION: LOGGED OUT / REMOVED**. `whatsmeow_device` = 0 on the VPS
    reflects the removed companion session. Re-evaluate the account only from post-countdown
    mobile evidence (banner gone, chats open, no permanent-ban message, Linked Devices
    available, number confirmed). _(An earlier version of this answer said "ACTIVE /
    NORMAL" — superseded.)_
14. **Was any Bridge1/Bridge2 data or routing affected** — **no.** B1/B2 `whatsmeow_device`
    = 1 each, services active, poller cursors advancing normally, all B1/B2 outbound in the
    window was routine admin/system traffic to the admin number. No auth/session file
    touched.

---

## 8. Minimal fix

> **2026-09-02 status (amended per owner instruction):** F1, F2, **F3 as an
> EXPLICIT STATE MACHINE (no timestamp auto-release)**, F4, and **F6 within the
> bridge ingestion scope** are **IMPLEMENTED** on branch
> `fix/bridge3-history-sync-quarantine-20260902` (core) +
> `fix/bridge3-history-sync-flag-20260902` (whatsapp-mcp). Unit tests green
> (§11/§15). **NOT deployed** — no push, no bridge-binary rebuild, no Bridge3
> restart, no pairing, no incident-data cleanup, no Phase-2. Awaiting owner
> review, then (separately) `scripts/restart_gate.sh --restart` + a reviewed
> bridge-binary rebuild for F1.
> **F5 (semantic-gate global block) is deliberately NOT implemented** — owner
> instruction: do not enable F5 globally.

### F3 — explicit pairing state machine (replaces the time-based quarantine)

Owner requirement: *a timestamp must never automatically authorise outbound
sending.* The `bridge3.quarantine_until` timestamp is **removed**. In its place,
the sanctioned runtime setting `<bridge>.pairing_state` holds one of:

| State | Bridge behaviour |
|---|---|
| _(unset)_ | normal — bridge never went through guarded pairing (Bridge1/Bridge2 today) |
| `UNPAIRED` | held (persist-only) |
| `HISTORY_SYNC_QUARANTINE` | held — **pairing always enters this state** |
| `LIVE_INBOUND_VERIFIED` | **still held / non-sending** — an operator checkpoint, not a release |
| `OUTBOUND_OWNER_APPROVED` | the **only** routing+sending state |
| _(any unrecognised value, incl. an ISO timestamp)_ | held (fail-safe) |

- `modules/bridge_poller/_bridge_pairing_state()` + `_bridge_outbound_held()` —
  no timestamp is read anywhere; release is only ever an explicit state change.
- Poller loop: while held, each inbound is `_save_raw_core_copy`-persisted then
  finalised `skipped / bridge_pairing_hold:<state>` and `_mark_processed`-deduped
  — **before** identity, media extraction, routing, drafts or any enqueue.
- `recruitment_recovery_sweep._decide_and_reply` also checks
  `_bridge_outbound_held(source_bridge)` and skips a held population.
- Transitions go through the **api-key-gated** `POST /api/wa/bridge/{bridge}/pairing-state`;
  reaching `OUTBOUND_OWNER_APPROVED` additionally requires
  `confirm == "RELEASE OUTBOUND <bridge>"` (a second deliberate signal).
  `GET` the same path to read current state + `outbound_allowed`.

### F6 — automated OTP / system-message suppression (bridge ingestion scope)

`shared/system_message.py::is_automated_system_message()` — a deterministic,
tight allowlist (Facebook/Instagram/WhatsApp/Google/… "`<code>` is your … code",
"your … verification code is …", one-time-password, "Your WhatsApp account is
successfully connected", new-login notices; Bengali-digit tolerant). A bare
numeric string is **not** flagged (could be a real answer). In the poller loop,
right after the pairing-hold check: a hit → `_save_raw_core_copy` keeps the raw
forensic row **with full provenance**, then finalise `skipped /
automated_system_message`, `_mark_processed`, `continue` — **no identity, no
`fazle_recruitment_sessions`, no `wbom_candidates`, no workflow-state write, no
reply.** A miss just falls through to normal routing (prefer false-negative).
Applies to every bridge's ingestion (an OTP is junk on any channel).

### Original F1/F2/F4 (unchanged from the first implementation)

### Implemented (F1–F4)

**F1 — bridge marks history-sync rows.** `whatsapp-mcp/whatsapp-bridge/main.go`:
new idempotent column `messages.from_history_sync INTEGER NOT NULL DEFAULT 0`
(same additive-migration pattern as `direct_path`). `StoreMessage(...)` gained a
trailing `fromHistorySync bool`; the live handler passes `false` (unchanged
`INSERT OR REPLACE`), `handleHistorySync` passes `true` and uses
`INSERT … ON CONFLICT(id,chat_jid) DO NOTHING` so a backfill can never
overwrite — or re-flag as historical — a row that already arrived live.
2 Go tests added; `go test ./...` green.

**F2 — poller excludes history rows from routing.**
`modules/bridge_poller/_fetch_new_messages`: `has_column(con,"messages",
"from_history_sync")` → when present, the inbound SELECT gets
`AND COALESCE(from_history_sync,0)=0`. History rows are still written to
`messages.db` (preserved, searchable) but never become "new inbound." A
`messages_skipped_total{reason="history_sync"}` metric + a
`[history_sync_guard]` log line report how many were held. Schema-tolerant:
older bridge binary (no column) → filter omitted, byte-identical to before.

**F3 — pairing quarantine.** New `modules/bridge_poller/_bridge_quarantine_until(bridge)`
reads the sanctioned runtime setting `<bridge>.quarantine_until` (ISO-8601) from
`fazle_runtime_settings`. While `now() < that`, the poll loop persists each
inbound (raw copy already written by `_save_raw_core_copy`), finalises the
canonical + queue rows to `skipped / bridge_pairing_quarantine`, calls
`_mark_processed()` (dedup), and `continue`s — **before** identity lookup, media
extraction, routing, draft creation or any outbound enqueue. `GAP_DIAG` now
carries `quarantine_persist_only=N` and logs for any bridge while quarantined.
Fail-open: an unreadable setting → not quarantined (logged), never freezes a
healthy bridge. The recovery sweep (`_decide_and_reply`) also checks
`_bridge_quarantine_until(source_bridge)` and skips quarantined populations —
defense-in-depth, since the sweep reads `wbom_whatsapp_messages` directly.

**F4 — boundary from metadata, not a blind jump.** With F1+F2 the poller cursor
only ever advances over *non-history* rows (`max_ts` of returned rows), and F3
`_mark_processed()` dedups anything seen during the settling window. So the
live-session boundary is "messages that arrive after the quarantine window,"
derived from real message timestamps + the history-sync flag — no `NOW()`
cursor slam is needed or done. The current stale cursor
(`2026-09-01 21:12:23Z`) is harmless post-fix: re-pair history rows are
`from_history_sync=1` (filtered), the 2 rows currently above it are
`is_from_me=1` (never routed).

### Deferred (F5–F6) — recommended, separate review

**F5 — semantic gate may block on high-confidence `no_match` for unknown
identity.** When `identity_role=unknown` **and** `semantic_gate` returns
`no_match` at confidence ≥ 0.9 **and** the message matches the
automated-notification/OTP shape, draft instead of auto-send. Narrow, additive.

**F6 — one junk inbound must not open a recruitment session.** Require a
minimal "looks like a person, not an automated code" check before
`fazle_recruitment_sessions` / `wbom_candidates` are created from a bridge3
cold inbound.

### Original design notes (for reference)

**F1 — History-sync events must never enter live business routing.**
In the bridge (`whatsapp-mcp/whatsapp-bridge/main.go`), `handleHistorySync` currently
`INSERT`s into the same `messages` table as live `events.Message`. Minimal change: add a
boolean column `from_history_sync` (default 0) and set it 1 for every row written by the
history-sync handler; **or** write history-sync rows to a separate `messages_history`
table. Preferred: the flag column — smallest change, preserves one queryable history.

**F2 — Poller ignores history-sync rows for routing, keeps them for preservation/search.**
In `modules/bridge_poller/_fetch_new_messages` (the `messages.db` SELECT), add
`AND COALESCE(from_history_sync, 0) = 0` to the inbound query. History rows are still
readable for search/preservation; they never become "new inbound." (Schema-tolerant: if
the column is absent, `COALESCE` keeps current behaviour.)

**F3 — Receive/persist quarantine mode for a freshly paired bridge.**
New sanctioned runtime setting `bridge3.quarantine_until` (timestamptz) — while
`now() < quarantine_until`, `process_bridge_inbound` for that bridge **persists** the
message (raw copy + `wbom_whatsapp_messages`) but **returns before any reply generation or
`outbound.enqueue`**. Interactive replies, recovery sweep, backlog processing and queue
flushing for that bridge are all blocked. Set it automatically at pair time (e.g.
pair + 30 min) and require an explicit clear.

**F4 — Establish the live-session boundary from message metadata, not a blind cursor jump.**
On the first clean poll after quarantine clears, set `bridge_poller_cursor[bridge3]` to
`max(timestamp)` **of non-history-sync rows only**, or to the pairing timestamp if that is
later — and log the chosen boundary with the transport id it came from. Never simply
`NOW()` without checking what is actually in `messages.db`.

**F5 — Let the semantic gate block on high-confidence `no_match` for unknown identity.**
When `identity_role=unknown` **and** `semantic_gate` returns `no_match` at confidence ≥ 0.9
**and** the message matches the automated-notification/OTP shape, suppress the auto-send
(draft instead). Narrow, additive; does not change behaviour for identified candidates or
genuine recruitment questions.

**F6 — Don't let a single junk inbound open a recruitment session.**
Require at least one message that passes a minimal "looks like a person, not an automated
code" check before `fazle_recruitment_sessions` / `wbom_candidates` are created from a
bridge3 cold inbound. (Blocks the "OTP became a candidate name" failure.)

**Data cleanup (owner-approved, separate):** void recruitment session **571** and candidate
**548**; leave `messages.db` history rows in place (they are the preserved history);
optionally tag the 5 `wbom_whatsapp_messages` rows (22794–22798) as incident artifacts.

---

## 9. Exact safe second-pairing procedure (gated — see the preconditions)

**Preconditions, ALL required before step 4:** the restriction countdown has fully
expired; the owner has re-verified the account from the mobile app (banner gone, chats
open, no permanent-ban message, Linked Devices available, number confirmed); F1–F4 is
reviewed **and deployed**; and the owner has **separately approved** the second pairing.
Do none of steps 1–3's production actions during the restriction countdown.

1. **Confirm account viability first** (§10 owner action) — the mobile app must show a
   usable account, not a restriction and not a permanent ban.
2. F1 (bridge `from_history_sync` flag) + F2 (poller filter) + F3 (quarantine setting) +
   F4 (metadata boundary): **implemented** on `fix/bridge3-history-sync-quarantine-20260902`,
   unit tests §11 green. Before deploy: `scripts/restart_gate.sh --check-only` clean.
3. Deploy core via **`scripts/restart_gate.sh --restart`** (never a bypass). Rebuild the
   bridge binary for F1; keep the current `whatsapp-bridge.candidate` as rollback.
4. **Before pairing:** set `bridge3.quarantine_until = now() + 45 min`. Confirm
   `auto_reply.recruitment` is still `false`, `recruitment_recovery_sweep_live_send` still
   kill-switched, `phone_block.447710173736` still set.
5. Owner pairs (foreground binary; QR in the owner's own terminal — never copied anywhere).
6. History sync runs → rows land in `messages.db` **flagged** `from_history_sync=1` → poller
   **persists but does not route** them. Verify: `wbom_whatsapp_messages platform='bridge3'`
   grows only by genuine *new* traffic; 0 outbound enqueued during quarantine; poller log
   shows `quarantine: persisted N, routed 0`.
7. After the quarantine window and a manual review of what synced, set the cursor via F4
   (metadata boundary, logged), then clear `bridge3.quarantine_until`.
8. Re-enable `auto_reply.recruitment=true` **only** after one clean live round-trip is
   observed (a real candidate message → correct reply) and the owner confirms.
9. Unblock `447710173736` only if the owner wants it handled at all (recommend: leave
   blocked — it is spam).

**No production WhatsApp test message may be sent for verification without a separate
explicit owner instruction, and the agent never chooses the recipient.**

---

## 10. Owner actions required

1. **Save the foreground pairing terminal output** (from the `whatsapp-bridge.candidate`
   run) to a file — it contains the only bridge-side record of the history-sync count and
   the device-removed/LoggedOut lines. Put it at
   `/home/azim/backups/fazle/bridge3-incident-20260902/owner-terminal.txt`. Mask nothing;
   it stays in that access-restricted dir.
2. **Account state — UPDATED 2026-09-02:** primary account **TEMPORARILY RESTRICTED**
   (≈ 6 h countdown), companion device "Logged out". Not a confirmed ban. **Record the
   exact observation time / screenshot timestamp and derived expiry** (this doc's top
   banner has blanks for them — do not invent). **After the countdown**, re-verify from
   the mobile app: banner gone · chats open · no permanent-ban message · Linked Devices
   available · number confirmed as the intended Bridge3 number.
3. **Confirm the number that was paired** — the original (2026-08-31-logged-out) Bridge3
   number, or a genuinely different new number? The synced history contains our own
   pre-ban Bridge3 conversations, which points to the same account being re-linked.
4. **Review** the F1–F4 fix (this doc §8 / §11). It is implemented + unit-tested; it is
   **not** deployed. Deployment (`scripts/restart_gate.sh --restart` + a bridge-binary
   rebuild for F1) needs **separate owner approval** — no production restart/deploy during
   the restriction countdown. Approve/amend the §5 data cleanup (void session 571 /
   candidate 548).
5. Keep Bridge3 `whatsapp-bridge3.service` **stopped**; keep `auto_reply.recruitment=false`,
   `recruitment_recovery_sweep_live_send` disabled, `phone_block.447710173736=true`.
6. **Do not pair again** until: the countdown has expired and the account re-verified;
   F1–F4 reviewed **and deployed**; Bridge3 configured to start in
   `bridge3.quarantine_until` receive/persist-only mode; every outbound path confirmed
   blocked; and the owner **separately approves** the second pairing.
7. Decide whether `META_APP_SECRET` rotation (from the recovery checkpoint) proceeds
   independently — it does not depend on Bridge3, but it does need a core restart, which is
   also on hold during the countdown.

---

## 11. Tests — required list → coverage

| Owner-required scenario | Covered by |
|---|---|
| mixed history + genuine live messages | `test_mixed_history_and_live_only_live_routes` |
| repeated history sync | `test_repeated_history_sync_still_excluded` |
| restart / reconnect replay (2,444-row shape) | `test_restart_reconnect_replay_the_2444_row_shape` |
| a timestamp cannot release quarantine | `test_no_timestamp_anywhere_in_release_logic` |
| no release without explicit owner action | `test_only_outbound_owner_approved_releases` + `test_unpaired_and_unknown_values_hold` + the endpoint's `confirm == "RELEASE OUTBOUND <bridge>"` gate |
| `LIVE_INBOUND_VERIFIED` remains non-sending | `test_live_inbound_verified_still_holds_outbound` |
| OTP / system messages create no recruitment entities | `test_f6_flags_automated_system_messages` + `test_hold_and_f6_branches_continue_before_routing` (F6 branch stops before identity / session / candidate / reply) |
| old queues do not flush | pairing-hold + F6 branches call `_mark_processed` and finalise `skipped` before any `outbound.enqueue`; recovery sweep `_decide_and_reply` returns early for a held bridge (`test_*` in `test_recruitment_recovery_sweep` unaffected; hold path asserted structurally) |
| new-core / old-bridge compatibility | `test_new_core_old_bridge_no_flag_column_unchanged` |
| new-bridge / old-core compatibility | `test_new_bridge_old_core_column_is_just_ignored` |
| Bridge1 / Bridge2 unchanged | `test_other_bridges_unaffected_by_bridge3_state` + F6 scoped `bridge_name == "bridge3"` + `_fetch_new_messages` filter is a no-op on flag-0 rows |
| Payment / FPE + business regression | existing suites — run at deploy time via `scripts/restart_gate.sh` checks 4/4b/4c (all PASS on the deployed branch; to be re-run on the fix branch at deploy) |

**Deferred integration test (owner review):** a full-poller-loop DB test that
asserts an OTP inbound on a live bridge3 produces **0** new
`fazle_recruitment_sessions` / `wbom_candidates` rows end-to-end (the unit +
structural tests prove the branch stops before those writes; a DB-level
end-to-end is a belt-and-suspenders add).

---

## 12. Evidence preserved

`/home/azim/backups/fazle/bridge3-incident-20260902/` (mode 700):
`messages.post-incident.db` + `whatsapp.post-incident.db` (online `.backup`,
`integrity_check` ok), `whatsapp3-tree.post-incident.tar.gz` (34 MB, byte-exact),
`fazle-core.log.snapshot`, `fazle-core-error.log.snapshot`, `bridge3.log.snapshot`,
`SHA256SUMS`. Pre-incident baseline in
`/home/azim/backups/fazle/bridge3-preban-20260901/`. Nothing in either the DBs, the
cursor, the queues, or the history-sync rows was modified or deleted.

---

## 13. Current state summary

- **Primary WhatsApp account:** **TEMPORARILY RESTRICTED** (owner mobile evidence, corrected
  2026-09-02) — ≈ 6 h countdown, not a confirmed permanent ban, not normal. Re-verify from
  the mobile app after the countdown.
- **Bridge3 companion session:** logged out / removed; `whatsmeow_device` = 0; session
  deleted; history rows present in `messages.db`; poller cursor `2026-09-01 21:12:23Z`.
  Service stopped. Contained by C2+C3+C4.
- **All production restart / deploy / pairing is on hold** until the countdown expires,
  the account is re-verified, F1–F4 is deployed, and the owner separately approves.
- **Bridge1 / Bridge2:** fully healthy and unaffected — devices 1/1, services active,
  cursors current.
- **fazle-core:** `/health` = `ok`; not restarted; `PHASE2_IDENTITY_RESOLVER_ENABLED` OFF;
  Phase-2 observation window still open (untouched by this incident).
- **Delivered blast radius:** one automated Bengali recruitment message to one automated
  third-party OTP number. No real candidate contacted. No payment/FPE/escort/attendance
  effect. No Meta message.
- **Blocked pending owner:** second pairing, `auto_reply.recruitment` re-enable, sweep
  re-enable, session 571 / candidate 548 cleanup, `META_APP_SECRET` rotation sequencing.

---

## 15. Test status (2026-09-02, after the state-machine + F6 amendment)

- `tests/unit/test_bridge_poller_history_sync_quarantine.py` — **37 tests pass**
  (F2 history filter incl. the 2,444-row shape; the full pairing-state machine incl.
  "timestamp cannot release" and "LIVE_INBOUND_VERIFIED still holds"; F6 positive +
  negative corpus; structural continue-before-routing).
- Bridge Go (`fix/bridge3-history-sync-flag-20260902`): `go test ./...` **pass**
  (2 tests: `from_history_sync` default 0 on the live path; history insert does not
  clobber or re-flag an existing live row).
- Adjacent suites green: `test_bridge_poller_processed_text_compat`, `_struct_bypass`,
  `_system_notification_guard`, `_system_echo_guard`, `_payment_relay_gate`,
  `_legacy_gate`, `_send_gate_timing_fix`, `_orphan_message_fix`,
  `test_recruitment_recovery_sweep`, `test_phase2_bridge_poller_regression`,
  `test_phase2_identity_resolver`, `test_phase2_p1_identity`,
  `test_media_placeholder_hardening`, `test_owner_contact_registry`,
  `test_group_ingestion_stage_e3` (source-scan assertions preserved — comments worded to
  avoid the literal routing-call tokens; no logic change).
- **Pre-existing failures, NOT caused by this change — verified by reproduction on the
  clean deployed branch `feat/phase2-inbound-identity-2026-09-01` with every fix change
  stashed:**
  - `test_bridge_poller_phone_block.py` (4 tests) fails **only** in a specific
    multi-file batch (`_struct_bypass + _system_notification_guard + _group_ingestion_stage_e3
    + _recruitment_recovery_sweep + _processed_text_compat + _owner_contact_registry +
    _phone_block`) with `AttributeError: 'module' object at modules.wa_chat_frontend has
    no attribute 'wa_chat_frontend'` — a `monkeypatch` import-path resolution corrupted by
    an earlier test in the chain. Passes in isolation and in every smaller combination.
    **Reproduces identically on the pristine deployed branch.**
  - `test_route_b_handoff.py` (2 tests) — no network to the Ollama cloud models in the
    sandbox (`ConnectError: Temporary failure in name resolution`).
  Both are tracked pre-existing test-isolation / environment issues (see `MEMORY.md`).

## 16. Fail-closed correction (owner pre-deployment review, 2026-09-02)

**Owner authoritative safety question:** *what happens when `bridge3.pairing_state` is
absent/unset?* — original answer: unset ⇒ normal (fail-OPEN, matching bridge1/bridge2).
**Corrected: for bridge3, unset ⇒ HELD (fail-CLOSED).** Commit `c42c587`:

| Condition on `bridge3.pairing_state` | Result |
|---|---|
| exactly `OUTBOUND_OWNER_APPROVED` | **released** — the only release |
| absent / row missing | HELD |
| empty / whitespace only | HELD |
| unknown / malformed value | HELD |
| a legacy ISO timestamp | HELD |
| settings storage unavailable | HELD |
| settings read raises an exception | HELD (`_PAIRING_READ_FAILED` sentinel) |
| `modules.wa_chat_frontend` import fails | HELD (`_pairing_held` wrapper is itself fail-closed for bridge3) |
| process restart while the key is still missing | HELD — `get_runtime_setting` does a live `fetch_one` every call; **no in-process cache** (asserted by `test_no_runtime_setting_cache_exists_for_pairing_state`) |
| API cannot persist the state (write fails) | value unchanged; unset/quarantine ⇒ HELD |

Held ⇒ **blocks** business routing · interactive auto-reply · outbound enqueue
(`modules.outbound.enqueue` refuses) · queue dispatch (`sweep_once` leaves the row
`pending`, no attempt consumed, no DLQ; failover to a held bridge re-raised) ·
recovery-sweep live send. **Raw inbound persistence continues.** **bridge1 / bridge2
byte-unchanged** — not in `_PAIRING_FAIL_CLOSED_BRIDGES`; unset ⇒ not held, read-error ⇒
not held.

## 17. Coordinated two-repo diff review (2026-09-02) — ALL PASS

core `fix/bridge3-history-sync-quarantine-20260902` (`ca510a0`) + whatsapp-mcp
`fix/bridge3-history-sync-flag-20260902` (`eb3a1ad`):

| Check | Evidence |
|---|---|
| history-sync column migration additive + idempotent | `ADD COLUMN from_history_sync INTEGER NOT NULL DEFAULT 0` gated by a `pragma_table_info` count (same as `direct_path`); `go test` covers fresh + no-clobber |
| old bridge / new core | `_fetch_new_messages` probes `has_column`; absent ⇒ filter omitted, byte-identical |
| new bridge / old core | old core's SELECT lists explicit columns, never names the new one; inert |
| no raw-message loss | F1 history insert is `INSERT … ON CONFLICT DO NOTHING`; F2 filters only the routing SELECT; `_save_raw_core_copy` persists before the hold/F6 branches |
| imported history cannot reach routing | F2 `AND COALESCE(from_history_sync,0)=0` |
| quarantine blocks newly-generated AND already-queued Bridge3 outbound | poller hold + `enqueue` refusal + `sweep_once` per-row hold |
| F6 cannot suppress a recruitment message with a bare number | `is_automated_system_message` has no bare-number rule; test covers `"25"`, `"1998"` |
| F6 applies to bridge3 only | `if bridge_name == "bridge3" and is_automated_system_message(text)` |
| Payment/FPE + Bridge1/2 unchanged | no `payment_relay*` / `fpe_*` / `payroll` file in the diff; bridge2 payment-relay enqueue (unset) → not held |
| no secret / session material committed | `scripts/secret_scan.sh` PASS on the full worktree; manual review clean |

`scripts/restart_gate.sh --check-only` on the fix branch: **run — OVERALL PASS (0 warnings)**,
all 10 checks; `db/.restart_gate_manifest` needed no new entry.

---

## 18. DEPLOYMENT — 2026-09-02 (owner-authorised)

**Both fix branches pushed (no force):**
`origin/fix/bridge3-history-sync-quarantine-20260902` (core, `e41965d`) ·
`origin/fix/bridge3-history-sync-flag-20260902` (whatsapp-mcp, `eb3a1ad`).

**Core deployed via `scripts/restart_gate.sh --restart`** (gate PASS both times):

| # | UTC | Core PID | HEAD | Notes |
|---|---|---|---|---|
| fix deploy | 2026-09-02 00:25:40Z | 3470442 → **3625932** | `06ea00b` | F1–F4 + fail-closed state machine + F6 |
| API-consistency follow-up | 2026-09-02 00:29:53Z | 3625932 → **3649821** | `e41965d` | `outbound_allowed` now computed from the authoritative `_bridge_outbound_held` |

Post-deploy: `/health` **ok** (all business probes) · `NRestarts` 0 · Phase-2 flag **absent
(OFF)** in `/proc/3649821/environ` · **Bridge1 PID 3640163 / Bridge2 PID 3688844 —
byte-unchanged, never restarted** · Bridge3 `inactive`.

**`bridge3.pairing_state` set through the sanctioned authenticated API** →
`HISTORY_SYNC_QUARANTINE`. Read back: API `GET /api/wa/bridge/bridge3/pairing-state` →
`{"pairing_state":"HISTORY_SYNC_QUARANTINE","outbound_allowed":false}`; authoritative
storage `fazle_runtime_settings['bridge3.pairing_state'] = 'HISTORY_SYNC_QUARANTINE'`
(updated 2026-09-02 00:26:20Z). bridge1/bridge2 → `pairing_state: null`, `outbound_allowed:
true` (unaffected).

**Reviewed Bridge3 bridge binary — built reproducibly, checksummed, staged (NOT started):**
- source: whatsapp-mcp `fix/bridge3-history-sync-flag-20260902 @ eb3a1ad`
- build: `CGO_ENABLED=1 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags="-buildid=" -o …` (go 1.23.4) — **two independent builds produced byte-identical output**
- artifact: `/home/azim/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge.candidate-b3fix-20260902`
  · **sha256 `36dee63f5c5802163355ca4238d064aab78ffe2fecc72eaf0f59ef1c5c08c781`** (also in the `.sha256` sidecar)
- `go test ./...` on that commit: **PASS**
- **rollback artifact preserved**: current shared candidate (`c1eaf67` build, sha256
  `25e2d7b025ccc6964f0404b21420d8630373a1527225e3aab71e0bb94de647ec`) copied to
  `/home/azim/backups/fazle/bridge3-incident-20260902/rollback-whatsapp-bridge.candidate-c1eaf67`
  (mode 600) + recorded in that dir's `SHA256SUMS`.
- the **shared `whatsapp-bridge.candidate` is UNCHANGED** — Bridge1/Bridge2 keep running it.

**Non-sending verification (Bridge3 outbound blocked):**
1. authoritative gate (`_bridge_outbound_held`, the same function the poller / `outbound.enqueue` / `sweep_once` / recovery sweep call) → `outbound_allowed = False`.
2. behavioural: `tests/unit/test_outbound_pairing_hold.py` (5) — real `enqueue()` returns `None` (refused) for a held bridge3; `sweep_once()` leaves an already-queued bridge3 row `pending`.
3. `fazle_outbound_queue` — **0** non-terminal bridge3 rows, 0 rows created since deploy.
4. `wbom_whatsapp_messages` platform=bridge3 — 0 rows since deploy.
5. `logs/fazle-core.log` since deploy — no `BRIDGE_SEND`/`OUTBOUND_SEND` for bridge3.
6. Bridge3 service `inactive`, `whatsmeow_device` 0.

**Other containment reconfirmed post-deploy:** `auto_reply.recruitment=false` ·
`recruitment_recovery_sweep_live_send` kill-switched · `phone_block.447710173736=true`.

### systemd wiring — DONE (owner, root, 2026-09-02)

The owner installed `/etc/systemd/system/whatsapp-bridge3.service.d/zz-b3fix.conf`
(`ExecStart=` reset + `ExecStart=…/whatsapp-bridge.candidate-b3fix-20260902`) and ran
`daemon-reload`. **Read-only verification (agent):**

| Check | Result |
|---|---|
| effective `whatsapp-bridge3.service` `ExecStart` | `…/whatsapp-bridge.candidate-b3fix-20260902` |
| `DropInPaths` | `canary.conf`, `zz-b3fix.conf` (zz- wins, alphabetical) |
| Bridge3 `ActiveState` / `SubState` | **inactive / dead** — not started |
| b3fix binary owner / mode / sha256 | `azim:azim` · `0755` · **`36dee63f5c5802163355ca4238d064aab78ffe2fecc72eaf0f59ef1c5c08c781`** ✓ |
| shared `whatsapp-bridge.candidate` sha256 | **`25e2d7b025ccc6964f0404b21420d8630373a1527225e3aab71e0bb94de647ec`** — unchanged |
| Bridge1 PID 3640163 `/proc/…/exe` sha256 | `25e2d7b0…` — still the shared candidate, `NRestarts` 0 |
| Bridge2 PID 3688844 `/proc/…/exe` sha256 | `25e2d7b0…` — still the shared candidate, `NRestarts` 0 |
| `bridge3.pairing_state` (API + `fazle_runtime_settings`) | `HISTORY_SYNC_QUARANTINE`, `outbound_allowed: false` |
| `auto_reply.recruitment` | `false` |
| `recruitment_recovery_sweep_live_send` | `is_active=false`, `kill_switch=true`, `effective_enabled=false` |
| `phone_block.447710173736` | `true` |
| core PID 3649821 `/health` · `NRestarts` · Phase-2 | `ok` · 0 · **absent from `.env` and `/proc/…/environ`** |

## 19. Pairing #2 under HISTORY_SYNC_QUARANTINE — 2026-09-02

Owner: restriction expired, account opens normally, "Link a device" available, the old
Bridge3 linked-device entry removed from the mobile list. `RESUME BRIDGE3 PAIRING` given.

**Pre-pairing read-only checks (all PASS, 2026-09-02 ~01:08Z):** `pairing_state =
HISTORY_SYNC_QUARANTINE` / `outbound_allowed = false` · `auto_reply.recruitment = false` ·
sweep `effective_enabled = false` · `phone_block.447710173736 = true` · bridge3 poller
cursor `2026-09-01 21:12:23Z` (unchanged) · 0 non-terminal bridge3 queue rows · effective
`ExecStart` = the b3fix binary (`36dee63f…`) · bridge3 `inactive`, port 8083 free ·
`whatsmeow_device` 0 · store dir `azim 775`, 92 G free · Bridge1/Bridge2 on the unchanged
shared candidate (`25e2d7b0…`), `NRestarts` 0 · core `/health` ok, Phase-2 OFF.

**Pre-pairing #2 snapshot:** `/home/azim/backups/fazle/bridge3-prepairing2-20260902/`
(`messages.pre-pairing2.db` 6,640 rows, `whatsapp.pre-pairing2.db` device 0 / lid_map 827,
`SHA256SUMS`, mode 700). `messages.db` byte-identical to the post-incident backup (nothing
touched Bridge3 since). **Postgres baseline to re-check unchanged after pairing:**
`wbom_whatsapp_messages` platform=bridge3 = **5,825** · `processed_bridge_messages` bridge3
= **3,377** · `wbom_candidates` = **546** · `fazle_recruitment_sessions` = **564**.

Why pairing #2 is safe this time (defence in depth): (1) `pairing_state =
HISTORY_SYNC_QUARANTINE` ⇒ every bridge3 inbound is persist-only, no routing / reply /
enqueue; (2) the b3fix binary flags every history-sync row `from_history_sync = 1` and F2
excludes them from the routing SELECT; (3) F6 suppresses OTP / system messages on bridge3;
(4) `bridge_poller_cursor[bridge3]` + `processed_bridge_messages` dedup still hold.

### Resume instruction (after pairing #2 is validated)

`pairing_state` **stays `HISTORY_SYNC_QUARANTINE`** through pairing and validation. Advancing
to `LIVE_INBOUND_VERIFIED` (still non-sending) and then `OUTBOUND_OWNER_APPROVED` (needs
`confirm == "RELEASE OUTBOUND bridge3"`) each require a **separate explicit owner
instruction**.

---

`CORE FIX DEPLOYED — FIXED BRIDGE3 BINARY WIRED BUT NOT STARTED — HISTORY_SYNC_QUARANTINE ACTIVE — NO PAIRING PERFORMED`
