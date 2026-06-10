"""
Fazle Core — Recruitment Funnel (async version)
Adapted from resources/recruitment.py for asyncpg DB interface.

Tables used:
  fazle_recruitment_sessions — tracks conversation state per phone
  (falls back gracefully if table doesn't exist yet)

Steps: name → age → area → job_preference → experience → phone_confirm
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.database import fetch_one, execute, fetch_val

log = logging.getLogger("fazle.recruitment")

# ── Intake trigger keywords ────────────────────────────────────────────────────
INTAKE_KEYWORDS: set[str] = {
    "job", "কাজ", "চাকরি", "vacancy", "apply", "hire",
    "recruit", "নিয়োগ", "কাজের", "চাই", "interested",
    "আগ্রহী", "পদ", "পারব", "নেবেন", "জয়েন",
    "cv", "joining",  # P17-FIX-3: added for "cv dibo" / "joining kobe" test cases
}

VALID_POSITIONS: dict[str, str] = {
    "1": "Escort / Survey Scout",
    "2": "Security Guard",
    "3": "Supervisor",
    "4": "Operation Officer",
    "5": "Labor",
    "escort": "Escort / Survey Scout",
    "survey": "Escort / Survey Scout",
    "guard": "Security Guard",
    "security": "Security Guard",
    "supervisor": "Supervisor",
    "operation": "Operation Officer",
    "labor": "Labor",
}

COLLECTION_STEPS: list[str] = [
    "name", "age", "area", "job_preference", "experience", "phone_confirm",
]

STEP_QUESTIONS: dict[str, str] = {
    "name": (
        "স্বাগতম। আমাদের কাছে আবেদন করার জন্য আপনাকে ধন্যবাদ।\n"
        "আপনার পুরো নাম কি?"
    ),
    "age": "আপনার বয়স কত বছর?",
    "area": "আপনি কোন জেলায় থাকেন? (বর্তমান ঠিকানা)",
    "job_preference": (
        "আপনি কোন পদে কাজ করতে চান?\n\n"
        "১. Escort / Survey Scout (জাহাজে)\n"
        "২. Security Guard\n"
        "৩. Supervisor\n"
        "৪. Operation Officer\n"
        "৫. Labor\n\n"
        "নম্বর বা পদের নাম লিখুন।"
    ),
    "experience": (
        "এই ক্ষেত্রে আপনার কত বছরের অভিজ্ঞতা আছে?\n"
        "(নতুন হলে 0 লিখুন)"
    ),
    "phone_confirm": "আপনার কনফার্মেশনের জন্য: আপনার মোবাইল নম্বর কত? (যেটায় কল করতে পারব)",
}

INTAKE_COMPLETE_MSG: str = (
    "আপনার তথ্য সফলভাবে সংগ্রহ করা হয়েছে।\n\n"
    "আমাদের টিম শীঘ্রই আপনার সাথে যোগাযোগ করবে।\n"
    "আরো জানতে: 01958 122322\n\n"
    "আবেদনের জন্য ধন্যবাদ।"
)

ALREADY_APPLIED_MSG: str = (
    "আপনার আবেদন ইতিমধ্যে প্রক্রিয়াধীন রয়েছে।\n"
    "আমাদের টিম শীঘ্রই যোগাযোগ করবে।"
)


# ── Parsers ────────────────────────────────────────────────────────────────────

def _parse_age(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        val = int(m.group(1))
        return val if 10 <= val <= 80 else None
    return None


def _parse_job_preference(text: str) -> Optional[str]:
    norm = text.strip().lower()
    for key, val in VALID_POSITIONS.items():
        if key in norm:
            return val
    return None


def _parse_experience(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else 0


def _parse_phone(text: str) -> Optional[str]:
    m = re.search(r"(0?1[3-9]\d{8})", re.sub(r"\D", "", text))
    return m.group(1) if m else None


# ── Scoring ────────────────────────────────────────────────────────────────────

def _compute_score(session: dict) -> tuple[int, str]:
    score = 0
    exp = session.get("experience_years") or 0
    if exp >= 6:
        score += 60
    elif exp >= 3:
        score += 40
    elif exp >= 1:
        score += 20
    if session.get("job_preference") in ("Escort / Survey Scout", "Security Guard"):
        score += 20
    required = ["full_name", "age", "area", "job_preference"]
    if all(session.get(f) for f in required):
        score += 20
    score = min(score, 100)
    bucket = "hot" if score >= 70 else ("warm" if score >= 40 else "cold")
    return score, bucket


# ── DB helpers (graceful fallback if table missing) ───────────────────────────

async def _get_session(phone: str) -> Optional[dict]:
    try:
        return await fetch_one(
            "SELECT * FROM fazle_recruitment_sessions WHERE phone = $1", phone
        )
    except Exception as e:
        log.debug(f"[recruit] session lookup failed: {e}")
        return None


async def _create_session(phone: str, source_msg: str, source: str) -> Optional[int]:
    try:
        sid = await fetch_val(
            """INSERT INTO fazle_recruitment_sessions
                   (phone, collection_step, funnel_stage, source_message, source_bridge, updated_at)
               VALUES ($1, 'name', 'collecting', $2, $3, NOW())
               RETURNING id""",
            phone, source_msg[:500], source,
        )
        return sid
    except Exception as e:
        log.warning(f"[recruit] create session failed: {e}")
        return None


async def _update_session(phone: str, fields: dict) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc)
    set_parts = []
    vals = []
    for i, (k, v) in enumerate(fields.items(), 1):
        set_parts.append(f"{k} = ${i}")
        vals.append(v)
    vals.append(phone)
    sql = f"UPDATE fazle_recruitment_sessions SET {', '.join(set_parts)} WHERE phone = ${len(vals)}"
    try:
        await execute(sql, *vals)
    except Exception as e:
        log.warning(f"[recruit] update session failed: {e}")


# ── Main intake function ───────────────────────────────────────────────────────

async def intake_message(phone: str, message: str, source: str = "whatsapp") -> dict:
    """
    Process one inbound WhatsApp message for recruitment funnel.

    Returns:
      {
        "reply": str,       — text to send back (or "" to ignore)
        "action": str,      — created | collecting | scored | ignored | already_applied
        "session_id": int | None
      }
    """
    phone = phone.strip()
    text = message.strip()
    lower = text.lower()

    session = await _get_session(phone)

    # ── New visitor ────────────────────────────────────────────────────────────
    if session is None:
        if not any(kw in lower for kw in INTAKE_KEYWORDS):
            return {"reply": "", "action": "ignored", "session_id": None}

        sid = await _create_session(phone, text, source)
        reply = STEP_QUESTIONS["name"]
        return {"reply": reply, "action": "created", "session_id": sid}

    # ── Already past collection ────────────────────────────────────────────────
    stage = session.get("funnel_stage", "collecting")
    if stage not in ("collecting", "new"):
        return {
            "reply": ALREADY_APPLIED_MSG,
            "action": "already_applied",
            "session_id": session["id"],
        }

    # ── Collecting answers step by step ───────────────────────────────────────
    step = session.get("collection_step") or "name"
    update: dict = {}

    if step == "name":
        update["full_name"] = text.strip()[:100]
    elif step == "age":
        val = _parse_age(text)
        if val:
            update["age"] = val
        else:
            return {
                "reply": "দয়া করে সঠিক বয়স লিখুন (যেমন: 25)",
                "action": "collecting",
                "session_id": session["id"],
            }
    elif step == "area":
        update["area"] = text.strip()[:100]
    elif step == "job_preference":
        pos = _parse_job_preference(text)
        if pos:
            update["job_preference"] = pos
        else:
            return {
                "reply": "দয়া করে ১ থেকে ৫ নম্বর বা পদের নাম লিখুন।\n" + STEP_QUESTIONS["job_preference"],
                "action": "collecting",
                "session_id": session["id"],
            }
    elif step == "experience":
        val = _parse_experience(text)
        update["experience_years"] = val if val is not None else 0
    elif step == "phone_confirm":
        phone_parsed = _parse_phone(text)
        if phone_parsed:
            update["confirmed_phone"] = phone_parsed
        # Accept even if not parsed — use original phone

    # Advance to next step
    try:
        next_idx = COLLECTION_STEPS.index(step) + 1
    except ValueError:
        next_idx = len(COLLECTION_STEPS)

    if next_idx < len(COLLECTION_STEPS):
        next_step = COLLECTION_STEPS[next_idx]
        update["collection_step"] = next_step
        await _update_session(phone, update)
        reply = STEP_QUESTIONS[next_step]
        return {"reply": reply, "action": "collecting", "session_id": session["id"]}

    # ── Collection complete — score ────────────────────────────────────────────
    merged = {**dict(session), **update}
    score, bucket = _compute_score(merged)
    update["collection_step"] = None
    update["funnel_stage"] = "scored"
    update["score"] = score
    update["score_bucket"] = bucket
    await _update_session(phone, update)

    log.info(f"[recruit] {phone} scored={score} ({bucket}) — {merged.get('full_name','?')}")
    return {
        "reply": INTAKE_COMPLETE_MSG,
        "action": "scored",
        "session_id": session["id"],
    }


def is_recruitment_trigger(text: str) -> bool:
    """Quick check: does this message look like a job inquiry?"""
    lower = text.lower()
    return any(kw in lower for kw in INTAKE_KEYWORDS)


async def get_active_session(phone: str) -> Optional[dict]:
    """Return active collecting session for phone, or None."""
    session = await _get_session(phone)
    if session and session.get("funnel_stage") in ("collecting", "new"):
        return session
    return None
