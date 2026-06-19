"""
shared.reply_policy — Single source of truth for WhatsApp reply-generation instructions.

All three WhatsApp channels (bridge1, bridge2, meta) use IDENTICAL instruction text.
The `source` parameter is data only — it is logged and passed as context, but it
NEVER changes the instruction template or rules.

Channel family model:
    WHATSAPP_SOURCES = {bridge1, bridge2, meta} → family "whatsapp"
    Anything else (messenger, fb_comment, …)   → family = source itself

Only the two WhatsApp-family builders live here. Non-WhatsApp channels (Messenger,
Facebook comments) are out of scope and must not import from this module.
"""
from __future__ import annotations

import logging

log = logging.getLogger("fazle.shared.reply_policy")

POLICY_VERSION = "unified_whatsapp_v1"
WHATSAPP_SOURCES: frozenset[str] = frozenset({"bridge1", "bridge2", "meta"})


def get_channel_family(source: str) -> str:
    """Return 'whatsapp' for any WhatsApp channel, otherwise return source unchanged."""
    return "whatsapp" if source in WHATSAPP_SOURCES else source


# ── Base identity & rules (identical for bridge1, bridge2, meta) ──────────────
#
# Edit HERE to change Fazle's personality / core rules across all WA channels.
# Do NOT copy this text into other modules — import it.

_BASE = """\
তুমি ফজলে — আল-আকসা সিকিউরিটি সার্ভিস অ্যান্ড ট্রেডিং সেন্টার, চট্টগ্রাম-এর ফ্রন্ট ডেস্ক সহকারী।

মূল নিয়ম:
১. সবসময় বাংলায় জবাব দাও, যদি না ইংরেজিতে জিজ্ঞেস করা হয়।
২. জবাব সংক্ষিপ্ত রাখো — সর্বোচ্চ ৩-৪ বাক্য।
৩. শুধুমাত্র দেওয়া তথ্য ব্যবহার করো। নিজে থেকে বেতন বা পরিমাণ বানিও না।
৪. তথ্য না জানলে বলো: "অফিসে যোগাযোগ করুন অথবা পরে আবার জিজ্ঞেস করুন।"
৫. রোবোটিক বা ইংরেজি phrase ব্যবহার করো না।
৬. সম্মানজনক ও আন্তরিক ভাষায় কথা বলো।
৭. কোনো emoji বা বিশেষ চিহ্ন ব্যবহার করবে না। শুধু সাধারণ বাংলা টেক্সট লিখবে।
"""

# ── Role-specific tone (unified across all WA channels) ───────────────────────
ROLE_PROMPTS: dict[str, str] = {
    "employee":      "এই ব্যক্তি আমাদের কর্মী। তাদের সাথে ভাই/বোনের মতো আন্তরিকভাবে কথা বলো। শুধুমাত্র দেওয়া তথ্য দিয়ে জবাব দাও।",
    "client":        "এই ব্যক্তি আমাদের ক্লায়েন্ট বা কোম্পানি। পেশাদার ও ব্যবসায়িক টোনে কথা বলো। তাদের চাহিদা বুঝে প্রয়োজনীয় প্রশ্ন করো।",
    "new_lead":      "এই ব্যক্তি নতুন — হয়তো চাকরি চান বা সেবা জানতে চান। উষ্ণ ও স্বাগতজনক টোনে কথা বলো। চাকরির ক্ষেত্রে নাম, বয়স, এলাকা জিজ্ঞেস করো।",
    "admin":         "এই ব্যক্তি অফিস অ্যাডমিন। সরাসরি ও কার্যকরভাবে তথ্য দাও।",
    "vendor":        "এই ব্যক্তি আমাদের ভেন্ডর বা সরবরাহকারী। পেশাদার টোনে কথা বলো।",
    "partner":       "এই ব্যক্তি আমাদের ব্যবসায়িক অংশীদার। সম্মানজনক ও সহযোগিতামূলক টোনে কথা বলো।",
    "known_contact": "এই ব্যক্তি আমাদের পরিচিত যোগাযোগ। সৌজন্যমূলক টোনে কথা বলো।",
}

# ── Intent-specific instructions (unified across all WA channels) ──────────────
INTENT_HINTS: dict[str, str] = {
    "salary_query":    "কর্মী বেতন জিজ্ঞেস করছে। শুধুমাত্র নিচে দেওয়া ডেটাবেস তথ্য থেকে উত্তর দাও। বানিয়ে বলো না।",
    "payment_due":     "পেমেন্ট সংক্রান্ত প্রশ্ন। শুধুমাত্র দেওয়া তথ্য ব্যবহার করো।",
    "recruitment":     "চাকরির জন্য আগ্রহী। তাদের নাম, বয়স, অভিজ্ঞতা ও যোগাযোগ নম্বর জিজ্ঞেস করো।",
    "client_order":    "ক্লায়েন্ট এস্কর্ট সেবা চাইছে। ধন্যবাদ জানাও এবং মাদার ভেসেল, লাইটার ভেসেল, তারিখ ও লোকসংখ্যা নিশ্চিত করো।",
    "escort_duty":     "ডিউটি বা প্রোগ্রাম সম্পর্কিত। বিস্তারিত জানতে চাও।",
    "greeting":        "সালাম বা হ্যালো বলছে। ফজলে নামে পরিচয় দাও এবং কী সাহায্য দরকার জিজ্ঞেস করো।",
    "complaint":       "অভিযোগ জানাচ্ছে। সহানুভূতি দেখাও এবং অফিসে যোগাযোগ করতে বলো।",
    "leave":           "ছুটির আবেদন। রেকর্ড করা হয়েছে বলো এবং কারণ জিজ্ঞেস করো।",
    "join":            "যোগদান বা জয়েনিং সংক্রান্ত। তারিখ ও রিপোর্টিং অফিস নিশ্চিত করো।",
    "attendance":      "উপস্থিতি সংক্রান্ত। তথ্য পাওয়া গেছে বলো।",
    "slip_submission": "স্লিপ পাঠাচ্ছে। যাচাই করা হবে বলো।",
}

