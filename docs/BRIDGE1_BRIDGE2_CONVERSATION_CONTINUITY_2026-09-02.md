# BRIDGE1 / BRIDGE2 CONVERSATIONAL CONTINUITY — 2026-09-02

**`READY FOR OWNER TEST — NO LIVE MESSAGE SENT`**

Owner decision (2026-09-02): Bridge3 stays stopped/quarantined (new ≈16 h WhatsApp
restriction); **Bridge1 becomes the primary Facebook boosting / Click-to-WhatsApp
recruitment number**; recruitment + employee questions may arrive on any active
authenticated business bridge; the reply leaves through the **same** bridge that received
it; no broadcast, no cross-bridge duplicate; all deterministic structured-parser
workflows unchanged.

Author: Claude (VS Code agent). Session type: implementation + deployment.

---

## 1. Executive status

`READY FOR OWNER TEST — NO LIVE MESSAGE SENT`

Bridge1/Bridge2 conversational continuity is **deployed dark-verified**: the F6
OTP/system-message suppression now covers Bridge1/Bridge2 (one-line poller change, gate
PASS, `fazle-core` PID **4169686**), and the sanctioned runtime toggles
`auto_reply.recruitment` + `auto_reply.employee` are **`true`** (applied live, no restart).
Bridge3 is provably still held (`outbound_allowed=false` under the very-same
`_bridge_outbound_held` the poller/queue use, with `auto_reply.recruitment=true` global).
Bridge1/Bridge2 processes were **not restarted**. No WhatsApp/Meta message was sent.
Actual end-to-end live behaviour is **not yet exercised** — the Facebook ad still points at
the restricted Bridge3 number and there was no organic recruitment traffic in the window.
The owner-controlled manual test plan (§12) is the next step.

## 2. Bridge3 — frozen state (unchanged this session)

| Item | State |
|---|---|
| `whatsapp-bridge3.service` | **inactive** (systemd `enabled` for boot, but `bridge3.pairing_state` fail-closes it regardless — see §8) |
| `bridge3.pairing_state` | `HISTORY_SYNC_QUARANTINE` (API + `fazle_runtime_settings`) |
| `outbound_allowed` (authoritative `_bridge_outbound_held`) | **false** |
| Bridge3 recovery live-send | OFF (`recruitment_recovery_sweep_live_send` kill-switched) |
| Bridge3 recruitment auto-reply | blocked by the pairing-state hold (runs before the auto-reply gate) |
| OTP/spam number block | `phone_block.447710173736 = true` |
| QR / pairing / message / state transition / incident-data cleanup | **none** |
| new ≈16 h restriction (owner-provided) | recorded; **no exact expiry invented**. `tail -f` on the bridge log is read-only and did **not** cause it; the likely trigger remains the earlier fresh-link + immediate automated outbound. |

## 3. Current Bridge1 / Bridge2 roles

| Bridge | Account (masked) | Role |
|---|---|---|
| Bridge1 (`whatsapp-bridge.service`, :8081) | `880195…` | HR + **now the Facebook Click-to-WhatsApp recruitment entry number** |
| Bridge2 (`whatsapp-bridge2.service`, :8082) | `880188…` | OPS / Super-Admin / accountant / Payment-relay authority; also answers ordinary recruitment/employee questions that land there |
| Bridge3 (`whatsapp-bridge3.service`, :8083) | — (logged out) | REC2 recruitment — **frozen**, restored later under a separate owner-approved flow |

## 4. Read-only audit findings (before any change)

