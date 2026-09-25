"""
Fazle Core — Unified Message Router

Single source of truth for all inbound message routing.
Imported by both app/main.py (webhook path) and modules/bridge_poller (SQLite path).

Returns: (reply_text, admin_notification | None)
  admin_notification = {"admin_phone": str, "text": str, "bridge": str}

Routing priority:
  1. family         → personal safe reply, no business workflow
  2. escort roles   → extract vessel data, draft to admin, no client reply
  3. admin          → commands (APPROVE / REJECT / PAID / ADVANCE / ESCORTCONFIRM / list)
  4. supervisor     → attendance check (attendance_parser) → then KB/AI
  5. accountant     → finance route (KB, then AI)
  6. candidate      → recruitment funnel
  7. employee       → verification → slip/advance/salary/attendance
  8. known contacts (repeat_client / vendor / vip_client) → KB → AI
  9. unknown        → intent engine → KB → AI
"""

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.database import fetch_one, fetch_all, fetch_val, execute
from shared.draft_reply import create_draft_reply
from modules.intent import classify
from modules.phone_normalizer import normalize_phone
from modules.identity_brain import detect_identity, ROUTE_B_ROLES, is_registered_employee_phone
from modules.number_identity import normalize_phone as get_phone_variants
from app import llm as ai
from modules.payroll_logic import get_payroll_summary, format_payroll_context
from modules.escort import (
    handle_escort_client_message,
    handle_admin_escort_completion,
    is_completed_escort_draft,
    parse_escort_message,
)
from modules.knowledge_base import get_reply as kb_get_reply
from modules.recruitment_flow import (
    recruitment_eligibility,
    route_recruitment_message,
)
from modules.admin_commands import is_admin_command, process_admin_command, list_payment_drafts
from modules.admin_commands.nl_router import is_nl_admin_query, process_nl_admin_query
from modules.admin_directives.router import is_admin_ai_command, process_admin_ai_command, process_admin_natural_query
from modules.payment_workflow import is_advance_request
from modules.attendance import handle_attendance_message, is_attendance_message, get_attendance_summary
from modules.attendance_parser import (
    parse_attendance, create_attendance_draft, save_supervisor_attendance,
    is_supervisor_attendance,
)
from modules.employee_verification import (
    get_verification_session,
    advance_verification,
    start_advance_verification,
    start_slip_verification,
    check_identity_mismatch,
)
# Sprint-3A: AI Conversation Workflow for employee payment requests.
# Produces ONLY a draft — no transaction, no ledger, no balance change.
from modules.employee_conversation import (
    handle_employee_payment_request as ec_handle_payment_request,
    detect_payment_request_trigger as ec_detect_trigger,
    get_conversation_session as ec_get_session,
)
from modules.escort_lifecycle import is_release_intent
from modules.fazle_payroll_engine.employee import match_or_create_employee
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone

log = logging.getLogger("fazle.router")

# Phase C v2 (2026-08-23) cross-reference gate. When the V2 flag is OFF
# (default), this returns False and the entire shadow block is a single
# boolean check. The legacy §7->§15 dispatch below is byte-identical.
_V2_ENABLED_CACHE: dict[bool] = {}


def _v2_cross_ref_enabled() -> bool:
    """Cached read of recruitment_turn_decision_v2_enabled.

    The cache is reset only when the running process restarts. A flipped
    flag therefore takes effect on the next service restart, which is
    the standard pattern for production safety gates.
    """
    if _V2_ENABLED_CACHE:
        return next(iter(_V2_ENABLED_CACHE))
    try:
        from app.config import get_settings as _gs_v2
        _V2_ENABLED_CACHE[bool(_gs_v2().recruitment_turn_decision_v2_enabled)] = True
        return bool(_gs_v2().recruitment_turn_decision_v2_enabled)
    except Exception:  # noqa: BLE001
        return False

# Roles that trigger the escort client flow
_ESCORT_ROLES = frozenset({"escort_client", "client_escort_buyer", "vip_client", "repeat_client"})
_ESCORT_ORDER_PREFIXES: tuple[str, ...] = ("escort", "client", "tcis", "gms")


def _is_verified_escort_identity(identity: dict, role_str: str) -> bool:
    """Phase 1B.2 — distinguishes a verified escort-client/vip/repeat-client
    identity (seed row, .env phone, contact-name marker, or a genuine
    wbom_contacts/escort_roster history match) from identity_brain's Step 6
    keyword-only fallback (identity_source == "text_hint"), which must never
    carry the same trust level per Owner Decision 5 — it's a real, previously
    -unseen sender whose message merely looked escort-shaped, not a known or
    confirmed escort client."""
    if role_str not in _ESCORT_ROLES:
        return False
    return identity.get("identity_source") != "text_hint"

# Default no-reply name keywords (Owner decision 2026-07-22: substring match
# anywhere in the saved contact name, not prefix-only). Checked in addition
# to, not instead of, app.config draft-always settings — see the union fix
# in _should_silent_skip() below. "al-manaratayn"/"আল-মানারাতাইন" added per
# the 2026-07-21 rebrand's augment-don't-replace rule (old "al-aqsa" keyword
# kept since older saved contacts may still carry the old name). "al-rifa'i"/
# "al rifai" added 2026-07-22 for the second rebrand (Al-Rifa'i Integrated
# Services) — same augment-don't-replace rule, both older brand tokens kept.
_SILENT_SKIP_NAME_TOKENS: tuple[str, ...] = (
    "al-aqsa", "আল-আকসা", "al-manaratayn", "আল-মানারাতাইন",
    "al-rifa'i", "al rifai",
    "escort", "client", "operation", "office", "accountant", "security guard",
    "tcis", "gms", "dalal",
)
# Compiled regex for silent-skip token matching — O(n) single pass vs O(n*k) loop
_SILENT_SKIP_PATTERN: re.Pattern = re.compile(
    r"(?:al-aqsa|আল-আকসা|al-manaratayn|আল-মানারাতাইন|al-rifa'i|al rifai|"
    r"escort|client|operation|office|accountant|security guard|tcis|gms|dalal)",
    re.IGNORECASE,
)

# Phase 4.5 / 6E: Intents cleared for auto-send (recruitment + employee info + office location).
# Financial complaints are protected by the complaint-phrase guard in bridge_poller.
# Roles in DRAFT_ALWAYS_ROLES (accountant, client_escort_buyer, vip_client, repeat_client)
# remain drafted regardless of intent — EXCEPT office_location which is safe for all roles.
_SAFE_AUTOSEND_INTENTS: frozenset[str] = frozenset({
    # ── Recruitment information ───────────────────────────────────────────────
    "recruitment",      # job queries, vacancy, requirements, joining process
    "join",             # joining date, first-duty scheduling
    "greeting",         # menu / welcome / first contact
    "office_location",  # office address queries — KB-only fast path, safe for all roles
    # ── Employee information (non-financial) ──────────────────────────────────
    "salary_query",     # salary schedule, payroll cycle info (complaint guard active)
    "payment_due",      # payment date queries (complaint guard active)
    # advance_request intentionally excluded: actual advance requests ("অ্যাডভান্স চাই")
    # must stay DRAFT — only informational advance policy answers are safe to auto-send
    # and those reach KB before classification matters.
    "attendance",       # attendance rules, absence policy
    "leave",            # leave policy, resignation rules
    "escort_duty",      # duty schedule, transport/food policy info
})

_OPERATIONAL_AUTO_CREATE_INTENTS: frozenset[str] = frozenset({
    "salary_query",
    "payment_due",
    "attendance",
    "leave",
    "escort_duty",
    "slip_submission",
})

# 2026-07-22: the assignment-field/slip-or-release regexes and the strict
# order-completeness gate moved to modules.escort (is_order_complete() and
# its dependencies) so modules.route_b_handoff can reuse the exact same
# canonical check without a reverse import back into this module. These
# names are kept as thin delegates -- behavior-preserving, no logic here.
from modules.escort import (
    has_client_supplied_escort_assignment as _has_client_supplied_escort_assignment,
    looks_like_slip_or_complaint as _looks_like_slip_or_complaint,
    is_order_complete as _is_strict_escort_order_text,
)


def _phone_variants(phone: str) -> list[str]:
    """Return all normalized forms of a phone number for DB lookup."""
    return get_phone_variants(phone)


def _contact_name_has_escort_order_prefix(identity: dict) -> bool:
    name = (identity.get("display_name") or "").strip().lower()
    return name.startswith(_ESCORT_ORDER_PREFIXES)


async def _seed_unknown_employee_identity(
    sender: str,
    text: str,
    identity: dict,
) -> tuple[dict, bool]:
    """Best-effort identity seed for phone-backed operational senders.

    DISABLED (Owner directive, Session 20 Phase 1, 2026-07-17): this function
    only ever runs when identity_brain.detect_identity() already returned
    role="unknown" -- meaning the sender failed every trust check the system
    has (not an admin, not the accountant, not an escort-client, not an
    existing wbom_employees/wbom_contacts/escort-roster match). Auto-creating
    a wbom_employees row (and indirectly seeding fpe_employees via
    match_or_create_employee()) for such a sender is exactly the
    "random/unapproved number" auto-create Owner Rule D prohibits. There is
    no narrower guard to add -- the entire remaining population is untrusted
    by construction. Kept in place, not deleted, as a documented reference
    for the original design intent (first-time genuine-employee self-report)
    in case a future Owner-approved trust signal is introduced.

    _OPERATIONAL_AUTO_CREATE_INTENTS (module level) is now unused outside
    this function -- flagged, not removed, per session hard rules.
    """
    return identity, False


def _looks_like_escort_buying_intent(text: str) -> bool:
    """Relaxed escort-order intent check for client-origin free-form messages.

    This keeps the strict parser gate for auto-program creation, but allows
    draft routing to admin for prefix contacts when the message is clearly
    escort-buying context even if formatted fields are incomplete.
    """
    if not text or _looks_like_slip_or_complaint(text):
        return False
    if _is_strict_escort_order_text(text):
        return True

    intent_hint = classify(text)
    if intent_hint in ("client_order", "escort_duty"):
        return True

    lowered = text.lower()
    keyword_hits = sum(
        1
        for kw in ("escort", "vessel", "lighter", "master", "destination", "mv", "o/a", "cargo", "importer")
        if kw in lowered
    )
    has_mobile = bool(re.search(r"(?:\+?880|0)1[3-9][\d\s\-]{8,}", text))
    return keyword_hits >= 2 and has_mobile


# Root-cause fix (2026-07-19, escort-pipeline audit): every title string a
# system-generated admin notification can start with. These messages are
# sent to the admin over the same WhatsApp bridge account that
# bridge_poller's outgoing-message scan later reads back looking for real
# admin-typed confirmations (modules/bridge_poller/__init__.py) -- a
# notification that happens to quote/instruct escort- or release-shaped
# text (e.g. "send an exact [RELEASE CONFIRMED] message...") can
# false-positive match is_completed_escort_draft()/is_release_confirmation()
# and be misread as the admin's own authoritative reply.
#
# 2026-07-27 (Owner directive: no marker/footer/hidden character may ever be
# appended to an outbound message body): this list is now the SOLE signal
# _is_system_generated_notification() uses -- the invisible zero-width-space
# origin marker it used to fall back on (app.bridge._AUTOMATED_SUFFIX) has
# been removed outright. MUST be kept in sync with every title string used
# below, in modules/bridge_poller/__init__.py's own notification templates
# (_build_admin_review_draft, release-slip review, OCR-unclear alert,
# safety-filter-blocked alert, pending-drafts list, Route B Ollama-failure
# alert), and app/main.py's send_to_admin path -- any new outbound
# admin-notification template must add its own prefix here in the same
# change, or it becomes invisible to this guard.
SYSTEM_NOTIFICATION_TITLE_PREFIXES: tuple[str, ...] = (
    "⚠️ ESCORT REVIEW DRAFT",
    "⚠️ ESCORT COMPLAINT/ASSIGNMENT-LIKE MESSAGE",
    "⚠️ ESCORT CLIENT MESSAGE",
    "⚠️ COMPLETED ESCORT SLIP RECEIVED FROM NON-ADMIN",
    "⚠️ RELEASE REVIEW REQUIRED",
    "📩 DRAFT-ONLY CONTACT MESSAGE",
    "⚠️ ALERT:",  # modules.outbound._alert_circuit_open() circuit-open alert
    "📋 ADMIN REVIEW DRAFT",  # bridge_poller._build_admin_review_draft() — 5-part draft-review message
    "📋 Release Slip",  # bridge_poller release-slip-review admin notification
    "⚠️ অস্পষ্ট ছবি",  # bridge_poller OCR-unclear admin notification
    "⚠️ AI outbound blocked by safety filter",  # bridge_poller._alert_admin_safety_block()
    "📋 পেন্ডিং ড্রাফট",  # message_router pending-drafts list command reply
    "⚠️ ROUTE B: OLLAMA UNAVAILABLE",  # route_b_handoff.record_ollama_failure() admin alert
)


