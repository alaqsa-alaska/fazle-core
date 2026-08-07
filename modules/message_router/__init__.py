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
    if settings.accountant_phone and sender == settings.accountant_phone:
        return True, f"accountant phone match ({sender})"
    try:
        contact = None
        for v in _phone_variants(sender):
            contact = await fetch_one(
                "SELECT display_name FROM wbom_contacts"
                " WHERE whatsapp_number = $1 AND is_active = true LIMIT 1",
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


async def process_message(
    sender: str, text: str, source: str, *, message_id: Optional[str] = None
) -> tuple[str, Optional[dict]]:
    """
    Route one inbound message and return (reply_text, admin_notification | None).
    Does NOT send anything — callers handle delivery.

    message_id: the bridge's own id for this message, when the caller has one
    (see modules.bridge_poller.process_bridge_inbound). Threaded through to
    handle_admin_escort_completion() for the draft-confirmation review queue's
    dedup identity (migration 052) — optional, only that one path uses it.
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
        )
        _hk_evt001 = KernelEvent(
            trace_id=_hk_record.trace_id,
            event_name="EVT-001 message.received",
            payload={"sender_phone": sender, "source": source},
        )
    except Exception:
        _hk_record = None  # type: ignore[assignment]
        _hk_evt001 = None  # type: ignore[assignment]

    def _hk_fire(role: str, intent: str, action: str, explanation: str = "") -> None:
        """Fire-and-forget shadow log; never raises; never blocks."""
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
    if _hk_record is not None:
        _hk_record.identity_role = role_str
        _hk_record.identity_confidence = identity.get("confidence")  # 0.0-1.0 float
        _hk_record.identity_source = identity.get("identity_source", "")

    # ── 1. FAMILY — personal, no business workflow ────────────────────────────
    if role_str == "family":
        name = identity.get("display_name") or "আপনি"
        return (
            f"আস-সালামু আলাইকুম {name}! 😊\n"
            f"এটা অফিসের নম্বর — ব্যক্তিগত কথা ফোনে বলুন।",
            None,
        )

    if role_str != "admin" and _looks_like_slip_or_complaint(text):
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
            return await handle_escort_client_message(
                text,
                sender,
                source,
                is_verified=is_verified_escort,
            )
        if escort_intent or _looks_like_escort_buying_intent(text):
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
        if is_completed_escort_draft(text):
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
            ai_reply = await process_admin_ai_command(
                text, sender_phone=sender
            )
            if ai_reply is not None:
                return ai_reply, None

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
        # called this engine "Hermes" — it is not. This is fazle-core's own
        # AI console (read-only RT-*/BUS-*/MEM-* tool registry), a
        # deliberately separate system from the real Hermes (assistant-
        # platform's BUILD/RUN-capable agent, reached via a dedicated
        # HERMES <request> command — see admin_directives/router.py). Do
        # not reintroduce the naming conflation.
        console_reply = await process_admin_natural_query(text, sender)
        if console_reply:
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
    log.info(f"[INTENT] {sender} → {intent}")

    # Trusted operational identities must never enter any recruitment reply
    # fallback, even if the message text itself looks recruitment-related.
    if intent == "recruitment" and role_str not in ("candidate", "new_lead", "unknown"):
        log.info(
            "[RECRUITMENT_BLOCKED_OPERATIONAL] sender=%s role=%s source=%s",
            sender, role_str, identity.get("identity_source"),
        )
        return "", None

    # ── 6. ACCOUNTANT ─────────────────────────────────────────────────────────
    if role_str == "accountant":
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
        return reply, None

    # ── 7/8. Recruitment — one eligibility decision for every unknown/candidate ─
    if role_str in ("candidate", "new_lead", "unknown"):
        recruit_decision = await recruitment_eligibility(
            sender, text, role=role_str, intent=intent,
        )
    else:
        recruit_decision = {"eligible": False}

    if recruit_decision["eligible"]:
        db_ctx = await get_contact_context(sender)
        history = "\n".join(await get_recent_history(sender))
        rec_route = await route_recruitment_message(
            phone=sender,
            text=text,
            source=source,
            role=role_str,
            intent=intent,
            contact_context=db_ctx,
            history=history,
        )
        ai_reply = (rec_route.get("reply") or "").strip()
        if ai_reply:
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
                return mismatch, None

        # ── Sprint-3A: Employee Conversation Workflow ──────────────────────────
        # Check for an active Sprint-3A conversation session FIRST.
        # This takes priority over the legacy verification flow so that an
        # ongoing AI conversation (reason → amount → payout → confirm) is
        # not interrupted by the older selfie/slip verification path.
        ec_session = await ec_get_session(sender)
        if ec_session:
            reply, admin_note = await ec_handle_payment_request(
                sender, text, source, emp_id
            )
            if reply:
                return reply, admin_note

        # Legacy verification session (selfie/slip flow) — still active for
        # release-slip submissions that pre-date Sprint-3A.
        session = await get_verification_session(sender)
        if session:
            return await advance_verification(sender, text, source, emp_id)

        if intent == "attendance" or is_attendance_message(text):
            return await handle_attendance_message(text, sender, source)

        if intent == "slip_submission":
            return await start_slip_verification(sender, text, source, emp_id)

        # Employee release messages request admin review. Only an admin's exact
        # [RELEASE CONFIRMED] message may finalize the release.
        if emp_id:
            from modules.escort_lifecycle import is_release_intent
            if is_release_intent(text):
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
            history = "\n".join(await get_recent_history(sender))
            reply = await ai.generate_reply(text, intent, db_ctx, history=history, role=role_str, source=source)
            return reply, None

        # Sprint-3A: Route payment requests through the AI conversation workflow.
        # This replaces the legacy start_advance_verification with a full
        # multi-step conversation that collects reason, amount, payout, and
        # produces a validated draft (NO transaction).
        ec_trigger = ec_detect_trigger(text)
        if ec_trigger:
            reply, admin_note = await ec_handle_payment_request(
                sender, text, source, emp_id
            )
            if reply:
                return reply, admin_note
            # Fallback: if Sprint-3A didn't start (e.g. unknown trigger edge),
            # fall through to legacy advance verification.
            if is_advance_request(text):
                return await start_advance_verification(sender, source, emp_id)

    # ── 11. ADVANCE/PAYMENT REQUEST (any role not already handled) ───────────
    # Catches employees, supervisors, unknown senders who ask for money.
    # Sprint-3A: Try the AI conversation workflow first (broader trigger
    # detection: advance/salary/food_bill/conveyance/emergency).
    ec_session_catchall = await ec_get_session(sender)
    if ec_session_catchall:
        reply, admin_note = await ec_handle_payment_request(
            sender, text, source, identity.get("employee_id")
        )
        if reply:
            return reply, admin_note

    ec_trigger_catchall = ec_detect_trigger(text)
    if ec_trigger_catchall and role_str != "admin":
        reply, admin_note = await ec_handle_payment_request(
            sender, text, source, identity.get("employee_id")
        )
        if reply:
            return reply, admin_note

    # Legacy fallback: advance request via old verification flow
    existing_session = await get_verification_session(sender)
    if not existing_session and is_advance_request(text) and role_str != "admin":
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
        reply, admin_note = await handle_route_b_message(
            sender, text, source, identity, intent, message_id=message_id,
        )
        if reply:
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
            return _reviewed["reply_text"], None
    except Exception as _rrm_err:
        log.debug("[reviewed] lookup non-fatal error: %s", _rrm_err)

    # ── 15. AI FALLBACK ────────────────────────────────────────────────────────
    db_ctx = await get_contact_context(sender)
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
    reply = await ai.generate_reply(text, intent, db_ctx, history=history, role=role_str, source=source)
    # Phase 4 Step 4 — strip artifact prefixes from structured prompt output
    from shared.reply_policy import clean_general_reply as _clean_reply
    reply = _clean_reply(reply)

    # Phase 2 (2026-08-06, Owner-approved "Universal AI Responder", shadow
    # mode -- see management_decisions.md "Universal AI Responder — Phase
    # 2"): compute the upgraded KB/RAG/faithfulness-validated pipeline in
    # parallel and log it alongside today's bare-AI reply for comparison.
    # Does NOT affect what's returned/sent unless
    # settings.tier15_universal_responder_live is explicitly True (default
    # False) -- a separate, later Owner decision. This is the ONLY change
    # at Tier 15; every upstream tier/gate (family, escort, admin,
    # accountant, employee's own branches, Route B) is unreached code from
    # here and is completely unaffected.
    try:
        from modules.conversation_layer import generate_universal_reply
        universal_reply = await generate_universal_reply(
            phone=sender, text=text, source=source,
            intent=intent, role=role_str, history=history, db_ctx=db_ctx,
        )
        log.info(
            "[tier15_shadow] sender=%s old_reply=%r new_reply=%r",
            sender, (reply or "")[:200], (universal_reply or "")[:200],
        )
        if get_settings().tier15_universal_responder_live and universal_reply:
            return universal_reply, None
    except Exception as e:
        log.debug("[tier15_shadow] universal responder failed (non-fatal): %s", e)

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
