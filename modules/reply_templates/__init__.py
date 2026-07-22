"""
Fazle Core — Reply Template Library (Stage 3)

Provides multiple safe, pre-approved Bengali templates per intent.
Templates rotate per sender so consecutive replies don't feel robotic.

Public API:
    get_template(intent, sender, frustrated=False)  → str | None
    get_emergency_ack()                             → str
    get_vendor_reply()                              → str
    get_incident_ack()                              → str
    get_followup_reply(sender)                      → str
"""

import hashlib
import logging

log = logging.getLogger("fazle.reply_templates")

# Rotation counter per sender (in-process, resets on restart)
_COUNTERS: dict[str, int] = {}


def _rotate(sender: str, count: int) -> int:
    """Return next index (0..count-1) for this sender, incrementing the counter."""
    n = _COUNTERS.get(sender, 0)
    _COUNTERS[sender] = (n + 1) % count
    return n % count


# ── Templates ──────────────────────────────────────────────────────────────────

_RECRUITMENT_NORMAL = [
    (
        "আস্সালামুয়ালাইকুম ভাই,\n\n"
        "আপনি চাকরির জন্য যোগাযোগ করেছেন মনে হচ্ছে।\n"
        "আপনার নাম, লোকেশন আর অভিজ্ঞতা একটু জানাবেন?"
    ),
    (
        "আস্সালামুয়ালাইকুম ভাই,\n\n"
        "চাকরির বিষয়ে জানাতে — নাম, এলাকা আর কী ধরনের কাজ করতে চান সেটা লিখুন।\n"
        "অফিস থেকে যোগাযোগ করা হবে।"
    ),
    (
        "আস্সালামুয়ালাইকুম ভাই,\n\n"
        "নিয়োগের জন্য আপনার নাম ও লোকেশন পাঠান।\n"
        "অফিস: ভিক্টোরিয়া গেইট, পাহাড়তলী, চট্টগ্রাম — সকাল ৯টা থেকে বিকাল ৫টা।"
    ),
]

_RECRUITMENT_FRUSTRATED = [
    (
        "আপনার ধৈর্যের জন্য ধন্যবাদ ভাই।\n"
        "অফিসে সরাসরি আসুন — দ্রুত সমাধান হবে।\n"
        "পাহাড়তলী, চট্টগ্রাম।"
    ),
]

_GREETING_NORMAL = [
    (
        "ওয়ালাইকুম আস্সালাম,\n\n"
        "আমি Fazle, The Assistant। আপনাকে Al-Rifa'i Integrated Services-এ স্বাগত।\n"
        "কী জানতে চান বলুন — আপনাকে কীভাবে সাহায্য করতে পারি?"
    ),
    (
        "আস্সালামুয়ালাইকুম ভাই,\n\n"
        "আমি Fazle, The Assistant। কীভাবে সাহায্য করতে পারি?"
    ),
]

_SALARY_NORMAL = [
    (
        "ভাই,\n\n"
        "বেতনের তথ্য জানতে আপনার কর্মী আইডি নম্বর পাঠান।\n"
        "যত দ্রুত সম্ভব জানানো হবে।"
    ),
    (
        "ভাই,\n\n"
        "আপনার আইডি নম্বর দিলে বেতনের আপডেট দিতে পারব।"
    ),
]

_COMPLAINT_NORMAL = [
    (
        "ভাই,\n\n"
        "আপনার অভিযোগ পেয়েছি। বিস্তারিত লিখুন — অফিস থেকে দ্রুত সমাধান করা হবে।"
    ),
    (
        "ভাই,\n\n"
        "আপনার বার্তা রেকর্ড করা হয়েছে। কর্তৃপক্ষ শীঘ্রই যোগাযোগ করবে।"
    ),
]

