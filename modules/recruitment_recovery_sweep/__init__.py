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
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database import fetch_all, fetch_one, execute

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

# ── Recovery send safety gate (Owner directive, 2026-08-27) ────────────────
# Root cause of the observed defect (candidate 8801744835680 asked for their
# name at 06:06 UTC despite giving it at 00:38 UTC, 7 total sends to one
# recipient): _decide_and_reply()'s live-send branch called outbound.
# enqueue() but never modules.bridge_poller._save_message(..., "outbound",
# ...) afterward -- every OTHER live send site in this codebase (bridge_
# poller's own customer-reply and admin-hermes-reply paths) pairs enqueue()
# with that write-back; this module's sweep branch never did. Without it,
# wbom_whatsapp_messages -- the exact table _find_stale_unanswered()'s own
# NOT EXISTS check reads -- never reflected the sweep's own prior sends, so
# every ~30min tick re-selected the same "unanswered" trigger message
# forever. Separately confirmed: shared.draft.should_generate_draft()'s own
# "already replied" guard reads fpe_wa_messages, which structurally only
# ever contains bridge1/bridge2/operator_ui traffic (verified live, zero
# bridge3 rows ever) -- it can never see bridge3 recruitment candidates at
# all, live or dead code for this population. Restoring the _save_message
# write-back (below, in the live-send branch) directly repairs the
# EXISTING, already-correct _find_stale_unanswered() SQL-level check for
# every future tick -- the single highest-leverage fix, requiring no new
# table and reusing the exact pattern every other live send site already
# follows.
#
# That structural fix stops MOST repeat-sends (no new inbound == no
# re-selection at all), but doesn't by itself stop a fresh, semantically-
# redundant reply to a genuinely NEW inbound message (e.g. candidate sends
# something new, but Hermes's reply asks for a field already on file, or
# restates something already told to them, in different wording). The
# functions below are that second, independent layer -- checked against the
# ACTUAL generated reply text, right before enqueue, never against exact
# prior wording alone.

# Deterministic, no-LLM-call layer: recognizes a reply asking for a field
# that is already present in structured recruitment state. Keyword sets are
# intentionally small/conservative (prefer false-negative -- let the
# semantic LLM layer below catch what this misses -- over blocking a
# reply that isn't actually redundant).
_OBJECTIVE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "collect_name": ("আপনার নাম", "নাম জানান", "নাম কি", "নাম বলুন", "your name"),
    "collect_age": ("আপনার বয়স", "বয়স কত", "বয়স জানান"),
    "collect_area": ("কোন জেলা", "বর্তমান ঠিকানা", "এলাকা"),
    "collect_experience": ("কত বছরের অভিজ্ঞতা", "অভিজ্ঞতা আছে"),
    "collect_previous_company": ("কোন কোম্পানিতে", "কোন প্রতিষ্ঠানে কাজ করেছেন", "contact person"),
    "clarify_job_preference": ("কোন পদে আগ্রহী", "কোন পদে কাজ"),
}


def _guess_reply_objective(reply: str) -> str:
    """Best-effort, record-keeping-only label for [RECOVERY_STATE] --
    NOT used for gating logic (see _no_progression_since_last_recovery's
    own docstring for why objective-matching isn't required there). Falls
    back to a generic label when no keyword matches."""
    for objective, keywords in _OBJECTIVE_KEYWORDS.items():
        if any(kw in reply for kw in keywords):
            return objective
    return "general_reply"


def _resolved_field_conflict(
    reply: str, session_row: dict, claim: Optional[dict],
) -> Optional[str]:
    """Returns the conflicting objective name if `reply` appears to ask
    for a field already present in session_row/claim, else None. Pure,
    synchronous, no DB/LLM call."""
    from modules.recruitment_flow import _experience_claim_resolved

    checks: dict[str, bool] = {
        "collect_name": bool(session_row.get("full_name")),
        "collect_age": session_row.get("age") is not None,
        "collect_area": bool(session_row.get("area")),
        "collect_experience": session_row.get("experience_years") is not None,
        "collect_previous_company": _experience_claim_resolved(claim),
        "clarify_job_preference": bool(session_row.get("job_preference")),
    }
    for objective, keywords in _OBJECTIVE_KEYWORDS.items():
        if checks.get(objective) and any(kw in reply for kw in keywords):
            return objective
    return None


