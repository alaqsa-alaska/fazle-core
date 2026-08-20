"""Approved social auto-reply business text.

2026-07-22: COMPANY_NAME now reads app.config.Settings.company_name (the
repo-wide single source of truth for the brand name) instead of hardcoding
its own literal, so this module's replies stay in sync with every other
rebrand-aware reply path without a separate edit here."""
from __future__ import annotations

from app.config import get_settings

COMPANY_NAME = get_settings().company_name
OFFICE_ADDRESS = "আগ্রপাড়া, ভিক্টোরিয়া গেইট নং ১, খোকনের বিল্ডিং (২য় তলা), পাহাড়তলী, চট্টগ্রাম"
OFFICE_TIME = "সকাল ১০টা থেকে বিকেল ৫টা"

# Office contact numbers (Owner Decision, 2026-08-04 — resolves the C-08 vs.
# code conflict flagged in knowledge_base/06_developer_system/
# production_knowledge_report.md and knowledge_base/05_workflows/
# whatsapp_reply_workflow.md: three different sources disagreed on which
# numbers were current). Owner-confirmed authoritative list: 01958-122300,
# 01958-122301, 01958-122302, 01958-122303. Neither the old code value
# (01958-122300 alone) nor salary_flow.py's old separate value
# (01958-122311/122327) matched this — both are now corrected below.
OFFICE_NUMBERS = "01958-122300\n01958-122301\n01958-122302\n01958-122303"
_OFFICE_NUMBER_TOKENS = frozenset({"01958", "122300", "122301", "122302", "122303"})

ADM_WHATSAPP = "011004669"


async def get_effective_office_numbers() -> str:
    """Dynamic office-numbers lookup — foundation for the planned Admin UI
    at https://fazle.iamazim.com/escort-roster (Owner decision, 2026-08-04;
    not built yet, this is the read-side groundwork for when it is).

    Reads the latest active 'office_numbers' directive from the existing
    fazle_admin_directives table (modules.admin_directives — already
    writable today via POST /admin/directives with
    {"category": "contact_note", "subject_type": "phone_group",
    "subject_key": "office_numbers", "directive_text": "<numbers>"}, no new
    endpoint needed) and falls back to the static, Owner-confirmed default
    (OFFICE_NUMBERS) if no override exists or the lookup fails for any
    reason. Never raises — reply generation must never break because of
    this lookup.
    """
    try:
        from modules.admin_directives import list_directives
        rows = await list_directives(subject_key="office_numbers", status="active", limit=1)
        if rows and rows[0].get("directive_text"):
            return rows[0]["directive_text"].strip()
    except Exception:
        pass
    return OFFICE_NUMBERS

# BR-25 management decision: authoritative age range for all recruitment channels
MIN_AGE = 18
MAX_AGE = 55