_LEAVE_NORMAL = [
    (
        "ভাই,\n\n"
        "ছুটির আবেদন পাওয়া গেছে। সুপারভাইজার অনুমোদনের পর জানানো হবে।"
    ),
    (
        "ভাই,\n\n"
        "ছুটির অনুরোধ রেকর্ড করা হয়েছে। দ্রুত অনুমোদন দেওয়া হবে।"
    ),
]

_JOIN_NORMAL = [
    (
        "ধন্যবাদ ভাই,\n\n"
        "আপনার তথ্য রেকর্ড করা হয়েছে।\n"
        "জাহাজে চাকরির জন্য প্রয়োজনীয় কাগজপত্র ও ব্যক্তিগত জিনিসপত্র নিয়ে সরাসরি অফিসে চলে আসুন। আসার দিন বা পরের দিন জয়েন করতে পারবেন।\n"
        "মার্কেটিং, সুপারভাইজার বা অপারেশন পদের জন্য সকাল ১০টা থেকে দুপুর ২টার মধ্যে আসুন।"
    ),
]

# ── Special single-template replies ────────────────────────────────────────────

EMERGENCY_ACK = (
    "জরুরি বার্তা পেয়েছি।\n\n"
    "দয়া করে সরাসরি যোগাযোগ করুন:\n"
    "01958-122300"
)

INCIDENT_ACK = (
    "ভাই,\n\n"
    "ঘটনার বিবরণ পেয়েছি। অফিস কর্তৃপক্ষকে এখনই জানানো হচ্ছে।\n"
    "বিস্তারিত তথ্য থাকলে পাঠান।"
)

VENDOR_REPLY = (
    "ভাই,\n\n"
    "আপনার প্রস্তাব পেয়েছি। ক্রয়ের বিষয়ে সিদ্ধান্ত অফিস থেকে নেওয়া হয়।\n"
    "আপনার proposal ও যোগাযোগ নম্বর পাঠান — দেখা হবে।"
)

FOLLOW_UP_REPLY = (
    "ভাই, কী অবস্থা?\n\n"
    "আপনার আগের বার্তা দেখা হয়েছে। কোনো সাহায্য লাগলে জানাবেন।"
)

FOLLOW_UP_FRUSTRATED = (
    "ভাই,\n\n"
    "উত্তর পেতে দেরি হচ্ছে — দুঃখিত। বার্তাটি অফিসে পাঠানো হয়েছে।\n"
    "দ্রুত সমাধান করা হবে।"
)

# ── Map: intent → (normal_list, frustrated_list | None) ──────────────────────
_TEMPLATE_MAP: dict[str, tuple[list, list | None]] = {
    "recruitment":    (_RECRUITMENT_NORMAL,  _RECRUITMENT_FRUSTRATED),
    "greeting":       (_GREETING_NORMAL,     None),
    "salary_query":   (_SALARY_NORMAL,       None),
    "complaint":      (_COMPLAINT_NORMAL,    None),
    "leave":          (_LEAVE_NORMAL,        None),
    "join":           (_JOIN_NORMAL,         None),
}


def get_template(intent: str, sender: str, frustrated: bool = False) -> str | None:
    """
    Return a rotating template for the given intent.
    Returns None if no template is defined (caller falls through to KB/other handler).
    """
    entry = _TEMPLATE_MAP.get(intent)
    if not entry:
        return None
    normal_list, frustrated_list = entry
    if frustrated and frustrated_list:
        idx = _rotate(f"{sender}:{intent}:f", len(frustrated_list))
        return frustrated_list[idx]
    idx = _rotate(f"{sender}:{intent}", len(normal_list))
    return normal_list[idx]


def get_emergency_ack() -> str:
    return EMERGENCY_ACK


def get_incident_ack() -> str:
    return INCIDENT_ACK


def get_vendor_reply() -> str:
    return VENDOR_REPLY


def get_followup_reply(frustrated: bool = False) -> str:
    return FOLLOW_UP_FRUSTRATED if frustrated else FOLLOW_UP_REPLY