| Question | Finding |
|---|---|
| Is `auto_reply.recruitment` per-bridge or global? | **Global** — `wa_chat_frontend.get_effective_auto_reply(role)` keys on the identity **role**, not the bridge. It gates the poller's `_recruit_gate` for every bridge. |
| Did my 2026-09-01 22:11 containment (`auto_reply.recruitment=false`) actually stop Bridge1/Bridge2? | **Yes.** `fazle_outbound_queue` shows **79** `bridge1` + **9** `bridge2` `customer-reply` rows in the 4 days before, **0** after 22:11Z. So Bridge1/Bridge2 recruitment conversational reply was **already working** and my containment turned it off globally. |
| Same-source reply affinity on Bridge1/Bridge2? | **Already correct.** Every customer-facing `outbound.enqueue` in the poller inbound path uses `source_bridge = bridge_name` (the inbound bridge). `LOOP_GUARD` diverts a reply to another bridge's own number to a draft. No fan-out / broadcast. |
| Can a global toggle for Bridge1 accidentally enable Bridge3? | **No.** The Bridge3 pairing-state hold (`_bridge_outbound_held("bridge3")` → held on `HISTORY_SYNC_QUARANTINE`) short-circuits in the poller **before** the auto-reply gate, and `outbound.enqueue` independently refuses a held Bridge3 enqueue. Proven by `test_bridge3_held_even_with_global_recruitment_and_employee_true`. This **is** the "per-bridge effective-control layer" the directive asks for — built in the 2026-09-02 fail-closed commit. |
| Do unknown contacts default to recruitment? | Unknown → `_id_role_to_setting_key` → `"all"` → gate passes via `auto_reply.all=true`; `reply_policy` (family `whatsapp` for bridge1/2/meta) produces a recruitment-flavoured reply. This is the intended behaviour for a recruitment entry number. |
| OTP / system-notification handling on Bridge1/Bridge2? | **F6 was bridge3-only** — an OTP on Bridge1 would have created a phantom candidate + session + reply (the exact 2026-09-02 Bridge3 failure). **Fixed this session** — F6 extended to bridge1/bridge2/bridge3. |
| `auto_reply.employee`? | `false` since 2026-06-15. `task13_employee_authoritative` and `hermes_customer_employee` feature flags are **active** (dispatch authorised), but the poller's `auto_reply.employee` gate blocks delivery. `employee_conversation` writes only `fazle_payment_drafts` / `fazle_draft_replies` — **no direct financial write**; the frozen Payment/FPE relay path is entirely separate. |
| Outbound failover for the customer path? | Customer-reply enqueues set `fallback_channel=None`. `sweep_once` additionally refuses failover **to** a pairing-held bridge (re-raises into normal backoff). No silent cross-send. |

## 5. Routing matrix (policy — verified/implemented)

| Inbound | Sender / message | Outbound | Path |
|---|---|---|---|
| Bridge1 | genuine recruitment applicant / unknown human | **Bridge1** (one reply) | recruitment conversational |
| Bridge1 | identified employee, employee question | **Bridge1** | employee conversational (informational / payment-request draft) |
| Bridge2 | genuine recruitment applicant | **Bridge2** | recruitment conversational — unless the sender/message is admin / accountant / Payment-FPE / a structured workflow (those keep their existing authority) |
| Bridge2 | identified employee | **Bridge2** | employee conversational |
| Bridge1/Bridge2 | admin / accountant / attendance / escort / release-slip / payment-format / Hermes-relay | unchanged | **deterministic structured parser / established authority — untouched** |
| Bridge1/Bridge2/Bridge3 | OTP / login-code / "account connected" / new-login notice | **none** — persisted for forensics only, no identity / session / candidate / reply | F6 suppress |
| Bridge3 | anything | **held** (persist-only) regardless of global toggles | `HISTORY_SYNC_QUARANTINE` |
| Meta (WhatsApp/Messenger/Instagram) | unchanged | unchanged | its own webhook path + guards |

Structured-parser messages take precedence over general recruitment/employee conversation
handling (unchanged — the poller runs the operational-router / structured branches before
the conversational `_recruit_gate` block).

## 6. Files / settings changed

### Code (branch `feat/bridge1-2-conversation-continuity-20260902`)

| File | Change |
|---|---|
| `modules/bridge_poller/__init__.py` | F6 gate `bridge_name == "bridge3"` → `bridge_name in ("bridge1", "bridge2", "bridge3")` (Meta excluded). **One line + comment.** No other logic touched. |
| `tests/unit/test_bridge1_bridge2_conversation_continuity.py` | new — F6-covers-3-bridges, same-source affinity (no hard-coded bridge, no fan-out), Bridge3 held with globals true, enqueue still refuses Bridge3 |
| `tests/unit/test_bridge_poller_history_sync_quarantine.py` | structural F6 assertion updated for the 3-bridge tuple |

