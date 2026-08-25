"""Periodic unanswered Recruitment/Employee conversation recovery sweep.

Phase 7, 2026-08-25 Hermes Owner-Authority recovery pass. Confirmed
genuinely missing in this session's own Phase 4 audit -- the only prior
artifact was scripts/recruitment_lead_recovery_analyzer.py, a standalone,
read-only, never-invoked report generator (never sends, never touched by
any scheduler or live path). This is the real thing.

Design principle (per the recovery-pass directive): reuse every existing
live-pipeline component rather than building a second, parallel routing/
messaging system --

  - modules.intent.classify                          (lightweight intent hint)
  - modules.identity_brain.detect_identity            (identity)
  - modules.owner_contact_registry.is_owner_managed_contact  (protection)
  - modules.recruitment_flow.recruitment_eligibility  (eligibility, UNCHANGED)
  - modules.bridge_poller._is_draft_always            (protected-workflow/risk gate)
  - shared.draft.should_generate_draft                (admin-already-replied guard)
  - modules.message_router.get_recent_history / get_contact_context (history/context)
  - modules.hermes_dispatch.generate_recruitment_reply /
    build_recruitment_context_with_directives          (recruitment reply -- SAME
    function the live interactive path calls)
  - modules.hermes_dispatch.generate_reply /
    build_employee_context_with_directives              (employee reply -- SAME)
  - modules.bridge_poller._save_draft                  (draft creation)
  - modules.outbound.enqueue                            (idempotent send)

No new intent classification, no new domain-routing logic, no new reply
generation -- this only decides WHICH stale conversations to revisit and
hands each one to the exact same functions the live pipeline already
trusts.

Activation: guarded behind `recruitment_recovery_sweep_live_send` (DB
feature flag via modules.preflight_guard, default False) -- mirrors
modules.scheduler.job_daily_payroll's own "dry-run/preview until Owner
sign-off" pattern exactly. While the flag is off, every run still
executes the FULL analysis (identifies candidates, resolves identity,
checks every gate, composes a real reply) but only logs + sends one
admin preview digest instead of actually messaging candidates/employees
-- this lets the real decision logic be observed safely before enabling
live-send, per the recovery-pass directive's explicit "dry-run/test mode
first" instruction.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database import fetch_all

log = logging.getLogger("fazle.recruitment_recovery_sweep")

# How far back to look for a stale unanswered conversation, and how
# recently is "too fresh" (avoid racing the live interactive pipeline,
# which already handles anything from the last few minutes).
_LOOKBACK_HOURS = 48
_MIN_STALE_MINUTES = 15
# Bound the amount of work/spend a single sweep run can do.
_MAX_CANDIDATES_PER_RUN = 20
# Roles this sweep will ever attempt a semantic reply for -- matches
# recruitment_eligibility()'s own role gate for recruitment, plus
# employee/supervisor for the employee informational path. Every other
# role (accountant, escort_client, admin, etc.) is protected-workflow
# territory this sweep does not touch at all.
_RECRUITMENT_ROLES = ("unknown", "new_lead", "candidate")
_EMPLOYEE_ROLES = ("employee", "supervisor")


async def _find_stale_unanswered(limit: int) -> list[dict]:
    """Latest inbound per sender, where no outbound to that sender exists
    after it, within the lookback window. Read-only, wbom_whatsapp_messages
    only (the same table modules.conversation_canonical already reads)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=_LOOKBACK_HOURS)
    until = now - timedelta(minutes=_MIN_STALE_MINUTES)
    rows = await fetch_all(
        """
        SELECT DISTINCT ON (i.sender_number)
            i.sender_number, i.message_id, i.received_at, i.message_body,
            i.workflow_triggered, i.platform
        FROM wbom_whatsapp_messages i
        WHERE i.direction = 'inbound'
          AND i.received_at > $1
          AND i.received_at < $2
          AND NOT EXISTS (
              SELECT 1 FROM wbom_whatsapp_messages o
              WHERE o.sender_number = i.sender_number
                AND o.direction = 'outbound'
                AND o.received_at > i.received_at
          )
        ORDER BY i.sender_number, i.received_at DESC
        LIMIT $3
        """,
        since, until, limit,
    )
    return [dict(r) for r in rows]