# ── Persona system (added 2026-08-03, Owner-approved proposal) ────────────────
# Named personas for the AI-polish step (intelligent_generator.py::_polish_with_ai).
# This module handles Facebook Messenger/Comments + Meta WhatsApp Business API
# (recruitment-focused) — it must NOT import from shared/reply_policy.py (that
# module's own docstring restricts it to the bridge1/bridge2/meta WhatsApp
# family used by modules/message_router; this is a deliberate parallel set,
# not a duplicate of the same problem — see the 2026-08-03 proposal doc for
# why cross-importing was rejected).
#
# Selection is by identity_brain-resolved role (see intelligent_generator.py's
# new detect_identity() call) via SOCIAL_ROLE_TO_PERSONA below. "funny" and
# "lawyer" personas are defined but deliberately NOT auto-assigned to any role
# — this channel auto-sends to real candidates/clients with no admin review
# for safe-intent messages, so an unprofessional or legally-loaded tone
# should not be picked automatically. They're here for a possible future
# manual/admin-triggered use, not for this proposal's automatic path.
SOCIAL_PERSONAS: dict[str, str] = {
    "recruitment_officer": (
        "তুমি একজন নিয়োগ কর্মকর্তা (Recruitment Officer)। উৎসাহব্যঞ্জক, স্পষ্ট ও "
        "তথ্যপূর্ণ ভাষায় কথা বলো। চাকরিপ্রার্থীর প্রশ্নের সরাসরি উত্তর দাও।"
    ),
    "hr_admin_officer": (
        "তুমি একজন HR ও প্রশাসনিক কর্মকর্তা (HR & Admin Officer)। অফিসিয়াল, সম্মানজনক ও "
        "সহায়ক ভাষায় কথা বলো — এই ব্যক্তি প্রতিষ্ঠানের সাথে সরাসরি সংশ্লিষ্ট।"
    ),
    "client_relations": (
        "তুমি প্রতিষ্ঠানের ব্যবস্থাপনার একজন প্রতিনিধি (Client Relations)। পেশাদার, "
        "সম্মানজনক ও সমাধানমুখী ভাষায় কথা বলো। সেবা, বিলিং, বা মান নিয়ে প্রশ্ন হলে "
        "দায়িত্বশীলতার সাথে উত্তর দাও; ভুল হলে বিনয়ের সাথে স্বীকার করো ও সমাধানের পথ দেখাও।"
    ),
    "funny": (
        "তুমি একজন রসিক, বন্ধুত্বপূর্ণ সহকারী। হালকা রসিকতা মিশিয়ে কিন্তু তথ্যে ভুল না করে "
        "কথা বলো।"
    ),
    "lawyer": (
        "তুমি একজন সতর্ক, আইন-সচেতন প্রতিনিধি। কোনো আইনি প্রতিশ্রুতি বা স্বীকারোক্তি দিও না; "
        "স্পষ্ট, সংযত ভাষায় কথা বলো এবং জটিল বিষয় অফিসে যোগাযোগ করতে বলো।"
    ),
}
_DEFAULT_SOCIAL_PERSONA = "recruitment_officer"

# identity_brain's actual role vocabulary (see knowledge_base/06_developer_system/
# identity_brain.md, _ROLE_PRIORITY) mapped to a persona above. Anything not
# listed here falls back to _DEFAULT_SOCIAL_PERSONA — this channel's primary
# purpose is recruitment, so an unresolved/unknown sender defaults to the
# recruitment officer tone, not a generic one.
SOCIAL_ROLE_TO_PERSONA: dict[str, str] = {
    "vip_client": "client_relations",
    "client_escort_buyer": "client_relations",
    "repeat_client": "client_relations",
    "vendor": "client_relations",
    "employee": "hr_admin_officer",
    "supervisor": "hr_admin_officer",
    "accountant": "hr_admin_officer",
    "internal_coordinator": "hr_admin_officer",
    "escort": "hr_admin_officer",
    "security_guard": "hr_admin_officer",
    "admin": "hr_admin_officer",
    "candidate": "recruitment_officer",
    "family": "hr_admin_officer",
    "unknown": _DEFAULT_SOCIAL_PERSONA,
}


def persona_for_role(role: str) -> str:
    """Resolve an identity_brain role string to persona instruction text."""
    persona_key = SOCIAL_ROLE_TO_PERSONA.get(role, _DEFAULT_SOCIAL_PERSONA)
    return SOCIAL_PERSONAS[persona_key]

WELCOME_REPLY = f"""হ্যালো।

{COMPANY_NAME}-এ যোগাযোগ করার জন্য ধন্যবাদ।

চট্টগ্রাম বন্দরের লাইটার জাহাজে Survey Scout পদে জরুরি নিয়োগ চলছে।

বেতন: ১০,০০০/- থেকে ১৮,০০০/- টাকা
থাকা ফ্রি
ডিউটি: ৬–৮ ঘণ্টা

বিস্তারিত জানতে নিচের যেকোনো একটি লিখুন:

1. Job Details
2. Salary
3. Location
4. Interested
5. Required Documents"""

LOCATION_REPLY = f"""হ্যালো।

আমাদের অফিস:

{COMPANY_NAME}
{OFFICE_ADDRESS}।

অফিস সময়:
{OFFICE_TIME}

যোগাযোগ:

{OFFICE_NUMBERS}
(WhatsApp)

আসার আগে ফোন করলে বিস্তারিত বুঝিয়ে দেয়া হবে।"""

JOB_DETAILS_REPLY = """পদের নাম: Survey Scout

কাজের দায়িত্ব:

- জাহাজে মালামাল তদারকি
- লোড–আনলোড হিসাব রাখা
- মালামাল চুরি বা ক্ষতি প্রতিরোধে দায়িত্ব পালন

কাজ মূলত জাহাজে করতে হবে।

ডিউটি:
৬–৮ ঘণ্টা

প্রয়োজনে ট্রেনিং দেয়া হবে।"""