### Runtime settings (sanctioned `PATCH /api/wa/settings`, no restart — read live)

| Setting | Before | After | Effect |
|---|---|---|---|
| `auto_reply.recruitment` | `false` (my 2026-09-01 containment) | **`true`** | restores Bridge1/Bridge2 recruitment conversational auto-reply (was working before the incident). Bridge3 stays held. |
| `auto_reply.employee` | `false` (since 2026-06-15) | **`true`** | enables Bridge1/Bridge2 employee conversational replies (dispatch already authorised via `task13_employee_authoritative`; draft-only, no financial write). Bridge3 stays held. |
| `auto_reply.all` | `true` | `true` (unchanged) |
| every other `auto_reply.<role>` | `false` | `false` (unchanged) |

**No global feature flag was enabled.** Phase-2 stays OFF. Recovery sweep stays OFF.

## 7. Tests

**New / changed** (isolated, existing fixtures, no live message):
- `tests/unit/test_bridge1_bridge2_conversation_continuity.py` (new) — F6 covers
  bridge1/bridge2/bridge3 (not Meta); customer-reply enqueues use `source_bridge=bridge_name`
  (no hard-coded bridge, no fan-out loop); **Bridge3 held even with
  `auto_reply.recruitment` + `auto_reply.employee` = true**; `outbound.enqueue` still refuses
  a held Bridge3 with the globals on.
- `tests/unit/test_bridge_poller_history_sync_quarantine.py` — structural F6 assertion
  updated to the 3-bridge tuple.

**Regression — all green:**
- 403-test batch: `admin_commands`, `rbac`, `drafts_router_mounted`, 6 recruitment suites
  (`bridge3_recruitment_golden_baseline` + cooldown / answer-first / salary-context /
  age-override / bengali-digits), `payment_relay_accounting`,
  `bridge_poller_payment_relay_gate`, `recruitment_recovery_sweep`,
  `history_sync_quarantine`, `outbound_pairing_hold`, `bridge1_bridge2_conversation_continuity`,
  `group_ingestion_stage_e3`, `struct_bypass`, `system_notification_guard`,
  `system_echo_guard`, `media_placeholder_hardening`, `owner_contact_registry`,
  `phase2_bridge_poller_regression`, `phase2_identity_resolver`.
- 193-test batch: `attendance`, `attendance_crud`, `escort_lifecycle`, `escort_module`,
  `escort_completion_company_name`, `admin_commands_escort_review`,
  `escort_client_identity_convergence`.
- `scripts/restart_gate.sh --restart` → **OVERALL PASS (0 warnings)**, all 10 checks,
  RESTART: SUCCESS. (Check 4b `Timeout >30.0s` flaked twice under box load1 ≈ 5–7 — a
  pre-existing LLM-network/CPU-contention artifact documented for this suite, unrelated to
  this change; the same batch passes in isolation and the gate passed cleanly at load1 2.79.
  The gate was **not** weakened.)

Pre-existing failures NOT introduced here (verified on the clean deployed branch):
`test_route_b_handoff.py` (2, no Ollama-cloud network in the sandbox);
`test_bridge_poller_phone_block.py` (4, a specific multi-file monkeypatch-pollution batch).

## 8. Bridge3 boot-safety

`whatsapp-bridge3.service` is systemd `enabled` (would start on reboot). **Not changed**
(needs root, not authorised). It is not a risk: on start with `whatsmeow_device = 0` the
bridge enters the QR-pair loop and connects nothing; and the core-side
`bridge3.pairing_state = HISTORY_SYNC_QUARANTINE` fail-closes every Bridge3 inbound and
refuses every Bridge3 outbound regardless of whether the bridge process is up. The
existing fail-closed quarantine is authoritative.

## 9. Payment / FPE freeze confirmation

**Untouched.** No file under `modules/payment_relay*`, `modules/fpe_payroll_engine`, or the
`owner-accountant-payment-relay` path is in this diff. `fpe_cash_transactions` remains the
sole canonical ledger; exact payment-format text still relays per the established
Bridge1/Bridge2 authority; successful owner/admin→accountant delivery remains the
authoritative completion event; no LLM interpretation enters that path; inactive-employee
lookup still works; duplicate/idempotency/ledger protections unchanged.
`employee_conversation` (now reachable via `auto_reply.employee`) writes only
`fazle_payment_drafts` (admin-reviewed) / `fazle_draft_replies` — never a cash txn or
ledger row.

