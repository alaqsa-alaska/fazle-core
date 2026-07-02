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

import logging
import re
from typing import Optional

from app.config import get_settings
from app.database import fetch_one, fetch_all, execute
from shared.draft_reply import create_draft_reply
from modules.intent import classify
from modules.phone_normalizer import normalize_phone
from modules.identity_brain import detect_identity
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
)
from modules.conversation_layer import generate_recruitment_reply
from modules.admin_commands import is_admin_command, process_admin_command, list_payment_drafts
from modules.admin_commands.nl_router import is_nl_admin_query, process_nl_admin_query
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

log = logging.getLogger("fazle.router")

# Roles that trigger the escort client flow
_ESCORT_ROLES = frozenset({"escort_client", "client_escort_buyer", "vip_client", "repeat_client"})

# Legacy fallback only; runtime source of truth is app.config draft-always settings.
_SILENT_SKIP_NAME_TOKENS: tuple[str, ...] = ("al-aqsa", "escort", "client", "operation", "tcis", "gms", "dalal", "office")

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

_ESCORT_ASSIGNMENT_FIELDS_RE = re.compile(
    r"escort\s*(?:name)?\s*:\s*\S.+?escort\s*(?:mobile|mob|number)?\s*:\s*(?:\+?880|0)1[3-9][\d\s\-]{8,}",
    re.IGNORECASE | re.DOTALL,
)
_SLIP_OR_RELEASE_HINT_RE = re.compile(
    r"(?:\b(?:release|released|completion|completed|duty\s+slip|release\s+slip|"
    r"food|conveyance|transport|car\s*fare)\b|ভাড়া|ভাড়া|খাবার|রিলিজ|স্লিপ|পালিয়েছে|পালিয়েছে)",
    re.IGNORECASE,
)


def _phone_variants(phone: str) -> list[str]:
    """Return all normalized forms of a phone number for DB lookup."""
    return get_phone_variants(phone)


def _contact_name_has_escort_order_prefix(identity: dict) -> bool:
    name = (identity.get("display_name") or "").strip().lower()
    return name.startswith(("escort", "tcis"))


def _has_client_supplied_escort_assignment(text: str) -> bool:
    return bool(_ESCORT_ASSIGNMENT_FIELDS_RE.search(text or ""))


def _looks_like_slip_or_complaint(text: str) -> bool:
    return bool(_SLIP_OR_RELEASE_HINT_RE.search(text or "")) or _has_client_supplied_escort_assignment(text)


