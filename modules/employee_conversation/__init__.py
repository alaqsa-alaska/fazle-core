"""
Fazle Core — Sprint-3A: Employee Conversation + Verification + Draft Generation
==============================================================================

This module implements the AI Conversation Workflow for employee payment
requests (Advance, Salary, Food Bill, Conveyance, Emergency, etc.).

Flow:
    Employee Request
        → Conversation
        → Verification
        → Knowledge Base
        → Draft
        → Admin

HARD RULE (Sprint-3A):
    This module NEVER calls create_transaction(), _upsert_ledger(),
    accounting_worker(), or any financial-write function.
    It produces ONLY a draft in fazle_payment_drafts with status='pending'
    and a 24-hour expiry.  No ledger, no balance, no payroll change.

Protected components (NOT imported / NOT called):
    • create_transaction()
    • _upsert_ledger()
    • accounting_worker()
    • parse_message()
    • WhatsApp Admin ↔ Accountant Flow
    • Ledger Update Logic
    • Existing Payroll Transaction Pipeline

Success Metric: Verified Draft Generated (NOT Transaction Created).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database import fetch_one, fetch_all, fetch_val, execute

log = logging.getLogger("fazle.employee_conversation")

# ── Constants ─────────────────────────────────────────────────────────────────

DRAFT_TTL_HOURS = 24
DRAFT_STATUS_PENDING = "pending"
DRAFT_CREATED_BY = "ai_conversation"

# Conversation step names (stored in fazle_draft_replies.status)
STEP_REASON        = "ec_reason"          # ask purpose / reason
STEP_AMOUNT        = "ec_amount"          # ask amount
STEP_PAYOUT        = "ec_payout"           # ask payout mobile + method
STEP_CONFIRM       = "ec_confirm"         # confirm and create draft
STEP_DONE          = "ec_done"            # conversation complete

# Identity resolution levels
ID_EMPLOYEE_ID   = "employee_id"
ID_REGISTERED    = "registered_mobile"
ID_DB_LOOKUP     = "db_lookup"
ID_NAME_MATCH    = "name_match"
ID_UNKNOWN       = "unknown"

# ── STEP 1: Trigger Detection ─────────────────────────────────────────────────

# Payment-request trigger keywords (Bengali + English).
# Grouped by purpose so we can classify the request type.
TRIGGER_GROUPS: dict[str, list[str]] = {
    "advance": [
        "advance", "অগ্রিম", "অগ্রীম", "advance চাই", "আগাম",
        "টাকা দরকার", "টাকা লাগবে", "টাকা চাই",
    ],
    "salary": [
        "বেতন", "salary", "বেতন চাই", "বেতন দেন",
        "বেতন পাইনি", "মাসিক বেতন",
    ],
    "food_bill": [
        "খাবারের বিল", "খাবার বিল", "food bill", "food বিল",
        "খাবার খরচ",
    ],
    "conveyance": [
        "ভাড়া", "ভাড়া লাগবে", "conveyance", "যাতায়াত ভাতা",
        "ট্রান্সপোর্ট", "ভাড়া",
    ],
    "emergency": [
        "অসুস্থ", "হাসপাতাল", "hospital", "doctor", "medical",
        "ইমার্জেন্সি", "জরুরি", "জরুরী", "emergency",
        "চিকিৎসা", "বিপদ",
    ],
}

# Flatten for quick membership test
_ALL_TRIGGERS: list[str] = [kw for kws in TRIGGER_GROUPS.values() for kw in kws]


def detect_payment_request_trigger(text: str) -> Optional[str]:
    """
    STEP 1 — Detect whether a message is a payment-request conversation trigger.

    Returns the purpose group name ('advance', 'salary', 'food_bill',
    'conveyance', 'emergency') or None if no trigger matched.

    False-positive reduction:
      • Only matches whole-word / substring triggers that are clearly
        payment-related.
      • A bare greeting ("হ্যালো") does NOT match.
      • Recruitment intent ("চাকরি চাই") does NOT match.
    """
    if not text:
        return None
    t = text.lower().strip()

    # Reject pure greetings / recruitment
    _NON_TRIGGERS = ("চাকরি", "চাকরী", "job", "apply", "আবেদন", "vacancy")
    # If the message is ONLY a greeting/recruitment word, skip
    stripped = re.sub(r"[^\w\s]", "", t).strip()
    if stripped in _NON_TRIGGERS or len(stripped) < 3:
        return None

    # Check each trigger group; return the first match.
    # Priority: emergency > advance > salary > food_bill > conveyance
    for purpose in ("emergency", "advance", "salary", "food_bill", "conveyance"):
        for kw in TRIGGER_GROUPS[purpose]:
            if kw.lower() in t:
                log.info("[ec] trigger detected: purpose=%s kw=%s", purpose, kw)
                return purpose
    return None


# ── STEP 2: Employee Identity Resolution ──────────────────────────────────────

async def resolve_employee_identity(
    phone: str,
    *,
    employee_id: Optional[int] = None,
    name_hint: Optional[str] = None,
) -> dict:
    """
    STEP 2 — Resolve employee identity.

    Priority:
        1. Employee ID (if provided)
        2. Registered mobile (phone → wbom_employees)
        3. Employee DB lookup (master_mobile in escort programs)
        4. Name match
        5. Unknown

    Returns a dict:
        {
            "employee_id": int | None,
            "employee_name": str | None,
            "employee_mobile": str | None,
            "status": str | None,            # Active / Inactive
            "resolution": str,               # one of ID_* constants
            "verified": bool,
        }

    Identity NOT confirmed → verification continues, NO transaction.
    """
    result: dict[str, Any] = {
        "employee_id": None,
        "employee_name": None,
        "employee_mobile": None,
        "status": None,
        "resolution": ID_UNKNOWN,
        "verified": False,
    }

    # 1. Employee ID
    if employee_id:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, employee_mobile, status
               FROM wbom_employees WHERE employee_id = $1""",
            employee_id,
        )
        if emp:
            result.update(
                employee_id=emp["employee_id"],
                employee_name=emp["employee_name"],
                employee_mobile=emp["employee_mobile"],
                status=emp.get("status"),
                resolution=ID_EMPLOYEE_ID,
                verified=True,
            )
            return result

    # 2. Registered mobile
    if phone:
        for variant in _phone_variants(phone):
            emp = await fetch_one(
                """SELECT employee_id, employee_name, employee_mobile, status
                   FROM wbom_employees WHERE employee_mobile = $1""",
                variant,
            )
            if emp:
                result.update(
                    employee_id=emp["employee_id"],
                    employee_name=emp["employee_name"],
                    employee_mobile=emp["employee_mobile"],
                    status=emp.get("status"),
                    resolution=ID_REGISTERED,
                    verified=True,
                )
                return result

    # 3. DB lookup via escort program master_mobile
    if phone:
        for variant in _phone_variants(phone):
            prog = await fetch_one(
                """SELECT e.employee_id, e.employee_name, e.employee_mobile, e.status
                   FROM wbom_escort_programs p
                   JOIN wbom_employees e ON e.employee_id = p.escort_employee_id
                   WHERE p.master_mobile = $1
                   ORDER BY p.program_date DESC LIMIT 1""",
                variant,
            )
            if prog:
                result.update(
                    employee_id=prog["employee_id"],
                    employee_name=prog["employee_name"],
                    employee_mobile=prog["employee_mobile"],
                    status=prog.get("status"),
                    resolution=ID_DB_LOOKUP,
                    verified=True,
                )
                return result

    # 4. Name match
    if name_hint:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, employee_mobile, status
               FROM wbom_employees
               WHERE employee_name ILIKE $1
               ORDER BY employee_id DESC LIMIT 1""",
            f"%{name_hint}%",
        )
        if emp:
            result.update(
                employee_id=emp["employee_id"],
                employee_name=emp["employee_name"],
                employee_mobile=emp["employee_mobile"],
                status=emp.get("status"),
                resolution=ID_NAME_MATCH,
                verified=False,  # name match is not fully verified
            )
            return result

    # 5. Unknown
    log.info("[ec] identity unresolved for phone=%s", phone)
    return result


def _phone_variants(phone: str) -> list[str]:
    """Return normalized phone variants for DB lookup."""
    variants = set()
    p = (phone or "").strip()
    if not p:
        return []
    variants.add(p)
    # 8801... → 01...
    if p.startswith("880"):
        variants.add("0" + p[3:])
    # 01... → 8801...
    if p.startswith("0"):
        variants.add("880" + p[1:])
    # +8801... → 8801...
    if p.startswith("+880"):
        variants.add("880" + p[4:])
        variants.add("0" + p[4:])
    return list(variants)


# ── STEP 3: Employee Status Check ─────────────────────────────────────────────

def is_employee_active(identity: dict) -> bool:
    """Return True if the employee status is 'Active' (case-insensitive)."""
    status = (identity.get("status") or "").strip().lower()
    return status == "active"


# ── STEP 5: Knowledge Base Integration ─────────────────────────────────────────

async def kb_lookup_employee_policy(text: str) -> Optional[str]:
    """
    STEP 5 — Look up an employee-policy answer from the Knowledge Base.

    Searches the 'employee_policy' category in fazle_knowledge_base.
    Returns the reply_text, or None if no match.

    The AI conversation MUST use KB answers for policy questions.
    It must NEVER fabricate policy answers.
    """
    if not text:
        return None
    t = text.lower().strip()
    try:
        rows = await fetch_all(
            """SELECT key, trigger_keywords, reply_text
               FROM fazle_knowledge_base
               WHERE is_active = true AND category = 'employee_policy'""",
        )
        for row in rows:
            keywords = row.get("trigger_keywords") or row.get("keywords") or []
            for kw in keywords:
                if kw and kw.lower() in t:
                    log.info("[ec] KB match: key=%s kw=%s", row.get("key"), kw)
                    return row["reply_text"]
    except Exception as e:
        log.debug("[ec] KB lookup failed: %s", e)
    return None


async def kb_inactive_employee_reply() -> str:
    """
    Return the KB reply for an inactive employee.
    Uses the 'inactive_employee_guidance' KB entry.
    """
    try:
        row = await fetch_one(
            """SELECT reply_text FROM fazle_knowledge_base
               WHERE is_active = true
                 AND category = 'employee_policy'
                 AND key = 'inactive_employee_guidance'
               LIMIT 1""",
        )
        if row and row.get("reply_text"):
            return row["reply_text"]
    except Exception as e:
        log.debug("[ec] inactive KB lookup failed: %s", e)
    # Hardcoded fallback (never fabricate; this mirrors the KB seed)
    return (
        "আপনার কর্মচারী স্ট্যাটাস বর্তমানে সক্রিয় নয়।\n\n"
        "প্রযোজ্য নীতি:\n"
        "• চাকরি ছাড়ার নিয়ম: ৩০ দিন লিখিত নোটিশ\n"
        "• বকেয়া পাওনা: চূড়ান্ত হিসাবে মিটিয়ে দেওয়া হবে\n"
        "• জয়েনিং ফি: ৬ মাস পূর্ণ হলে ফেরত\n"
        "• পুনর্যোগদান: ৯০ দিনের মধ্যে সম্ভব\n\n"
        "বিস্তারিতের জন্য অফিসে যোগাযোগ করুন।"
    )


# ── Conversation Session (stored in fazle_draft_replies) ──────────────────────

async def get_conversation_session(phone: str) -> Optional[dict]:
    """Return the active employee-conversation session for this phone, or None."""
    try:
        row = await fetch_one(
            """SELECT id, status, reply_text, source
               FROM fazle_draft_replies
               WHERE recipient = $1
                 AND intent = 'employee_conversation'
                 AND status NOT IN ($2, 'rejected', 'cancelled')
               ORDER BY created_at DESC LIMIT 1""",
            phone, STEP_DONE,
        )
        if row:
            try:
                ctx = json.loads(row["reply_text"] or "{}")
            except (json.JSONDecodeError, TypeError):
                ctx = {}
            return {
                "session_id": row["id"],
                "step": row["status"],
                "context": ctx,
                "source": row.get("source") or "",
            }
    except Exception as e:
        log.debug("[ec] session lookup error: %s", e)
    return None


async def _create_conversation_session(
    phone: str, source: str, ctx: dict, first_step: str
) -> int:
    """Create a new conversation session row in fazle_draft_replies."""
    from shared.draft_reply import create_draft_reply

    # Close stale open sessions
    try:
        await execute(
            """UPDATE fazle_draft_replies
               SET status='cancelled'
               WHERE recipient=$1 AND intent='employee_conversation'
                 AND status NOT IN ($2, 'rejected', 'cancelled')""",
            phone, STEP_DONE,
        )
    except Exception as e:
        log.warning("[ec] stale session cleanup error: %s", e)

    session_id = await create_draft_reply(
        sender=phone,
        bridge=source,
        draft_text=json.dumps(ctx, ensure_ascii=False),
        role="employee",
        intent="employee_conversation",
        source_module="employee_conversation",
    )
    if not session_id:
        return 0
    await execute(
        "UPDATE fazle_draft_replies SET status = $1 WHERE id = $2",
        first_step, session_id,
    )
    return session_id


async def _advance_session(
    session_id: int, new_step: str, updated_ctx: Optional[dict] = None
):
    if updated_ctx is not None:
        await execute(
            "UPDATE fazle_draft_replies SET status=$1, reply_text=$2 WHERE id=$3",
            new_step, json.dumps(updated_ctx, ensure_ascii=False), session_id,
        )
    else:
        await execute(
            "UPDATE fazle_draft_replies SET status=$1 WHERE id=$2",
            new_step, session_id,
        )


async def _close_session(session_id: int):
    await execute(
        "UPDATE fazle_draft_replies SET status=$1 WHERE id=$2",
        STEP_DONE, session_id,
    )


# ── Conversation context helpers ───────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"(\d[\d,]*\.?\d*)")
_MOBILE_RE = re.compile(r"\b((?:880|0)1[3-9]\d{8})\b")
_BKASH_RE  = re.compile(r"\b(bkash|বিকাশ)\b", re.IGNORECASE)
_NAGAD_RE  = re.compile(r"\b(nagad|নগদ)\b", re.IGNORECASE)
_CASH_RE   = re.compile(r"\b(cash|ক্যাশ|নগত)\b", re.IGNORECASE)


def _extract_amount(text: str) -> Optional[float]:
    """Extract a monetary amount from text."""
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _extract_payout(text: str) -> tuple[Optional[str], Optional[str]]:
    """Extract (method, mobile_number) from payout confirmation text."""
    t = (text or "").lower()
    mobile_m = _MOBILE_RE.search(text or "")
    number = mobile_m.group(1) if mobile_m else None

    if _CASH_RE.search(t) and not number:
        return "cash", "cash"
    if not number:
        return None, None
    if _BKASH_RE.search(t):
        return "bkash", number
    if _NAGAD_RE.search(t):
        return "nagad", number
    # Bare number → default bkash
    return "bkash", number


# ── STEP 6: Conversation Completion Check ─────────────────────────────────────

REQUIRED_DRAFT_FIELDS = [
    "employee_id",
    "employee_name",
    "employee_mobile",
    "purpose",
    "amount",
    "payout_mobile",
    "payment_method",
]


def is_verification_complete(ctx: dict) -> bool:
    """
    STEP 6 — Check whether all required information has been collected.

    Required:
        employee_id, employee_name, employee_mobile,
        purpose, amount, payout_mobile, payment_method
    """
    for field in REQUIRED_DRAFT_FIELDS:
        val = ctx.get(field)
        if val is None or val == "":
            return False
    return True


def missing_fields(ctx: dict) -> list[str]:
    """Return the list of required fields still missing from context."""
    return [f for f in REQUIRED_DRAFT_FIELDS if not ctx.get(f)]


# ── STEP 7: Draft Validation ──────────────────────────────────────────────────

def validate_draft(identity: dict, ctx: dict) -> tuple[bool, list[str]]:
    """
    STEP 7 — Validate before draft creation.

    Checks:
        • Employee identity confirmed
        • Employee active
        • Reason present
        • Amount present
        • Payout number present
        • Payment method present
        • Verification complete
    """
    errors: list[str] = []

    if not identity.get("verified"):
        errors.append("employee_identity_not_confirmed")
    if not is_employee_active(identity):
        errors.append("employee_not_active")
    if not ctx.get("purpose"):
        errors.append("reason_missing")
    if ctx.get("amount") is None:
        errors.append("amount_missing")
    if not ctx.get("payout_mobile"):
        errors.append("payout_number_missing")
    if not ctx.get("payment_method"):
        errors.append("payment_method_missing")
    if not is_verification_complete(ctx):
        errors.append("verification_incomplete")

    return (len(errors) == 0, errors)


# ── STEP 8: Draft Generation ──────────────────────────────────────────────────

async def create_employee_payment_draft(
    identity: dict,
    ctx: dict,
    source_message: str,
    conversation_log: list[dict],
    source: str = "bridge1",
) -> dict:
    """
    STEP 8 — Create a payment draft in fazle_payment_drafts.

    Status: 'pending'
    Expiry: 24 hours from now.

    Mandatory fields written:
        employee_id, employee_name, employee_mobile,
        payout_mobile, payment_method, amount, purpose,
        verification_summary, source_message, conversation_summary,
        draft_created_by, created_at, expires_at

    This function does NOT call create_transaction() or any financial write.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=DRAFT_TTL_HOURS)
    conversation_id = ctx.get("conversation_id") or f"ec-{uuid.uuid4().hex[:12]}"

    verification_summary = {
        "identity_resolution": identity.get("resolution"),
        "identity_verified": identity.get("verified"),
        "employee_status": identity.get("status"),
        "verification_complete": is_verification_complete(ctx),
        "verified_at": now.isoformat(),
    }

    conversation_summary = {
        "conversation_id": conversation_id,
        "purpose": ctx.get("purpose"),
        "amount": ctx.get("amount"),
        "payment_method": ctx.get("payment_method"),
        "payout_mobile": ctx.get("payout_mobile"),
        "turns": conversation_log,
        "turn_count": len(conversation_log),
    }

    draft_type = ctx.get("purpose", "advance")
    amount = float(ctx.get("amount") or 0)

    draft_text = _build_draft_text(identity, ctx, draft_id_placeholder="<draft_id>")

    try:
        draft_id = await fetch_val(
            """INSERT INTO fazle_payment_drafts
                   (draft_type, employee_id, employee_name, employee_mobile,
                    payout_mobile, payment_method, expected_amount, purpose,
                    status, source, draft_text,
                    verification_summary, source_message, conversation_summary,
                    draft_created_by, conversation_id, expires_at, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                       'pending', $9, $10,
                       $11, $12, $13,
                       $14, $15, $16, NOW(), NOW())
               RETURNING id""",
            draft_type,
            identity.get("employee_id"),
            identity.get("employee_name"),
            identity.get("employee_mobile"),
            ctx.get("payout_mobile"),
            ctx.get("payment_method"),
            amount,
            ctx.get("purpose"),
            source,
            draft_text,
            json.dumps(verification_summary, ensure_ascii=False),
            source_message,
            json.dumps(conversation_summary, ensure_ascii=False),
            DRAFT_CREATED_BY,
            conversation_id,
            expires_at,
        )
    except Exception as e:
        log.error("[ec] draft insert failed: %s", e)
        return {"error": str(e), "draft_id": None}

    if not draft_id:
        return {"error": "insert returned no id", "draft_id": None}

    # Replace placeholder with real id
    draft_text = draft_text.replace("<draft_id>", str(draft_id))
    await execute(
        "UPDATE fazle_payment_drafts SET draft_text=$1 WHERE id=$2",
        draft_text, draft_id,
    )

    log.info(
        "[ec] draft #%d created — emp=%s purpose=%s amount=%.2f expires=%s",
        draft_id, identity.get("employee_name"), ctx.get("purpose"),
        amount, expires_at.isoformat(),
    )

    return {
        "draft_id": draft_id,
        "draft_text": draft_text,
        "status": DRAFT_STATUS_PENDING,
        "expires_at": expires_at.isoformat(),
        "conversation_id": conversation_id,
        "verification_summary": verification_summary,
        "conversation_summary": conversation_summary,
    }