# Semantic fallback: one lightweight LLM call, same call_decision_llm()
# shape modules.recruitment_flow._classify_post_intake_turn already uses --
# reused, not a new integration. Only invoked when the deterministic layer
# above found nothing, so a candidate whose reply is obviously fine never
# pays this cost. Fails OPEN (no block) on any error/timeout/ambiguous
# response, matching _classify_post_intake_turn's own "never guess" -- a
# best-effort second layer must never itself become a reason recovery stops
# working.
_RECOVERY_SAFETY_SYSTEM_PROMPT = (
    "তুমি একটি প্রস্তাবিত WhatsApp reply যাচাই করছ, candidate-কে পাঠানোর আগে। "
    "নিচে candidate-এর সাম্প্রতিক কথোপকথনের ইতিহাস এবং প্রস্তাবিত reply দেওয়া আছে। "
    "নির্ধারণ কর: প্রস্তাবিত reply কি এমন কোনো তথ্য জিজ্ঞাসা করছে বা পুনরাবৃত্তি করছে "
    "যা candidate ইতিমধ্যে কথোপকথনে স্পষ্টভাবে দিয়েছে, বা যার উত্তর candidate-কে "
    "ইতিমধ্যে দেওয়া হয়ে গেছে (শব্দচয়ন ভিন্ন হলেও)? শুধু স্পষ্ট পুনরাবৃত্তির ক্ষেত্রেই "
    "redundant বলে চিহ্নিত কর; সামান্যতম অনিশ্চয়তা থাকলে redundant=false দাও, কখনো "
    "অনুমান করো না। কোনো ব্যাখ্যা ছাড়া শুধুমাত্র এই JSON ফরম্যাটে উত্তর দাও:\n"
    '{"redundant": <true|false>, "confidence": <0.0-1.0>, "resolved_topic": "<short label or null>"}'
)
_RECOVERY_SAFETY_CONFIDENCE_FLOOR = 0.70