## 10. Structured-parser regression status

**Untouched — all green.** attendance (`test_attendance`, `test_attendance_crud`), escort
(`test_escort_lifecycle`, `test_escort_module`, `test_escort_completion_company_name`,
`test_admin_commands_escort_review`, `test_escort_client_identity_convergence`), admin
(`test_admin_commands`), RBAC (`test_rbac`), recruitment golden baseline + 5 recruitment
suites, Payment-relay (`test_payment_relay_accounting`,
`test_bridge_poller_payment_relay_gate`), restart-gate check 4/4b/4c. The diff contains no
attendance / escort / admin / payment / FPE / parser file.

## 11. Facebook ad — OWNER ACTION

The software side is ready: Bridge1 receives inbound, resolves identity, routes recruitment
conversationally, replies through Bridge1, and suppresses OTP/system noise. No autonomous
Meta/ad mutation was made (no authenticated Meta Marketing API capability in this
environment, and it would need separate owner authorisation regardless).

**To point the Facebook Click-to-WhatsApp ad at Bridge1:**
1. In **Meta Ads Manager / Business Suite**, open the ad set (or the WhatsApp
   Click-to-Message ad) and change the **WhatsApp destination / connected WhatsApp number**
   from the old (restricted) Bridge3 number to the **Bridge1 number** (`880195…`, the
   account currently linked to `whatsapp-bridge.service`).
2. If the ad was previously **rejected/restricted** because of the old destination, changing
   the number may not auto-clear it — you may need to **duplicate the ad** with the new
   destination or submit it for a fresh review. Do not assume the existing ad goes live.
3. Before switching ad spend on, confirm with the agent (read-only): Bridge1 healthy;
   Bridge1 can receive new unknown inbound; same-source reply routing correct;
   `auto_reply.recruitment = true`; F6 active on Bridge1; no bulk/unsolicited messaging.
4. **The agent will not claim the ad is live without direct evidence** — report the ad
   status back after you change it.

No bulk messaging, no automatic outreach to old contacts, and no unsolicited first-contact
message is introduced by this change — Bridge1 only ever *replies* to an inbound it
received.

## 12. Manual live-test plan (owner-controlled phones — PREPARED, NOT EXECUTED)

Send from an owner-controlled phone that is **not** a known admin/employee/accountant
number (so it resolves as unknown/candidate), unless the test says otherwise.

