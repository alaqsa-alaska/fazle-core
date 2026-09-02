# BRIDGE1 / BRIDGE2 CONVERSATIONAL CONTINUITY — 2026-09-02

**`<STATUS — filled at the end>`**

Owner decision (2026-09-02): Bridge3 stays stopped/quarantined (new ≈16 h WhatsApp
restriction); **Bridge1 becomes the primary Facebook boosting / Click-to-WhatsApp
recruitment number**; recruitment + employee questions may arrive on any active
authenticated business bridge; the reply leaves through the **same** bridge that received
it; no broadcast, no cross-bridge duplicate; all deterministic structured-parser
workflows unchanged.

Author: Claude (VS Code agent). Session type: implementation + deployment.

---

## 1. Executive status

_(filled at the end)_

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

_(filled with the run results)_

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

_(filled with results — attendance / escort / admin / RBAC / recruitment / Payment-relay suites)_

## 11. Facebook ad — OWNER ACTION

_(§ below)_

## 12. Manual live-test plan (owner-controlled phones — DO NOT auto-execute)

_(§ below)_

## 13. Deployment / restart evidence

_(filled)_

## 14. Rollback

- Code: `git checkout fix/bridge3-history-sync-quarantine-20260902` (→ `8ce05d8`) then
  `scripts/restart_gate.sh --restart`.
- Settings: `PATCH /api/wa/settings {"auto_reply.recruitment": false, "auto_reply.employee": false}`
  (live, no restart).

## 15. Remaining owner actions

_(filled)_