def _is_strict_escort_order_text(text: str) -> bool:
    """Code-based order gate: mother vessel + lighter + master mobile, no assignment fields."""
    if not text or _looks_like_slip_or_complaint(text):
        return False
    try:
        order = parse_escort_message(text)
    except Exception as exc:
        log.debug("[escort-gate] parse failed: %s", exc)
        return False
    if not order.get("mother_vessel"):
        return False
    return any(
        (lighter.get("lighter_vessel") and lighter.get("master_mobile"))
        for lighter in (order.get("lighters") or [])
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
    """Return (should_skip, reason) for blocked or draft-only contacts.

    Rules:
      1. sender == ACCOUNTANT_PHONE → skip
      2. configured draft-always display_name match → caller creates admin draft
      3. blocked contact role → skip
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
            name_lower = (contact.get("display_name") or "").lower()
            name_tokens = settings.draft_always_name_list or list(_SILENT_SKIP_NAME_TOKENS)
            for token in name_tokens:
                if token in name_lower:
                    return True, f"display_name contains '{token}' ({contact['display_name']!r})"
            for prefix in settings.draft_name_prefix_list:
                if prefix and name_lower.startswith(prefix):
                    return True, f"display_name starts with '{prefix}' ({contact['display_name']!r})"
    except Exception as _e:
        log.debug("[SILENT_SKIP] contact lookup error for %s: %s", sender, _e)

    # blocked role — admin dashboard থেকে block করা নম্বর
    try:
        for v in _phone_variants(sender):
            _role_row = await fetch_one(
                "SELECT role FROM fazle_contact_roles"
                " WHERE phone = $1 AND is_active = true"
                " ORDER BY priority DESC LIMIT 1",
                v,
            )
            if _role_row and _role_row.get("role") == "blocked":
                return True, f"phone blocked by admin ({sender})"
    except Exception as _be:
        log.debug("[SILENT_SKIP] blocked role check error for %s: %s", sender, _be)

    return False, ""


def _is_safe_autosend_intent(intent: str, role: str) -> bool:  # noqa: ARG001
    """Return True if this intent is safe for auto-send without manual review.

    Safe: salary_query, payment_due, advance_request, recruitment.
    Unsafe: employee_salary_complaint, legal_issue, payment_issue, release-slip estimates.
    """
    return intent in _SAFE_AUTOSEND_INTENTS


async def process_message(
    sender: str, text: str, source: str
) -> tuple[str, Optional[dict]]:
    """
    Route one inbound message and return (reply_text, admin_notification | None).
    Does NOT send anything — callers handle delivery.
    """
    settings = get_settings()
    sender = normalize_phone(sender) or sender.strip()

    # TASK 2: Silent-skip excluded contacts before any processing or draft creation.
    # Escort work messages are operational inputs, so they must still create
    # admin-review drafts even when the contact name contains "client/escort".
    _skip, _skip_reason = await _should_silent_skip(sender)
    if _skip:
        if is_completed_escort_draft(text) or _looks_like_escort_order(text):
            log.info("[SILENT_SKIP_BYPASS] %s → escort workflow allowed (%s)", sender, _skip_reason)
        elif "display_name contains" in _skip_reason or "display_name starts with" in _skip_reason:
            log.info("[DRAFT_ONLY_CONTACT] %s → admin draft, no auto-reply (%s)", sender, _skip_reason)
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
            return "", None

    identity = await detect_identity(sender, text)
    role_str = identity["role"]
    log.info(
        f"[IDENTITY] {sender} → {role_str} "
        f"(conf={identity['identity_confidence']}, src={identity['identity_source']})"
    )

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

    # ── 2. ESCORT CLIENT roles — never reply, always draft to admin ───────────
    if role_str in _ESCORT_ROLES:
        has_order_prefix = _contact_name_has_escort_order_prefix(identity)
        intent_check = classify(text)
        strict_order = _is_strict_escort_order_text(text)
        if (has_order_prefix or role_str in {"escort_client", "client_escort_buyer"}) and strict_order:
            return await handle_escort_client_message(text, sender, source)
        if intent_check in ("client_order", "escort_duty") or _looks_like_escort_order(text):
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
            return (
                "Escort confirmation is finalized only after the bridge sends it to an authorised escort client. "
                "This admin-side text was not applied directly.",
                None,
            )

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

        lower = text.lower()
        if "draft" in lower or "পেন্ডিং" in lower or "list" in lower:
            return await _cmd_admin_list(), None
        if "payment" in lower or "পেমেন্ট" in lower or "paid" in lower:
            return await list_payment_drafts(sender), None
        if "attendance" in lower or "হাজিরা" in lower or "উপস্থিতি" in lower:
            return await get_attendance_summary(), None

        # B25 (H4): Admin sent something unrecognised. Do NOT fall through to
        # LLM — that produced garbage apologies that got queued as new drafts.
        # Return inline help so admin sees the correct command syntax instead.
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
        reply = await ai.generate_reply(text, intent, db_ctx, role=role_str, source=source)
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
        ai_reply = await generate_recruitment_reply(
            phone=sender,
            text=text,
            source=source,
            contact_context=db_ctx,
            history=history,
        )
        if ai_reply:
            return ai_reply, None

    # ── 9. ESCORT ORDER (intent-triggered for non-registered senders) ─────────
    if intent in ("client_order", "escort_duty"):
        return await handle_escort_client_message(text, sender, source)

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

        if intent in ("salary_query", "payment_due") and emp_id:
            payroll = await get_payroll_summary(emp_id)
            db_ctx = format_payroll_context(payroll)
            reply = await ai.generate_reply(text, intent, db_ctx, role=role_str, source=source)
            return reply, None

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

    # ── 12. OFFICE LOCATION FAST PATH (KB-only, no AI, no reviewed memory) ──────
    # office_location intent skips reviewed-memory lookup and AI entirely.
    # The answer is always b11_office_address — deterministic and safe for all roles.
    if intent == "office_location":
        office_reply = await kb_get_reply(text, intent)
        if office_reply:
            log.info("[OFFICE_FAST] %s → office_location → KB direct return", sender)
            return office_reply, None
        # Hardcoded fallback if KB unavailable
        return (
            "আমাদের অফিস:\n"
            "আল-আকসা সিকিউরিটি অ্যান্ড লজিস্টিকস সার্ভিসেস লিমিটেড\n"
            "আগ্রপাড়া, ভিক্টোরিয়া গেইট নং ১, খোকনের বিল্ডিং (২য় তলা)\n"
            "পাহাড়তলী, চট্টগ্রাম সিটি কর্পোরেশন\n\n"
            "সকাল ৯টা – বিকাল ৫টা (শুক্রবার বন্ধ)\n"
            "WhatsApp: 01958 122322"
        ), None

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
        from modules.rag import search as _rag_search
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
    reply = await ai.generate_reply(text, intent, db_ctx, role=role_str, source=source)
    # Phase 4 Step 4 — strip artifact prefixes from structured prompt output
    from shared.reply_policy import clean_general_reply as _clean_reply
    return _clean_reply(reply), None


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
    """Return recent inbound message texts for a given phone number."""
    try:
        rows = await fetch_all(
            """SELECT message_body
               FROM wbom_whatsapp_messages
               WHERE sender_number = $1 AND direction = 'inbound'
               ORDER BY received_at DESC LIMIT $2""",
            phone, limit,
        )
        return [r["message_body"] for r in reversed(rows) if r.get("message_body")]
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