| # | Sender type | Send to | Message | Expected routing | Expected outbound | Expected persistence | Must NOT happen |
|---|---|---|---|---|---|---|---|
| 1 | unknown human, genuine recruitment | **Bridge1** | e.g. "আমি Security Guard পদে চাকরি করতে চাই" | recruitment conversational | **exactly one reply via Bridge1** | inbound + outbound rows on `platform='bridge1'`; a `fazle_recruitment_sessions` row opens | no Bridge2/Bridge3 reply; no duplicate; no financial write |
| 2 | known employee (use a real employee's phone) | **Bridge1** | e.g. "আমার এই মাসের বেতন কবে হবে?" | employee conversational | **one reply via Bridge1** (informational, or a `fazle_payment_drafts` row for admin review) | inbound + outbound on `platform='bridge1'` | employee NOT turned into a new candidate; no cash txn / ledger row; no Bridge2/3 reply |
| 3 (optional) | unknown human, genuine recruitment | **Bridge2** | e.g. "চাকরির জন্য আবেদন করতে চাই" | recruitment conversational | **one reply via Bridge2** | rows on `platform='bridge2'` | not misrouted to admin; no Bridge1/3 reply |
| 4 (only if separately authorised) | admin, structured format | **Bridge2** | one existing **approved** structured-parser command in its exact format | the established deterministic parser | whatever that parser already does | its existing row(s) | no change vs. before this session |

**Do not** include a Payment/FPE relay as a live test just for verification. **Do not** send
any Meta/Messenger/Instagram message. After each test, the agent verifies read-only (queue,
`wbom_whatsapp_messages`, `fazle_recruitment_sessions`, `fazle_payment_drafts`, logs) that
the "expected" happened and the "must not" did not.

## 13. Deployment / restart evidence

| Repo / branch | Commits | Pushed |
|---|---|---|
| core `feat/bridge1-2-conversation-continuity-20260902` | `21015f1` F6→b1/b2 · `bb4c79c` doc | (pushed at end) |

`scripts/restart_gate.sh --restart` — all 10 checks PASS (0 warnings), **RESTART: SUCCESS**:

| | before → after |
|---|---|
| `fazle-core` PID | 3649821 → **4169686** · `NRestarts` 0 |
| started | 2026-09-02 06:36:18 CEST |
| branch / HEAD | `feat/bridge1-2-conversation-continuity-20260902` / `bb4c79c` |
| `/health` | ok → **ok** (all business probes) |
| Phase-2 flag | absent → **absent (OFF)** in `/proc/4169686/environ` |
| Bridge1 PID / Bridge2 PID | 3640163 / 3688844 — **unchanged, not restarted** |
| Bridge3 | inactive → inactive · `pairing_state=HISTORY_SYNC_QUARANTINE` · `outbound_allowed=false` |

Runtime settings via `PATCH /api/wa/settings` (live, no restart):
`auto_reply.recruitment` false → **true**, `auto_reply.employee` false → **true**.

**Post-deploy verification (read-only, non-sending):**
- Bridge3 `outbound_allowed=false` via the authoritative `_bridge_outbound_held` **with
  `auto_reply.recruitment=true` global** — the exact proof required. Bridge1/Bridge2
  `outbound_allowed=true`, `pairing_state=null`.
- recovery sweep `effective_enabled=false`; `phone_block.447710173736=true`; bridge3 poller
  cursor `2026-09-01 21:12:23Z` unchanged.
- 0 Bridge3 rows in `fazle_outbound_queue` / `wbom_whatsapp_messages` since the deploy
  window; the only 2 queue rows in the window are routine `draft-only-contact-review`
  admin notifications on Bridge1. No `customer-reply` on any bridge since the toggles were
  applied (no organic traffic; ad still restricted).
- Payment/FPE: latest relay txn `id 4510` / `payment-relay-send:7747:0` /
  `2026-09-01 17:03Z` — **identical to the pre-deploy baseline**.
- No WhatsApp/Meta message sent during deployment. Pollers fresh (age ≈ 14 s). No new
  errors in `logs/fazle-core.log` since the restart.

## 14. Rollback

- Code: `git checkout fix/bridge3-history-sync-quarantine-20260902` (→ `8ce05d8`) then
  `scripts/restart_gate.sh --restart`.
- Settings: `PATCH /api/wa/settings {"auto_reply.recruitment": false, "auto_reply.employee": false}`
  (live, no restart).

## 15. Remaining owner actions

1. **Facebook ad** (§11) — change the Click-to-WhatsApp destination from the old Bridge3
   number to the Bridge1 number (`880195…`); expect a fresh review / a new ad. Confirm the
   status back to the agent.
2. **Manual live test** (§12) — run tests 1–3 from owner-controlled phones; the agent
   verifies each read-only. Test 4 (structured parser) only if you separately authorise it.
3. **Bridge3** — still frozen. Pairing #2 stays blocked until: the new ≈16 h restriction
   expires, the account is re-verified from the mobile app, and you give
   `RESUME BRIDGE3 PAIRING` again. `pairing_state` stays `HISTORY_SYNC_QUARANTINE`;
   advancing to `LIVE_INBOUND_VERIFIED` / `OUTBOUND_OWNER_APPROVED` each need a separate
   explicit instruction.
4. From earlier: incident-data soft-invalidation (spurious `fazle_recruitment_sessions`
   571 / `wbom_candidates` 548); MCP server reload onto `8a3f7f0`; prune the old-secret
   `.env.bak.*` snapshots.
5. Consider re-running the pre-existing flaky-suite fixes (`test_route_b_handoff`,
   `test_bridge_poller_phone_block` multi-file isolation) — tracked, not ours.