def _admin_review_note(
    *,
    sender: str,
    source: str,
    purpose: str,
    title: str,
    text: str,
    role: str = "",
) -> dict:
    return {
        "admin_phone": get_primary_admin(),
        "bridge": source,
        "purpose": purpose,
        "text": (
            f"{title}\n"
            f"Sender: {sender}\n"
            f"Role: {role or 'unknown'}\n\n"
            f"{text}"
        ),
    }


async def _should_silent_skip(sender: str) -> tuple[bool, str]:
    """Return (should_skip, reason) for accountant-routed or draft-only contacts.

    Rules:
      1. sender == ACCOUNTANT_PHONE → skip
      2. configured draft-always display_name match → caller creates admin draft

    Note: there is no "blocked number" concept in this system (Owner
    decision, 2026-07-11) — a sender is either a known role or unknown;
    unknown senders simply do not trigger business actions. A prior
    'blocked' role check + identity_conflict tracking existed here and has
    been fully removed (see migration 042).
    """
    settings = get_settings()
    sender = normalize_phone(sender) or sender.strip()
    # 2026-09-01: normalize both sides — a settings value entered in
    # 01XXXXXXXXX / +8801 form would otherwise silently fail to match the
    # canonical `sender` and stop silencing the accountant.
    _acct = normalize_phone(settings.accountant_phone) or (settings.accountant_phone or "").strip()
    if _acct and sender == _acct:
        return True, f"accountant phone match ({sender})"
    try:
        contact = None
        for v in _phone_variants(sender):
            contact = await fetch_one(
                "SELECT display_name FROM wbom_contacts"
                " WHERE whatsapp_number = $1 AND is_active = true"
                "   AND COALESCE(platform, 'whatsapp') = 'whatsapp' LIMIT 1",
                v,
            )
            if contact:
                break
        if contact:
            # 2026-07-22 (Final Instruction §2): a phone verified in the
            # canonical employee tables must never be silenced just because
            # its saved contact name happens to contain a protected term
            # ("Escort Karim", "Office Runner", etc.) -- active employees
            # must keep reaching their existing employee/Tier 10 workflow,
            # and inactive/former employees must still reach the normal
            # identity-based verification/admin-review path below, not this
            # name-only gate. Checked BEFORE the name-token match so a
            # registered employee is never even evaluated against it.
            if await is_registered_employee_phone(sender):
                return False, ""

            name_lower = (contact.get("display_name") or "").lower()
            # 2026-07-22: this must be a UNION of the static default keywords
            # and any .env-configured draft-always names/roles, not an
            # either/or fallback — a previous version fell into the `else`
            # branch (and silently dropped the static list entirely) as soon
            # as DRAFT_ALWAYS_NAMES/DRAFT_ALWAYS_ROLES had any custom tokens
            # configured, which is the case in production today. That meant
            # a generic contact like "Escort Rahim" (not one of the specific
            # configured names) was never actually being matched.
            m = _SILENT_SKIP_PATTERN.search(name_lower)
            if m:
                return True, f"display_name contains '{m.group()}' ({contact['display_name']!r})"
            for token in settings.draft_always_name_list:
                if token in name_lower:
                    return True, f"display_name contains '{token}' ({contact['display_name']!r})"
            for prefix in settings.draft_name_prefix_list:
                if prefix and name_lower.startswith(prefix):
                    return True, f"display_name starts with '{prefix}' ({contact['display_name']!r})"
    except Exception as _e:
        log.debug("[SILENT_SKIP] contact lookup error for %s: %s", sender, _e)

    return False, ""


def _is_safe_autosend_intent(intent: str, role: str) -> bool:  # noqa: ARG001
    """Return True if this intent is safe for auto-send without manual review.

    Safe: salary_query, payment_due, advance_request, recruitment.
    Unsafe: employee_salary_complaint, legal_issue, payment_issue, release-slip estimates.
    """
    return intent in _SAFE_AUTOSEND_INTENTS


def _family_deflection_reply(name: str, text: str) -> str:
    """The fixed personal-message deflection sent to a 'family' role
    (someone using the office line for personal contact). Extracted as its
    own function (2026-08-20) so its salam-gating is independently
    testable without mocking the rest of process_message()'s pipeline.

    2026-08-20 (2nd pass): the first fix here only gated salam on whether
    the SENDER said salam first (echo it back if so) -- that was the OLD
    rule. The business rule has since been sharpened by explicit Owner
    directive: Hermes/Fazle Core customer replies must NEVER open with
    Wa Alaikum Salam, even when the customer said salam first. Both
    branches now use the same neutral opening; `text` is kept in the
    signature (existing callers pass it) even though this function no
    longer branches on its salam content."""
    return f"হ্যালো {name}। 😊\nএটা অফিসের নম্বর — ব্যক্তিগত কথা ফোনে বলুন।"


# ── Task 13 authoritative dispatch (2026-08-22, flag-gated by TWO
# independent flags — task13_recruitment_authoritative and
# task13_employee_authoritative, both default OFF) ─────────────────────────
# Split (2026-08-22, same-day) from the original single
# task13_semantic_authoritative flag so recruitment_conversation delivery
# and employee_conversation delivery (both the verified-identity handoff
# AND the unresolved-identity clarification-only branch) can be activated
# independently — recruitment has accumulated live-shadow evidence and is
# READY; employee has none yet and must stay in shadow until it earns its
# own. task13_recruitment_authoritative governs ONLY the
# recruitment_conversation branch below; task13_employee_authoritative
# governs BOTH employee_conversation branches (resolved handoff AND
# unresolved clarification-only) — never the other way around. The caller
# (process_message()) reads both flags once via is_feature_active() and
# passes them in as plain bools so this function never has to re-check a
# flag mid-dispatch.
#
# Translates a validated SemanticDecision into an actual reply via EXISTING
# deterministic handlers / the new narrowly-scoped recruitment delivery
# path (modules.hermes_dispatch.deliver_authoritative_recruitment_reply) —
# this function itself never decides business facts, never writes, and
# never talks to a model. It returns (None, None) for any domain/condition
# it does not own, so process_message()'s caller falls through to the
# unchanged legacy §7-15 dispatch chain exactly as shadow mode already
# guaranteed. Never raises — every branch below is a pure dispatch, and the
# functions it calls already fail closed on their own.
async def _dispatch_candidate_recruitment_semantic(
    *,
    sender: str,
    text: str,
    source: str,
    role_str: str,
    intent: str,
    db_ctx: str,
    history: str,
    message_id: Optional[str],
) -> tuple[Optional[str], Optional[dict]]:
    """Task-14B (2026-08-22): candidate/new_lead/unknown semantic-
    authoritative Recruitment dispatch. Called only from
    _task13_authoritative_dispatch()'s recruitment_conversation branch,
    only when task13_candidate_recruitment_authoritative is ON, only for
    role_str in ("candidate","new_lead","unknown"), and only when the
    LEGACY deterministic recruit_decision the caller already computed
    (§7/8) was NOT already eligible — i.e. exactly the gap this Phase
    exists to close: the semantic gate recognized a recruitment-shaped
    message the deterministic keyword/detector layer alone did not.

    Reuses modules.recruitment_flow.route_recruitment_message() unchanged
    (force_eligible=True is the ONLY difference from the legacy §7/8 call
    shape below) — same active-session continuation, same fact-checked
    LLM informational reply, same intake_message()/candidate-creation
    fallback, same source-of-truth guard. This function builds no new
    funnel and makes no business decision itself; it only decides WHETHER
    to call the existing canonical function with the override, based on
    the already-validated SemanticDecision. Returns (None, None) — never a
    reply — if route_recruitment_message() itself produces nothing, so the
    caller falls through to the unchanged legacy chain exactly as section
    4 requires."""
    rec_route = await route_recruitment_message(
        phone=sender, text=text, source=source, message_id=message_id,
        role=role_str, intent=intent, contact_context=db_ctx, history=history,
        force_eligible=True,
    )
    reply = (rec_route.get("reply") or "").strip()
    return (reply or None), None


async def _task13_authoritative_dispatch(
    sem_decision,
    *,
    sender: str,
    text: str,
    source: str,
    role_str: str,
    intent: str,
    db_ctx: str,
    history: str,
    recruitment_authoritative: bool,
    employee_authoritative: bool,
    candidate_recruitment_authoritative: bool = False,
    legacy_recruitment_eligible: bool = False,
    message_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[dict]]:
    domain = sem_decision.domain
    identity_reference = sem_decision.identity_reference or {}

    if domain == "recruitment_conversation" and sem_decision.confidence >= 0.6:
        from modules.recruitment_flow import OPERATIONAL_ROLES as _OPERATIONAL_ROLES

        if role_str in _OPERATIONAL_ROLES:
            # Gated ONLY by task13_recruitment_authoritative — never by the
            # employee flag, and never by a combined/OR check. This is the
            # single most important line in this function to get right (see
            # test_cross_domain_flag_independence* in
            # tests/unit/test_task13_authoritative_mode.py).
            if not recruitment_authoritative:
                return None, None
            # Scoped precisely to the OPERATIONAL_ROLES bug this path exists
            # to fix (Example A: a known-employee sender asking about
            # someone else's job) — recruitment_eligibility()/
            # route_recruitment_message() already own delivery for every
            # role they were ALREADY eligible for (candidate/new_lead/
            # unknown), including active-session funnel continuation, step
            # collection, and the priority-phone allowlist — none of that
            # richer, well-tested logic may be shortcut by this path.
            from modules.hermes_dispatch import deliver_authoritative_recruitment_reply
            reply = await deliver_authoritative_recruitment_reply(
                phone=sender, text=text, source=source,
                contact_context=db_ctx, history=history,
            )
            return (reply or None), None

        if role_str in ("candidate", "new_lead", "unknown"):
            # Task-14B (2026-08-22): NEW, SEPARATE flag
            # (task13_candidate_recruitment_authoritative) — deliberately
            # never task13_recruitment_authoritative, which has its own
            # existing rollout/evidence scoped to OPERATIONAL_ROLES only.
            # Default OFF; with it OFF this branch is a complete no-op and
            # process_message() falls through to the unchanged legacy
            # recruit_decision/route_recruitment_message() chain exactly as
            # before Task-14B existed.
            if not candidate_recruitment_authoritative:
                return None, None
            if legacy_recruitment_eligible:
                # The legacy deterministic recruit_decision (§7/8,
                # unchanged) already claims this turn — never invoke the
                # semantic-gate-triggered path a second time for the same
                # message; let the existing, already-eligible legacy path
                # run exactly as it did before Task-14B existed (this also
                # keeps active-session continuation, test E, on its
                # original code path whenever the legacy layer already
                # recognizes the session).
                return None, None
            return await _dispatch_candidate_recruitment_semantic(
                sender=sender, text=text, source=source, role_str=role_str,
                intent=intent, db_ctx=db_ctx, history=history, message_id=message_id,
            )

        # Any other role should not realistically reach recruitment_conversation
        # here — validate_semantic_decision() already downgrades admin/
        # accountant/escort_client to no_match via _PROTECTED_ROLES. Defensive
        # fail-closed no-op for anything unanticipated.
        return None, None

    if domain == "employee_conversation" and sem_decision.confidence >= 0.6:
        # Gated ONLY by task13_employee_authoritative — never by the
        # recruitment flag. Employee-domain authoritative routing is
        # enabled by Phase 5 (2026-08-26, Owner directive).
        if not employee_authoritative:
            return None, None
        emp_id = identity_reference.get("employee_id") or identity_reference.get("linked_employee_id")
        if not emp_id:
            # validate_semantic_decision() already downgrades an
            # unresolved-identity employee_conversation decision to
            # no_match/identity_unresolved before this point is ever
            # reached — this is a defensive fail-closed guard, not the
            # normal path. Never guess an identity here.
            return None, None
        # Phase 6 (2026-08-26): refine short-reply intent (হ্যাঁ/না/৩২/
        # চট্টগ্রাম/আগেই বলেছি/etc.) using the existing Sprint-3A
        # conversation session + history. Pure read-only helper in
        # modules.employee_conversation.refine_short_intent. Fails closed
        # to the original intent when no session exists or the reply
        # isn't short. Never mutates payroll/ledger.
        try:
            from modules.employee_conversation import refine_short_intent
            intent = await refine_short_intent(sender, text, intent)
        except Exception as _ref_exc:  # noqa: BLE001
            log.debug("[router] refine_short_intent skipped (non-fatal): %s", _ref_exc)
        # Phase 5 (2026-08-26): salary_query / payment_due now enter the
        # semantic employee path via the reusable informational handler.
        # The handler uses get_payroll_summary(emp_id) + format_payroll_context
        # + RAG enrichment + Hermes (hermes_customer_employee flag gated)
        # + Tier-15 confidentiality net. NEVER mutates payroll/ledger/
        # attendance. Returns a conversational reply or safe clarification.
        if intent in ("salary_query", "payment_due"):
            from modules.employee_conversation import (
                handle_employee_informational_query as _ec_informational,
            )
            _ec_reply, _ec_admin_note = await _ec_informational(
                sender, text, source, emp_id, intent=intent,
            )
            return _ec_reply, _ec_admin_note
        # Other conversational triggers (advance/food/conveyance/emergency,
        # or an already-open Sprint-3A session) continue to use the legacy
        # ec_handle_payment_request path -- the 2026-08-21 salary/keyword
        # trigger-context-routing fix and the existing release-intent
        # admin-review branch (both in the legacy §10 EMPLOYEE chain) are
        # order-dependent protections this function must not shortcut.
        if is_release_intent(text):
            return None, None
        return await ec_handle_payment_request(sender, text, source, emp_id, intent=intent)

    if (
        domain == "no_match"
        and sem_decision.rationale == "identity_unresolved"
        and sem_decision.requested_action == "identity_clarification"
    ):
        # Still the employee flag, not the recruitment flag — this branch
        # is an employee_conversation decision that validate_semantic_
        # decision() downgraded to no_match/identity_unresolved because the
        # sender's identity couldn't be resolved; it is NOT a recruitment
        # decision and must never be gated on task13_recruitment_authoritative.
        if not employee_authoritative:
            return None, None
        from modules.employee_conversation import task13_handle_unresolved_employee_question
        return await task13_handle_unresolved_employee_question(sender, text, source)

    return None, None


