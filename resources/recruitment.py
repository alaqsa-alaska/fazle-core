"""
services/recruitment.py  —  Sprint-3 WhatsApp Candidate Funnel
--------------------------------------------------------------
Functions:
  intake_message()       – process inbound WhatsApp message, return reply
  compute_score()        – deterministic 0-100 scoring
  score_candidate()      – persist score, advance to 'scored', create reminder
  assign_recruiter()     – assign recruiter, advance to 'assigned', create 48h reminder
  advance_stage()        – manual funnel stage advance
  get_recruitment_metrics() – owner KPI aggregates
  get_pending_reminders() – due reminders (for polling)
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from database import execute_query, insert_row, get_row, update_row

# ── Intake configuration ────────────────────────────────────────────────────

INTAKE_KEYWORDS: set[str] = {
    "job", "কাজ", "চাকরি", "vacancy", "apply", "hire",
    "recruit", "নিয়োগ", "কাজের", "চাই", "interested",
}

VALID_POSITIONS: set[str] = {
    "Escort", "Seal-man", "Security Guard", "Supervisor", "Labor",
}

COLLECTION_STEPS: list[str] = [
    "name", "age", "area", "job_preference", "experience", "join_date",
]

STEP_QUESTIONS: dict[str, str] = {
    "name": (
        "স্বাগতম! আমাদের কাছে আবেদন করার জন্য ধন্যবাদ। \n"
        "আপনার পুরো নাম কি? (What is your full name?)"
    ),
    "age": "আপনার বয়স কত বছর? (How old are you?)",
    "area": "আপনি কোথায় থাকেন? (Which area/district do you live in?)",
    "job_preference": (
        "আপনি কোন পদে কাজ করতে চান?\n"
        "1. Escort\n"
        "2. Seal-man\n"
        "3. Security Guard\n"
        "4. Supervisor\n"
        "5. Labor\n"
        "(Reply with the position name)"
    ),
    "experience": (
        "আপনার এই ক্ষেত্রে কত বছরের অভিজ্ঞতা আছে?\n"
        "(How many years of experience do you have? Reply with a number)"
    ),
    "join_date": (
        "আপনি কবে থেকে কাজ শুরু করতে পারবেন?\n"
        "(When can you start? Format: YYYY-MM-DD or DD/MM/YYYY)"
    ),
}

INTAKE_COMPLETE_MSG: str = (
    "আপনার তথ্য সফলভাবে সংগ্রহ করা হয়েছে। \n"
    "আপনি প্রয়োজনীয় জাতীয় পরিচয় পত্র / জন্ম-নিবন্ধন, শিক্ষাগত যোগ্যতার সনদ, ৪ কপি পাসপোর্ট ও ২ কপি স্ট্যাম্প সাইজ ছবি, আপনার মা-বাবার ভোটার আইডি কার্ডের ফটোকপি ইত্যাদি নিয়ে সরাসরি আমাদের অফিসে চলে আসুন। আসার পূর্বে অবশ্যই ০১৯৫৮১২২৩২২ নাম্বারে সরাসরি কল দিয়ে আসবেন। \n"
    "(Your profile is complete. Our team will contact you soon.)"
)

ALREADY_APPLIED_MSG: str = (
    "আপনার আবেদন ইতিমধ্যে প্রক্রিয়াধীন রয়েছে। \n"
    "আমাদের টিম শীঘ্রই যোগাযোগ করবে।\n"
    "(Your application is already being processed. We will contact you soon.)"
)


# ── Low-level helpers ────────────────────────────────────────────────────────

def _get_candidate_by_phone(phone: str) -> Optional[dict]:
    rows = execute_query(
        "SELECT * FROM wbom_candidates WHERE phone = %s LIMIT 1",
        (phone,),
    )
    return dict(rows[0]) if rows else None


def _get_candidate_by_id(candidate_id: int) -> Optional[dict]:
    row = get_row("wbom_candidates", "candidate_id", candidate_id)
    return dict(row) if row else None


def _log_conversation(candidate_id: int, step: str, message: str,
                      direction: str = "inbound") -> None:
    insert_row("wbom_candidate_conversations", {
        "candidate_id": candidate_id,
        "step": step,
        "direction": direction,
        "message_text": message,
    })


def _create_reminder(candidate_id: int, due_at: datetime, reason: str) -> None:
    insert_row("wbom_recruitment_reminders", {
        "candidate_id": candidate_id,
        "due_at": due_at.isoformat(),
        "reason": reason,
        "status": "pending",
    })


# ── Answer parsing ───────────────────────────────────────────────────────────

def _parse_age(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        val = int(m.group(1))
        return val if 10 <= val <= 80 else None
    return None


def _parse_job_preference(text: str) -> Optional[str]:
    normalised = text.strip().lower()
    # Accept digit shortcut (1–5) or partial name match
    shortcuts = {
        "1": "Escort", "2": "Seal-man", "3": "Security Guard",
        "4": "Supervisor", "5": "Labor",
    }
    if normalised in shortcuts:
        return shortcuts[normalised]
    for pos in VALID_POSITIONS:
        if pos.lower() in normalised or normalised in pos.lower():
            return pos
    return None


def _parse_experience(text: str) -> Optional[int]:
    m = re.search(r"\b(\d{1,2})\b", text)
    if m:
        return int(m.group(1))
    return None


def _parse_join_date(text: str) -> Optional[date]:
    text = text.strip()
    # Try ISO (YYYY-MM-DD)
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    # Try DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _apply_step_answer(candidate: dict, step: str, text: str) -> dict:
    """Return update_fields dict for the answered step."""
    update: dict = {}
    if step == "name":
        update["full_name"] = text.strip()[:100]
    elif step == "age":
        val = _parse_age(text)
        if val:
            update["age"] = val
    elif step == "area":
        update["area"] = text.strip()[:100]
    elif step == "job_preference":
        pos = _parse_job_preference(text)
        if pos:
            update["job_preference"] = pos
    elif step == "experience":
        val = _parse_experience(text)
        if val is not None:
            update["experience_years"] = val
    elif step == "join_date":
        d = _parse_join_date(text)
        if d:
            update["available_join_date"] = d.isoformat()
    return update


# ── Scoring ──────────────────────────────────────────────────────────────────

def compute_score(candidate: dict) -> tuple[int, str]:
    """
    Deterministic 0-100 lead score.
      experience (0/20/40/60) + quick availability (20) + high-demand position (10) + complete profile (10)
    Returns (score, bucket) where bucket is 'hot'|'warm'|'cold'.
    """
    score = 0

    # Experience — up to 60 pts
    exp = candidate.get("experience_years") or 0
    if exp >= 6:
        score += 60
    elif exp >= 3:
        score += 40
    elif exp >= 1:
        score += 20

    # Available within 7 days — 20 pts
    join_raw = candidate.get("available_join_date")
    if join_raw:
        if isinstance(join_raw, str):
            try:
                join_raw = date.fromisoformat(join_raw)
            except ValueError:
                join_raw = None
        if isinstance(join_raw, date):
            if join_raw <= date.today() + timedelta(days=7):
                score += 20

    # High-demand position — 10 pts
    if candidate.get("job_preference") in ("Escort", "Security Guard"):
        score += 10

    # Complete profile bonus — 10 pts
    required = ["full_name", "age", "area", "job_preference",
                "experience_years", "available_join_date"]
    if all(candidate.get(f) for f in required):
        score += 10

    score = min(score, 100)
    if score >= 70:
        bucket = "hot"
    elif score >= 40:
        bucket = "warm"
    else:
        bucket = "cold"
    return score, bucket


# ── Intake pipeline ──────────────────────────────────────────────────────────

def intake_message(phone: str, message: str) -> dict:
    """
    Process one inbound WhatsApp message for recruitment.

    Returns:
      {
        "reply": str,                  # text to send back
        "action": "created"|"collecting"|"scored"|"ignored",
        "candidate_id": int|None,
      }
    """
    phone = phone.strip()
    text = message.strip()
    lower = text.lower()

    candidate = _get_candidate_by_phone(phone)

    # ── New visitor ─────────────────────────────────────────
    if candidate is None:
        if not any(kw in lower for kw in INTAKE_KEYWORDS):
            return {"reply": "", "action": "ignored", "candidate_id": None}

        # Create candidate record
        candidate_id = insert_row("wbom_candidates", {
            "phone": phone,
            "funnel_stage": "collecting",
            "collection_step": "name",
            "source_message": text[:500],
        })
        _log_conversation(candidate_id, "intake", text)
        reply = STEP_QUESTIONS["name"]
        _log_conversation(candidate_id, "name", reply, direction="outbound")
        return {"reply": reply, "action": "created", "candidate_id": candidate_id}

    # ── Already past collection ──────────────────────────────
    if candidate["funnel_stage"] not in ("new", "collecting"):
        return {
            "reply": ALREADY_APPLIED_MSG,
            "action": "already_applied",
            "candidate_id": candidate["candidate_id"],
        }

    # ── Still collecting ────────────────────────────────────
    step = candidate.get("collection_step") or "name"
    _log_conversation(candidate["candidate_id"], step, text)

    update_fields = _apply_step_answer(candidate, step, text)

    # Determine next step
    try:
        next_idx = COLLECTION_STEPS.index(step) + 1
    except ValueError:
        next_idx = len(COLLECTION_STEPS)

    if next_idx < len(COLLECTION_STEPS):
        next_step = COLLECTION_STEPS[next_idx]
        update_fields["collection_step"] = next_step
        update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        update_row("wbom_candidates", "candidate_id", candidate["candidate_id"],
                   update_fields)
        reply = STEP_QUESTIONS[next_step]
        _log_conversation(candidate["candidate_id"], next_step, reply, direction="outbound")
        return {"reply": reply, "action": "collecting",
                "candidate_id": candidate["candidate_id"]}

    # ── Collection complete — score and close ───────────────
    merged = {**candidate, **update_fields}
    score, bucket = compute_score(merged)
    update_fields["funnel_stage"] = "scored"
    update_fields["collection_step"] = None
    update_fields["score"] = score
    update_fields["score_bucket"] = bucket
    update_fields["last_contact_at"] = datetime.now(timezone.utc).isoformat()
    update_fields["next_follow_up_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=24)
    ).isoformat()
    update_fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    update_row("wbom_candidates", "candidate_id", candidate["candidate_id"],
               update_fields)

    # Schedule 24h follow-up reminder
    _create_reminder(
        candidate["candidate_id"],
        datetime.now(timezone.utc) + timedelta(hours=24),
        "follow_up",
    )

    _log_conversation(candidate["candidate_id"], "complete",
                      INTAKE_COMPLETE_MSG, direction="outbound")
    return {
        "reply": INTAKE_COMPLETE_MSG,
        "action": "scored",
        "candidate_id": candidate["candidate_id"],
    }


# ── Score candidate (manual re-score) ───────────────────────────────────────

def score_candidate(candidate_id: int) -> dict:
    """Recompute and persist score for a candidate."""
    candidate = _get_candidate_by_id(candidate_id)
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} not found")

    score, bucket = compute_score(candidate)
    update_row("wbom_candidates", "candidate_id", candidate_id, {
        "score": score,
        "score_bucket": bucket,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"candidate_id": candidate_id, "score": score, "score_bucket": bucket}


# ── Assign recruiter ─────────────────────────────────────────────────────────

def assign_recruiter(candidate_id: int, recruiter_name: str) -> dict:
    """Assign a recruiter and advance stage to 'assigned'."""
    candidate = _get_candidate_by_id(candidate_id)
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} not found")

    now = datetime.now(timezone.utc)
    update_row("wbom_candidates", "candidate_id", candidate_id, {
        "assigned_recruiter": recruiter_name.strip()[:80],
        "assigned_at": now.isoformat(),
        "funnel_stage": "assigned",
        "updated_at": now.isoformat(),
    })

    # 48-hour no-response reminder
    _create_reminder(candidate_id, now + timedelta(hours=48), "no_response_48h")

    return {
        "candidate_id": candidate_id,
        "assigned_recruiter": recruiter_name,
        "funnel_stage": "assigned",
    }


# ── Advance funnel stage ─────────────────────────────────────────────────────

STAGE_ORDER = [
    "new", "collecting", "scored", "assigned",
    "contacted", "interviewed", "hired",
]
TERMINAL_STAGES = {"hired", "rejected", "dropped"}

VALID_TRANSITIONS: dict[str, list[str]] = {
    "scored":      ["assigned"],
    "assigned":    ["contacted", "rejected", "dropped"],
    "contacted":   ["interviewed", "rejected", "dropped"],
    "interviewed": ["hired", "rejected", "dropped"],
}


def advance_stage(candidate_id: int, to_stage: str) -> dict:
    """Manually advance candidate's funnel stage."""
    candidate = _get_candidate_by_id(candidate_id)
    if not candidate:
        raise ValueError(f"Candidate {candidate_id} not found")

    from_stage = candidate["funnel_stage"]
    allowed = VALID_TRANSITIONS.get(from_stage, [])
    if to_stage not in allowed:
        raise ValueError(
            f"Cannot advance from '{from_stage}' to '{to_stage}'. "
            f"Allowed: {allowed}"
        )

    now = datetime.now(timezone.utc)
    update_fields: dict = {
        "funnel_stage": to_stage,
        "updated_at": now.isoformat(),
    }
    if to_stage == "contacted":
        update_fields["last_contact_at"] = now.isoformat()

    update_row("wbom_candidates", "candidate_id", candidate_id, update_fields)
    return {
        "candidate_id": candidate_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
    }