def _build_draft_text(identity: dict, ctx: dict, draft_id_placeholder: str) -> str:
    """Build the human-readable draft text (Bengali)."""
    purpose_labels = {
        "advance": "অগ্রিম",
        "salary": "বেতন",
        "food_bill": "খাবারের বিল",
        "conveyance": "কনভেয়েন্স",
        "emergency": "জরুরি সহায়তা",
    }
    purpose_label = purpose_labels.get(ctx.get("purpose", ""), ctx.get("purpose", ""))
    amount = ctx.get("amount", 0)

    return (
        f"📝 কর্মচারী পেমেন্ট রিকোয়েস্ট (Draft):\n\n"
        f"কর্মী: {identity.get('employee_name', '?')}\n"
        f"মোবাইল: {identity.get('employee_mobile', '?')}\n"
        f"উদ্দেশ্য: {purpose_label}\n"
        f"পরিমাণ: ৳{amount:,.0f}\n"
        f"পেআউট: {ctx.get('payout_mobile', '?')} ({ctx.get('payment_method', '?')})\n\n"
        f"Draft ID: {draft_id_placeholder}\n"
        f"স্ট্যাটাস: pending (২৪ ঘণ্টায় মেয়াদোত্তীর্ণ)"
    )


# ── STEP 9: Admin Draft Message ───────────────────────────────────────────────