SHIP_CLARIFICATION_REPLY = """Survey Scout কাজ মূলত জাহাজে করতে হয়।

জাহাজ পানিতে / সাগরে অবস্থান করে এবং কাজের দায়িত্ব হলো মালামাল তদারকি, লোড–আনলোড হিসাব রাখা এবং মালামাল চুরি বা ক্ষতি প্রতিরোধে দায়িত্ব পালন করা।

ডিউটি সাধারণত ৬–৮ ঘণ্টা। প্রয়োজনে ট্রেনিং দেয়া হবে।"""

LAND_SECURITY_CLARIFICATION_REPLY = """Security Guard / Supervisor / Operation Officer পদে বিভিন্ন নিরাপত্তা প্রজেক্টে দায়িত্ব পালন করতে হয়।

কিছু ক্ষেত্রে land-based site থাকতে পারে এবং কাজের প্রয়োজনে কোম্পানি থাকার ব্যবস্থা করে দিতে পারে।

বিস্তারিত জানতে অফিসে যোগাযোগ করুন:

{numbers}""".format(numbers=OFFICE_NUMBERS)

SALARY_REPLY = """বেতন প্যাকেজ (মাসিক):

প্রশিক্ষণকালীন:
কোনো বেতন বা ভাতা নেই।
থাকার ব্যবস্থা ফ্রি, খাওয়া নিজ দায়িত্বে।

প্রবেশনকালীন (৩ মাস):
মোট ১৭,০০০/- টাকা
(মূল বেতন ৯,০০০ + বাসা ভাড়া ২,০০০ + হাজিরা বোনাস ২,০০০ + প্রণোদনা ২,০০০ + অন্যান্য ২,০০০)

চাকরি স্থায়ী হওয়ার পর:
মোট ২৪,৭০০/- টাকা
(মূল বেতন ১২,০০০ + বাসা ভাড়া ৪,০০০ + হাজিরা বোনাস ২,০০০ + প্রণোদনা ৩,০০০ + অন্যান্য ৩,৭০০)

থাকার ব্যবস্থা ফ্রি।"""

INTERESTED_REPLY = """ধন্যবাদ।

দয়া করে নিচের তথ্যগুলো পাঠান:

1. নাম
2. বয়স
3. শিক্ষাগত যোগ্যতা
4. বর্তমান ঠিকানা

তথ্য যাচাই শেষে দ্রুত যোগাযোগ করা হবে।"""

APPLICANT_INFO_RECEIVED_REPLY = f"""আপনার তথ্য পাওয়া গেছে।

এখন অফিস সময়ের মধ্যে প্রয়োজনীয় কাগজপত্র ও ব্যক্তিগত প্রয়োজনীয় জিনিসপত্র নিয়ে অফিসে যোগাযোগ করুন।

অফিস:

{COMPANY_NAME}
{OFFICE_ADDRESS}।

অফিস সময়:
{OFFICE_TIME}

যোগাযোগ:

{OFFICE_NUMBERS}
(WhatsApp)

কোম্পানি যেখানে থাকার ব্যবস্থা করবে সেখানে অবস্থান করে দায়িত্ব পালন করতে হতে পারে, তাই প্রয়োজনীয় প্রস্তুতি নিয়ে আসবেন।"""

AGE_ISSUE_REPLY = f"""{COMPANY_NAME}-এর নিয়োগ নীতি অনুয়ায়ী:

বয়সসীমা: {MIN_AGE}–{MAX_AGE} বছর।

আপনার বয়স এই সীমার মধ্যে থাকলে আবেদন করতে পারবেন।

যোগাযোগ:
{OFFICE_NUMBERS}"""

AGE_OUT_OF_RANGE_REPLY = f"""ধন্যবাদ আপনার আগ্রহের জন্য।

দুঃখিত, আমাদের নিয়োগ নীতি অনুয়ায়ী বয়সসীমা {MIN_AGE}–{MAX_AGE} বছর।

আপনার বয়স এই সীমার বাইরে হওয়ায় এই মুহূর্তে আবেদন করা সম্ভব হবে না।

অন্য কোনো তথ্যের জন্য যোগাযোগ করুন:
{OFFICE_NUMBERS}"""


