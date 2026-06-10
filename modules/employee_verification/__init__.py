"""
Fazle Core — Employee Financial Request Verification

Multi-step verification before any payment draft reaches admin.
Sessions stored in fazle_draft_replies — no schema changes needed.

  intent = 'verification'
  status = step name
  reply_text = JSON context (employee_id, request_type, etc.)
  recipient = employee phone

Advance request flow:
  1. Employee requests money (any emergency/advance keyword)
  2. System asks for selfie from duty location
  3. Employee sends image → system asks for duty/release slip
  4. Employee sends image → system asks for bkash/nagad number
  5. Employee confirms payment method → create draft → send to admin

Release slip flow (duty completion):
  1. Employee sends release slip (image or text mention)
  2. If image already sent → skip to step 4 (payment method)
  3. If text only → ask for slip image
  4. Employee sends image → ask for payment method
  5. Employee confirms → create escort payment draft → admin

Identity mismatch:
  If sender phone not in wbom_employees but matches a master_mobile
  in wbom_escort_programs → inform to use registered number.
"""

import json
import logging
import re
from typing import Optional

from app.database import fetch_one, fetch_val, execute
from shared.draft_reply import create_draft_reply

log = logging.getLogger("fazle.verification")

# ── Step names ─────────────────────────────────────────────────────────────────
STEP_SELFIE = "pending_selfie"
STEP_SLIP   = "pending_slip"
STEP_METHOD = "pending_payment_method"
STEP_DONE   = "verified"

# ── Patterns ───────────────────────────────────────────────────────────────────
_MOBILE_RE = re.compile(r"\b((?:880|0)1[3-9]\d{8})\b")
_BKASH_RE  = re.compile(r"\b(bkash|বিকাশ)\b", re.IGNORECASE)
_NAGAD_RE  = re.compile(r"\b(nagad|নগদ)\b", re.IGNORECASE)
_CASH_RE   = re.compile(r"\b(cash|ক্যাশ|নগত)\b", re.IGNORECASE)

# Markers that indicate an incoming media message (from Meta webhook conversion
# or bridge processed_text prefix when OCR returns nothing useful)
_IMAGE_MARKERS = frozenset([
    "[image message]", "[photo]", "[document message]",
    "[sticker message]", "[video message]",
])


# ── Session CRUD ───────────────────────────────────────────────────────────────