def build_admin_draft_message(draft: dict, identity: dict, ctx: dict) -> str:
    """
    STEP 9 — Build the WhatsApp template message for the admin.

    Includes:
        Employee → Reason → Amount → Payout → Verification Summary
        → Suggested Payment Message → Commands (APPROVED / EDIT / REJECT)

    NOTE: Commands are NOT processed in Sprint-3A.
    """
    purpose_labels = {
        "advance": "অগ্রিম (Advance)",
        "salary": "বেতন (Salary)",
        "food_bill": "খাবারের বিল (Food Bill)",
        "conveyance": "কনভেয়েন্স (Conveyance)",
        "emergency": "জরুরি সহায়তা (Emergency)",
    }
    purpose_label = purpose_labels.get(ctx.get("purpose", ""), ctx.get("purpose", ""))
    amount = ctx.get("amount", 0)
    draft_id = draft.get("draft_id", "?")
    ver = draft.get("verification_summary", {})

    verification_lines = (
        f"  • Identity: {ver.get('identity_resolution', '?')} "
        f"({'verified' if ver.get('identity_verified') else 'unverified'})\n"
        f"  • Status: {ver.get('employee_status', '?')}\n"
        f"  • Verification: {'complete' if ver.get('verification_complete') else 'incomplete'}"
    )

    suggested = (
        f"APPROVED {draft_id} {amount:.0f} {ctx.get('payment_method', 'bkash')}"
    )

    return (
        f"📋 *নতুন পেমেন্ট ড্রাফট*\n\n"
        f"👤 কর্মী: {identity.get('employee_name', '?')}\n"
        f"📱 মোবাইল: {identity.get('employee_mobile', '?')}\n"
        f"📝 কারণ: {purpose_label}\n"
        f"💰 পরিমাণ: ৳{amount:,.0f}\n"
        f"📲 পেআউট: {ctx.get('payout_mobile', '?')} ({ctx.get('payment_method', '?')})\n\n"
        f"✅ যাচাই সারাংশ:\n{verification_lines}\n\n"
        f"📨 সাজেস্টেড পেমেন্ট মেসেজ:\n{suggested}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"কমান্ড:\n"
        f"• APPROVED {draft_id} <amount> <method>\n"
        f"• EDIT {draft_id}\n"
        f"• REJECT {draft_id}\n\n"
        f"⚠️ এই Sprint-3A-এ কমান্ড প্রসেস করা হবে না।"
    )


