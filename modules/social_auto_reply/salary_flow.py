"""Step-by-step employee salary / payment complaint conversation flow.

State is persisted in social_thread_state.context_summary (JSONB).
No writes to wbom_* tables. All DB access is read-only.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.database import execute

log = logging.getLogger("fazle.social.salary_flow")

# Owner-confirmed 2026-08-04 — see reply_rules.OFFICE_NUMBERS's comment for
# the full C-08-conflict context this corrects (was 01958-122311/122327,
# neither of which matched the Owner's confirmed list).
_OFFICE_CONTACT = "01958-122300\n01958-122301\n01958-122302\n01958-122303"

VOICE_REPLY = (
    "ভয়েস নোট প্রসেস করা যাচ্ছে না। "
    "অনুগ্রহ করে লিখিতভাবে তথ্য পাঠান।"
)

COMMENT_REDIRECT = (
    "বেতন বা payment সংক্রান্ত বিষয় public comment-এ আলোচনা করা সম্ভব নয়। "
    "অনুগ্রহ করে inbox-এ বা WhatsApp-এ যোগাযোগ করুন।"
)

_STEP_REPLIES: dict[str, list[str]] = {
    "name": [
        "আপনার পুরো নাম লিখুন।",
        "দয়া করে আপনার সম্পূর্ণ নাম জানান।",
    ],
    "mobile": [
        "আপনার মোবাইল নম্বর লিখুন।",
        "আপনার যোগাযোগের মোবাইল নম্বরটি লিখুন।",
    ],
    "joining_date": [
        "আপনি কবে join করেছেন? তারিখ বা মাস লিখুন।",
        "আপনার যোগদানের তারিখ বা আনুমানিক সময় লিখুন।",
    ],
    "duty_period": [
        "এ পর্যন্ত আপনি মোট কতদিন duty করেছেন লিখুন।",
        "আপনার মোট duty-র সময়কাল বা দিনের সংখ্যা লিখুন।",
    ],
    "duty_detail_ship": [
        "আপনি এ পর্যন্ত কয়টি program বা জাহাজ duty সম্পন্ন করেছেন লিখুন।",
        "মোট কয়টি escort program বা জাহাজ complete করেছেন লিখুন।",
    ],
    "duty_detail_security": [
        "আপনি কোথায় duty করেছেন — project বা site-এর নাম লিখুন।",
        "আপনার duty-র location বা project-এর নাম জানান।",
    ],
    "absence": [
        "আপনি বর্তমানে duty-তে আছেন নাকি অনুপস্থিত? অনুপস্থিত হলে কতদিন লিখুন।",
        "এখন কি duty চলছে? নাকি কোনো কারণে কাজে আসছেন না? জানান।",
    ],
    "resignation": [
        "আপনি কি লিখিতভাবে resignation বা অব্যাহতির আবেদন করেছেন?",
        "চাকরি ছেড়ে দেওয়ার জন্য কোনো আবেদন কি অফিসে দিয়েছেন?",
    ],
}

_ABUSE_KEYWORDS = ("শালা", "চুদ", "হারাম", "মারবো", "মেরে ফেল", "abuse", "ধমকি")


def is_abusive(text: str) -> bool:
    lower = text.lower()
    return any(k in lower for k in _ABUSE_KEYWORDS)


def calm_reply() -> str:
    return (
        "আপনার payment সংক্রান্ত বিষয়টি যাচাই করে সঠিক তথ্য জানানো হবে। "
        "অফিসের নথি, উপস্থিতির রেকর্ড ও নিয়ম অনুযায়ী হিসাব নির্ধারিত হয়। "
        "অনুগ্রহ করে তথ্য দিলে দ্রুত যাচাই করা সম্ভব।"
    )


# ── State helpers ──────────────────────────────────────────────────────────────

def get_collected(state: dict | None) -> dict[str, Any]:
    """Extract salary_data from thread state context_summary."""
    if not state:
        return {}
    ctx = state.get("context_summary") or {}
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except Exception:
            return {}
    return dict(ctx.get("salary_data") or {})


def is_salary_flow_active(state: dict | None) -> bool:
    collected = get_collected(state)
    if collected:
        return True
    answered = (state or {}).get("answered_intents") or []
    return "employee_salary_complaint" in answered


# ── Step determination ─────────────────────────────────────────────────────────

def determine_next_step(collected: dict[str, Any], platform: str) -> str:
    if not collected.get("name"):
        return "name"
    # Messenger has no phone identity — must collect
    if platform == "messenger" and not collected.get("mobile"):
        return "mobile"
    if not collected.get("joining_date"):
        return "joining_date"
    if not collected.get("duty_period"):
        return "duty_period"
    if not collected.get("duty_detail"):
        role = collected.get("role_type", "ship")
        return "duty_detail_ship" if role != "security" else "duty_detail_security"
    if not collected.get("absence_asked"):
        return "absence"
    if not collected.get("resignation_asked"):
        return "resignation"
    return "estimate"


def step_reply(step: str, variation: int = 0) -> str:
    options = _STEP_REPLIES.get(step, [])
    if not options:
        return ""
    return options[variation % len(options)]


# ── Text extractors ────────────────────────────────────────────────────────────

def _extract_name(text: str) -> str | None:
    text = text.strip()
    if 2 <= len(text.split()) <= 4 and re.match(r"^[ঀ-৿a-zA-Z \.]+$", text):
        return text
    for prefix in ("নাম:", "name:", "আমার নাম", "নাম হলো", "নাম হল", "আমি"):
        idx = text.lower().find(prefix.lower())
        if idx >= 0:
            rest = text[idx + len(prefix):].strip()
            words = rest.split()[:3]
            if words:
                return " ".join(words)
    return None


def _extract_mobile(text: str) -> str | None:
    m = re.search(r"(?:01[3-9]\d{8}|8801[3-9]\d{8}|\+8801[3-9]\d{8})", re.sub(r"[\s\-]", "", text))
    return m.group() if m else None


def _extract_date_hint(text: str) -> str | None:
    patterns = [
        r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}",
        r"\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}",
        r"(?:জানুয়ারি|ফেব্রুয়ারি|মার্চ|এপ্রিল|মে|জুন|জুলাই|আগস্ট|সেপ্টেম্বর|অক্টোবর|নভেম্বর|ডিসেম্বর|"
        r"january|february|march|april|may|june|july|august|september|october|november|december)"
        r"[\s,]*\d{0,4}",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group().strip()
    return text.strip()[:60] if text.strip() else None


def _extract_number(text: str) -> int | None:
    m = re.search(r"\b(\d+)\b", text)
    return int(m.group(1)) if m else None


def update_collected(text: str, current_step: str, collected: dict[str, Any]) -> dict[str, Any]:
    """Try to extract data for the current step from the user's reply. Returns updated copy."""
    updated = dict(collected)
    if current_step == "name":
        name = _extract_name(text)
        if name:
            updated["name"] = name
    elif current_step == "mobile":
        mobile = _extract_mobile(text)
        if mobile:
            updated["mobile"] = mobile
    elif current_step == "joining_date":
        hint = _extract_date_hint(text)
        if hint:
            updated["joining_date"] = hint
    elif current_step == "duty_period":
        updated["duty_period"] = text.strip()[:80]
        lower = text.lower()
        if any(x in lower for x in ("জাহাজ", "ship", "program", "escort", "vessel", "lighter", "marine")):
            updated["role_type"] = "ship"
        elif any(x in lower for x in ("security", "guard", "project", "site", "এলাকা", "location")):
            updated["role_type"] = "security"
    elif current_step in ("duty_detail_ship", "duty_detail_security"):
        updated["duty_detail"] = text.strip()[:100]
        n = _extract_number(text)
        if n is not None:
            updated["duty_count"] = n
    elif current_step == "absence":
        updated["absence"] = text.strip()[:120]
        updated["absence_asked"] = True
    elif current_step == "resignation":
        updated["resignation"] = text.strip()[:120]
        updated["resignation_asked"] = True
    return updated