async def _semantic_redundancy_check(phone: str, reply: str, history: str) -> Optional[str]:
    """Returns a short topic label if the LLM confidently judges `reply`
    redundant against `history`, else None. Never raises."""
    try:
        from modules.hermes_dispatch import call_decision_llm
    except Exception as exc:  # noqa: BLE001 -- fail open
        log.debug("[recovery_sweep] safety-gate import failed: %s", exc)
        return None
    payload = f"সাম্প্রতিক কথোপকথন:\n{(history or '')[-1500:]}\n\nপ্রস্তাবিত reply:\n{reply}"
    try:
        raw = await call_decision_llm(
            system_prompt=_RECOVERY_SAFETY_SYSTEM_PROMPT, user_payload=payload, sender_phone=phone,
        )
    except Exception as exc:  # noqa: BLE001 -- fail open
        log.debug("[recovery_sweep] safety-gate call failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        import json
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else json.loads(raw)
        confidence = float(data.get("confidence") or 0.0)
    except Exception:
        return None
    if data.get("redundant") is True and confidence >= _RECOVERY_SAFETY_CONFIDENCE_FLOOR:
        topic = str(data.get("resolved_topic") or "").strip()
        return topic or "semantic_duplicate"
    return None


# Recovery-progression tracking: same marker-block-in-notes pattern
# modules.recruitment_flow already established for [EXPERIENCE_CLAIM]
# (wbom_candidates.notes, no schema change) -- a separate, independently
# parsed [RECOVERY_STATE] block so the two coexist in the same free-text
# column without interfering. Records the last objective a recovery reply
# pursued for this candidate, so "another scheduler interval elapsed" alone
# can never re-trigger the SAME unresolved objective -- only a new inbound
# meaningfully longer than a bare acknowledgement can.
_RECOVERY_STATE_BLOCK_RE = re.compile(r"\[RECOVERY_STATE\].*?\[/RECOVERY_STATE\]", re.DOTALL)
_SHORT_ACK_RE = re.compile(
    r"^(ওকে|ok|okay|হ্যাঁ|জী|জি|ধন্যবাদ|আচ্ছা|thanks|thank you)[.!।\s]*$", re.IGNORECASE,
)


def _format_recovery_state_block(*, objective: str, topic: str = "", sent_at: str) -> str:
    return (
        "[RECOVERY_STATE]\n"
        f"objective: {objective}\n"
        f"topic: {topic or '—'}\n"
        f"sent_at: {sent_at}\n"
        "[/RECOVERY_STATE]"
    )


def _parse_recovery_state_block(notes: Optional[str]) -> Optional[dict]:
    if not notes:
        return None
    matches = _RECOVERY_STATE_BLOCK_RE.findall(notes)
    if not matches:
        return None
    data: dict = {}
    for line in matches[-1].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key in ("objective", "topic", "sent_at"):
            data[key] = None if value == "—" else value
    return data or None


async def _upsert_recovery_state(candidate_id: int, *, objective: str, topic: str = "") -> None:
    """Best-effort; never blocks/raises. Preserves any [EXPERIENCE_CLAIM]
    block already present in notes -- only the [RECOVERY_STATE] marker is
    replaced/appended."""
    try:
        row = await fetch_one("SELECT notes FROM wbom_candidates WHERE candidate_id=$1", candidate_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("[recovery_sweep] recovery_state read failed candidate=%s: %s", candidate_id, exc)
        return
    existing_notes = (row or {}).get("notes") or ""
    block = _format_recovery_state_block(
        objective=objective, topic=topic, sent_at=datetime.now(timezone.utc).isoformat(),
    )
    if _RECOVERY_STATE_BLOCK_RE.search(existing_notes):
        new_notes = _RECOVERY_STATE_BLOCK_RE.sub(lambda _m: block, existing_notes, count=1)
    else:
        new_notes = f"{existing_notes}\n\n{block}".strip() if existing_notes else block
    try:
        await execute(
            "UPDATE wbom_candidates SET notes=$1, updated_at=NOW() WHERE candidate_id=$2",
            new_notes, candidate_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[recovery_sweep] recovery_state write failed candidate=%s: %s", candidate_id, exc)


def _no_progression_since_last_recovery(recovery_state: Optional[dict], text: str) -> bool:
    """True if a recovery reply was already sent to this candidate for
    SOME objective, and this turn's triggering inbound text is only a bare
    acknowledgement (no meaningful new information) -- regardless of
    whether the new candidate reply's own topic happens to match the prior
    one, since a short ack carries no real progression either way. Time
    elapsed since the last recovery send is deliberately NOT part of this
    check -- a scheduler interval alone must never be sufficient
    authorization for a repeat."""
    if not recovery_state:
        return False
    return bool(_SHORT_ACK_RE.match((text or "").strip()))


async def _recovery_safety_gate(
    *, phone: str, candidate_id: Optional[int], reply: str, text: str, history: str,
) -> Optional[str]:
    """Combined pre-send gate, run against the ACTUAL generated reply.
    Returns a block reason string, or None if the reply is safe to send.
    Never raises -- any internal failure fails OPEN (returns None) so this
    best-effort layer can never itself take recovery offline; the
    structural _save_message fix above remains the primary safety net
    regardless of this function's outcome."""
    try:
        from modules.recruitment_flow import _parse_experience_claim_block

        session_row: dict = {}
        claim: Optional[dict] = None
        notes: Optional[str] = None
        if candidate_id is not None:
            cand = await fetch_one(
                "SELECT * FROM fazle_recruitment_sessions WHERE candidate_id=$1 "
                "ORDER BY updated_at DESC NULLS LAST LIMIT 1",
                candidate_id,
            )
            session_row = dict(cand) if cand else {}
            notes_row = await fetch_one(
                "SELECT notes FROM wbom_candidates WHERE candidate_id=$1", candidate_id,
            )
            notes = (notes_row or {}).get("notes")
            claim = _parse_experience_claim_block(notes)

        objective = _resolved_field_conflict(reply, session_row, claim)
        if objective:
            return f"resolved_field_conflict:{objective}"

        # Item D: a prior recovery was already sent to this candidate, and
        # THIS turn's triggering inbound is nothing more than a bare
        # acknowledgement -- elapsed scheduler time alone can never justify
        # another send, regardless of what the new reply's own topic is.
        recovery_state = _parse_recovery_state_block(notes) if candidate_id is not None else None
        if _no_progression_since_last_recovery(recovery_state, text):
            return f"no_progression:{recovery_state.get('objective') or 'unknown'}"

        topic = await _semantic_redundancy_check(phone, reply, history)
        if topic:
            return f"semantic_duplicate:{topic}"

        return None
    except Exception as exc:  # noqa: BLE001 -- fail open, never block recovery entirely
        log.warning("[recovery_sweep] safety gate failed open phone=%s: %s", phone, exc)
        return None


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

        # Owner directive (2026-08-27): mandatory safety gate against the
        # ACTUAL generated reply -- resolved-field conflict, semantic
        # duplicate, and no-progression-since-last-recovery. Recruitment
        # role only (the employee path has no wbom_candidates row / claim
        # state this gate reasons about). Checked before the dry-run/
        # live-send split so a dry-run digest also shows what WOULD be
        # blocked, not just what would be sent.
        candidate_id: Optional[int] = None
        if role in _RECRUITMENT_ROLES:
            try:
                cand_row = await fetch_one(
                    "SELECT candidate_id FROM wbom_candidates WHERE phone=$1", phone,
                )
                candidate_id = (cand_row or {}).get("candidate_id")
            except Exception as exc:  # noqa: BLE001 -- gate itself fails open below
                log.debug("[recovery_sweep] candidate_id lookup failed phone=%s: %s", phone, exc)

            block_reason = await _recovery_safety_gate(
                phone=phone, candidate_id=candidate_id, reply=reply, text=text, history=history,
            )
            if block_reason:
                decision["action"] = "blocked_redundant"
                decision["reason"] = block_reason
                return decision

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
            if qid:
                # Root-cause fix (Owner directive, 2026-08-27): every OTHER
                # live send site in this codebase (bridge_poller's own
                # customer-reply and admin-hermes-reply paths) pairs
                # outbound.enqueue() with this write-back so wbom_
                # whatsapp_messages -- the table _find_stale_unanswered()'s
                # own NOT EXISTS check reads -- reflects what was actually
                # sent. This sweep never did, which is the structural root
                # cause of repeat-sends to the same unanswered trigger
                # message across scheduler ticks. See this module's own
                # top-of-file comment for the full trace.
                try:
                    from modules.bridge_poller import _save_message
                    await _save_message(
                        source_bridge, phone, reply, "outbound",
                        identity_role=role, workflow=intent,
                    )
                except Exception as exc:  # noqa: BLE001 -- never block on this
                    log.warning("[recovery_sweep] _save_message failed phone=%s: %s", phone, exc)
                if candidate_id is not None:
                    await _upsert_recovery_state(
                        candidate_id, objective=_guess_reply_objective(reply), topic=intent,
                    )
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