# ── Conversation Orchestrator ─────────────────────────────────────────────────

async def start_employee_conversation(
    phone: str,
    text: str,
    source: str,
    purpose: str,
    identity: Optional[dict] = None,
) -> tuple[str, Optional[dict]]:
    """
    Begin a new employee payment-request conversation.

    Returns (reply_to_employee, admin_notification | None).
    Admin notification is None at this stage (draft not yet created).
    """
    if identity is None:
        identity = await resolve_employee_identity(phone)

    # STEP 3: Employee status check
    if identity.get("employee_id") and not is_employee_active(identity):
        # Inactive → KB reply, no conversation, no draft
        kb_reply = await kb_inactive_employee_reply()
        log.info(
            "[ec] inactive employee %s — KB reply sent, no conversation",
            identity.get("employee_id"),
        )
        return kb_reply, None

    conversation_id = f"ec-{uuid.uuid4().hex[:12]}"
    ctx: dict[str, Any] = {
        "conversation_id": conversation_id,
        "purpose": purpose,
        "employee_id": identity.get("employee_id"),
        "employee_name": identity.get("employee_name"),
        "employee_mobile": identity.get("employee_mobile") or phone,
        "source_message": text,
        "conversation_log": [{"role": "employee", "text": text, "ts": _now_iso()}],
    }

    # If identity not confirmed, ask for employee ID first
    if not identity.get("verified"):
        ctx["awaiting_employee_id"] = True
        await _create_conversation_session(phone, source, ctx, STEP_REASON)
        return (
            "আপনার রিকোয়েস্ট পেয়েছি 🙏\n\n"
            "যাচাইয়ের জন্য আপনার **Employee ID** বা **নাম** লিখুন।",
            None,
        )

    # Identity confirmed + active → ask for reason/amount
    await _create_conversation_session(phone, source, ctx, STEP_AMOUNT)
    purpose_labels = {
        "advance": "অগ্রিম",
        "salary": "বেতন",
        "food_bill": "খাবারের বিল",
        "conveyance": "কনভেয়েন্স",
        "emergency": "জরুরি সহায়তা",
    }
    label = purpose_labels.get(purpose, purpose)
    return (
        f"ধন্যবাদ {identity.get('employee_name', '')} 🙏\n\n"
        f"আপনার {label} রিকোয়েস্ট গ্রহণ করেছি।\n"
        f"অনুগ্রহ করে **পরিমাণ** লিখুন (টাকা):\n\n"
        f"উদাহরণ: ২০০০",
        None,
    )