# ── Estimate builder ───────────────────────────────────────────────────────────

def build_estimate(
    collected: dict[str, Any],
    db_employee: dict | None,
    db_total_paid: float,
    db_programs: int,
) -> str:
    lines: list[str] = []
    lines.append("আপনার দেওয়া তথ্য ও প্রাথমিক হিসাব অনুযায়ী আপাতত নিচের বিষয়গুলো উল্লেখযোগ্য:\n")

    name = collected.get("name") or (db_employee or {}).get("employee_name") or "কর্মচারী"
    lines.append(f"নাম: {name}")

    if db_employee:
        lines.append(f"পদ: {db_employee.get('designation') or 'অজ্ঞাত'}")
        if db_employee.get("basic_salary"):
            lines.append(f"মূল বেতন (রেকর্ড): ৳{int(db_employee['basic_salary']):,}")
        if db_employee.get("joining_date"):
            lines.append(f"যোগদানের তারিখ (রেকর্ড): {db_employee['joining_date']}")
    elif collected.get("joining_date"):
        lines.append(f"যোগদান (আপনার তথ্য): {collected['joining_date']}")

    duty_count = collected.get("duty_count") or db_programs
    if duty_count:
        lines.append(f"সম্পন্ন duty / program: {duty_count}টি")

    if db_total_paid > 0:
        lines.append(f"এ পর্যন্ত প্রদত্ত (রেকর্ড): ৳{int(db_total_paid):,}")

    absence = collected.get("absence") or ""
    if absence and any(x in absence.lower() for x in ("অনুপস্থিত", "absent", "নেই", "দিন", "আসিনি")):
        lines.append(
            "\nআপনার উপস্থিতি / অনুপস্থিতি তথ্য অনুযায়ী payroll eligibility verification প্রয়োজন। "
            "অফিস রেকর্ড যাচাই করে চূড়ান্ত সিদ্ধান্ত নেওয়া হবে।"
        )

    resignation = collected.get("resignation") or ""
    if resignation and any(x in resignation.lower() for x in ("হ্যাঁ", "yes", "দিয়েছি", "করেছি", "দিয়েছেন")):
        lines.append(
            "\nresignation সংক্রান্ত: ৩০ দিনের notice period নিয়ম প্রযোজ্য হতে পারে। "
            "লিখিত আবেদনের তারিখ অনুযায়ী চূড়ান্ত হিসাব নির্ধারিত হবে।"
        )

    lines.append(
        "\nচূড়ান্ত হিসাব অফিস যাচাইয়ের পরে নিশ্চিত হবে। "
        f"যোগাযোগ করুন:\n{_OFFICE_CONTACT}"
    )
    return "\n".join(lines)


# ── State persistence ──────────────────────────────────────────────────────────

async def save_collected(platform: str, target_id: str, collected: dict[str, Any]) -> None:
    """Persist salary_data into social_thread_state.context_summary. No wbom_* writes."""
    await execute(
        """
        INSERT INTO social_thread_state
            (platform, target_id, answered_intents, last_reply_text, context_summary, updated_at)
        VALUES ($1, $2, ARRAY['employee_salary_complaint']::text[], '', $3::jsonb, NOW())
        ON CONFLICT (platform, target_id) DO UPDATE SET
            answered_intents = ARRAY(
                SELECT DISTINCT unnest(
                    social_thread_state.answered_intents ||
                    ARRAY['employee_salary_complaint']::text[]
                )
            ),
            context_summary = social_thread_state.context_summary || $3::jsonb,
            updated_at = NOW()
        """,
        platform,
        target_id,
        json.dumps({"salary_data": collected}),
    )
