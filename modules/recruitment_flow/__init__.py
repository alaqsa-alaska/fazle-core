"""
Fazle Core — Recruitment Funnel (async version)
Adapted from resources/recruitment.py for asyncpg DB interface.

Tables used:
  fazle_recruitment_sessions — tracks conversation state per phone
    wbom_candidates — canonical candidate identity linked to each session
    wbom_candidate_conversations — append-only inbound conversation audit
  (falls back gracefully if table doesn't exist yet)

Steps: name → age → area → job_preference → experience → phone_confirm
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.database import db_conn, fetch_one, execute, fetch_val

log = logging.getLogger("fazle.recruitment")
SESSION_TTL = timedelta(hours=24)


class _ConversationAuditUnavailable(RuntimeError):
    pass


OPERATIONAL_ROLES = frozenset({
    "admin", "accountant", "employee", "supervisor", "family",
    "escort_client", "client_escort_buyer", "vip_client", "repeat_client",
    "vendor", "known_contact",
})
OPERATIONAL_INTENTS = frozenset({
    "attendance", "leave", "payment_due", "advance_request",
    "client_order", "slip_submission",
})
# "salary_query" and "escort_duty" are deliberately excluded: by the time
# this set is consulted in
# recruitment_eligibility(), role has already been confirmed non-operational
# (unknown/new_lead/candidate) -- a real employee's salary question never
# reaches this check at all, it's already rejected by the role check above.
# Candidate/unknown questions about escort salary or duty are legitimate
# recruitment topics and must reach the recruitment detector. Actual escort
# orders still fail recruitment detection and continue to the escort-order
# gate in modules.message_router.

# ── Intake trigger keywords ────────────────────────────────────────────────────
INTAKE_KEYWORDS: set[str] = {
    "চাকরি", "vacancy", "apply", "hire",
    "recruit", "নিয়োগ", "কাজের",
    "পদ", "পারব", "নেবেন", "জয়েন",
    "cv", "joining",  # P17-FIX-3: added for "cv dibo" / "joining kobe" test cases
    "office location", "office address", "contact number", "whatsapp number",
    "অফিস কোথায়", "অফিস কোথায়", "অফিসের ঠিকানা", "যোগাযোগ নম্বর",
}

# 2026-08-06 fix: "job", "interested", and their Bangla equivalent
# "আগ্রহী" were removed from the set below -- confirmed live, a bare
# substring match on any of the three fires on ordinary non-recruitment
# text (an internal test message "run job health_summary", and a generic
# business inquiry "I am interested in learning more about your business"
# both silently opened a recruitment funnel session). The Bangla word is
# included even though only the two English words were reported live --
# it's the direct translation of "interested" and carries the identical
# false-positive risk. All three now only count as a trigger inside a
# job-shaped phrase (RECRUITMENT_TRIGGER_PHRASES, below) -- matching the
# specificity every other entry here already has on its own (চাকরি/
# vacancy/recruit/cv/... are distinctive enough as bare words;
# job/interested/আগ্রহী were not).

VALID_POSITIONS: dict[str, str] = {
    "1": "Escort",
    "2": "Survey Scout",
    "3": "Security Guard",
    "4": "Security Supervisor",
    "5": "Assistant Supervisor",
    "6": "Operation Officer",
    "7": "Security In-Charge",
    "8": "Marketing Officer",
    "9": "Ghat Supervisor",
    "escort": "Escort",
    "survey": "Survey Scout",
    "guard": "Security Guard",
    "security": "Security Guard",
    "supervisor": "Security Supervisor",
    "assistant supervisor": "Assistant Supervisor",
    "in-charge": "Security In-Charge",
    "incharge": "Security In-Charge",
    "marketing": "Marketing Officer",
    "ghat supervisor": "Ghat Supervisor",
    "ghat": "Ghat Supervisor",
    "operation": "Operation Officer",
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
        "১. Escort\n"
        "২. Survey Scout (জাহাজে)\n"
        "৩. Security Guard\n"
        "৪. Security Supervisor\n"
        "৫. Assistant Supervisor\n"
        "৬. Operation Officer\n"
        "৭. Security In-Charge\n"
        "৮. Marketing Officer\n"
        "৯. Ghat Supervisor\n\n"
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
    "আরো জানতে: 01958 122300\n\n"
    "আবেদনের জন্য ধন্যবাদ।"
)

ALREADY_APPLIED_MSG: str = (
    "আপনার আবেদন ইতিমধ্যে প্রক্রিয়াধীন রয়েছে।\n"
    "আমাদের টিম শীঘ্রই যোগাযোগ করবে।"
)

# Soft-ask prepended to the first recruitment reply only when identity_brain
# found NO signal at all for this number (role="unknown" -- distinct from
# role="candidate", which already means an explicit job-related keyword
# matched). Covers the narrow case of an existing employee texting from a
# genuinely new number with zero prior cash/attendance/escort/contact
# history on it -- identity_brain's phone-only secondary-evidence lookups
# (already run before this point) would have resolved a known number to
# "employee" already, so this only fires for a true first-ever contact.
# This is a soft ask, not a gate -- the funnel proceeds either way.
UNKNOWN_SENDER_SOFT_ASK: str = (
    "আপনাকে চিনতে পারছি না। আপনি কি আমাদের পুরনো employee?\n"
    "হলে দয়া করে আপনার নাম এবং পুরনো নম্বর জানান।\n\n"
)


# ── Parsers ────────────────────────────────────────────────────────────────────

_BENGALI_DIGITS_OUT = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def _bn_digits(n: int) -> str:
    """ASCII int -> Bengali-numeral string, for candidate-facing replies
    (2026-08-19) -- an admin-directive age override arrives as an ASCII
    number (remember_business_rule's directive_text) but every other
    number in this funnel's Bangla messages uses Bengali numerals; without
    this the rejection reply would visibly mix "23" into an otherwise
    Bengali-numeral sentence."""
    return str(n).translate(_BENGALI_DIGITS_OUT)


def _parse_age(text: str, age_range: tuple[int, int] = (18, 55)) -> Optional[int]:
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        val = int(m.group(1))
        lo, hi = age_range
        return val if lo <= val <= hi else None
    return None


async def _get_effective_age_range() -> tuple[int, int]:
    """Admin-directive override for the recruitment minimum age (2026-08-19,
    Item 3 of the Hermes->Earth rename+autonomy pass). Mirrors the exact
    fallback shape of social_auto_reply/reply_rules.py's
    get_effective_office_numbers(): check the most recent active directive
    for subject_key="recruitment_minimum_age" first, fall back to the
    hardcoded 18-55 default on no row, a malformed value, or any error --
    never let a bad directive value widen/invert the range or raise. Written
    via Earth's remember_business_rule tool or the /admin/directives
    endpoint (see core/.claude/CLAUDE.md's admin_directives bullet)."""
    try:
        from modules.admin_directives import list_directives
        rows = await list_directives(subject_key="recruitment_minimum_age", status="active", limit=1)
        if rows and rows[0].get("directive_text"):
            m = re.search(r"\d{1,3}", rows[0]["directive_text"])
            if m:
                val = int(m.group())
                if 0 < val <= 55:
                    return (val, 55)
    except Exception:
        pass
    return (18, 55)


_NAME_REJECT_KEYWORDS: tuple[str, ...] = (
    "hsc", "ssc", "এইচএসসি", "এসএসসি", "শিক্ষাগত", "যোগ্যতা", "ডিগ্রি",
    "অনার্স", "মাস্টার্স", "পাস করেছি", "শেষ করেছি", "কোন পদ", "পদ আছে",
    "চাকরি আছে", "চাকরি খুঁজছি", "বেতন কত", "কত টাকা", "বেতন কি",
)


def _looks_like_name(text: str) -> bool:
    """Reject obvious non-name free text at the 'name' collection step
    (2026-08-21, Owner Correction 2 / regression case 9694: a candidate's
    education statement/question -- "আমি HSC শেষ করেছি, এই শিক্ষাগত
    যোগ্যতায় কোন পদ আছে" -- was stored verbatim as full_name because this
    step accepted ANY text unconditionally, corrupting the field). Wired
    the same way as the existing age/job_preference validate-or-reask
    steps below -- not a new mechanism.

    Deliberately conservative: a real name is short, plain, and has no
    question mark or education/position/salary vocabulary. Anything
    ambiguous still passes (fails OPEN toward accepting a real name) --
    this only catches text that unmistakably is not a name. The raw text
    is never lost even when rejected: _audit_candidate_conversation()
    still records it verbatim in wbom_candidate_conversations, which is
    the "structured conversation/context metadata" this fact belongs in
    instead of full_name."""
    stripped = text.strip()
    if not stripped or len(stripped) > 60:
        return False
    if "?" in stripped or "؟" in stripped:
        return False
    lower = stripped.lower()
    return not any(kw in lower for kw in _NAME_REJECT_KEYWORDS)


def _parse_job_preference(text: str) -> Optional[str]:
    norm = text.strip().lower()
    for key, val in sorted(VALID_POSITIONS.items(), key=lambda item: len(item[0]), reverse=True):
        if key in norm:
            return val
    return None


def _parse_experience(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,2})\b", text)
    return int(m.group(1)) if m else 0


def _parse_phone(text: str) -> Optional[str]:
    # 2026-08-21 fix: transliterate Bengali numerals to ASCII first, reusing
    # modules.phone_normalizer's canonical transliteration helper (real
    # candidate case: "০১৭১৬০৮১৯২৫" -- a valid number, just Bengali digits
    # -- previously never parsed here, repeatedly re-asking for a phone
    # number the candidate had already given). Output format/contract is
    # unchanged (still an 11-digit 01XXXXXXXXX-shaped string, not the
    # normalizer's own 13-digit 8801XXXXXXXXX canonical form) -- this only
    # reuses the digit-transliteration step, not the full normalizer, so
    # every existing caller of this function keeps seeing the same shape.
    from modules.phone_normalizer import to_ascii_digits
    m = re.search(r"(0?1[3-9]\d{8})", re.sub(r"\D", "", to_ascii_digits(text)))
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


async def _update_session(phone: str, fields: dict, *, conn: Any = None) -> None:
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
        if conn is not None:
            await conn.execute(sql, *vals)
        else:
            await execute(sql, *vals)
    except Exception as e:
        if conn is not None:
            raise
        log.warning(f"[recruit] update session failed: {e}")


async def _ensure_candidate_for_session(
    phone: str,
    session_id: int,
    source_message: str,
    source: str,
) -> Optional[int]:
    try:
        candidate_id = await fetch_val(
            """INSERT INTO wbom_candidates
                   (phone, funnel_stage, collection_step, source_message, updated_at)
               VALUES ($1, 'collecting', 'name', $2, NOW())
               ON CONFLICT (phone) DO NOTHING
               RETURNING candidate_id""",
            phone, source_message[:500],
        )
        if candidate_id is None:
            candidate_id = await fetch_val(
                "SELECT candidate_id FROM wbom_candidates WHERE phone = $1",
                phone,
            )
        if candidate_id is not None:
            await execute(
                """UPDATE fazle_recruitment_sessions
                   SET candidate_id = $1, updated_at = NOW()
                   WHERE id = $2""",
                candidate_id, session_id,
            )
            return int(candidate_id)
    except Exception as e:
        log.warning(
            "[recruit] candidate link failed phone=%s session=%s source=%s: %s",
            phone, session_id, source, e,
        )
    return None


async def _audit_candidate_conversation(
    candidate_id: int,
    step: str,
    message: str,
    source: str,
    message_id: Optional[str] = None,
    *,
    conn: Any = None,
) -> Optional[bool]:
    try:
        sql = """INSERT INTO wbom_candidate_conversations
                     (candidate_id, step, direction, message_text, source_bridge, message_id)
                 VALUES ($1, $2, 'inbound', $3, $4, $5)
                 ON CONFLICT (source_bridge, message_id)
                     WHERE message_id IS NOT NULL
                 DO NOTHING
                 RETURNING conv_id"""
        args = (candidate_id, step, message, source, message_id or None)
        if conn is not None:
            conv_id = await conn.fetchval(sql, *args)
        else:
            conv_id = await fetch_val(sql, *args)
        return conv_id is not None
    except Exception as e:
        if conn is not None:
            raise
        log.warning(
            "[recruit] conversation audit failed candidate=%s step=%s: %s",
            candidate_id, step, e,
        )
        return None


async def _finalize_candidate(candidate_id: int, session: dict, *, conn: Any = None) -> None:
    try:
        sql = """UPDATE wbom_candidates
                 SET full_name = $1, age = $2, area = $3,
                     job_preference = $4, experience_years = $5,
                     funnel_stage = 'scored', collection_step = NULL,
                     score = $6, score_bucket = $7,
                     last_contact_at = NOW(), updated_at = NOW()
                 WHERE candidate_id = $8"""
        args = (
            session.get("full_name"), session.get("age"), session.get("area"),
            session.get("job_preference"), session.get("experience_years") or 0,
            session.get("score") or 0, session.get("score_bucket") or "cold",
            candidate_id,
        )
        if conn is not None:
            await conn.execute(sql, *args)
        else:
            await execute(sql, *args)
    except Exception as e:
        if conn is not None:
            raise
        log.warning("[recruit] candidate finalize failed candidate=%s: %s", candidate_id, e)


async def _apply_audited_transition(
    phone: str,
    candidate_id: int,
    step: str,
    message: str,
    source: str,
    message_id: Optional[str],
    update: dict,
    final_session: Optional[dict] = None,
) -> Optional[bool]:
    try:
        async with db_conn() as conn:
            async with conn.transaction():
                try:
                    inserted = await _audit_candidate_conversation(
                        candidate_id=candidate_id,
                        step=step,
                        message=message,
                        source=source,
                        message_id=message_id,
                        conn=conn,
                    )
                except Exception as e:
                    raise _ConversationAuditUnavailable from e
                if inserted is False:
                    return False
                await _update_session(phone, dict(update), conn=conn)
                if final_session is not None:
                    await _finalize_candidate(candidate_id, final_session, conn=conn)
        return True
    except _ConversationAuditUnavailable as e:
        log.warning(
            "[recruit] conversation audit failed; continuing without audit "
            "candidate=%s step=%s: %s",
            candidate_id, step, e,
        )
        try:
            async with db_conn() as conn:
                async with conn.transaction():
                    await _update_session(phone, dict(update), conn=conn)
                    if final_session is not None:
                        await _finalize_candidate(candidate_id, final_session, conn=conn)
            return None
        except Exception as fallback_error:
            log.warning(
                "[recruit] audit fail-open transition rolled back "
                "candidate=%s step=%s: %s",
                candidate_id, step, fallback_error,
            )
            return False
    except Exception as e:
        log.warning(
            "[recruit] audited transition rolled back candidate=%s step=%s: %s",
            candidate_id, step, e,
        )
        return False


# ── Main intake function ───────────────────────────────────────────────────────

async def intake_message(
    phone: str,
    message: str,
    source: str = "whatsapp",
    role: str = "unknown",
    message_id: Optional[str] = None,
    trigger_text: Optional[str] = None,
) -> dict:
    """
    Process one inbound WhatsApp message for recruitment funnel.

    role: identity_brain's resolved role for this sender, e.g. "unknown" or
    "candidate". Only used to decide whether to prepend a soft ask to the
    very first reply for a brand-new session (see UNKNOWN_SENDER_SOFT_ASK) --
    never changes funnel behavior otherwise.

    Returns:
      {
        "reply": str,       — text to send back (or "" to ignore)
        "action": str,      — created | collecting | scored | ignored | already_applied
        "session_id": int | None
      }
    """
    phone = phone.strip()
    raw_message = message
    text = raw_message.strip()
    lower = (trigger_text if trigger_text is not None else text).lower()

    session = await _get_session(phone)

    # ── New visitor ────────────────────────────────────────────────────────────
    if session is None:
        # 2026-08-21 fix (Owner Correction 1 / regression case ...1609): this
        # used to check only the bare INTAKE_KEYWORDS set, which is NARROWER
        # than RECRUITMENT_TRIGGER_PHRASES — e.g. "কাজ করতে চাই" ("I want to
        # work") matches RECRUITMENT_TRIGGER_PHRASES (so recruitment_
        # eligibility() correctly calls this "explicit_recruitment", highest
        # confidence) but contains no INTAKE_KEYWORDS substring at all, so a
        # brand-new session here silently returned action="ignored" for
        # exactly the message type that should most reliably open a lead.
        # is_recruitment_trigger() is the superset (INTAKE_KEYWORDS OR
        # RECRUITMENT_TRIGGER_PHRASES) — using it here instead keeps this
        # gate at least as permissive as every caller that already decided
        # this message is a genuine recruitment trigger, and callers that
        # only relied on the narrower bare-keyword match still pass (it's a
        # superset, never narrower).
        if not is_recruitment_trigger(lower):
            return {"reply": "", "action": "ignored", "session_id": None}

        sid = await _create_session(phone, text, source)
        if sid is not None:
            candidate_id = await _ensure_candidate_for_session(phone, sid, text, source)
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id=candidate_id,
                    step="intake",
                    message=raw_message,
                    source=source,
                    message_id=message_id,
                )
        reply = STEP_QUESTIONS["name"]
        if (role or "").strip().lower() == "unknown":
            reply = UNKNOWN_SENDER_SOFT_ASK + reply
            # KNOWN GAP (2026-07-11): we send a soft-ask but do NOT
            # automatically cross-reference against employee records.
            # An existing employee texting from a new SIM is
            # indistinguishable from a stranger until admin manually
            # links the number. To fix: implement
            # secondary_evidence_match() in identity_brain that checks
            # cash_transactions/attendance/escort_roster by name hint
            # from the soft-ask reply. Tracked as a separate task.
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
    candidate_id = session.get("candidate_id")
    if candidate_id is None:
        candidate_id = await _ensure_candidate_for_session(
            phone,
            session["id"],
            session.get("source_message") or text,
            session.get("source_bridge") or source,
        )
    update: dict = {}

    if step == "name":
        if not _looks_like_name(text):
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id, step, raw_message, source, message_id,
                )
            return {
                "reply": "দুঃখিত, বুঝতে পারিনি। দয়া করে শুধু আপনার পুরো নাম লিখুন (যেমনঃ Md Karim)।",
                "action": "collecting",
                "session_id": session["id"],
            }
        update["full_name"] = text.strip()[:100]
    elif step == "age":
        age_range = await _get_effective_age_range()
        val = _parse_age(text, age_range)
        if val:
            update["age"] = val
        else:
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id, step, raw_message, source, message_id,
                )
            return {
                "reply": f"সাধারণ বয়সসীমা {_bn_digits(age_range[0])}–{_bn_digits(age_range[1])} বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)",
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
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id, step, raw_message, source, message_id,
                )
            return {
                "reply": "দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন।\n" + STEP_QUESTIONS["job_preference"],
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
        if candidate_id is not None:
            audit_inserted = await _apply_audited_transition(
                phone, candidate_id, step, raw_message, source, message_id, update,
            )
            if audit_inserted is False:
                return {
                    "reply": STEP_QUESTIONS.get(step, STEP_QUESTIONS["name"]),
                    "action": "collecting",
                    "session_id": session["id"],
                }
        else:
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
    final_session = {**merged, **update}
    if candidate_id is not None:
        audit_inserted = await _apply_audited_transition(
            phone, candidate_id, step, raw_message, source, message_id, update, final_session,
        )
        if audit_inserted is False:
            return {
                "reply": STEP_QUESTIONS.get(step, STEP_QUESTIONS["name"]),
                "action": "collecting",
                "session_id": session["id"],
            }
    else:
        await _update_session(phone, update)

    log.info(f"[recruit] {phone} scored={score} ({bucket}) — {merged.get('full_name','?')}")
    return {
        "reply": INTAKE_COMPLETE_MSG,
        "action": "scored",
        "session_id": session["id"],
    }


RECRUITMENT_TRIGGER_PHRASES: tuple[str, ...] = (
    "কাজ চাই", "কাজ করতে চাই", "কাজ আছে", "কাজ পাব", "কাজ করতে আগ্রহী",
    "looking for work", "need a job", "job available", "job interest",
    # "job" in job-seeking context (was a bare INTAKE_KEYWORDS entry)
    "a job", "any job", "job vacancy", "job apply", "apply for job",
    "job korte chai", "job er jonno", "job ache", "job lagbe",
    "job pete chai", "job dorkar", "job chai",
    # "interested" in job-seeking context (was a bare INTAKE_KEYWORDS entry;
    # its Bangla equivalent "আগ্রহী" keeps working via the existing
    # phrase above and via "চাকরি"/"নিয়োগ" (still bare keywords) --
    # no new Bangla phrase added here, see comment on INTAKE_KEYWORDS above.
    "interested in this job", "interested in the job", "interested to apply",
    "interested in working", "interested in joining", "job e interested",
    "interested for the post", "interested in this post",
    "interested in recruitment", "interested in the vacancy",
)


def is_recruitment_trigger(text: str) -> bool:
    """Quick check: does this message look like a job inquiry?"""
    lower = text.lower()
    if any(kw in lower for kw in INTAKE_KEYWORDS):
        return True
    return any(phrase in lower for phrase in RECRUITMENT_TRIGGER_PHRASES)


async def get_active_session(phone: str) -> Optional[dict]:
    """Return a fresh collecting session for phone, or expire it."""
    session = await _get_session(phone)
    if not session or session.get("funnel_stage") not in ("collecting", "new"):
        return None
    updated_at = session.get("updated_at") or session.get("created_at")
    if updated_at:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - updated_at > SESSION_TTL:
            await execute(
                """UPDATE fazle_recruitment_sessions
                   SET funnel_stage='expired'
                   WHERE phone=$1 AND funnel_stage IN ('collecting','new')""",
                phone,
            )
            log.info("[recruit] expired stale session phone=%s updated_at=%s", phone, updated_at)
            return None
    return session


# Moved from modules.recruitment_ai (2026-07-11) — this was the only live
# import from that module; recruitment_ai's own reply-generation pipeline
# (generate_recruitment_reply etc.) is superseded by
# modules.conversation_layer.recruitment, called below via
# route_recruitment_message(). recruitment_ai has no live callers left.
_QUESTION_HINTS = (
    "who are you",
    "who r u",
    "আপনি কে",
    "তুমি কে",
    "কেন",
    "why",
    "am i asked for job",
    "asked for job",
    "lok lagbe",
    "লোক লাগবে",
    "কাজ আছে",
    "job ache",
)


def looks_like_recruitment_followup(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return False
    return any(hint in lower for hint in _QUESTION_HINTS)


async def recruitment_eligibility(
    phone: str,
    text: str,
    *,
    role: str = "unknown",
    intent: str = "unknown",
    source: str = "",
) -> dict:
    """Return the single recruitment routing/delivery decision.

    source (2026-08-21, urgent Facebook-boost recovery pass): the bridge
    this message arrived on, e.g. "bridge3". Two real gaps found live
    against today's actual Facebook-ad traffic:

    1. Once a candidate has an active collecting session, their next reply
       is very often free text answering the funnel's own question (a
       district name, "Any post", a bare age/height line) -- it will never
       match is_recruitment_trigger() or looks_like_recruitment_followup()'s
       keyword hints, so it silently fell out of the funnel mid-conversation
       and got answered by Route B's generic LLM reply instead, with no
       lead/session update at all. Confirmed live: a candidate ("Abuhanif")
       held a complete real conversation -- name, age, height, phone,
       district, education, position preference, all given -- entirely
       outside the funnel, zero lead/session rows, because no single reply
       after the first matched a keyword. Fix: ANY message from a phone
       with an active collecting/new session is eligible, unconditionally
       -- they already explicitly entered the funnel once; a session
       existing at all is a far stronger signal than any keyword.

    2. bridge3 (config label "REC2") is a dedicated, single-purpose
       recruitment channel -- not a general inquiry line -- confirmed by
       its own no-cross-bridge-fallback design elsewhere in this codebase.
       Its actual live traffic today is dominated by Meta/Facebook Click-
       to-WhatsApp ad opener text ("Hello! Can I get more info on this?"),
       which is a Meta-generated template, not the candidate's own words,
       and matches no recruitment keyword or detector layer -- yet on this
       specific number it is, for all practical purposes, always a
       recruitment inquiry. Fix: for a brand-new (no active session)
       unknown/new_lead/candidate sender on bridge3 specifically, treat
       the message as recruitment-eligible even with no keyword/detector
       match. Scoped to bridge3 only -- this does NOT change eligibility
       on bridge1/bridge2/meta, where "Hello, can you help me" is a
       genuinely ambiguous general inquiry, not a recruitment-line signal.
    """
    role = (role or "unknown").lower()
    intent = (intent or "unknown").lower()
    explicit = is_recruitment_trigger(text)
    active_session = await get_active_session(phone)

    if role in OPERATIONAL_ROLES or role not in ("unknown", "new_lead", "candidate"):
        return {"eligible": False, "autosend": False, "reason": "operational_identity",
                "active_session": active_session}
    if intent in OPERATIONAL_INTENTS:
        return {"eligible": False, "autosend": False, "reason": "operational_intent",
                "active_session": active_session}
    if active_session:
        # Superset of the old "session_followup_draft" keyword-hint check --
        # a session already existing is itself the strong signal; still
        # draft-only (autosend=False) so an established candidate's free-
        # text answer never bypasses the normal review posture, only the
        # explicit-trigger/detector-entity paths below autosend.
        return {"eligible": True, "autosend": False, "reason": "active_session_continuation",
                "active_session": active_session}
    if explicit:
        return {"eligible": True, "autosend": True, "reason": "explicit_recruitment",
                "active_session": active_session}

    # 3-layer detector (2026-07-11): catches natural phrasing the flat
    # is_recruitment_trigger() keyword set above misses (e.g. "ভাই সুযোগ
    # দেন", "আমি available"). Conservative: only the fast, deterministic
    # entity layer autosends; the intent/context layers are lower-confidence
    # and go to draft review instead, same as active_session_continuation.
    from modules.recruitment_detector import detect_recruitment_intent

    detector = await detect_recruitment_intent(text=text, phone=phone, intent=intent)
    if detector["is_recruitment"]:
        return {
            "eligible": True,
            "autosend": detector["layer"] == "entity",
            "reason": f"detector_{detector['layer']}",
            "active_session": active_session,
            "detector": detector,
        }

    if source == "bridge3":
        return {"eligible": True, "autosend": True, "reason": "bridge3_dedicated_channel",
                "active_session": active_session}

    return {"eligible": False, "autosend": False, "reason": "no_recruitment_context",
            "active_session": active_session}


def _is_llm_priority_phone(phone: str) -> bool:
    """Phase 1B (2026-08-06, Owner-approved allowlist, see
    management_decisions.md "Recruitment Phase 1B — Active-Session LLM
    Priority Allowlist"). True only for phones explicitly approved to have
    the live KB/RAG/LLM reply layer (generate_recruitment_reply()) tried
    BEFORE intake_message() during an active recruitment session --
    everyone else keeps the unconditional intake_message()-first behavior
    in route_recruitment_message() unchanged. Deliberately a plain exact-
    string check, not phone-normalized (unlike e.g.
    modules.escort._escort_client_phone_list()'s normalize_bd_phone()
    usage) -- every phone already reaching route_recruitment_message() is
    already in this module's own canonical 8801XXXXXXXXX form (see
    fazle_recruitment_sessions.phone in knowledge_base/06_developer_system/
    recruitment_flow_system.md), so adding a second normalization step
    here would risk a silent format mismatch rather than prevent one.
    Config value is empty by default -- see recruitment_llm_priority_phones
    in app/config.py for the full comment and admission-criteria record."""
    from app.config import get_settings
    raw = getattr(get_settings(), "recruitment_llm_priority_phones", "") or ""
    allowlist = {p.strip() for p in raw.split(",") if p.strip()}
    return phone in allowlist


async def route_recruitment_message(
    phone: str,
    text: str,
    *,
    source: str,
    message_id: Optional[str] = None,
    role: str = "unknown",
    intent: str = "unknown",
    history: str = "",
    contact_context: str = "",
) -> dict:
    """Canonical recruitment route: eligibility + live reply in one place."""
    decision = await recruitment_eligibility(phone, text, role=role, intent=intent, source=source)
    if not decision.get("eligible"):
        return {
            "eligible": False,
            "autosend": bool(decision.get("autosend")),
            "reason": decision.get("reason", "no_recruitment_context"),
            "reply": None,
        }

    active_session = decision.get("active_session")

    # Phase 1B (2026-08-06, Owner-approved allowlist): for an explicitly
    # allowlisted phone only, try the live KB/RAG/LLM reply layer FIRST,
    # even during an active session -- falling through to the unchanged
    # intake_message()-first logic below if it returns nothing (same
    # empty-reply fallback semantics as the non-active-session path
    # further down). This is the one case where the 2026-07-22 rule right
    # below does not apply unconditionally; every non-allowlisted phone
    # (i.e. everyone, while the allowlist is empty) is completely
    # unaffected by this block.
    if active_session and _is_llm_priority_phone(phone):
        # Hermes customer-dispatch Phase 1 (2026-08-12, Minimal
        # Modification Plan item 2): flag-gated substitution, default
        # OFF, same fall-through contract as the other three migrated
        # call sites — see modules/hermes_dispatch.py's own docstring.
        # The deterministic session/candidate bookkeeping below is
        # completely unaffected either way, since it only consumes
        # whatever `priority_reply` text ends up here.
        priority_reply = None
        from modules.preflight_guard import is_feature_active
        if is_feature_active("hermes_customer_recruitment", default=False):
            # KB gap fix (2026-08-12, Owner-confirmed), targeted revision
            # (2026-08-12): resources/ops/recruitment_source_of_truth.txt
            # is the deliberate single authoritative source for
            # recruitment answers (see its own header). This call site
            # originally passed no kb_context at all (Hermes correctly
            # declined to invent position details rather than
            # hallucinate). A first fix tried the same generic RAG fetch
            # (k=2, min_score=2.0) conversation_layer/recruitment.py's own
            # _safe_rag_context() uses -- live-tested and found it doesn't
            # reliably surface this file for natural phrasings, so it's
            # read whole instead (short file, ~4KB, cached after first
            # read). "Finding E" revision (2026-08-12, Owner-directed):
            # the fee/deposit section is now included only when this
            # message itself looks like a fee question (matches the
            # source file's own "don't volunteer fee info" instruction),
            # and hermes_dispatch.generate_recruitment_reply() itself now
            # fact-checks the reply against this same kb_context before
            # returning it -- see hermes_dispatch.build_recruitment_context
            # / _recruitment_reply_matches_source().
            from modules.hermes_dispatch import (
                generate_recruitment_reply as _hermes_recruitment_reply,
                build_recruitment_context as _build_recruitment_context,
            )
            _kb_context = _build_recruitment_context(text)
            priority_reply = await _hermes_recruitment_reply(
                phone=phone, text=text, source=source,
                kb_context=_kb_context, contact_context=contact_context, history=history,
            )
        if not priority_reply:
            from modules.conversation_layer import generate_recruitment_reply as _llm_priority_reply
            priority_reply = await _llm_priority_reply(
                phone=phone, text=text, source=source,
                contact_context=contact_context, history=history,
            )
        if priority_reply:
            candidate_id = active_session.get("candidate_id")
            if candidate_id is None:
                candidate_id = await _ensure_candidate_for_session(
                    phone,
                    active_session["id"],
                    active_session.get("source_message") or text,
                    active_session.get("source_bridge") or source,
                )
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id=candidate_id,
                    step=active_session.get("collection_step") or "name",
                    message=text,
                    source=source,
                    message_id=message_id,
                )
            return {
                "eligible": True,
                "autosend": bool(decision.get("autosend")),
                "reason": "llm_priority_session_continuation",
                "reply": priority_reply,
            }
        # LLM layer produced nothing (empty or safety-restricted) -- fall
        # through to the unchanged intake_message()-first logic below,
        # same as every non-allowlisted phone.

    # 2026-07-22 (Final Instruction Section 4 — recruitment audit found
    # this was FAIL for multi-message continuation): an in-progress funnel
    # must always advance via intake_message() first. Previously
    # intake_message() was only reached as a fallback when
    # generate_recruitment_reply() returned an empty string, which it
    # almost never does (its KB/rule/LLM layer nearly always produces some
    # informational text) -- so collection_step effectively never advanced
    # past the first question in practice, even with an active session.
    if active_session:
        intake = await intake_message(
            phone, text, source, role=role, message_id=message_id,
        )
        intake_reply = (intake.get("reply") or "").strip()
        if intake_reply:
            return {
                "eligible": True,
                "autosend": bool(decision.get("autosend")),
                "reason": decision.get("reason", "session_continuation"),
                "reply": intake_reply,
            }
        # intake_message() produced nothing (e.g. an "already_applied"
        # template that happened to be empty) — fall through to the
        # informational reply below as a safety net, same as before.

    # Hermes customer-dispatch Phase 1 (2026-08-12, Minimal Modification
    # Plan item 2): flag-gated substitution, default OFF, same
    # fall-through contract as every other migrated call site. The
    # deterministic intake_message()/STEP_QUESTIONS fallback below is
    # completely unaffected either way — it only runs when `reply` ends
    # up empty from either engine.
    reply = None
    from modules.preflight_guard import is_feature_active
    if is_feature_active("hermes_customer_recruitment", default=False):
        # KB gap fix (2026-08-12, Owner-confirmed), targeted revision
        # (2026-08-12) — see the identical comment at this function's
        # other call site above.
        from modules.hermes_dispatch import (
            generate_recruitment_reply as _hermes_recruitment_reply,
            build_recruitment_context as _build_recruitment_context,
        )
        _kb_context = _build_recruitment_context(text)
        reply = await _hermes_recruitment_reply(
            phone=phone,
            text=text,
            source=source,
            kb_context=_kb_context,
            contact_context=contact_context,
            history=history,
        )
    if not reply:
        from modules.conversation_layer import generate_recruitment_reply
        reply = await generate_recruitment_reply(
            phone=phone,
            text=text,
            source=source,
            contact_context=contact_context,
            history=history,
        )

    # 2026-08-21 (urgent Facebook-boost recovery pass): route_recruitment_
    # message() only ever reaches this point after recruitment_eligibility()
    # already authoritatively decided decision["eligible"] is True (checked
    # at the top of this function) -- but intake_message()'s own "New
    # visitor" gate independently re-derives eligibility from the raw text
    # via is_recruitment_trigger(), a NARROWER check than what
    # recruitment_eligibility() itself now supports (active-session
    # continuation, bridge3's dedicated-channel rule, the context/intent
    # detector layers). Confirmed live: a bridge3 candidate's real, complete
    # conversation (name/age/height/education/position all given) held
    # entirely outside the funnel -- zero lead/session rows -- because no
    # single message ever happened to contain a bare recruitment keyword,
    # even though every one of those messages was correctly judged eligible
    # upstream. Passing this forcing trigger_text makes intake_message()
    # trust the eligibility decision this function's caller already made,
    # instead of re-deriving (and disagreeing with) it from raw text.
    _FORCE_RECRUITMENT_TRIGGER_TEXT = "চাকরি"

    if not reply:
        step = active_session.get("collection_step") if isinstance(active_session, dict) else None
        if step:
            reply = STEP_QUESTIONS.get(step, STEP_QUESTIONS["name"])
        else:
            intake = await intake_message(
                phone, text, source, role=role, message_id=message_id,
                trigger_text=_FORCE_RECRUITMENT_TRIGGER_TEXT,
            )
            reply = (intake.get("reply") or "").strip() or STEP_QUESTIONS["name"]
    elif not active_session and decision.get("autosend"):
        # 2026-07-22: a genuinely new, high-confidence job inquiry (explicit
        # keyword trigger or the entity-layer detector) must actually open
        # a fazle_recruitment_sessions row in the background, not just
        # receive an informational answer -- otherwise there is nothing for
        # the NEXT message to continue via the branch above, and the
        # candidate never enters the real data-collection funnel at all.
        # Best-effort only: never overrides the informational reply already
        # computed above. (2026-08-21: no longer gated a second time by
        # intake_message()'s own INTAKE_KEYWORDS/is_recruitment_trigger()
        # check -- see _FORCE_RECRUITMENT_TRIGGER_TEXT above -- this branch
        # only runs when decision.get("autosend") is already True, i.e.
        # recruitment_eligibility() already made the high-confidence call.)
        try:
            await intake_message(
                phone, text, source, role=role, message_id=message_id,
                trigger_text=_FORCE_RECRUITMENT_TRIGGER_TEXT,
            )
        except Exception as e:
            log.debug(f"[recruit] background session-open failed: {e}")

    return {
        "eligible": True,
        "autosend": bool(decision.get("autosend")),
        "reason": decision.get("reason", "explicit_recruitment"),
        "reply": reply,
    }