async def _decide_and_reply(row: dict, *, live_send: bool) -> dict:
    """One candidate conversation -> a logged decision dict. Never raises
    (any per-candidate error is caught and recorded, so one bad row can't
    abort the whole sweep)."""
    phone = row.get("sender_number") or ""
    text = (row.get("message_body") or "").strip()
    msg_id = row.get("message_id")
    received_at = row.get("received_at")
    # 2026-08-25 live-restart-caught bug fix: wbom_whatsapp_messages has
    # no source_bridge column -- the bridge name is stored in `platform`
    # (see modules.bridge_poller._save_raw_core_copy's own INSERT).
    source_bridge = row.get("platform") or "bridge3"
    decision: dict[str, Any] = {"phone": phone, "message_id": msg_id, "action": "skip"}
    if not phone or not text:
        decision["reason"] = "empty_phone_or_text"
        return decision

    try:
        from modules.identity_brain import detect_identity
        identity = await detect_identity(phone, text)
        role = identity.get("identity_role") or "unknown"
        display_name = (identity.get("display_name") or "").strip()

        from modules.owner_contact_registry import is_owner_managed_contact
        if is_owner_managed_contact(phone):
            decision["reason"] = "owner_managed_contact"
            return decision

        if role not in _RECRUITMENT_ROLES and role not in _EMPLOYEE_ROLES:
            decision["reason"] = f"role_not_in_sweep_scope:{role}"
            return decision

        from modules.bridge_poller import _is_draft_always
        draft_always = await _is_draft_always(phone, role, display_name)

        from shared.draft import should_generate_draft
        chat_jid = phone if "@" in phone else f"{phone}@s.whatsapp.net"
        # 2026-08-25, WS2 live-sample verification: should_generate_draft()'s
        # OWN default historical_cutoff_hours is 2 -- a value tuned for its
        # original caller (skip drafting replies to freshly-imported WA
        # history), not for this sweep's intentionally much wider
        # _LOOKBACK_HOURS=48 window. Calling it unqualified silently capped
        # every candidate older than 2h to the SAME "already_replied" skip
        # as a genuine prior reply -- confirmed against a real 20-candidate
        # sample: 19/20 were >2h old and 100% of them were skipped this way,
        # while the one candidate under 2h old was the only one that reached
        # real reply generation. That made "already_replied" true for the
        # wrong reason for the large majority of the sweep's actual target
        # population (stale 2h-48h conversations -- the ones that most need
        # recovery) and silently shrank the sweep's real effective window to
        # ~15min-2h. Passing this sweep's own lookback aligns the reused
        # guard's historical cutoff with the window _find_stale_unanswered()
        # already enforces at the SQL level, so the guard's remaining
        # (fpe_wa_messages is_from_me) check is what actually decides
        # "already_replied" going forward.
        ok_to_consider = await should_generate_draft(
            chat_jid, received_at, historical_cutoff_hours=_LOOKBACK_HOURS
        )
        if not ok_to_consider:
            # 2026-08-25, Workstream 2 live-sample verification: label
            # corrected from "admin_already_replied" to "already_replied".
            # should_generate_draft() checks ANY outbound (is_from_me=TRUE)
            # after the inbound -- confirmed via a real live sample
            # (8801306116146) that this includes AI/system-generated
            # replies, not only a human admin's manual reply. The
            # underlying safety property (never double-reply) was already
            # correct; only this label was misleading about who/what sent
            # the prior reply.
            decision["reason"] = "already_replied"
            return decision

        from modules.intent import classify as classify_intent
        intent = classify_intent(text)

        from modules.message_router import get_recent_history
        history_lines = await get_recent_history(phone, 10)
        history = "\n".join(history_lines)

        reply: Optional[str] = None
        autosend = False

        if role in _RECRUITMENT_ROLES:
            from modules.recruitment_flow import recruitment_eligibility
            elig = await recruitment_eligibility(phone, text, role=role, intent=intent, source=source_bridge)
            if not elig.get("eligible"):
                decision["reason"] = f"not_recruitment_eligible:{elig.get('reason')}"
                return decision
            autosend = bool(elig.get("autosend")) and not draft_always
            from modules.hermes_dispatch import (
                generate_recruitment_reply,
                build_recruitment_context_with_directives,
            )
            kb_context = await build_recruitment_context_with_directives(text)
            reply = await generate_recruitment_reply(
                phone=phone, text=text, source=source_bridge,
                kb_context=kb_context, contact_context="", history=history,
            )
        else:  # employee/supervisor
            autosend = not draft_always
            from modules.hermes_dispatch import generate_reply, build_employee_context_with_directives
            db_ctx = await build_employee_context_with_directives(text)
            reply = await generate_reply(
                text, intent, db_ctx, history=history, role=role, source=source_bridge,
                sender_phone=phone,
            )

        if not reply:
            decision["reason"] = "no_reply_generated"
            return decision

        decision["reply_preview"] = reply[:200]
        decision["role"] = role
        decision["autosend"] = autosend

        if not live_send:
            decision["action"] = "dry_run_preview"
            return decision

        if autosend:
            from modules import outbound
            key = f"recovery-sweep-{phone}-{msg_id}"
            qid = await outbound.enqueue(
                phone, reply, source_bridge=source_bridge, fallback_channel=None,
                purpose="recovery_sweep_reply", idempotency_key=key,
                meta={"recovery_sweep": True, "intent": intent},
            )
            decision["action"] = "sent" if qid else "dedup_skipped"
            decision["queue_id"] = qid
        else:
            from modules.bridge_poller import _save_draft
            await _save_draft(
                source_bridge, phone, reply, intent,
                sender_name=display_name, sender_message=text,
            )
            decision["action"] = "drafted"
        return decision
    except Exception as exc:  # noqa: BLE001 -- one bad candidate must not abort the sweep
        log.warning("[recovery_sweep] candidate phone=%s error=%s", phone, exc)
        decision["action"] = "error"
        decision["error"] = str(exc)
        return decision


