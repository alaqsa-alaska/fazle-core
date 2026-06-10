"""
Fazle Core — Admin NL: outbound preview / send-with-confirm (Phase 1.2 / v1.1.0)

Per master prompt: admin says "send 017xxx about salary" → system creates a
DRAFT in `fazle_draft_replies` (status='pending'). NEVER sends immediately.
Admin then approves with the EXISTING APPROVE/REJECT/EDIT/CANCEL commands.

Public:
    intent_outbound_preview(text, admin_phone) -> reply str
    is_outbound_preview(text) -> bool

Triggers (phone is REQUIRED):
    send 017xxxxxxxx about salary tomorrow 10am
    send <phone>: <freeform message>
    01XXXXXXXXX কে বলো অফিসে আসুন
    msg 01XXXXXXXXX joining details

Topic-templated message generation (no LLM):
    salary, advance, joining, interview, payment, office, escort, generic
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.database import execute, fetch_val
from shared.draft_reply import create_draft_reply
from .nl_router import extract_phone

log = logging.getLogger("fazle.admin_nl_send")

# ── Trigger detection ────────────────────────────────────────────────────────
_SEND_TRIGGER_RE = re.compile(
    r"^\s*(?:send|msg|message|বলো|বলুন|পাঠাও|পাঠান|বার্তা)\b",
    re.IGNORECASE,
)
# Bangla pattern: "<phone> কে বলো ..."
_BANGLA_KE_BOLO_RE = re.compile(r"কে\s*(?:বলো|বলুন|পাঠাও|পাঠান)", re.IGNORECASE)


def is_outbound_preview(text: str) -> bool:
    if extract_phone(text) is None:
        return False
    return bool(_SEND_TRIGGER_RE.match(text) or _BANGLA_KE_BOLO_RE.search(text))


# ── Topic templates (Bangla, polite, business-safe) ──────────────────────────
_TOPIC_KEYWORDS = {
    "salary": (
        r"\b(salary|বেতন|মাইনে|মজুরি)\b",
        "আস-সালামু আলাইকুম।\n\n"
        "আপনার বেতন সংক্রান্ত তথ্য জানাতে যোগাযোগ করছি। "
        "অনুগ্রহ করে অফিসে আসুন অথবা ফোনে যোগাযোগ করুন।\n\n"
        "— আল-আকসা সিকিউরিটি",
    ),
    "advance": (
        r"\b(advance|অগ্রিম|আগাম)\b",
        "আস-সালামু আলাইকুম।\n\n"
        "আপনার অগ্রিম পেমেন্টের আবেদন প্রক্রিয়াধীন। "
        "চূড়ান্ত হলে আপনাকে জানানো হবে।\n\n"
        "— আল-আকসা সিকিউরিটি",
    ),
    "joining": (
        r"\b(joining|join|যোগদান|জয়েনিং|চাকরি)\b",
        "আস-সালামু আলাইকুম।\n\n"
        "চাকরিতে যোগদানের বিষয়ে আপনার সাথে কথা বলতে চাই। "
        "অনুগ্রহ করে অফিসে আসুন বা ফোন করুন।\n\n"
        "— আল-আকসা সিকিউরিটি",
    ),
    "interview": (
        r"\b(interview|ইন্টারভিউ|সাক্ষাৎকার)\b",
        "আস-সালামু আলাইকুম।\n\n"
        "ইন্টারভিউয়ের জন্য আপনাকে আমন্ত্রণ জানাচ্ছি। "
        "সময় ও স্থান নিশ্চিত করতে যোগাযোগ করুন।\n\n"
        "— আল-আকসা সিকিউরিটি",
    ),
    "payment": (
        r"\b(payment|পেমেন্ট|পরিশোধ)\b",
        "আস-সালামু আলাইকুম।\n\n"
        "আপনার পেমেন্ট সংক্রান্ত আপডেট রয়েছে। "
        "বিস্তারিত জানতে যোগাযোগ করুন।\n\n"
        "— আল-আকসা সিকিউরিটি",
    ),
    "office": (
        r"\b(office|অফিস|ঠিকানা)\b",
        "আস-সালামু আলাইকুম।\n\n"
        "আমাদের অফিসে আসার সময় ও ঠিকানার জন্য যোগাযোগ করুন। "
        "আমরা সাহায্য করতে প্রস্তুত।\n\n"
        "— আল-আকসা সিকিউরিটি",
    ),
    "escort": (
        r"\b(escort|duty|ডিউটি|এসকর্ট|vessel)\b",
        "আস-সালামু আলাইকুম।\n\n"
        "এসকর্ট ডিউটি সংক্রান্ত বিষয়ে যোগাযোগ করছি। "
        "অনুগ্রহ করে দ্রুত সাড়া দিন।\n\n"
        "— আল-আকসা সিকিউরিটি",
    ),
}

_GENERIC_TEMPLATE = (
    "আস-সালামু আলাইকুম।\n\n"
    "{topic_line}\n\n"
    "অনুগ্রহ করে আমাদের সাথে যোগাযোগ করুন।\n\n"
    "— আল-আকসা সিকিউরিটি"
)


def _strip_command_prefix(text: str, phone_canonical: str) -> str:
    """Remove send/msg verb + phone fragment + 'about', leaving the topic body."""
    t = text.strip()
    t = re.sub(r"^(send|msg|message|বলো|বলুন|পাঠাও|পাঠান|বার্তা)\b", "", t, flags=re.IGNORECASE).strip()
    # remove the phone (canonical OR local 11-digit)
    local = phone_canonical[2:] if phone_canonical.startswith("88") else phone_canonical
    for v in (phone_canonical, "+" + phone_canonical, local):
        t = t.replace(v, "")
    t = re.sub(r"\b(about|regarding|বিষয়ে|সম্পর্কে|কে)\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip(" :,-।")
    return t


def _generate_message(text: str, phone_canonical: str) -> tuple[str, str]:
    """Return (topic_label, generated_message)."""
    body_hint = _strip_command_prefix(text, phone_canonical)
    for label, (rx, tmpl) in _TOPIC_KEYWORDS.items():
        if re.search(rx, text, re.IGNORECASE):
            return label, tmpl
    if body_hint:
        return "generic", _GENERIC_TEMPLATE.format(topic_line=body_hint.capitalize())
    return "generic", _GENERIC_TEMPLATE.format(
        topic_line="আপনার সাথে যোগাযোগের প্রয়োজন।"
    )


# ── Handler ──────────────────────────────────────────────────────────────────
async def intent_outbound_preview(text: str, admin_phone: str) -> str:
    phone = extract_phone(text)
    if not phone:
        return "❌ কোন নম্বরে পাঠাব? উদাহরণ: send 01712345678 about salary"

    topic, message = _generate_message(text, phone)
    recipient = phone + "@s.whatsapp.net"

    # Decide source bridge based on which admin sent the request.
    # bridge2 (8801880446111) is the OPS number; default for admin sends.
    source = "bridge2"
    draft_id = await create_draft_reply(
        sender=recipient,
        bridge=source,
        draft_text=message,
        role="admin",
        intent="admin_send",
        context=json.dumps({
            "origin": "admin_nl",
            "topic": topic,
            "admin_phone": admin_phone,
            "draft_type": "admin_initiated",
        }),
        source_module="nl_outbound",
    )
    if not draft_id:
        log.error("[admin_nl_send] insert failed: create_draft_reply returned None")
        return "⚠️ ড্রাফট তৈরি ব্যর্থ"

    try:
        from modules import observability as _obs
        _obs.inc("admin_nl_outbound_preview_total", labels={"topic": topic})
    except Exception:
        pass

    return (
        f"📝 Preview: ID#{draft_id}  ({topic})\n\n"
        f"➡️  পাঠাবে: {phone}\n\n"
        f"────────────\n{message}\n────────────\n\n"
        f"উত্তর দিন:\n"
        f"  APPROVE {draft_id}      = এখনই পাঠাও\n"
        f"  EDIT {draft_id} <text>  = মেসেজ পাল্টাও\n"
        f"  REJECT {draft_id}       = বাতিল (CANCEL {draft_id} ও কাজ করে)"
    )