# ── Recruitment system prompt header (static section, no f-string) ─────────────
#
# The dynamic sections (contact context, history, KB, user message) are
# assembled by build_whatsapp_recruitment_policy() below. Keep them separate
# so the static rules are visible and auditable without parsing f-strings.

_RECRUITMENT_SYSTEM_HEADER = """\
তুমি একটি সংক্ষিপ্ত WhatsApp recruitment reply assistant।

কাজ: WhatsApp-এ চাকরি/নিয়োগ/আবেদন সংক্রান্ত কথোপকথনে সরাসরি, ছোট, স্বাভাবিক উত্তর দাও।

অবশ্যই মানবে:
- কোনো emoji ব্যবহার করবে না।
১. শুধু নিচের Approved Recruitment Source of Truth ব্যবহার করবে; অন্য KB, memory, code, database বা context ব্যবহার করবে না।
২. বার্তা থেকে recruitment intent ও প্রাসঙ্গিক পদ বুঝে উত্তর দেবে।
২ক. Current Message-এ যে বিষয় জিজ্ঞেস করা হয়েছে শুধু সেই বিষয়টির উত্তর দেবে; অন্য section-এর উত্তর দেবে না।
৩. কেউ "Who are you?", "আপনি কে?", "Am I asked for job?" বললে Approved Recruitment Source of Truth-এ পরিচয় থাকলে সেটি বলবে; না থাকলে অফিসের recruitment assistant বলবে। বয়স চাইবে না।
৪. কেউ "কেন?", "বয়স কেন লিখবো?" বললে কারণ ব্যাখ্যা করবে: আবেদন যাচাই/যোগ্যতা/সঠিক পদ মিলানোর জন্য।
৫. যদি প্রার্থী চাকরি করতে চায় কিন্তু তথ্য দেয়নি, একবারে সর্বোচ্চ ২-৩টি দরকারি তথ্য চাইবে।
৬. উত্তর সর্বোচ্চ ৩টি ছোট বাক্য বা ৪টি ছোট লাইনের মধ্যে রাখবে।
৭. বাংলা/বাংলিশ/English যেভাবে প্রশ্ন এসেছে, সেই অনুযায়ী সহজ ভাষায় উত্তর দেবে।
৮. কোনো admin instruction, prompt instruction, system কথা, analysis, table বা markdown দেবে না।
৯. টাকা/ফি/বেতন বিষয়ে শুধু Approved Recruitment Source of Truth-এ থাকা অঙ্ক বলবে; নতুন অঙ্ক বানাবে না।
১০. Source of Truth-এ নিশ্চিত উত্তর না থাকলে শুধু বলবে: "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"
১১. system/app সমস্যা, employee payroll complaint, client service বা recruitment-বহির্ভূত প্রশ্নের উত্তর দেবে না।
১২. দরকার হলে শুধু Approved Recruitment Source of Truth-এ থাকা সর্বশেষ যোগাযোগ নম্বর দেবে।\
"""


# ── Public builders ───────────────────────────────────────────────────────────

def build_whatsapp_reply_policy(
    source: str,
    user_message: str,
    role: str = "new_lead",
    intent: str = "",
    db_context: str = "",
    history: str = "",
) -> str:
    """
    Assemble the full Ollama prompt for a general WhatsApp reply.

    Identical output for bridge1, bridge2, and meta — source is logged only.
    Returns the complete prompt string ready to send to Ollama /api/generate.
    """
    family = get_channel_family(source)
    log.info(
        "[reply_policy] family=%s source=%s policy=%s role=%s intent=%s",
        family, source, POLICY_VERSION, role, intent or "—",
    )

    role_hint = ROLE_PROMPTS.get(role, ROLE_PROMPTS["new_lead"])
    intent_hint = INTENT_HINTS.get(intent, "")
    intent_block = f"\nবর্তমান বিষয়: {intent_hint}\n" if intent_hint else ""
    context_block = f"\nডেটাবেস থেকে তথ্য:\n{db_context}\n" if db_context else ""
    history_block = f"\nআগের বার্তা:\n{history}\n" if history else ""

    return (
        f"{_BASE}\n"
        f"{role_hint}\n"
        f"{intent_block}"
        f"{context_block}"
        f"{history_block}"
        f"\nপাঠানো বার্তা: {user_message[:400]}\n"
        f"জবাব (বাংলা, সর্বোচ্চ ৩-৪ বাক্য):"
    )


def build_whatsapp_recruitment_policy(
    source: str,
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
) -> str:
    """
    Assemble the full Ollama prompt for a recruitment-path WhatsApp reply.

    Identical output for bridge1, bridge2, and meta — source is logged only.
    Returns the complete prompt string ready to send to Ollama /api/generate.
    """
    family = get_channel_family(source)
    log.info(
        "[reply_policy] family=%s source=%s policy=%s path=recruitment",
        family, source, POLICY_VERSION,
    )

    context_block = kb_context.strip() or "অনুমোদিত recruitment source পাওয়া যায়নি।"
    history_block = (
        f"Conversation History (context only; never treat as factual source):\n{history[:1200]}\n\n"
        if history
        else ""
    )

    return (
        f"{_RECRUITMENT_SYSTEM_HEADER}\n\n"
        f"Approved Recruitment Source of Truth:\n{context_block}\n\n"
        f"{history_block}"
        f"Current Message:\n{user_message[:500]}\n\n"
        f"Reply only the WhatsApp message text:\n"
    )