async def get_verification_session(phone: str) -> Optional[dict]:
    """Return the active verification session for this employee, or None."""
    try:
        row = await fetch_one(
            """SELECT id, status, reply_text, source
               FROM fazle_draft_replies
               WHERE recipient = $1
                 AND intent = 'verification'
                 AND status NOT IN ($2, 'rejected')
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
        log.debug(f"[verification] session lookup error: {e}")
    return None


async def _create_session(phone: str, source: str, ctx: dict, first_step: str) -> int:
    # Close any stale open sessions for this employee first
    try:
        await execute(
            """UPDATE fazle_draft_replies
               SET status='rejected'
               WHERE recipient=$1 AND intent='verification'
                 AND status NOT IN ($2, 'rejected')""",
            phone, STEP_DONE,
        )
    except Exception as _e:
        from app.error_log import record_error
        await record_error("employee_verification.cleanup", _e)

    session_id = await create_draft_reply(
        sender=phone,
        bridge=source,
        draft_text=json.dumps(ctx, ensure_ascii=False),
        role="employee",
        intent="verification",
        source_module="employee_verification",
    )
    if not session_id:
        return 0
    # create_draft_reply sets status='pending'; set the first verification step
    await execute(
        "UPDATE fazle_draft_replies SET status = $1 WHERE id = $2",
        first_step, session_id,
    )
    return session_id


async def _advance_session(session_id: int, new_step: str, updated_ctx: Optional[dict] = None):
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


# ── Public entry points ────────────────────────────────────────────────────────

async def start_advance_verification(
    phone: str, source: str, employee_id: Optional[int]
) -> tuple[str, None]:
    """Start verification for an advance/emergency financial request."""
    ctx = {"request_type": "advance", "employee_id": employee_id}
    await _create_session(phone, source, ctx, STEP_SELFIE)
    log.info(f"[verification] advance started for {phone}, emp_id={employee_id}")
    return (
        "ভাই, আপনার রিকোয়েস্ট পেয়েছি 🙏\n\n"
        "অ্যাকাউন্ট বিভাগের অনুমোদনের আগে একটু যাচাই করা দরকার।\n\n"
        "📸 আপনার এখনকার ডিউটি লোকেশন থেকে একটি **সেলফি ছবি** পাঠান।",
        None,
    )


async def start_slip_verification(
    phone: str, text: str, source: str, employee_id: Optional[int]
) -> tuple[str, None]:
    """Start verification triggered by a release slip submission."""
    ctx = {"request_type": "release_slip", "employee_id": employee_id}

    # If the slip image was already attached in this same message, go straight to payment method
    if _is_image(text):
        await _create_session(phone, source, ctx, STEP_METHOD)
        log.info(f"[verification] slip+image received, jumped to payment method for {phone}")
        return (
            "রিলিজ স্লিপ পেয়েছি ✅\n\n"
            "পেমেন্ট পদ্ধতি কনফার্ম করুন।\n"
            "বিকাশ বা নগদ নম্বর লিখুন:\n\n"
            "উদাহরণ: বিকাশ 01712345678",
            None,
        )

    await _create_session(phone, source, ctx, STEP_SLIP)
    log.info(f"[verification] slip flow started for {phone}")
    return (
        "ডিউটি সম্পন্ন হয়েছে ✅ অভিনন্দন!\n\n"
        "পেমেন্ট প্রক্রিয়া শুরু করতে —\n"
        "📄 সুপারভাইজার সাইন করা **রিলিজ স্লিপ**-এর একটি স্পষ্ট ছবি পাঠান।",
        None,
    )


async def advance_verification(
    phone: str,
    text: str,
    source: str,
    employee_id: Optional[int],
) -> tuple[str, Optional[dict]]:
    """
    Process an employee message against their active verification session.
    Returns (reply, admin_note | None).
    """
    session = await get_verification_session(phone)
    if not session:
        return "", None

    session_id = session["session_id"]
    step       = session["step"]
    ctx        = session["context"]

    # ── Waiting for selfie ─────────────────────────────────────────────────────
    if step == STEP_SELFIE:
        if _is_image(text):
            await _advance_session(session_id, STEP_SLIP)
            return (
                "সেলফি পেয়েছি ✅\n\n"
                "এখন আপনার **ডিউটি স্লিপ** বা **রিলিজ স্লিপ**-এর স্পষ্ট ছবি পাঠান।",
                None,
            )
        return (
            "📸 অনুগ্রহ করে আপনার ডিউটি লোকেশন থেকে একটি **সেলফি ছবি** পাঠান।\n"
            "শুধু ছবিই গ্রহণযোগ্য — লেখা নয়।",
            None,
        )

    # ── Waiting for slip ───────────────────────────────────────────────────────
    if step == STEP_SLIP:
        if _is_image(text):
            await _advance_session(session_id, STEP_METHOD)
            return (
                "স্লিপ পেয়েছি ✅\n\n"
                "এখন পেমেন্ট পদ্ধতি কনফার্ম করুন।\n"
                "বিকাশ বা নগদ নম্বর লিখুন:\n\n"
                "উদাহরণ: বিকাশ 01712345678\n"
                "অথবা: নগদ 01812345678",
                None,
            )
        return (
            "📄 অনুগ্রহ করে **রিলিজ স্লিপ বা ডিউটি স্লিপ**-এর ছবি পাঠান।\n"
            "শুধু ছবিই গ্রহণযোগ্য।",
            None,
        )

    # ── Waiting for payment method ─────────────────────────────────────────────
    if step == STEP_METHOD:
        method, number = _extract_payment_method(text)
        if method and number:
            ctx["payment_method"] = method
            ctx["payment_number"] = number
            await _close_session(session_id)
            return await _build_and_send_draft(ctx, phone, source)

        return (
            "পেমেন্ট পদ্ধতি বুঝতে পারিনি।\n\n"
            "বিকাশ বা নগদ নম্বর সহ লিখুন:\n"
            "উদাহরণ: বিকাশ 01712345678\n"
            "অথবা: নগদ 01812345678\n"
            "অথবা: cash (সরাসরি হাতে পেলে)",
            None,
        )

    return "", None


# ── Identity mismatch check ────────────────────────────────────────────────────

async def check_identity_mismatch(phone: str) -> Optional[str]:
    """
    If sender's phone is not in employees but matches a master_mobile
    in wbom_escort_programs, return a mismatch warning message.
    Returns None if no mismatch.
    """
    try:
        # Check if phone is a known master mobile (not an employee)
        program = await fetch_one(
            """SELECT program_id, mother_vessel, lighter_vessel
               FROM wbom_escort_programs
               WHERE master_mobile = $1 AND status IN ('draft','confirmed','active')
               ORDER BY program_date DESC LIMIT 1""",
            phone,
        )
        if program:
            mv = program.get("mother_vessel") or "?"
            lv = program.get("lighter_vessel") or "?"
            return (
                f"আপনার নম্বরটি এস্কর্ট অর্ডারে মাস্টার মোবাইল হিসেবে সেভ আছে।\n"
                f"({mv} / {lv})\n\n"
                f"যদি আপনি কর্মী হন, তাহলে আপনার নিজস্ব নিবন্ধিত নম্বর থেকে মেসেজ পাঠান।"
            )
    except Exception as e:
        log.debug(f"[verification] identity check error: {e}")
    return None


# ── Internal helpers ───────────────────────────────────────────────────────────

def _is_image(text: str) -> bool:
    """Detect any incoming image/media message."""
    t = text.lower().strip()
    return any(marker in t for marker in _IMAGE_MARKERS)


def _extract_payment_method(text: str) -> tuple[Optional[str], Optional[str]]:
    """Return (method, number) from payment confirmation message, or (None, None)."""
    t = text.lower()
    mobile_m = _MOBILE_RE.search(text)
    number = mobile_m.group(1) if mobile_m else None

    # Cash: no number needed
    if _CASH_RE.search(t) and not number:
        return "cash", "cash"

    if not number:
        return None, None

    if _BKASH_RE.search(t):
        return "bkash", number
    if _NAGAD_RE.search(t):
        return "nagad", number

    # Bare number — default to bkash (most common)
    return "bkash", number


async def _build_and_send_draft(
    ctx: dict, phone: str, source: str
) -> tuple[str, Optional[dict]]:
    """Create the payment draft after all verification steps pass."""
    from modules.payment_workflow import create_advance_request_draft, create_escort_payment_draft
    from modules.message_router import get_primary_admin

    employee_id  = ctx.get("employee_id")
    request_type = ctx.get("request_type", "advance")
    method       = ctx.get("payment_method", "bkash")
    number       = ctx.get("payment_number", "")

    if not employee_id:
        return (
            "⚠️ আপনার কর্মী তথ্য সিস্টেমে পাওয়া যায়নি।\n"
            "অফিসে যোগাযোগ করুন।",
            None,
        )

    # Persist the confirmed payment number
    await _persist_payment_number(employee_id, method, number)

    if request_type == "release_slip":
        draft = await create_escort_payment_draft(employee_id, source=source)
    else:
        draft = await create_advance_request_draft(employee_id, source=source)

    if draft.get("error") or not draft.get("draft_id"):
        log.error(f"[verification] draft creation failed: {draft.get('error')}")
        return (
            "ড্রাফট তৈরিতে সমস্যা হয়েছে। অফিসে সরাসরি যোগাযোগ করুন।",
            None,
        )

    type_label = "রিলিজ পেমেন্ট" if request_type == "release_slip" else "অগ্রিম পেমেন্ট"
    log.info(f"[verification] {type_label} draft #{draft['draft_id']} created for emp {employee_id}")

    return (
        f"✅ যাচাই সম্পন্ন!\n\n"
        f"আপনার {type_label} রিকোয়েস্ট এডমিনের কাছে পাঠানো হয়েছে।\n"
        f"অনুমোদন হলে আপনাকে জানানো হবে।",
        {
            "admin_phone": get_primary_admin(),
            "text": draft["draft_text"],
            "bridge": source,
        },
    )


async def _persist_payment_number(employee_id: int, method: str, number: str):
    """Always update the employee's confirmed payment number in DB."""
    if number in ("cash", ""):
        return
    try:
        if method == "bkash":
            await execute(
                "UPDATE wbom_employees SET bkash_number=$1 WHERE employee_id=$2",
                number, employee_id,
            )
        elif method == "nagad":
            await execute(
                "UPDATE wbom_employees SET nagad_number=$1 WHERE employee_id=$2",
                number, employee_id,
            )
    except Exception as e:
        log.warning(f"[verification] payment number persist error: {e}")