_inbound_execution = ContextVar("inbound_execution", default=None)


def inbound_reference() -> dict:
    """Exact source of work in this turn; never infer from a recipient."""
    execution = _inbound_execution.get()
    if not execution:
        return {}
    # Detached existing tasks inherit this immutable identity even after the
    # synchronous turn ends; their queue/draft INSERTs remain traceable.
    return {k: execution[k] for k in ("core_message_id", "source", "source_message_ref")}


def record_inbound_action(kind: str, **fields) -> None:
    execution = _inbound_execution.get()
    if execution and execution.get("active"):
        action = {"kind": kind, **fields}
        execution["actions"].append(action)


def set_inbound_outcome(status: str, reason: str = "") -> None:
    execution = _inbound_execution.get()
    if execution and execution.get("active"):
        execution["processing_outcome"] = status
        if reason:
            execution["processing_reason"] = reason


@asynccontextmanager
async def inbound_execution(core_message_id, source, source_message_ref, **identity):
    """Finalize existing message metadata, preserving one-to-many actions.

    Draft/queue references are also stored on those rows at INSERT. Failed
    or interrupted turns require review: domain writes are not one atomic
    transaction, so silently replaying them could duplicate business work.
    """
    from app.database import fetch_one as read, execute as write
    import json

    if core_message_id is None:
        yield None
        return
    current = _inbound_execution.get()
    if current and current.get("active") and current["core_message_id"] == core_message_id:
        yield current
        return
    claimed = await read(
        """UPDATE wbom_whatsapp_messages
           SET metadata_json=COALESCE(metadata_json, '{}'::jsonb) ||
               jsonb_build_object('provenance', $2::jsonb)
           WHERE message_id=$1 AND is_processed=false AND NOT (COALESCE(metadata_json, '{}'::jsonb) ? 'provenance')
           RETURNING message_id""",
        core_message_id, json.dumps({"processing_outcome": "processing",
                                     "started_at": datetime.now(timezone.utc).isoformat()}),
    )
    if not claimed:
        yield None
        return
    execution = {
        "core_message_id": core_message_id, "source": source,
        "source_message_ref": source_message_ref, "identity": identity,
        "processing_outcome": "done", "actions": [], "active": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    token = _inbound_execution.set(execution)
    try:
        yield execution
    except BaseException as exc:
        execution["processing_outcome"] = "failed"
        execution["error_type"] = type(exc).__name__
        raise
    finally:
        execution["active"] = False
        _inbound_execution.reset(token)
        decisions = [a for a in execution["actions"] if a["kind"] == "decision"]
        identities = [a for a in execution["actions"] if a["kind"] == "identity"]
        if identities:
            execution["identity"] = {k: v for k, v in identities[-1].items() if k != "kind"}
        workflows = list(dict.fromkeys(a["workflow"] for a in decisions if a.get("workflow")))
        execution["workflows"] = workflows
        execution["finished_at"] = datetime.now(timezone.utc).isoformat()
        decision = decisions[-1] if decisions else {}
        # Keep active=False on the shared object: child tasks inherit its
        # reference, and must not append events after this turn has ended.
        await write(
            """UPDATE wbom_whatsapp_messages SET
                   metadata_json=COALESCE(metadata_json, '{}'::jsonb) ||
                       jsonb_build_object('provenance', $2::jsonb, 'stage', 'processed',
                         'status', $2::jsonb->>'processing_outcome'),
                   is_processed=true,
                   workflow_triggered=COALESCE(NULLIF($3::text, ''), workflow_triggered),
                   intent_detected=COALESCE(intent_detected, NULLIF($4::text, '')),
                   identity_role=COALESCE(NULLIF($5::text, ''), identity_role)
               WHERE message_id=$1""",
            core_message_id, json.dumps(execution), ",".join(workflows),
            decision.get("intent", ""), decision.get("role") or execution["identity"].get("role", ""),
        )


async def reconcile_stranded_inbound(core_message_id: int) -> dict:
    """Finalize one stranded inbound turn without replaying its handlers.

    The message ID is the only selector. This is intentionally a metadata
    and queue-projection repair, not a second processing path.
    """
    from app.database import fetch_one as read, execute as write
    import json

    row = await read(
        """SELECT message_id, direction, source_message_ref, is_processed,
                         metadata_json
                  FROM wbom_whatsapp_messages
                 WHERE message_id=$1""",
        core_message_id,
    )
    if not row:
        return {"ok": False, "status": "not_found", "message_id": core_message_id}
    if row.get("direction") != "inbound":
        return {"ok": False, "status": "not_inbound", "message_id": core_message_id}

    metadata = row.get("metadata_json") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    provenance = metadata.get("provenance") or {}
    if provenance.get("processing_outcome") != "processing" or row.get("is_processed"):
        return {
            "ok": False, "status": "not_stranded", "message_id": core_message_id,
            "processing_outcome": provenance.get("processing_outcome"),
        }
    claimed_id = provenance.get("core_message_id")
    if claimed_id is not None and int(claimed_id) != int(core_message_id):
        return {"ok": False, "status": "identity_mismatch", "message_id": core_message_id}

    recovered = dict(provenance)
    actions = list(recovered.get("actions") or [])
    actions.append({
        "kind": "recovery",
        "outcome": "finalized_without_replay",
        "core_message_id": core_message_id,
        "source_message_ref": row.get("source_message_ref"),
    })
    recovered.update({
        "processing_outcome": "done",
        "processing_reason": "finalization_reconciled",
        "recovered_without_replay": True,
        "actions": actions,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
    updated_metadata = dict(metadata)
    updated_metadata.update({"stage": "processed", "status": "done", "provenance": recovered})
    updated = await read(
        """UPDATE wbom_whatsapp_messages
              SET metadata_json=$2::jsonb, is_processed=true
            WHERE message_id=$1
              AND COALESCE(metadata_json->'provenance'->>'processing_outcome', '')=$3
         RETURNING message_id""",
        core_message_id, json.dumps(updated_metadata, ensure_ascii=False), "processing",
    )
    if not updated:
        return {"ok": False, "status": "already_reconciled", "message_id": core_message_id}

    await write(
        """UPDATE fazle_message_queue
              SET status=$2, last_error=NULL, processed_at=NOW()
            WHERE extra->>'core_message_id'=$1 AND status=$3""",
        str(core_message_id), "done", "processing",
    )
    return {
        "ok": True,
        "status": "reconciled",
        "message_id": core_message_id,
        "source_message_ref": row.get("source_message_ref"),
        "processing_outcome": "done",
        "replayed": False,
    }


async def process_message(
    sender: str, text: str, source: str, *, message_id: Optional[str] = None,
    is_extracted_media: bool = False,
) -> tuple[str, Optional[dict]]:
    """
    Route one inbound message and return (reply_text, admin_notification | None).
    Does NOT send anything — callers handle delivery.

    message_id: the bridge's own id for this message, when the caller has one
    (see modules.bridge_poller.process_bridge_inbound). Threaded through to
    handle_admin_escort_completion() for the draft-confirmation review queue's
    dedup identity (migration 052) — optional, only that one path uses it.

    is_extracted_media (2026-08-25 recovery-pass, narrow cf_W001 mitigation):
    True when `text` is raw OCR/STT output that bridge_poller could NOT
    classify as a known slip/document type (i.e. it fell through to
    general routing rather than being handled by its own dedicated,
    already-safe branch -- a recognized escort/release slip or candidate
    document never sets this). Real incident (cf_W001, regression corpus,
    2026-08-23): a candidate photographed unrelated notes describing this
    system's own architecture; OCR'd text containing "টাকা"/"পেমেন্ট" tripped
    a bare-keyword operational trigger and produced a nonsensical
    "send your Employee ID" reply. This flag only suppresses the specific
    bare-keyword ec_trigger* checks below (see their own comments) --
    it does NOT touch precise intent-classifier-driven routing
    (salary_query/payment_due+emp_id, recruitment, etc.), and it is NOT a
    general "distrust OCR text" mechanism. The project's own regression
    corpus documents the full fix as "Phase D: multi-signal domain
    routing redesign" (not yet built) -- this is a deliberately narrow,
    contained guard against the one concrete failure mode that already
    broke live, not that redesign.
    """
    settings = get_settings()
    sender = normalize_phone(sender) or sender.strip()

    # Hermes Kernel C0.2: create a shadow DecisionRecord and EVT-001 event at
    # the start of every turn. Both are written fire-and-forget only when
    # their respective feature flags are True. No routing logic is changed.
    try:
        from modules.hermes_kernel.data_classes import DecisionRecord, KernelEvent
        _hk_record = DecisionRecord(
            sender_phone=sender,
            source=source,
            raw_input_text=(text or "")[:2000],
            source_message_id=message_id or "",
        )
        _hk_evt001 = KernelEvent(
            trace_id=_hk_record.trace_id,
            event_name="EVT-001 message.received",
            payload={"sender_phone": sender, "source": source, "message_id": message_id},
        )
    except Exception:
        _hk_record = None  # type: ignore[assignment]
        _hk_evt001 = None  # type: ignore[assignment]

    def _hk_fire(role: str, intent: str, action: str, explanation: str = "") -> None:
        """Fire-and-forget shadow log; never raises; never blocks."""
        record_inbound_action("decision", role=role, intent=intent,
                              action=action, workflow=explanation,
                              audit_trace_id=str(_hk_record.trace_id) if _hk_record else None)
        if _hk_record is None:
            return
        try:
            _hk_record.identity_role = role
            _hk_record.intent = intent
            _hk_record.chosen_action = action
            _hk_record.explanation = explanation
            from modules.hermes_kernel.shadow_logger import log_decision, log_event
            asyncio.create_task(log_decision(_hk_record))
            if _hk_evt001 is not None:
                asyncio.create_task(log_event(_hk_evt001))
        except Exception as _hk_exc:
            log.warning("[hermes_kernel] _hk_fire non-fatal: %s", _hk_exc)

    # P1b (2026-08-19, reliability-hardening pass): internal/system message
    # provenance guard. scripts/ai_console_monitor.sh's health-check alert
    # (own stable literal prefix, hardcoded in that script's send_wa_alert())
    # was found live-generating a nonsensical customer-style draft
    # ("AI Generated Reply: মোট রোস্টারে..." in response to a system health
    # alert) -- the underlying trigger (a raw bridge POST invisible to every
    # Hermes tool) was already fixed same-day (commit 532527d, routes
    # through /admin/send-whatsapp now), but this guard is deliberate
    # defense-in-depth against the whole *class* of system-generated text
    # ever reaching identity/intent/draft/recruitment/employee-conversation
    # logic, not just this one instance. Mirrors the existing, Owner-
    # approved cross-bridge Hermes-echo guard below (exact content-signature
    # match, narrow and local -- same precedent, not a new pattern).
    if text.lstrip().startswith("🚨 AI Console ALERT"):
        log.info(
            "[system_provenance_guard] ignoring system-generated AI Console "
            "alert text arriving as inbound (sender=%s, source=%s) -- not a "
            "real customer/candidate/employee message",
            sender, source,
        )
        _hk_fire("system", "system_alert", "no_action", "ai-console-alert-provenance-guard")
        return "", None

    # TASK 2: Silent-skip excluded contacts before any processing or draft creation.
    # Escort work messages are operational inputs, so they must still create
    # admin-review drafts even when the contact name contains "client/escort".
    _skip, _skip_reason = await _should_silent_skip(sender)
    if _skip:
        if is_completed_escort_draft(text) or _looks_like_escort_order(text) or _looks_like_escort_buying_intent(text):
            log.info("[SILENT_SKIP_BYPASS] %s → escort workflow allowed (%s)", sender, _skip_reason)
        elif "display_name contains" in _skip_reason or "display_name starts with" in _skip_reason:
            log.info("[DRAFT_ONLY_CONTACT] %s → admin draft, no auto-reply (%s)", sender, _skip_reason)
            _hk_fire("", "", "draft", "draft-only-contact")
            return "", {
                "admin_phone": get_primary_admin(),
                "bridge": source,
                "purpose": "draft-only-contact-review",
                "text": (
                    "📩 DRAFT-ONLY CONTACT MESSAGE\n"
                    f"Sender: {sender}\n"
                    f"Reason: {_skip_reason}\n\n"
                    f"{text}"
                ),
            }
        else:
            log.info("[SILENT_SKIP] %s → no reply, no draft (%s)", sender, _skip_reason)
            _hk_fire("", "", "no_action", "silent-skip")
            return "", None

    identity = await detect_identity(sender, text)
    role_str = identity["role"]

    identity, seeded = await _seed_unknown_employee_identity(sender, text, identity)
    if seeded:
        role_str = identity.get("role") or "employee"
    log.info(
        f"[IDENTITY] {sender} → {role_str} "
        f"(conf={identity['identity_confidence']}, src={identity['identity_source']})"
    )
    # Populate kernel record with identity data now that it's resolved.
    record_inbound_action("identity", role=role_str, confidence=identity.get("identity_confidence"),
                          identity_source=identity.get("identity_source"))
    if _hk_record is not None:
        _hk_record.identity_role = role_str
        _hk_record.identity_confidence = identity.get("confidence")  # 0.0-1.0 float
        _hk_record.identity_source = identity.get("identity_source", "")

    # ── 1. FAMILY — personal, no business workflow ────────────────────────────
    if role_str == "family":
        name = identity.get("display_name") or "আপনি"
        _hk_fire("family", "greeting", "reply", "family-personal")
        return (_family_deflection_reply(name, text), None)

    if role_str != "admin" and _looks_like_slip_or_complaint(text):
        _hk_fire(role_str, "escort_duty", "draft", "escort-review-not-order")
        title = "⚠️ ESCORT REVIEW DRAFT — ORDER নয়"
        if _has_client_supplied_escort_assignment(text):
            title = "⚠️ ESCORT COMPLAINT/ASSIGNMENT-LIKE MESSAGE — ADMIN REVIEW"
        return "", _admin_review_note(
            sender=sender,
            source=source,
            purpose="escort-review-not-order",
            title=title,
            text=(
                f"{text}\n\n"
                "Note: Client message contains escort assignment/slip/release/food/conveyance context. "
                "Do not create escort order automatically; admin must verify against DB/roster."
            ),
            role=role_str,
        )

    # ── 2. ESCORT CLIENT roles — strict order gate + admin draft only ─────────
    if role_str in _ESCORT_ROLES:
        # Phase B Stage 1 (2026-07-19): shadow-mode conversation-memory
        # observation only -- feature-flagged off by default
        # (conversation_memory_enabled / whatsapp_thread_reconstruction_
        # enabled), fully exception-safe, never changes the return value
        # below. Deliberately placed before the existing routing decision
        # so it observes every authorized escort-client message, whether
        # or not this single message alone completes an order.
        from modules.conversation_memory.thread_reconstruction import shadow_observe_escort_client_message
        await shadow_observe_escort_client_message(
            text=text, sender_phone=sender, source=source,
            message_id=message_id, sender_role=role_str,
        )

        is_verified_escort = _is_verified_escort_identity(identity, role_str)
        intent_check = classify(text)
        strict_order = _looks_like_escort_order(text)
        escort_intent = intent_check in ("client_order", "escort_duty") or strict_order
        if strict_order and escort_intent:
            _hk_fire(role_str, "escort_duty", "business_action", "escort-order")
            return await handle_escort_client_message(
                text,
                sender,
                source,
                is_verified=is_verified_escort,
            )
        if escort_intent or _looks_like_escort_buying_intent(text):
            _hk_fire(role_str, "escort_duty", "draft", "escort-client-non-order-review")
            return "", _admin_review_note(
                sender=sender,
                source=source,
                purpose="escort-client-non-order-review",
                title="⚠️ ESCORT CLIENT MESSAGE — STRICT ORDER MATCH হয়নি",
                text=(
                    f"{text}\n\n"
                    "Required for auto order: Escort/TCIS buyer context + Mother Vessel + Lighter Vessel + Master Mobile."
                ),
                role=role_str,
            )
        # Otherwise fall through to normal routing below

    # Completed escort slips sent by a client/non-admin are not final admin
    # confirmations. Keep them visible by sending them to admin review.
    if role_str != "admin" and is_completed_escort_draft(text):
        _hk_fire(role_str, "escort_duty", "draft", "escort-slip-review")
        return "", {
            "admin_phone": get_primary_admin(),
            "bridge": source,
            "purpose": "escort-completed-slip-review",
            "text": (
                "⚠️ COMPLETED ESCORT SLIP RECEIVED FROM NON-ADMIN\n"
                f"Sender: {sender}\n"
                f"Detected role: {role_str}\n\n"
                f"{text}\n\n"
                "Review and resend/confirm from admin number if this should create or update an escort program."
            ),
        }

    # ── 3. ADMIN ───────────────────────────────────────────────────────────────
    if role_str == "admin":
        # Cross-bridge Hermes-reply echo guard (bridge1 Hermes Control
        # Channel, Owner-directed, 2026-08-12 -- accepted structural
        # tradeoff, see modules.admin_directives.router._handle_hermes_relay's
        # docstring). A reply _deliver_hermes_reply sends out via bridge1
        # to settings.admin_bridge2_number physically lands on bridge2 as
        # a genuine new is_from_me=0 inbound message, sender = bridge1's
        # own number -- identity_brain._check_admin_settings() already
        # globally recognizes admin_bridge1_number as role="admin"
        # regardless of source, so without this guard that message would
        # re-enter this exact ADMIN branch. bridge_poller.
        # process_bridge_inbound()'s existing LOOP_GUARD ("cross-bridge
        # bounce" check) already stops it being auto-*sent* back out a
        # second time, but only after a full RBAC-audited AI-console call
        # already ran -- wasted LLM cost, latency, and audit-log noise on
        # every single Hermes reply. Recognized narrowly by exact content
        # signature ("🤖 Hermes:" is the literal, stable prefix
        # _deliver_hermes_reply's own outbound.enqueue() call always uses)
        # + sender + arrival channel -- mirrors bridge_poller's
        # _is_bridge3_admin_recognition()'s "narrow, one-directional,
        # local" precedent rather than widening any global identity rule,
        # so it can never collide with scheduler alerts (different
        # prefixes) or real bridge1 HR/recruitment traffic (never sent by
        # bridge1's own number). Silently ignored -- nothing was
        # authorized or attempted, so no RBAC audit call, just an INFO log
        # line for observability.
        if (
            source == "bridge2"
            and sender == (settings.bridge1_number or "")
            and text.lstrip().startswith("🤖 Hermes:")
        ):
            log.info(
                "[hermes_echo_guard] ignoring bridge1-originated Hermes reply "
                "arriving on bridge2 (sender=%s) -- not a new admin request",
                sender,
            )
            _hk_fire("admin", "hermes_reply_echo", "no_action", "hermes-cross-bridge-echo-guard")
            return "", None

        if is_completed_escort_draft(text):
            _hk_fire(role_str, "escort_duty", "business_action", "escort-admin-confirmation")
            # Admin can finalize by replying directly to the system as well.
            confirm_text, client_delivery = await handle_admin_escort_completion(
                text,
                sender,
                source,
                recipient_phone=None,
                message_id=message_id,
            )
            return (confirm_text or "Escort confirmation processed."), client_delivery

        if is_admin_command(text):
            _hk_fire(role_str, "admin_command", "business_action", "admin-command")
            result = await process_admin_command(text, sender)
            if isinstance(result, tuple):
                confirm_text, extra_msg = result
                if extra_msg and len(result) == 2:
                    # Could be accountant_msg or buyer_msg — check context
                    # process_admin_command now returns (confirm, msg_to_forward)
                    # Route to accountant for PAID/ADVANCE; to buyer for ESCORTCONFIRM
                    target_phone = _resolve_forward_target(text, settings)
                    if target_phone and extra_msg:
                        return confirm_text, {
                            "admin_phone": target_phone,
                            "text": extra_msg,
                            "bridge": source,
                        }
                return confirm_text, None
            return result, None  # type: ignore[return-value]

        # Phase 1.1 (v1.1.0): Natural-language admin queries (no LLM).
        # Runs AFTER structured commands so APPROVE/REJECT/etc still win.
        if is_nl_admin_query(text):
            _hk_fire(role_str, "admin_query", "query", "admin-natural-query")
            reply = await process_nl_admin_query(text, sender)
            if reply:
                return reply, None

        # Phase 3 Step 6: explicit-trigger WhatsApp admin AI.
        # Checked AFTER is_admin_command/is_nl_admin_query so
        # those always win. Checked BEFORE hardcoded-help so
        # unmatched text still falls through to help (B25 fix
        # preserved by construction — only explicit keywords
        # reach this branch, never unmatched free text).
        if is_admin_ai_command(text):
            _hk_fire(role_str, "admin_query", "query", "admin-ai-command")
            ai_reply = await process_admin_ai_command(
                text, sender_phone=sender, source_bridge=source
            )
            if ai_reply is not None:
                return ai_reply, None

        # Bridge1 Hermes Control Channel reliability fix (2026-08-13,
        # found during a live hardening pass -- real log evidence, not
        # speculative): the loose substring keyword shortcuts just below
        # ("draft"/"payment"/"attendance" etc.) run BEFORE
        # process_admin_natural_query's own bridge1/bridge2 Hermes bypass,
        # so any Admin message on the Hermes control channel that happens
        # to contain one of those common English/Bangla words (e.g. "আজকের
        # attendance record...", "employee attendance এবং payroll...")
        # never reaches Hermes at all -- it's answered by the OLDER,
        # narrower get_attendance_summary()/list_payment_drafts()/
        # _cmd_admin_list() handlers instead. On bridge1 specifically that
        # reply then hits bridge_poller's pre-existing (2026-08-04)
        # cross-bridge LOOP_GUARD -- which correctly, if too broadly,
        # treats "reply recipient == another bridge's own number" as a
        # likely bounce and diverts it to a manual-review draft instead of
        # sending -- so the Admin sees NO reply at all. This traced back
        # and fully explains 2 requests (1b, 14a) that the 2026-08-13
        # capability assessment reported as unexplained hangs; they were
        # never sent to hermes-runner in the first place. Fix: for the
        # Admin's own phone on a dedicated Hermes control channel, skip
        # straight to process_admin_natural_query (which itself decides
        # Hermes vs the AI console) -- the same precedence the bridge2
        # self-chat path already has by construction (it never passes
        # through this keyword-shortcut block at all, see bridge_poller's
        # separate is_from_me=1 handling). Every other sender/channel is
        # completely unaffected -- these keyword shortcuts still run
        # exactly as before for bridge2/bridge3's own AI-console users.
        if sender == (settings.admin_bridge2_number or "") and source in ("bridge1", "bridge2"):
            return await process_admin_natural_query(text, sender, source_bridge=source), None

        lower = text.lower()
        if "draft" in lower or "পেন্ডিং" in lower or "list" in lower:
            return await _cmd_admin_list(), None
        if "payment" in lower or "পেমেন্ট" in lower or "paid" in lower:
            return await list_payment_drafts(sender), None
        if "attendance" in lower or "হাজিরা" in lower or "উপস্থিতি" in lower:
            return await get_attendance_summary(), None

        # Owner decision 2026-08-04 (bridge3 natural-language admin
        # routing, supersedes the B25 (H4) hardcoded fallback below for the
        # default case): admin text that matched no structured command/
        # keyword above now goes to the same tool-grounded, RAG-grounded
        # fazle-core AI console (orchestrate_console_request) used for
        # explicit "AI <question>" commands, so the Admin can query it in
        # natural Bangla/English/mixed text without an "AI " prefix. This
        # is NOT the raw ungrounded LLM call B25 (H4) was hardened against
        # (see process_admin_natural_query's docstring) — the static help
        # text below is now only a last-resort safety net if the console
        # itself returns nothing.
        #
        # NOTE (2026-08-04, corrected same day): earlier comments here
        # called this engine "Hermes" — it is not, EXCEPT on the dedicated
        # bridge2/superadmin Admin<->Hermes channel, where (2026-08-12,
        # direct Owner instruction) process_admin_natural_query itself now
        # delegates unprefixed free text straight to the real Hermes relay
        # instead of this AI console — see that function's docstring. This
        # is still fazle-core's own AI console (read-only RT-*/BUS-*/MEM-*
        # tool registry) for every other admin/channel, a deliberately
        # separate system from the real Hermes (assistant-platform's
        # BUILD/RUN-capable agent).
        console_reply = await process_admin_natural_query(text, sender, source_bridge=source)
        # "is not None", not a truthy check: a real Hermes relay dispatch
        # returns "" on purpose (2026-08-12 UX fix — no intermediate "sent
        # to Hermes" acknowledgement message; see _handle_hermes_relay) and
        # must still short-circuit here, matching process_admin_ai_command's
        # own is-not-None check above for the identical reason.
        if console_reply is not None:
            return console_reply, None

        return (
            "❌ কমান্ড বুঝিনি।\n\n"
            "ব্যবহার:\n"
            "  APPROVE <id>            — ড্রাফট পাঠান\n"
            "  APPROVE <id> <id> ...   — একসাথে একাধিক\n"
            "  REJECT <id>             — বাতিল\n"
            "  EDIT <id> <নতুন বার্তা>\n"
            "  PAID <id> <amount> <method>\n"
            "  STATUS / DRAFTS         — পেন্ডিং তালিকা\n\n"
            "🔎 প্রশ্ন (Natural Language):\n"
            "  show last 10 chats of 01XXXXXXXXX\n"
            "  last contact of 01XXXXXXXXX\n"
            "  01XXXXXXXXX এর শেষ ১০ চ্যাট\n\n"
            "বাংলা সংখ্যাও কাজ করে: APPROVE ১৬৫"
        ), None

    # ── 4. ATTENDANCE (any role) — draft for admin approval ───────────────────
    # Checks supervisor AND any sender — admin approval required before DB save
    if is_supervisor_attendance(text) or (role_str != "admin" and is_attendance_message(text)):
        parsed = parse_attendance(text)
        result = await create_attendance_draft(parsed, sender, source)
        admin_phone = settings.admin_bridge1_number if source == "bridge1" \
            else settings.admin_bridge2_number
        _hk_fire(role_str, "attendance", "draft", "attendance-draft")
        return result["message"], {
            "admin_phone": admin_phone,
            "text": result["admin_msg"],
            "bridge": source,
        }

    # ── 5. Intent classification ───────────────────────────────────────────────
    # AI is authoritative for intent; deterministic rules are the availability fallback.
    intent = await ai.classify_intent_llm(text)
    if intent == "unknown":
        intent = classify(text)
        _intent_method = "rule"
    else:
        _intent_method = "llm"
    log.info(f"[INTENT] {sender} → {intent}")
    record_inbound_action("intent", intent=intent, method=_intent_method)
    if _hk_record is not None:
        _hk_record.intent = intent
        _hk_record.intent_method = _intent_method

    # G6 (predates Task-13; 2026-06-19 fix; NARROWED 2026-08-22, Task-14B).
    # This guard used to hard-return "" here for EVERY operational-role
    # sender whose intent classified as "recruitment", before ANY
    # downstream logic — including the Task-13 semantic gate below — ever
    # ran. Its documented purpose was preventing an operational sender from
    # being auto-routed into the candidate/recruitment FUNNEL purely
    # because intent=="recruitment" (the 2026-06-19 fix for a narrower
    # OR-condition bug). That specific protection is already, independently,
    # guaranteed by two later, narrower checks that don't depend on this
    # line at all: (a) the recruit_decision computation below (§7/8) only
    # ever calls recruitment_eligibility()/route_recruitment_message() for
    # role_str in ("candidate","new_lead","unknown") — every other role is
    # unconditionally {"eligible": False} regardless of intent; and (b)
    # _task13_authoritative_dispatch()'s own OPERATIONAL_ROLES/candidate
    # role split, itself additionally gated by task13_recruitment_
    # authoritative / task13_candidate_recruitment_authoritative (both
    # default OFF). The AND-condition below had over-corrected into a
    # silent full-message drop for every operational sender — including a
    # known employee's genuine recruitment-shaped question about someone
    # else's job (Example A: "আমার ভাইয়ের Guard-এর চাকরি হবে? বেতন কত?") —
    # that never even reached the semantic gate to be classified, let alone
    # any other legitimate downstream handler (accountant/§6, employee/§10,
    # Route B/§11.5, KB/§13, generic AI fallback/§15). Narrowed here: no
    # longer returns — only logs — so the real funnel-entry protection
    # above still applies while every other tier gets the same fair chance
    # to process this message that any other intent value would get.
    #
    # A residual, real risk this narrowing exposes on its own: tier §10's
    # ec_detect_trigger() bare "বেতন"/salary keyword scan would otherwise
    # now see messages like the Example A text above (which contains
    # "বেতন") and misroute them into an advance/payment-request workflow —
    # guarded separately at both of its call sites below via
    # `intent != "recruitment"` (same "precise classification wins over a
    # generic keyword trigger" principle the existing 2026-07-22 salary_
    # query/payment_due check right above them already established).
    if intent == "recruitment" and role_str not in ("candidate", "new_lead", "unknown"):
        log.info(
            "[RECRUITMENT_INTENT_OPERATIONAL] sender=%s role=%s source=%s -- "
            "continuing to downstream dispatch (funnel entry remains role-gated "
            "at recruit_decision/§7-8 and _task13_authoritative_dispatch's own "
            "OPERATIONAL_ROLES/candidate-population split)",
            sender, role_str, identity.get("identity_source"),
        )

    # ── 6. ACCOUNTANT ─────────────────────────────────────────────────────────
    if role_str == "accountant":
        # Phase 1 "Accountant Reports" (2026-08-12, Owner-directed, from the
        # 2026-08-12 System Operations Audit's priority recommendations):
        # the existing REPORT command family (daily cash, reconciliation,
        # payroll, escort, list) was previously reachable only from the
        # Admin branch above -- an accountant typing "report cash 30" fell
        # through to a generic AI reply instead of the real reports engine.
        # is_report_command() is a narrow subset of is_admin_command() (see
        # its own docstring) matching ONLY the 6 report_* shapes, nothing
        # else in the admin command set (no APPROVE/PAID/REVERSE/etc reach
        # this branch). process_admin_command() additionally re-checks RBAC
        # permission for the caller's own phone before running anything --
        # this call is not a bypass of that check.
        # process_admin_command is already imported at module level (top of
        # file) -- re-importing it here as a local name would shadow that
        # module-level binding for this function's ENTIRE scope (Python
        # treats any name assigned anywhere in a function, including via a
        # local `import`, as local throughout the whole function), which
        # breaks the unrelated Admin branch's own `await
        # process_admin_command(...)` call above with UnboundLocalError --
        # confirmed via bytecode disassembly (LOAD_FAST before any STORE_FAST
        # on that path). Only is_report_command is genuinely new here.
        from modules.admin_commands import is_report_command
        if is_report_command(text):
            return await process_admin_command(text, admin_phone=sender), None

        from modules.accountant_summary import is_accountant_summary, ack_accountant_summary
        if is_accountant_summary(text):
            return ack_accountant_summary(text), None

        from modules.admin_commands.nl_advance_record import (
            is_advance_record_query, intent_advance_record,
        )
        if is_advance_record_query(text):
            return await intent_advance_record(text, admin_phone=sender), None

        from modules.payment_ingest import looks_like_payment_sms, ingest_payment_sms
        if looks_like_payment_sms(text):
            result = await ingest_payment_sms(text, sender_number=sender)
            return _fmt_ingest_reply(result), None

        from modules.payment_ingest import is_admin_cash_shorthand, ingest_admin_cash_entry
        if is_admin_cash_shorthand(text):
            result = await ingest_admin_cash_entry(text, sender_number=sender)
            return _fmt_ingest_reply(result), None

        kb_reply = await kb_get_reply(text, intent)
        if kb_reply:
            return kb_reply, None
        db_ctx = await get_contact_context(sender)
        history = "\n".join(await get_recent_history(sender))
        reply = await ai.generate_reply(text, intent, db_ctx, history=history, role=role_str, source=source)
        # S6 (2026-08-08): accountant is trusted for its own domain's data
        # (payments/cash), but is still not admin -- the same final
        # confidentiality net Tier 15 already has (blocks credential-like
        # output, echoed prompt sections, and any non-sender phone number)
        # closes this AI-reply exit point too. See shared/reply_policy.py's
        # enforce_general_reply_confidentiality() docstring.
        from shared.reply_policy import (
            clean_general_reply as _clean_reply,
            enforce_general_reply_confidentiality as _enforce_confidentiality,
        )
        reply = _enforce_confidentiality(_clean_reply(reply), sender_phone=sender)
        return reply, None

    # ── 7/8. Recruitment — one eligibility decision for every unknown/candidate ─
    if role_str in ("candidate", "new_lead", "unknown"):
        recruit_decision = await recruitment_eligibility(
            sender, text, role=role_str, intent=intent, source=source,
        )
    else:
        recruit_decision = {"eligible": False}

    # ── Task 13 (2026-08-22): SHADOW-ONLY semantic gate for the Recruitment +
    # Employee conversational target. Runs AFTER all high-confidence
    # business-action consumers (admin/accountant/escort/cash/attendance/
    # slip) have had the chance to claim the message and either returned
    # (admin/accountant) or been gated by their intent. Identity, keywords,
    # and recruitment_detector are EVIDENCE inside the gate; they do NOT
    # select the domain on their own.
    #
    # This block is NOT authoritative yet -- it computes what
    # semantic_gate()/validate_semantic_decision() WOULD decide and records
    # it on the DecisionRecord (via _hk_record.semantic_decision, persisted
    # by whichever _hk_fire() call further down actually fires for this
    # turn) purely for offline comparison against the legacy deterministic
    # router's real decision. Regardless of sem_decision.domain, this block
    # MUST NOT call route_recruitment_message(), MUST NOT call
    # task13_handle_unresolved_employee_question() or any other handler,
    # MUST NOT set ai_reply/return a reply, MUST NOT trigger an identity-
    # clarification reply, and MUST NOT create a draft or write any
    # business data. Execution always falls through unconditionally to the
    # pre-existing (pre-Task-13) §7→§15 dispatch chain below. The whole
    # block is wrapped in try/except so a shadow-logging failure can never
    # affect the real reply path.
    try:
        from modules.hermes_dispatch import (
            semantic_gate as _semantic_gate,
            validate_semantic_decision as _validate_semantic_decision,
        )
        from modules.hermes_kernel.data_classes import SemanticDecision as _SemanticDecision
        recruitment_detector_result = {}
        try:
            from modules.recruitment_detector import (
                detect_recruitment_intent as _detect_recruitment_intent,
            )
            recruitment_detector_result = await _detect_recruitment_intent(
                text=text, phone=sender, intent=intent,
            ) or {}
        except Exception as _det_exc:
            log.debug("[router] recruitment_detector skipped: %s", _det_exc)
        db_ctx_pre = await get_contact_context(sender)
        history_pre = "\n".join(await get_recent_history(sender))
        gate_input = {
            "text": text,
            "sender_phone": sender,
            "identity_reference": dict(identity or {}),
            "intent_hint": intent,
            "recruitment_detector_result": recruitment_detector_result,
            "keyword_hints": [],
            "history": history_pre,
            "db_ctx": db_ctx_pre,
            "kb_ctx": "",
            "business_data": {},
            "source": source,
            "language": "",
        }
        raw_decision = await _semantic_gate(gate_input)
        sem_decision = _validate_semantic_decision(
            raw_decision,
            identity_reference=gate_input["identity_reference"],
            intent_hint=intent,
        )
        if _hk_record is not None:
            _hk_record.semantic_decision = sem_decision
        # Phase C v2 (2026-08-23) cross-reference: when
        # recruitment_turn_decision_v2_enabled is True (default OFF), compute
        # a V2 decision in shadow and annotate the existing DecisionRecord
        # with non-decision-affecting fields (multi_domain_review_required,
        # reason_codes, turn_type, semantic_domain, confidence). NO routing
        # effect. The legacy §7→§15 dispatch chain below is byte-identical.
        if _v2_cross_ref_enabled():
            try:
                _v2_thread_phone = (
                    gate_input.get("phone_normalized")
                    or gate_input.get("phone")
                )
                if _v2_thread_phone:
                    from modules.recruitment_turn_decision import (
                        decide as _rtd_decide_v2,
                        propose as _rtd_propose_v2,
                    )
                    from modules.conversation_canonical import (
                        build_canonical_history as _build_history_v2,
                    )
                    from modules.hermes_dispatch import (
                        build_recruitment_context as _build_sot_v2,
                    )
                    _v2_history = await _build_history_v2(_v2_thread_phone, n=10)
                    _v2_proposals = _rtd_propose_v2(
                        gate_input.get("text") or "",
                        gate_input.get("current_step"),
                        gate_input.get("session") or {},
                    )
                    _v2_sot = _build_sot_v2(gate_input.get("text") or "")
                    _v2_dec = await _rtd_decide_v2(
                        text=gate_input.get("text") or "",
                        current_step=gate_input.get("current_step"),
                        session=gate_input.get("session") or {},
                        identity=gate_input.get("identity_reference") or {},
                        history=_v2_history,
                        intake_proposals=_v2_proposals,
                        source_of_truth=_v2_sot,
                        flag_enabled=True,
                    )
                    if _v2_dec is not None and _hk_record is not None:
                        # Phase C directive correction 11: expose only via
                        # structured logs (not DecisionRecord attributes --
                        # DecisionRecord is owned by hermes_kernel and is
                        # out of Phase C's authorized modification surface).
                        log.info(
                            "[message_router_v2_shadow] phone=%s sem_domain=%s "
                            "v2_turn_type=%s v2_multi_domain=%s v2_reason_codes=%s "
                            "v2_confidence=%.3f",
                            _v2_thread_phone,
                            _v2_dec.semantic_domain,
                            _v2_dec.turn_type,
                            _v2_dec.multi_domain_review_required,
                            list(_v2_dec.reason_codes),
                            float(_v2_dec.confidence),
                        )
            except Exception as _v2_exc:  # noqa: BLE001
                log.warning(
                    "[message_router] V2 cross-reference failed (non-fatal): %s",
                    _v2_exc,
                )
        # SHADOW LOGGING ONLY, below this line. sem_decision.domain is
        # deliberately never inspected to make a routing choice here -- no
        # branch below this point may call a handler, build a reply, or
        # write business data for ANY domain value (recruitment_conversation
        # / employee_conversation / no_match / identity_unresolved variant).
        # The legacy §7→§15 dispatch chain that follows this try/except is
        # the only code path in this function that can still do those
        # things, exactly as it did before Task 13 existed.
        log.info(
            "[router] task13_shadow sender=%s domain=%s confidence=%.2f "
            "rationale=%s requested_action=%s (non-authoritative, no side effect)",
            sender, sem_decision.domain, sem_decision.confidence,
            sem_decision.rationale, sem_decision.requested_action,
        )
    except Exception as _sem_exc:
        log.warning("[router] semantic_gate skipped (non-fatal): %s", _sem_exc)

    # ── Task 13 AUTHORITATIVE DISPATCH (flag-gated, 2026-08-22; split into
    # two independent flags same-day) ───────────────────────────────────────
    # `sem_decision` is bound only when the shadow try/except block above
    # completed without raising — the "sem_decision" in locals() guard means
    # a semantic_gate failure (caught above) makes this section a complete
    # no-op too, the same fail-open guarantee the shadow block already has.
    #
    # Gated end-to-end by THREE independent flags, all default OFF:
    # task13_recruitment_authoritative (recruitment_conversation delivery
    # for OPERATIONAL_ROLES senders only — unchanged scope),
    # task13_employee_authoritative (both employee_conversation branches —
    # verified-identity handoff AND unresolved-identity clarification-
    # only), and task13_candidate_recruitment_authoritative (Task-14B,
    # 2026-08-22 — recruitment_conversation delivery for candidate/
    # new_lead/unknown senders only, a SEPARATE flag from
    # task13_recruitment_authoritative by design — see that flag's own
    # rollout/evidence note). All three flags are read ONCE here and
    # handed to _task13_authoritative_dispatch() as plain bools; that
    # function is the single place that maps a domain+role to the flag
    # that governs it (see its own docstring/comments) — this call site
    # never itself decides which flag applies to which domain/role, so
    # there is exactly one place a cross-domain/cross-population gating
    # bug could hide, and it is covered by test_cross_domain_flag_
    # independence* and test_candidate_population_flag_independence* in
    # tests/unit/test_task13_authoritative_mode.py.
    #
    # With ALL THREE flags OFF, _task13_authoritative_dispatch() returns
    # (None, None) for every domain — process_message() falls through to
    # the unchanged section-7-through-15 chain below exactly as it did
    # before this section existed. When any flag is ON, a reply produced
    # for the domain/population it governs is returned immediately
    # (duplicate-reply guard: this is the ONLY return point in this
    # section, and it short-circuits before the legacy recruitment-
    # eligibility dispatch immediately below — let alone any later
    # section — can ever run for this turn).
    if "sem_decision" in locals():
        try:
            from modules.preflight_guard import is_feature_active as _task13_flag_active
            _t13_recruitment_flag = _task13_flag_active("task13_recruitment_authoritative", default=False)
            _t13_employee_flag = _task13_flag_active("task13_employee_authoritative", default=False)
            _t13_candidate_recruitment_flag = _task13_flag_active(
                "task13_candidate_recruitment_authoritative", default=False
            )
            _t13_reply, _t13_admin_note = await _task13_authoritative_dispatch(
                sem_decision,
                sender=sender,
                text=text,
                source=source,
                role_str=role_str,
                intent=intent,
                db_ctx=db_ctx_pre,
                history=history_pre,
                recruitment_authoritative=_t13_recruitment_flag,
                employee_authoritative=_t13_employee_flag,
                candidate_recruitment_authoritative=_t13_candidate_recruitment_flag,
                legacy_recruitment_eligible=bool(recruit_decision.get("eligible")),
                message_id=message_id,
            )
            if _t13_reply:
                _hk_fire(role_str, intent, "reply", f"task13-authoritative-{sem_decision.domain}")
                return _t13_reply, _t13_admin_note
        except Exception as _t13_auth_exc:
            log.warning("[router] task13 authoritative dispatch skipped (non-fatal): %s", _t13_auth_exc)

    if recruit_decision["eligible"]:
        db_ctx = await get_contact_context(sender)
        history = "\n".join(await get_recent_history(sender))
        rec_route = await route_recruitment_message(
            phone=sender,
            text=text,
            source=source,
            message_id=message_id,
            role=role_str,
            intent=intent,
            contact_context=db_ctx,
            history=history,
        )
        ai_reply = (rec_route.get("reply") or "").strip()
        if ai_reply:
            _hk_fire(role_str, intent, "reply", "recruitment")
            return ai_reply, None

    # ── 9. ESCORT ORDER (intent-triggered for non-registered senders) ─────────
    # Reached only when role_str wasn't in _ESCORT_ROLES at all (Tier 2 above
    # already returned otherwise) — by construction an unverified sender,
    # per Owner Decision 5. Never persisted as client_escort_buyer identity.
    if intent in ("client_order", "escort_duty") and _looks_like_escort_order(text):
        return await handle_escort_client_message(
            text,
            sender,
            source,
            is_verified=False,
        )

    # ── 10. EMPLOYEE ───────────────────────────────────────────────────────────
    if role_str == "employee":
        emp_id = identity.get("employee_id")

        if not emp_id:
            mismatch = await check_identity_mismatch(sender)
            if mismatch:
                _hk_fire(role_str, intent, "reply", "employee-identity-mismatch")
                return mismatch, None

        # ── Sprint-3A: Employee Conversation Workflow ──────────────────────────
        # Check for an active Sprint-3A conversation session FIRST.
        # This takes priority over the legacy verification flow so that an
        # ongoing AI conversation (reason → amount → payout → confirm) is
        # not interrupted by the older selfie/slip verification path.
        ec_session = await ec_get_session(sender)
        if ec_session:
            reply, admin_note = await ec_handle_payment_request(
                sender, text, source, emp_id, intent=intent
            )
            if reply:
                _hk_fire(role_str, intent, "reply", "employee-sprint3a-session")
                return reply, admin_note

        # Legacy verification session (selfie/slip flow) — still active for
        # release-slip submissions that pre-date Sprint-3A.
        session = await get_verification_session(sender)
        if session:
            _hk_fire(role_str, intent, "reply", "employee-verification-session")
            return await advance_verification(sender, text, source, emp_id)

        if intent == "attendance" or is_attendance_message(text):
            _hk_fire(role_str, "attendance", "draft", "employee-attendance")
            return await handle_attendance_message(text, sender, source)

        if intent == "slip_submission":
            _hk_fire(role_str, "slip_submission", "draft", "employee-slip")
            return await start_slip_verification(sender, text, source, emp_id)

        # Employee release messages request admin review. Only an admin's exact
        # [RELEASE CONFIRMED] message may finalize the release.
        if emp_id:
            from modules.escort_lifecycle import is_release_intent
            if is_release_intent(text):
                _hk_fire(role_str, intent, "draft", "employee-release-review")
                return (
                    "✅ আপনার release request পাওয়া গেছে। Admin যাচাই করে "
                    "[RELEASE CONFIRMED] পাঠানোর পর attendance ও settlement তৈরি হবে।",
                    {
                        "admin_phone": get_settings().admin_bridge1_number
                        or ((get_settings().admin_number_list or [""])[0]),
                        "bridge": "bridge1",
                        "purpose": "release-request",
                        "text": (
                            "⚠️ RELEASE REVIEW REQUIRED\n"
                            f"Employee ID: {emp_id}\nPhone: {sender}\n"
                            f"Message: {text}\n\n"
                            "Review the active program and send an exact "
                            "[RELEASE CONFIRMED] message with End Date, Shift, Days, "
                            "Food, Conveyance, Escort and Lighter."
                        ),
                    },
                )

        if intent in ("employee_salary_complaint", "legal_issue", "payment_issue"):
            await create_draft_reply(
                sender=sender,
                bridge=source,
                draft_text=f"[{intent}] {text}",
                role="employee",
                intent=intent,
                context='{"draft_type": "complaint"}',
                source_module="message_router",
            )
            log.warning("[COMPLAINT_DRAFT] intent=%s sender=%s emp_id=%s", intent, sender, emp_id)
            _hk_fire(role_str, intent, "draft", "employee-complaint")
            return "আপনার বার্তা পেয়েছি। দায়িত্বশীল ব্যক্তি শীঘ্রই যোগাযোগ করবেন।", None

        # 2026-07-22 (Final Instruction Section 5): an INFORMATIONAL salary
        # question ("আমার বেতন কবে দিবেন?") must reach the real payroll-backed
        # answer, not the payment-REQUEST workflow below. Previously this
        # check ran after the bare-keyword ec_trigger scan (TRIGGER_GROUPS
        # includes the plain word "বেতন"), so a genuine known employee's
        # salary question was intercepted by ec_handle_payment_request()
        # ("started an advance request, please enter an amount") before ever
        # reaching get_payroll_summary() -- the classifier's own
        # intent=="salary_query"/"payment_due" signal is deliberately
        # checked FIRST here, ahead of the generic keyword trigger, so the
        # already-more-precise classification wins over a bare substring
        # match. An employee genuinely wanting to REQUEST an advance still
        # reaches ec_trigger below (e.g. intent will classify as
        # advance_request/payment_issue, not salary_query/payment_due).
        if intent in ("salary_query", "payment_due") and emp_id:
            payroll = await get_payroll_summary(emp_id)
            db_ctx = format_payroll_context(payroll)
            # KB gap fix (2026-08-12, Owner-confirmed): db_ctx above only ever
            # carried this employee's OWN payroll numbers -- it never included
            # general salary POLICY content (payment-date rule, its
            # attendance condition, deductions, etc.), even though that
            # content already exists and is indexed for this role (see
            # knowledge_base/01_employee_knowledge/salary_policy.md, mirrored
            # customer-reachable at resources/generated_kb/...). Same
            # RAG-enrichment pattern Tier 15 already uses (line ~999 above) --
            # fail-safe, never blocks the reply if RAG is slow/unavailable.
            try:
                from modules.rag import safe_search as _rag_search
                _rag_hits = await _rag_search(text, k=3, role=role_str)
                if _rag_hits:
                    _rag_lines = [
                        f"- {h.get('title', '')}: {(h.get('text') or '').strip()[:300]}"
                        for h in _rag_hits
                        if (h.get("text") or "").strip()
                    ]
                    if _rag_lines:
                        db_ctx = (db_ctx + "\n\nKB Context:\n" + "\n".join(_rag_lines)).strip()
                        log.info("[router] employee-salary RAG enrichment added hits=%d sender=%s", len(_rag_hits), sender)
            except Exception as _rag_err:
                log.debug("[router] employee-salary rag enrichment skipped: %s", _rag_err)
            history = "\n".join(await get_recent_history(sender))
            # Hermes customer-dispatch Phase 1 (2026-08-12, Minimal
            # Modification Plan item 1): flag-gated substitution, default
            # OFF. Reuses the SAME pre-fetched db_ctx/history this branch
            # already builds -- Hermes gets no extra data access beyond
            # what ai.generate_reply() already receives here. Falls
            # through to the unchanged app.llm chain whenever the flag is
            # off or the Hermes call fails/times out/returns nothing --
            # never both, never neither.
            reply = None
            from modules.preflight_guard import is_feature_active
            if is_feature_active("hermes_customer_employee", default=False):
                from modules.hermes_dispatch import generate_reply as _hermes_generate_reply
                reply = await _hermes_generate_reply(
                    text, intent, db_ctx, history=history, role=role_str, source=source,
                    sender_phone=sender,
                )
            if not reply:
                reply = await ai.generate_reply(text, intent, db_ctx, history=history, role=role_str, source=source)
            # S6 (2026-08-08): db_ctx above is scoped to this employee's own
            # emp_id (server-resolved, not user-supplied), so their own
            # salary is expected and allowed here (enforce_general_reply_
            # confidentiality() already permits the sender's own phone the
            # same way) -- this only adds the same last-resort net Tier 15
            # has against credential-like output or an echoed prompt
            # section, and against any OTHER phone number appearing.
            from shared.reply_policy import (
                clean_general_reply as _clean_reply,
                enforce_general_reply_confidentiality as _enforce_confidentiality,
            )
            reply = _enforce_confidentiality(_clean_reply(reply), sender_phone=sender)
            _hk_fire(role_str, intent, "reply", "employee-payroll")
            return reply, None

        # Sprint-3A: Route payment requests through the AI conversation workflow.
        # This replaces the legacy start_advance_verification with a full
        # multi-step conversation that collects reason, amount, payout, and
        # produces a validated draft (NO transaction).
        #
        # Task-14B (2026-08-22): intent=="recruitment" is a more precise
        # classification than ec_detect_trigger()'s bare "বেতন"/salary
        # keyword substring match — same "precise classification wins over
        # a generic keyword trigger" principle the salary_query/payment_due
        # check just above already established (2026-07-22, Final
        # Instruction Section 5). Without this, G6's 2026-08-22 narrowing
        # (no longer silently dropping an operational sender's
        # recruitment-shaped message before this tier could ever run) would
        # let a message like "আমার ভাইয়ের Guard-এর চাকরি হবে? বেতন কত?" fall
        # through this bare keyword scan into an advance/payment-request
        # workflow instead. Does not affect a genuine advance request
        # (those classify as advance_request/payment_issue, not
        # recruitment) or any unchanged salary_query/payment_due behavior
        # above.
        # is_extracted_media guard (see process_message docstring, cf_W001):
        # don't let unclassified OCR/STT text trip this bare-keyword scan.
        ec_trigger = (
            ec_detect_trigger(text)
            if (intent != "recruitment" and not is_extracted_media)
            else None
        )
        if ec_trigger:
            reply, admin_note = await ec_handle_payment_request(
                sender, text, source, emp_id, intent=intent
            )
            if reply:
                _hk_fire(role_str, intent, "reply", "employee-sprint3a-trigger")
                return reply, admin_note
            # Fallback: if Sprint-3A didn't start (e.g. unknown trigger edge),
            # fall through to legacy advance verification.
            if is_advance_request(text):
                _hk_fire(role_str, intent, "draft", "employee-advance-legacy")
                return await start_advance_verification(sender, source, emp_id)

    # ── 11. ADVANCE/PAYMENT REQUEST (any role not already handled) ───────────
    # Catches employees, supervisors, unknown senders who ask for money.
    # Sprint-3A: Try the AI conversation workflow first (broader trigger
    # detection: advance/salary/food_bill/conveyance/emergency).
    ec_session_catchall = await ec_get_session(sender)
    if ec_session_catchall:
        reply, admin_note = await ec_handle_payment_request(
            sender, text, source, identity.get("employee_id"), intent=intent
        )
        if reply:
            _hk_fire(role_str, intent, "reply", "catchall-sprint3a-session")
            return reply, admin_note

    # 2026-08-21 (Salary context-routing bug, live-reproduced against
    # "Abuhanif"): ec_detect_trigger() is a bare keyword scan (TRIGGER_GROUPS
    # includes plain "salary"/"বেতন") that used to fire here for ANY
    # non-admin role, completely blind to recruitment context -- even when
    # §7/8 above had ALREADY determined this sender is recruitment-eligible
    # (active session, explicit trigger, detector hit, or bridge3's
    # dedicated-channel rule) and simply produced no reply text that turn
    # (e.g. a dedup/cooldown-suppressed enqueue). That let a recruitment
    # candidate's own salary/pay/duty question fall through into
    # ec_handle_payment_request()'s employee flow and get an
    # "Employee ID যাচাই" verification prompt instead. recruit_decision is
    # already computed once, above (§7/8) -- reusing it here (rather than a
    # second keyword check) means: an employee's real payment trigger is
    # completely unaffected (recruit_decision is unconditionally
    # {"eligible": False} for role_str=="employee", see line ~838), while a
    # sender §7/8 already judged recruitment-eligible never gets silently
    # re-routed into the employee-verification flow just because that
    # turn's recruitment reply happened to be empty.
    # Task-14B (2026-08-22): same "precise classification wins over a bare
    # keyword trigger" guard as the tier §10 ec_trigger check above,
    # extended to this catchall for the same G6-narrowing-exposure reason
    # (an employee/other-operational sender whose message classified
    # intent=="recruitment" is not covered by recruit_decision, which is
    # unconditionally {"eligible": False} for any role_str outside
    # candidate/new_lead/unknown).
    # is_extracted_media guard (see process_message docstring, cf_W001):
    # don't let unclassified OCR/STT text trip this bare-keyword scan.
    ec_trigger_catchall = (
        ec_detect_trigger(text)
        if (intent != "recruitment" and not is_extracted_media)
        else None
    )
    if ec_trigger_catchall and role_str != "admin" and not recruit_decision.get("eligible"):
        reply, admin_note = await ec_handle_payment_request(
            sender, text, source, identity.get("employee_id"), intent=intent
        )
        if reply:
            _hk_fire(role_str, intent, "reply", "catchall-sprint3a-trigger")
            return reply, admin_note

    # Legacy fallback: advance request via old verification flow
    existing_session = await get_verification_session(sender)
    if not existing_session and is_advance_request(text) and role_str != "admin":
        _hk_fire(role_str, intent, "draft", "advance-legacy")
        return await start_advance_verification(sender, source, identity.get("employee_id"))

    # ── 11.5 ROUTE B (2026-07-21) — escort / security_guard / candidate / unknown ─
    # Placed AFTER every mature, role-agnostic path above it (attendance,
    # escort-order safety net, Sprint-3A advance/payment catchall, legacy
    # advance verification) so none of that existing, tested behavior is
    # ever preempted — escort/security_guard have real wbom_employees rows
    # and must keep using the Sprint-3A conversation flow for genuine
    # advance requests exactly like before the escort/guard role split.
    # Route B only takes over what would otherwise fall through to the
    # office_location fast path / KB / generic multi-provider AI fallback
    # below — i.e. non-operational chat, office/deposit questions, and any
    # money/advance phrasing Sprint-3A's own trigger groups didn't already
    # catch. Ollama-only per Owner spec — see modules.route_b docstring.
    if role_str in ROUTE_B_ROLES:
        # Backstop (2026-07-22 Owner decision): _should_silent_skip() above
        # already catches name-matched contacts from their wbom_contacts row
        # at the very top of this function, before Route B ever runs — this
        # is a second, cheap check against identity_brain's own resolved
        # display_name for the rare case a brand-new contact hasn't reached
        # wbom_contacts yet (daily sync) but identity_brain already saw a
        # name via the bridge. Guarantees Route B/Ollama never auto-replies
        # to a Client/Escort/Office/Operation/Accountant/Security-Guard/
        # Al-Aqsa-Al-Manaratayn-named contact, matching the "Route A — no
        # Auto Reply" business rule.
        _bn = (identity.get("display_name") or "").lower()
        if _bn and _SILENT_SKIP_PATTERN.search(_bn):
            if "escort" in _bn and _looks_like_escort_order(text):
                return await handle_escort_client_message(text, sender, source, is_verified=False)
            log.info("[NO_REPLY_NAME_MATCH] %s → no reply, no draft (display_name=%r)", sender, identity.get("display_name"))
            return "", None
        from modules.route_b import handle_route_b_message
        # Task-14B (2026-08-22) dedup: pass the already-computed central
        # SemanticDecision (sem_decision, from the shadow block above) into
        # Route B so its own handoff evaluation can reuse it instead of
        # making a second, independent recruitment-domain LLM
        # classification for the same turn — see
        # modules.route_b_handoff.evaluate_handoff()'s own comment for the
        # exact dedup condition. None when the shadow block failed/raised
        # (same "sem_decision" in locals() guard used above), in which case
        # Route B's existing classify_route_b_category() fallback runs
        # completely unchanged.
        reply, admin_note = await handle_route_b_message(
            sender, text, source, identity, intent, message_id=message_id,
            central_semantic_decision=sem_decision if "sem_decision" in locals() else None,
        )
        if reply:
            _hk_fire(role_str, intent, "reply", "route-b")
            return reply, admin_note

    # ── 12. OFFICE LOCATION FAST PATH (KB-only, no AI, no reviewed memory) ──────
    # office_location intent skips reviewed-memory lookup and AI entirely.
    # The answer is always b11_office_address — deterministic and safe for all roles.
    if intent == "office_location":
        office_reply = await kb_get_reply(text, intent)
        if office_reply:
            log.info("[OFFICE_FAST] %s → office_location → KB direct return", sender)
            return office_reply, None
        # 2026-07-21: the separate hardcoded copy that used to live here was
        # redundant with — and had drifted out of sync with —
        # modules.knowledge_base.get_reply()'s own two-layer fallback (DB row
        # b11_office_address, then its internal _FALLBACK constant). Removed
        # per the KB-consolidation directive; kb_get_reply() above is now the
        # single source of truth and should never actually reach this line.
        log.error("[OFFICE_FAST] %s → office_location → KB unavailable (DB + fallback both empty)", sender)
        return "", None

    # ── 13. KNOWLEDGE BASE (all roles, all other intents) ─────────────────────
    kb_reply = await kb_get_reply(text, intent)
    if kb_reply:
        _hk_fire(role_str, intent, "reply", "kb")
        return kb_reply, None

    # ── 14. REVIEWED REPLY LOOKUP ─────────────────────────────────────────────
    # Check admin-approved edited replies before falling back to LLM.
    # Fails safe: any exception is caught and routing continues to AI fallback.
    try:
        from modules import reviewed_reply_memory as _rrm
        _reviewed = await _rrm.lookup_reviewed_reply(
            sender_phone=sender,
            intent=intent,
            role=role_str,
        )
        if _reviewed:
            log.info(
                "[reviewed] hit sender=%s intent=%s scope=%s id=%s",
                sender, intent, _reviewed.get("match_scope"), _reviewed.get("id"),
            )
            _hk_fire(role_str, intent, "reply", "reviewed-memory")
            return _reviewed["reply_text"], None
    except Exception as _rrm_err:
        log.debug("[reviewed] lookup non-fatal error: %s", _rrm_err)

    # ── 15. AI FALLBACK ────────────────────────────────────────────────────────
    db_ctx = await get_contact_context(sender)
    # Phase 3, 2026-08-25 recovery pass ("employee-info grounding gap"):
    # a known employee's general company-rules/leave/attendance/duty
    # question landed here with only get_contact_context() + generic
    # top-3 RAG below -- the same generic RAG mechanism this codebase's
    # own comments (modules/hermes_dispatch.py, 2026-08-12) document as
    # unreliable for surfacing a single authoritative file's content.
    # build_employee_context() reads master_recruitment_employee_
    # information.txt's অংশ-১ (company policy) section directly, the same
    # dedicated-whole-file-read pattern already used for Recruitment.
    # Scoped to employee/supervisor roles only -- unrelated roles
    # (accountant, escort_client, etc.) don't get internal HR policy
    # text injected into their general-fallback context. Fail-safe:
    # never blocks the reply if the source file is unavailable.
    if role_str in ("employee", "supervisor"):
        try:
            from modules.hermes_dispatch import build_employee_context_with_directives as _build_emp_ctx
            _emp_policy = await _build_emp_ctx(text)
            if _emp_policy:
                db_ctx = (db_ctx + "\n\nCompany Policy:\n" + _emp_policy).strip()
        except Exception as _emp_ctx_err:
            log.debug("[router] employee policy context skipped: %s", _emp_ctx_err)
    # Phase 4 — Hybrid RAG enrichment: enrich general queries with KB context
    try:
        from modules.rag import safe_search as _rag_search
        _rag_hits = await _rag_search(text, k=3, role=role_str)
        if _rag_hits:
            _rag_lines = [
                f"- {h.get('title', '')}: {(h.get('text') or '').strip()[:300]}"
                for h in _rag_hits
                if (h.get("text") or "").strip()
            ]
            if _rag_lines:
                db_ctx = (db_ctx + "\n\nKB Context:\n" + "\n".join(_rag_lines)).strip()
                log.info("[router] RAG enrichment added hits=%d sender=%s", len(_rag_hits), sender)
    except Exception as _rag_err:
        log.debug("[router] rag enrichment skipped: %s", _rag_err)
    history = "\n".join(await get_recent_history(sender))
    # Hermes customer-dispatch Phase 1 (2026-08-12, Minimal Modification
    # Plan item 1): flag-gated substitution, default OFF, same
    # fail-through contract as the employee branch above -- see that
    # branch's comment for the full rationale. This is the general/
    # company-policy/unrecognized-intent catch-all tier.
    reply = None
    from modules.preflight_guard import is_feature_active
    if is_feature_active("hermes_customer_general", default=False):
        from modules.hermes_dispatch import generate_reply as _hermes_generate_reply
        reply = await _hermes_generate_reply(
            text, intent, db_ctx, history=history, role=role_str, source=source,
            sender_phone=sender,
        )
    if not reply:
        reply = await ai.generate_reply(text, intent, db_ctx, history=history, role=role_str, source=source)
    # Populate provider_used from the provider attempt trace (zero cost — already computed).
    try:
        _attempts = ai.get_last_attempts()
        if _attempts and _hk_record is not None:
            _hk_record.provider_used = _attempts[0].get("provider", "")
    except Exception:
        pass
    # Phase 4 Step 4 — strip artifact prefixes from structured prompt output
    from shared.reply_policy import (
        clean_general_reply as _clean_reply,
        enforce_general_reply_confidentiality as _enforce_confidentiality,
    )
    reply = _enforce_confidentiality(_clean_reply(reply), sender_phone=sender)

    # Retired (2026-08-12, Owner decision — see the Tier-15 shadow forensics
    # report and modules/hermes_dispatch.py's own Phase 1 docstring): this
    # tier used to also run modules.conversation_layer.generate_universal_reply()
    # in shadow mode here (Phase 2, 2026-08-06 — 0cadbfc), purely for
    # side-by-side comparison logging, gated live by
    # settings.tier15_universal_responder_live (always False in production;
    # never enabled outside a unit test's own monkeypatch — see the
    # forensics report). Retired because the shadow pipeline reasoned from
    # its own independently-fetched context rather than the identity/intent/
    # routing context this tier already resolved, and because it issued a
    # second, redundant ai.generate_reply() call on every Tier-15 message
    # regardless of whether anything used its output (confirmed via
    # llm_learning_memory: two real provider calls per turn while shadow
    # mode was active). Reply generation above (Hermes when
    # hermes_customer_general is on, else the unchanged app.llm chain) is
    # now the ONLY customer-facing AI generation path this tier has.
    # generate_universal_reply() itself is not deleted — see its own
    # docstring — only this call site is removed. Historical shadow-mode
    # log lines and hermes_decision_audit_log rows from before this date
    # are untouched and remain available as audit history.

    # Hermes Kernel C0.2: fire shadow audit log for this turn (Tier 15 — AI reply).
    _hk_fire(role_str, intent, "reply", "ai-fallback-tier15")
    return reply, None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fmt_ingest_reply(result: dict) -> str:
    """Format a human-readable WhatsApp reply from a payment ingest result dict."""
    if not result.get("ok"):
        reason = result.get("reason", "অজানা সমস্যা")
        return f"❌ রেকর্ড করা যায়নি: {reason}"
    status = result.get("status", "")
    emp = result.get("employee_name") or result.get("matched_employee_id") or "?"
    amt = result.get("amount", 0)
    method = result.get("method", "")
    if status == "duplicate":
        ref = result.get("transaction_id") or result.get("staging_id")
        return f"⚠️ ইতিমধ্যে রেকর্ড আছে (#{ref})।"
    if status == "finalized":
        return (
            f"✅ Final transaction saved — {result.get('employee_name') or emp}, "
            f"৳{amt:.0f} ({method})।\n"
            f"Txn ID: {result.get('transaction_id')}"
        )
    if status == "unmatched":
        mob = result.get("mobile", "?")
        return (
            f"⚠️ কর্মী খুঁজে পাওয়া যায়নি ({mob})।\n"
            f"পেমেন্ট pending হিসেবে সেভ হয়েছে (#{result.get('staging_id')})।\n"
            f"Admin অনুমোদন প্রয়োজন।"
        )
    if status == "auto_approved":
        return f"✅ রেকর্ড হয়েছে — {emp}, ৳{amt:.0f} ({method}) — auto-approved।"
    return f"✅ রেকর্ড হয়েছে — {emp}, ৳{amt:.0f} ({method}) — pending admin approval।"


def get_primary_admin() -> str:
    settings = get_settings()
    admins = settings.admin_number_list
    return admins[0] if admins else settings.admin_meta_number


def _looks_like_escort_order(text: str) -> bool:
    """Strict code-based check for a real escort order, not a complaint/slip."""
    return _is_strict_escort_order_text(text)


def _resolve_forward_target(command_text: str, settings) -> Optional[str]:
    """Determine who to forward the secondary message to based on command type."""
    t = command_text.strip().lower()
    if t.startswith(("paid", "advance", "approved")):
        return settings.accountant_phone or None
    if t.startswith("escortconfirm"):
        # Buyer phone is embedded in the result from _cmd_escort_confirm
        # The escort confirm handler already returns None if no buyer found
        # Forward via the admin_note mechanism in the caller
        return None  # handled by the tuple itself
    return None


async def get_recent_history(phone: str, limit: int = 5) -> list:
    """Return recent BIDIRECTIONAL message texts for a given phone number.

    Phase 2 (2026-08-06, Owner-approved "Universal AI Responder"):
    extended from inbound-only to include the bot's own outbound replies
    too, so every caller (recruitment's live LLM layer, Tier 15's new
    Universal Responder, recruitment_detector) gets real back-and-forth
    context instead of only the human's side. `sender_number` represents
    the counterparty phone for BOTH directions in this table -- confirmed
    against live data, an outbound row's sender_number is still the
    human's number, not the bot's -- so no join or recipient-tracking
    change was needed, only dropping the `direction = 'inbound'` filter.
    Each line is prefixed "Them: " (inbound) or "You: " (outbound) so a
    downstream prompt can tell who said what; this is a formatting change
    for every existing caller of this function, all of which build a
    plain history block for an LLM prompt where this is a pure
    improvement, not a behavior-affecting one."""
    try:
        rows = await fetch_all(
            """SELECT message_body, direction
               FROM wbom_whatsapp_messages
               WHERE sender_number = $1 AND direction IN ('inbound', 'outbound')
               ORDER BY received_at DESC LIMIT $2""",
            phone, limit,
        )
        out = []
        for r in reversed(rows):
            body = (r.get("message_body") or "").strip()
            if not body:
                continue
            prefix = "You: " if r.get("direction") == "outbound" else "Them: "
            out.append(prefix + body)
        return out
    except Exception as e:
        log.debug(f"get_recent_history error: {e}")
        return []


async def get_contact_context(phone: str) -> str:
    lines: list[str] = []
    try:
        phone_variants = _phone_variants(phone)

        contact = None
        for v in phone_variants:
            contact = await fetch_one(
                """SELECT c.display_name, c.company_name, rt.relation_name
                   FROM wbom_contacts c
                   LEFT JOIN wbom_relation_types rt ON rt.relation_type_id = c.relation_type_id
                   WHERE c.whatsapp_number = $1 AND c.is_active = true
                   LIMIT 1""",
                v,
            )
            if contact:
                break

        if contact:
            relation = contact.get("relation_name") or "Contact"
            company = f" ({contact.get('company_name','')})" if contact.get("company_name") else ""
            lines.append(f"{relation}: {contact.get('display_name','?')}{company}")

        emp = None
        for v in phone_variants:
            emp = await fetch_one(
                "SELECT employee_name, designation, basic_salary, status FROM wbom_employees WHERE employee_mobile=$1",
                v,
            )
            if emp:
                break
        if emp:
            lines.append(
                f"কর্মী: {emp['employee_name']}, {emp.get('designation','')}, "
                f"বেতন: ৳{emp['basic_salary']}, স্ট্যাটাস: {emp['status']}"
            )

        # S2: inject user_memory facts as conversational context (read-only).
        # Priority: authoritative DB > memory. Memory is labeled as context only.
        mem_rows = []
        for v in phone_variants:
            mem_rows = await fetch_all(
                """SELECT memory_type, content FROM user_memory
                   WHERE phone_canonical = $1
                   ORDER BY created_at DESC LIMIT 5""",
                v,
            )
            if mem_rows:
                break
        if mem_rows:
            mem_lines = [f"- [{m['memory_type']}] {m['content'][:120]}" for m in mem_rows]
            lines.append("স্মৃতি-প্রসঙ্গ (context only — not authoritative):\n" + "\n".join(mem_lines))

    except Exception as e:
        log.debug(f"Context fetch error: {e}")
    return "\n".join(lines)


async def _cmd_admin_list() -> str:
    try:
        rows = await fetch_all(
            """SELECT id, recipient, intent, status, LEFT(reply_text, 80) AS preview, created_at
               FROM fazle_draft_replies
               WHERE COALESCE(status, 'pending') = 'pending'
               ORDER BY created_at DESC LIMIT 10"""
        )
        if not rows:
            return "✅ কোনো পেন্ডিং ড্রাফট নেই।"
        lines = [f"📋 পেন্ডিং ড্রাফট ({len(rows)} টি):\n"]
        for r in rows:
            lines.append(
                f"#{r['id']} [{r.get('intent','?')}] → {r.get('recipient','?')}\n"
                f"   {r.get('preview','')!r}"
            )
        lines.append("\nঅনুমোদন: APPROVE <id> | বাতিল: REJECT <id>")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ ড্রাফট লোড ব্যর্থ: {e}"


# ── Message normalization ──────────────────────────────────────────────────────
_BANGLA_TO_EN = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_EMOJI_ONLY_RE = re.compile(
    r"^[\U0001F000-\U0001FFFF\U00002600-\U000027BF\U0000FE00-\U0000FEFF\s]+$"
)
_MESSAGE_MAX_LEN = int(os.getenv("MESSAGE_MAX_LEN", "4096"))


def normalize_incoming_message(body: str) -> dict:
    """Normalize a raw WhatsApp message body before routing.

    Returns:
      {"body": str, "type": "normal"|"empty"|"emoji_only"|"truncated", "truncated": bool}
    """
    if not body or not body.strip():
        return {"body": "", "type": "empty", "truncated": False}

    body = body.strip()
    truncated = False

    if len(body) > _MESSAGE_MAX_LEN:
        body = body[:_MESSAGE_MAX_LEN]
        truncated = True

    # Normalize Bangla digits → English for consistent downstream processing
    body = body.translate(_BANGLA_TO_EN)

    if _EMOJI_ONLY_RE.match(body):
        return {"body": body, "type": "emoji_only", "truncated": truncated}

    return {"body": body, "type": "truncated" if truncated else "normal", "truncated": truncated}


# ── Timeout wrapper ────────────────────────────────────────────────────────────
_ROUTE_TIMEOUT_S: float = float(os.getenv("MESSAGE_ROUTE_TIMEOUT", "30"))


async def route_message_with_timeout(
    sender: str,
    text: str,
    source: str,
    timeout: float = _ROUTE_TIMEOUT_S,
) -> "tuple[str, Optional[dict]]":
    """Call process_message with a configurable timeout.

    Returns (reply, admin_notification). On timeout returns a Bangla retry
    message instead of hanging. Timeout configurable via MESSAGE_ROUTE_TIMEOUT.
    """
    try:
        return await asyncio.wait_for(
            process_message(sender, text, source),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning(
            "[router] timeout sender=%s source=%s timeout=%.1fs",
            sender, source, timeout,
        )
        return "অনুগ্রহ করে আবার চেষ্টা করুন।", None