DOCUMENTS_REPLY = f"""প্রয়োজনীয় কাগজপত্র:

- ছবি ২ কপি
- নিজের NID / জন্ম নিবন্ধন
- মা ও বাবার NID কপি

সাথে ব্যক্তিগত প্রয়োজনীয় কাপড়চোপড় নিয়ে আসতে হবে।

কারণ চাকরির প্রয়োজনে কোম্পানি যেখানে থাকার ব্যবস্থা করবে সেখানে অবস্থান করতে হতে পারে।

যোগাযোগ:

{OFFICE_NUMBERS}"""

FEES_REPLY = f"""চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না।

যদিও প্রসেসিং ফি, জয়েনিং ফি ইত্যাদি অন্যান্য প্রতিষ্ঠানের মত আমাদের এখানেও আছে। তবুও আমরা অগ্রীম গ্রহনের পরিবর্তে মাসিক বেতনের সাথে অর্থ্যাৎ বেতন থেকে কিস্তিতে কাটার ব্যবস্থা রেখেছি।

আর্থিক সমস্যার বিষয়ে আলোচনা করা যায়। টাকা নেই - এজন্য কারো চাকরিতে জয়েন বাঁধা হবে না।
প্রতিষ্ঠানের নির্দিষ্ট মানি-রিসিট বা টাকা জমা দেয়ার রশিদ ছাড়া কেউ টাকা চাইলে তা প্রতারণা — সরাসরি অফিসে যাচাই করুন বা নিচের যেকোনো নম্বরে কল দিন:
{OFFICE_NUMBERS}"""

PAYMENT_INFO_REQUEST_REPLY = """দয়া করে নিচের তথ্যগুলো পাঠান:

1. নাম
2. মোবাইল নম্বর
3. কত তারিখ থেকে জয়েন করেছেন
4. কতগুলো জাহাজ / প্রোগ্রামে কাজ করেছেন
5. অফিস থেকে মোট কত টাকা পেয়েছেন"""

PAYMENT_ESCALATION_REPLY = f"""বিষয়টি অফিসে জানানো হচ্ছে এবং যাচাই করে ব্যবস্থা নেওয়া হবে।

ADM WhatsApp:
{ADM_WHATSAPP}"""

COMPLAINT_REPLY = """আপনার যদি কোনো নির্দিষ্ট অভিযোগ থাকে, দয়া করে প্রয়োজনীয় তথ্য ও ডকুমেন্টসহ অফিসে যোগাযোগ করুন।

আপনি চাকরিতে যোগদানের সময় যে শর্তে নিয়োগ পেয়েছেন, সেই শর্ত অনুযায়ী বিষয়টি যাচাই করা হবে।

যদি কোনো ভুল বা অসঙ্গতি পাওয়া যায়, অফিস কর্তৃপক্ষ বিষয়টি পর্যালোচনা করবে।

আমরা আইনগত অনুমোদন অনুযায়ী কার্যক্রম পরিচালনা করি।

অভিযোগ থাকলে অফিসে এসে লিখিতভাবে জানাতে পারেন।"""

ABUSIVE_REPLY = """আপনার যদি কোনো নির্দিষ্ট অভিযোগ থাকে, দয়া করে প্রয়োজনীয় তথ্যসহ অফিসে যোগাযোগ করুন।

অশোভন ভাষা ব্যবহার না করার অনুরোধ করা হচ্ছে।

যাচাইযোগ্য অভিযোগ থাকলে তা পর্যালোচনা করা হবে।"""

NEGATIVE_COMMENT_REPLY = """আপনার যদি কোনো নির্দিষ্ট অভিযোগ বা অভিজ্ঞতা থাকে, দয়া করে অফিসে যোগাযোগ করে বিস্তারিত জানান।

আমরা দীর্ঘদিন ধরে নিয়ম অনুযায়ী কার্যক্রম পরিচালনা করছি।

যাচাইযোগ্য অভিযোগ থাকলে তা পর্যালোচনা করা হবে।"""

COMMENT_INTERESTED_REPLY = f"""ধন্যবাদ।

দয়া করে ইনবক্স/WhatsApp-এ আপনার নাম, বয়স, শিক্ষাগত যোগ্যতা ও বর্তমান ঠিকানা পাঠান।

যোগাযোগ:
{OFFICE_NUMBERS}"""