async def run_recovery_sweep() -> dict:
    """Entry point for the scheduler job (and manual `trigger_job`/admin
    RUN JOB invocation). Always returns a summary dict; never raises."""
    # 2026-08-25 live-restart-caught bug fix: is_feature_active() is
    # SYNCHRONOUS (in-memory cache read, see modules.preflight_guard) --
    # `await`ing it raised "object bool can't be used in 'await'
    # expression" on every real scheduler tick, caught only after the
    # first live post-restart run (unit tests mocked this with an
    # AsyncMock, which masked the sync/async mismatch entirely).
    from modules.preflight_guard import is_feature_active
    live_send = is_feature_active("recruitment_recovery_sweep_live_send", default=False)

    candidates = await _find_stale_unanswered(_MAX_CANDIDATES_PER_RUN)
    decisions = []
    for row in candidates:
        decisions.append(await _decide_and_reply(row, live_send=live_send))

    counts: dict[str, int] = {}
    for d in decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1

    log.info(
        "[recovery_sweep] live_send=%s candidates=%d actions=%s",
        live_send, len(candidates), counts,
    )

    # Admin visibility: always send a short digest when there's anything
    # to report (either real sends/drafts, or dry-run previews worth
    # reviewing) -- day+hour-bucketed idempotency key so a stuck/duplicate
    # scheduler tick can't spam.
    actionable = sum(v for k, v in counts.items() if k != "skip")
    if actionable:
        from modules.scheduler import _admin_number
        admin = _admin_number()
        if admin:
            from modules import outbound
            mode_label = "LIVE" if live_send else "DRY-RUN preview"
            now = datetime.now(timezone.utc)
            key = f"recovery-sweep-digest-{now.strftime('%Y%m%d%H')}"
            summary = ", ".join(f"{k}={v}" for k, v in counts.items() if k != "skip")
            msg = (
                f"🔁 Recruitment/Employee recovery sweep ({mode_label}): "
                f"{len(candidates)} stale conversation(s) checked. {summary}"
            )
            await outbound.enqueue(
                admin, msg, source_bridge="bridge1", fallback_channel="bridge2",
                purpose="recovery-sweep-digest", idempotency_key=key,
            )

    return {
        "status": "ok",
        "live_send": live_send,
        "candidates_checked": len(candidates),
        "actions": counts,
        "decisions": decisions,
    }