# ── Owner metrics ────────────────────────────────────────────────────────────

def get_recruitment_metrics(ref_date: Optional[date] = None) -> dict:
    """
    Returns owner KPI aggregates:
      new_leads_today, total_candidates, conversion_rate,
      recruiter_performance, no_response_leads, funnel_breakdown
    """
    if ref_date is None:
        ref_date = date.today()

    day_start = datetime.combine(ref_date, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    month_start = day_start.replace(day=1)

    # New leads today
    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM wbom_candidates WHERE created_at >= %s",
        (day_start.isoformat(),),
    )
    new_leads_today = int(rows[0]["cnt"]) if rows else 0

    # Total + conversion
    rows = execute_query(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN funnel_stage = 'hired'    THEN 1 ELSE 0 END) AS hired,
            SUM(CASE WHEN funnel_stage = 'rejected' THEN 1 ELSE 0 END) AS rejected
        FROM wbom_candidates
        WHERE created_at >= %s
        """,
        (month_start.isoformat(),),
    )
    r = rows[0] if rows else {}
    total = int(r.get("total") or 0)
    hired = int(r.get("hired") or 0)
    rejected = int(r.get("rejected") or 0)
    eligible = total - rejected
    conversion_rate = round(hired / eligible * 100, 1) if eligible > 0 else 0.0

    # Funnel breakdown
    rows = execute_query(
        """
        SELECT funnel_stage, COUNT(*) AS cnt
        FROM wbom_candidates
        GROUP BY funnel_stage
        ORDER BY funnel_stage
        """,
        (),
    )
    funnel_breakdown = {row["funnel_stage"]: int(row["cnt"]) for row in rows}

    # Recruiter performance
    rows = execute_query(
        """
        SELECT
            assigned_recruiter,
            COUNT(*) AS assigned_count,
            SUM(CASE WHEN funnel_stage = 'hired' THEN 1 ELSE 0 END) AS hired_count
        FROM wbom_candidates
        WHERE assigned_recruiter IS NOT NULL
        GROUP BY assigned_recruiter
        ORDER BY assigned_count DESC
        """,
        (),
    )
    recruiter_performance = []
    for row in rows:
        a = int(row["assigned_count"])
        h = int(row["hired_count"] or 0)
        recruiter_performance.append({
            "recruiter": row["assigned_recruiter"],
            "assigned_count": a,
            "hired_count": h,
            "conversion_pct": round(h / a * 100, 1) if a > 0 else 0.0,
        })

    # No-response leads (assigned but no contact for 48h+)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    rows = execute_query(
        """
        SELECT candidate_id, full_name, phone, assigned_recruiter, assigned_at
        FROM wbom_candidates
        WHERE funnel_stage = 'assigned'
          AND (last_contact_at IS NULL OR last_contact_at < %s)
        ORDER BY assigned_at
        """,
        (cutoff,),
    )
    no_response_leads = [
        {
            "candidate_id": row["candidate_id"],
            "full_name": row["full_name"],
            "phone": row["phone"],
            "assigned_recruiter": row["assigned_recruiter"],
            "assigned_at": str(row["assigned_at"]) if row["assigned_at"] else None,
        }
        for row in rows
    ]

    return {
        "ref_date": str(ref_date),
        "new_leads_today": new_leads_today,
        "total_this_month": total,
        "hired_this_month": hired,
        "conversion_rate": conversion_rate,
        "funnel_breakdown": funnel_breakdown,
        "recruiter_performance": recruiter_performance,
        "no_response_leads": no_response_leads,
    }


# ── Pending reminders ─────────────────────────────────────────────────────────

def get_pending_reminders(limit: int = 50) -> list[dict]:
    """Return reminders that are due now (for a polling/cron endpoint)."""
    now = datetime.now(timezone.utc).isoformat()
    rows = execute_query(
        """
        SELECT r.reminder_id, r.candidate_id, r.due_at, r.reason,
               c.full_name, c.phone, c.assigned_recruiter
        FROM wbom_recruitment_reminders r
        JOIN wbom_candidates c ON c.candidate_id = r.candidate_id
        WHERE r.status = 'pending' AND r.due_at <= %s
        ORDER BY r.due_at
        LIMIT %s
        """,
        (now, limit),
    )
    return [dict(row) for row in rows]