async def continue_employee_conversation(
    phone: str,
    text: str,
    source: str,
) -> tuple[str, Optional[dict]]:
    """
    Continue an active employee conversation session.

    Returns (reply_to_employee, admin_notification | None).
    admin_notification is set ONLY when a draft is created (STEP 8/9).
    """
    session = await get_conversation_session(phone)
    if not session:
        return "", None

    session_id = session["session_id"]
    step = session["step"]
    ctx = session["context"]

    # Append to conversation log
    log_entry = {"role": "employee", "text": text, "ts": _now_iso()}
    conv_log = ctx.get("conversation_log") or []
    conv_log.append(log_entry)
    ctx["conversation_log"] = conv_log

    # ── STEP_REASON: waiting for employee ID / name ────────────────────────────
    if step == STEP_REASON:
        # Try to extract employee ID from text
        emp_id_match = re.search(r"\b(\d{2,6})\b", text)
        name_hint = text.strip() if not emp_id_match else None

        identity = await resolve_employee_identity(
            phone,
            employee_id=int(emp_id_match.group(1)) if emp_id_match else None,
            name_hint=name_hint,
        )

        if not identity.get("employee_id"):
            return (
                "আপনার তথ্য পাওয়া যায়নি।\n"
                "অনুগ্রহ করে সঠিক **Employee ID** বা **পূর্ণ নাম** লিখুন।",
                None,
            )

        ctx["employee_id"] = identity["employee_id"]
        ctx["employee_name"] = identity["employee_name"]
        ctx["employee_mobile"] = identity["employee_mobile"]

        # STEP 3: status check
        if not is_employee_active(identity):
            await _close_session(session_id)
            kb_reply = await kb_inactive_employee_reply()
            return kb_reply, None

        # Active → ask amount
        await _advance_session(session_id, STEP_AMOUNT, ctx)
        return (
            f"ধন্যবাদ {identity['employee_name']} 🙏\n\n"
            f"এখন **পরিমাণ** লিখুন (টাকা):\n"
            f"উদাহরণ: ২০০০",
            None,
        )

    # ── STEP_AMOUNT: waiting for amount ────────────────────────────────────────
    if step == STEP_AMOUNT:
        amount = _extract_amount(text)
        if amount is None or amount <= 0:
            return (
                "পরিমাণ বুঝতে পারিনি।\n"
                "অনুগ্রহ করে সংখ্যায় লিখুন:\n"
                "উদাহরণ: ২০০০",
                None,
            )
        ctx["amount"] = amount
        await _advance_session(session_id, STEP_PAYOUT, ctx)
        return (
            f"পরিমাণ: ৳{amount:,.0f} ✅\n\n"
            f"এখন **পেআউট নম্বর ও পদ্ধতি** লিখুন:\n"
            f"উদাহরণ: বিকাশ 01712345678\n"
            f"অথবা: নগদ 01812345678\n"
            f"অথবা: cash",
            None,
        )

    # ── STEP_PAYOUT: waiting for payout mobile + method ────────────────────────
    if step == STEP_PAYOUT:
        method, number = _extract_payout(text)
        if not method:
            return (
                "পেআউট তথ্য বুঝতে পারিনি।\n"
                "বিকাশ/নগদ নম্বর সহ লিখুন:\n"
                "উদাহরণ: বিকাশ 01712345678\n"
                "অথবা: cash",
                None,
            )
        ctx["payment_method"] = method
        ctx["payout_mobile"] = number

        # STEP 6: completion check
        if not is_verification_complete(ctx):
            missing = missing_fields(ctx)
            log.warning("[ec] verification incomplete, missing=%s", missing)
            # Continue conversation — don't draft
            await _advance_session(session_id, STEP_PAYOUT, ctx)
            return (
                "কিছু তথ্য এখনও দরকার।\n"
                f"অনুপস্থিত: {', '.join(missing)}\n"
                "অনুগ্রহ করে সম্পূর্ণ তথ্য দিন।",
                None,
            )

        # Verification complete → confirm
        await _advance_session(session_id, STEP_CONFIRM, ctx)
        purpose_labels = {
            "advance": "অগ্রিম",
            "salary": "বেতন",
            "food_bill": "খাবারের বিল",
            "conveyance": "কনভেয়েন্স",
            "emergency": "জরুরি সহায়তা",
        }
        label = purpose_labels.get(ctx.get("purpose", ""), ctx.get("purpose", ""))
        return (
            f"নিশ্চিত করুন:\n\n"
            f"• নাম: {ctx.get('employee_name', '?')}\n"
            f"• উদ্দেশ্য: {label}\n"
            f"• পরিমাণ: ৳{ctx.get('amount', 0):,.0f}\n"
            f"• পেআউট: {ctx.get('payout_mobile', '?')} ({ctx.get('payment_method', '?')})\n\n"
            f"সঠিক হলে **হ্যাঁ** বা **confirm** লিখুন।",
            None,
        )

    # ── STEP_CONFIRM: waiting for confirmation ─────────────────────────────────
    if step == STEP_CONFIRM:
        t = text.lower().strip()
        if t not in ("হ্যাঁ", "confirm", "হ্যাঁ।", "yes", "ok", "ঠিক আছে", "confirm করুন"):
            return (
                "নিশ্চিত করতে **হ্যাঁ** বা **confirm** লিখুন।\n"
                "বাতিল করতে **cancel** লিখুন।",
                None,
            )

        # Re-resolve identity for validation
        identity = await resolve_employee_identity(
            ctx.get("employee_mobile") or phone,
            employee_id=ctx.get("employee_id"),
        )

        # STEP 7: draft validation
        ok, errors = validate_draft(identity, ctx)
        if not ok:
            log.warning("[ec] draft validation failed: %s", errors)
            return (
                "যাচাই সম্পূর্ণ হয়নি।\n"
                f"সমস্যা: {', '.join(errors)}\n"
                "অফিসে যোগাযোগ করুন।",
                None,
            )

        # STEP 8: draft generation
        source_message = ctx.get("source_message", "")
        conv_log = ctx.get("conversation_log") or []
        draft = await create_employee_payment_draft(
            identity, ctx, source_message, conv_log, source=source,
        )

        if draft.get("error") or not draft.get("draft_id"):
            return (
                "ড্রাফট তৈরিতে সমস্যা হয়েছে। অফিসে যোগাযোগ করুন।",
                None,
            )

        # Close conversation session
        await _close_session(session_id)

        # STEP 9: admin draft message
        admin_msg = build_admin_draft_message(draft, identity, ctx)

        from modules.message_router import get_primary_admin
        admin_notification = {
            "admin_phone": get_primary_admin(),
            "text": admin_msg,
            "bridge": source,
            "purpose": "employee_payment_draft",
            "draft_id": draft["draft_id"],
        }

        return (
            f"✅ যাচাই সম্পন্ন!\n\n"
            f"আপনার রিকোয়েস্ট এডমিনের কাছে পাঠানো হয়েছে।\n"
            f"Draft ID: #{draft['draft_id']}\n"
            f"২৪ ঘণ্টায় মেয়াদোত্তীর্ণ হবে।\n"
            f"অনুমোদন হলে আপনাকে জানানো হবে।",
            admin_notification,
        )

    return "", None


# ── Utility ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Public entry point (called by message_router) ─────────────────────────────

async def handle_employee_payment_request(
    phone: str,
    text: str,
    source: str,
    employee_id: Optional[int] = None,
) -> tuple[str, Optional[dict]]:
    """
    Main entry point for employee payment-request conversations.

    Called by message_router when a payment-request trigger is detected
    OR an active conversation session exists.

    Returns (reply_to_employee, admin_notification | None).

    GUARANTEE: This function NEVER calls create_transaction(),
    _upsert_ledger(), or any financial-write function.
    """
    # Check for existing active conversation
    session = await get_conversation_session(phone)
    if session:
        return await continue_employee_conversation(phone, text, source)

    # New conversation — detect trigger
    purpose = detect_payment_request_trigger(text)
    if not purpose:
        return "", None

    identity = await resolve_employee_identity(phone, employee_id=employee_id)
    return await start_employee_conversation(phone, text, source, purpose, identity)