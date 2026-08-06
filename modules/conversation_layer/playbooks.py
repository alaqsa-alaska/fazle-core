"""Recruitment playbook primitives for shadow-mode reply generation --
plus, since Phase 2 (2026-08-06), validate_faithfulness() at the bottom
of this file: a topic-agnostic safety check for Tier 15's Universal
Responder, co-located with classify_reply_safety() (the closest existing
analog) rather than in a new file. See
knowledge_base/00_governance/management_decisions.md "Universal AI
Responder — Phase 2"."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class RecruitmentSignals:
    focus: str
    temperature: str
    risk: str
    language: str
    wants_application: bool
    needs_trust_repair: bool
    asks_multiple_questions: bool


RESTRICTED_REPLY_KEYWORDS = (
    "employee salary",
    "কর্মীর বেতন",
    "bank account",
    "ব্যাংক একাউন্ট",
    "vessel name",
    "ship name",
    "lc number",
    "bl number",
    "profit",
    "loss",
    "owner personal",
)

SAFE_RECRUITMENT_MARKERS = (
    "চাকরি",
    "job",
    "survey scout",
    "সার্ভে স্কট",
    "security guard",
    "সিকিউরিটি গার্ড",
    "বেতন",
    "salary",
    "যোগ্যতা",
    "training",
    "ট্রেনিং",
    "অফিস",
    "ভিক্টোরিয়া",
    "পাহাড়তলী",
    "নাম",
    "বয়স",
    "আবেদন",
)

FOCUS_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("salary", ("salary", "বেতন", "মাসে", "কত পাব", "টাকা", "বেতন কাঠামো", "প্যাকেজ")),
    ("fee", ("fee", "ফি", "জামানত", "joining fee", "টাকা লাগবে", "ট্রেনিং ফি", "ডিপোজিট")),
    ("documents", ("কাগজ", "document", "nid", "ছবি", "সার্টিফিকেট", "কি লাগবে")),
    ("office_location", ("ঠিকানা", "location", "অফিস", "কোথায়", "address", "একে খান")),
    ("training", ("training", "ট্রেনিং", "শিখ", "অভিজ্ঞতা", "experience", "প্রশিক্ষণ")),
    ("ship_duty", ("জাহাজ", "ship", "ডিউটি", "duty", "route", "থাকা", "খাবার", "এসকর্ট")),
    ("trust", ("fake", "ভুয়া", "বাটপার", "fraud", "scam", "প্রতার")),
    ("application", ("apply", "আবেদন", "করতে চাই", "interested", "আগ্রহী", "জয়েন", "join")),
    ("positions", ("পদ কী কী", "কোন কোন পদ", "কী কী চাকরি", "available position", "পদসমূহ")),
    ("escort_duty", ("এসকর্ট", "কতদিন থাকতে", "লোড আনলোড", "জাহাজে কী করতে")),
    ("probation", ("প্রবেশন", "ট্রায়াল পিরিয়ড", "মূল্যায়ন সময়", "trial period")),
    ("leave", ("ছুটি", "সাপ্তাহিক ছুটি", "চিকিৎসা ছুটি", "বার্ষিক ছুটি", "leave")),
    ("resign", ("রিজাইন", "চাকরি ছাড়", "নোটিশ কতদিন", "resignation")),
    ("discipline", ("ইউনিফর্ম", "পোস্ট ত্যাগ", "নিষিদ্ধ", "অসদাচরণ")),
    ("operation_officer", ("অপারেশন অফিসার", "operation officer", "রোস্টার তৈরি")),
    ("marketing_officer", ("মার্কেটিং অফিসার", "marketing officer", "কমিশন")),
    ("ghat_supervisor", ("ঘাট সুপারভাইজার", "ghat supervisor", "ঘাটের কাজ")),
    ("payment_method", ("bKash", "Nagad", "মোবাইল ব্যাংকিং", "payment method")),
    ("conveyance", ("যাতায়াত ভাতা", "কনভেয়েন্স", "conveyance", "transport allowance")),
)

CANONICAL_FACTS: dict[str, str] = {
    "salary": (
        "বেতন পদ অনুযায়ী — প্রশিক্ষণকালীন: ৯,০০০-১৫,০০০ টাকা; "
        "স্থায়ী গার্ড মোট ~২৪,৭০০ (মূল ১২,০০০ + ভাতা); "
        "অপারেশন অফিসার: ট্রায়াল ১২,০০০-১৮,০০০ → স্থায়ী ১৮,০০০-৩৫,০০০+; "
        "মার্কেটিং অফিসার: ১৫,০০০-৩০,০০০+; "
        "ইনচার্জ: ১৬,০০০-৩২,০০০+; ঘাট সুপারভাইজার: ১৫,০০০-৩০,০০০+। "
        "বেতন মাসের ১০-১২ তারিখে nগদ/bKash/Nagad/ব্যাংকে দেওয়া হয়।"
    ),
    "fee": (
        "কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না। প্রসেসিং/জয়েনিং ফি অন্যান্য "
        "প্রতিষ্ঠানের মত এখানেও আছে, তবে অগ্রিম নেওয়ার বদলে মাসিক বেতন থেকে "
        "কিস্তিতে কাটা হয় — টাকার অভাবে কারো জয়েন বাঁধাগ্রস্ত হয় না। "
        "নির্দিষ্ট মানি-রিসিট ছাড়া কেউ টাকা চাইলে তা প্রতারণা, অফিসে যাচাই করুন।"
    ),
    "documents": (
        "প্রয়োজনীয়: নিজের NID/জন্ম নিবন্ধন, ২ কপি ছবি, "
        "মা-বাবার NID কপি, সার্টিফিকেট (থাকলে), "
        "চেয়ারম্যান/কমিশনার সনদ (সম্ভব হলে)।"
    ),
    "office_location": (
        "অফিস: Al-Rifa'i Integrated Services, আগ্রপাড়া, ভিক্টোরিয়া গেইট নং ১, "
        "খোকনের বিল্ডিং (২য় তলা), থানা: পাহাড়তলী, চট্টগ্রাম সিটি কর্পোরেশন। "
        "সময়: সকাল ১০টা - বিকাল ৫টা (শুক্রবার বন্ধ)। "
        "যোগাযোগ: 01958-122300।"
    ),
    "training": (
        "নতুনদের জন্য ২ সপ্তাহ থেকে ৪৫ দিনের প্রশিক্ষণ। "
        "অভিজ্ঞতা না থাকলেও আবেদন করা যায়। "
        "ট্রেনিংয়ে থাকার ব্যবস্থা ফ্রি; খাওয়া নিজ দায়িত্বে।"
    ),
    "ship_duty": (
        "Survey Scout/Escort কাজে জাহাজে মালামাল তদারকি, লোড-আনলোড হিসাব, "
        "চুরি/ক্ষতি রোধ ও রিপোর্টিং। একটি প্রোগ্রাম সাধারণত ৭-২০ দিন। "
        "থাকা জাহাজে ফ্রি; খাবার মেস সিস্টেমে নিজ খরচে।"
    ),
    "trust": (
        "Al-Rifa'i Integrated Services একটি নিবন্ধিত প্রতিষ্ঠান। "
        "সন্দেহ থাকলে সরাসরি অফিসে এসে যাচাই করে সিদ্ধান্ত নিতে বলা নিরাপদ।"
    ),
    "application": (
        "আবেদনের জন্য নাম, বয়স, শিক্ষা, বর্তমান ঠিকানা/জেলা, "
        "অভিজ্ঞতা এবং যোগাযোগ নম্বর সংগ্রহ করা দরকার।"
    ),
    "general": (
        "Survey Scout/Escort/Security Guard নিয়োগে সততা, দায়িত্বশীলতা ও "
        "শারীরিক সক্ষমতা গুরুত্বপূর্ণ। নতুনদেরও আবেদন করার সুযোগ আছে।"
    ),
    "positions": (
        "পদসমূহ: সিকিউরিটি গার্ড, এসকর্ট, "
        "সার্ভে স্কাউট/ক্যালিম্যান/সিলম্যান, "
        "সিকিউরিটি সুপারভাইজার, অ্যাসিস্ট্যান্ট সুপারভাইজার, "
        "সিকিউরিটি ইনচার্জ, ঘাট সুপারভাইজার, "
        "অপারেশন অফিসার, মার্কেটিং অফিসার।"
    ),
    "escort_duration": (
        "একটি এসকর্ট প্রোগ্রাম সাধারণত ৭ থেকে ২০ দিন স্থায়ী হয়। "
        "কাজের ধরন ও গন্তব্য অনুসারে কম বা বেশি হতে পারে।"
    ),
    "probation": (
        "পদভেদে ৩-৬ মাস প্রবেশন। সার্ভে স্কাউট প্রায় ৪৫ দিন। "
        "মূল্যায়নে সময়ানুবর্তিতা, আচরণ ও কাজের দক্ষতা দেখা হয়।"
    ),
    "leave": (
        "স্থায়ী কর্মীদের জন্য সাপ্তাহিক ছুটি, সরকারি ছুটি, "
        "বার্ষিক ছুটি ও চিকিৎসা ছুটি প্রযোজ্য।"
    ),
    "provident_fund": (
        "২ বছর চাকরির পর স্থায়ী কর্মীদের জন্য প্রভিডেন্ট ফান্ড সুবিধা।"
    ),
    "resign": (
        "চাকরি ছাড়তে হলে ৩০ দিনের লিখিত নোটিশ দিতে হবে "
        "এবং দায়িত্ব সঠিকভাবে হস্তান্তর করতে হবে।"
    ),
    "discipline": (
        "ডিউটির ১৫ মিনিট আগে পোস্টে থাকা, পরিষ্কার ইউনিফর্ম বাধ্যতামূলক। "
        "পোস্টে ঘুম ও অনুমতি ছাড়া পোস্ট ত্যাগ নিষিদ্ধ।"
    ),
    "operation_officer": (
        "অপারেশন অফিসার মাঠ পর্যায়ের নিরাপত্তা, রোস্টার, ডিউটি প্ল্যান, "
        "ক্লায়েন্ট অভিযোগ সমাধান ও ইমার্জেন্সি নিয়ন্ত্রণ করেন। "
        "যোগ্যতা: স্নাতক + কম্পিউটার দক্ষতা।"
    ),
    "marketing_officer": (
        "মার্কেটিং অফিসার নতুন ক্লায়েন্ট সংগ্রহ, প্রেজেন্টেশন, "
        "প্রপোজাল তৈরি ও বাজার বিশ্লেষণ করেন।"
    ),
    "ghat_supervisor": (
        "ঘাট সুপারভাইজার ঘাট, জাহাজ, লাইটার ভেসেল ও পণ্য পরিবহন তদারকি করেন। "
        "কাজ ফিল্ডভিত্তিক, শিফট রোটেশন আছে।"
    ),
    "payment_method": (
        "বেতন নগদ, bKash, Nagad বা ব্যাংক ট্রান্সফারে প্রদান করা হয়। "
        "পেমেন্ট নম্বর সরাসরি অফিস থেকে জানানো হবে।"
    ),
    "conveyance": (
        "কিছু পদে যাতায়াত ভাতা বা কনভেয়েন্স অ্যালাওয়েন্স থাকতে পারে। "
        "পদ ও কাজের ধরন অনুযায়ী ভিন্ন।"
    ),
}

APPLICATION_FIELDS = "নাম, বয়স, শিক্ষা, জেলা/বর্তমান ঠিকানা, অভিজ্ঞতা এবং মোবাইল নম্বর"


def analyze_recruitment_signals(text: str) -> RecruitmentSignals:
    lowered = text.lower().strip()
    focus = "general"
    matches = 0
    for name, keywords in FOCUS_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            if focus == "general":
                focus = name
            matches += 1

    hot_words = ("apply", "করতে চাই", "জয়েন", "join", "ready", "confirm", "আগ্রহী")
    warm_words = ("details", "বিস্তারিত", "salary", "বেতন", "কাজ", "job", "কোথায়", "কিভাবে")
    risk_words = ("fake", "ভুয়া", "বাটপার", "fraud", "scam", "প্রতার")

    temperature = "cold"
    if any(word in lowered for word in hot_words):
        temperature = "hot"
    elif any(word in lowered for word in warm_words):
        temperature = "warm"

    risk = "trust" if any(word in lowered for word in risk_words) else "normal"
    language = "en" if lowered.isascii() else "bn"

    return RecruitmentSignals(
        focus=focus,
        temperature=temperature,
        risk=risk,
        language=language,
        wants_application=focus == "application" or temperature == "hot",
        needs_trust_repair=risk == "trust" or focus == "trust",
        asks_multiple_questions=matches > 1 or text.count("?") > 1,
    )


def classify_reply_safety(reply: str) -> str:
    lowered = reply.lower()
    if any(keyword in lowered for keyword in RESTRICTED_REPLY_KEYWORDS):
        return "restricted"
    if any(marker in lowered for marker in SAFE_RECRUITMENT_MARKERS):
        return "safe"
    if len(reply.strip()) <= 160 and any(
        greeting in lowered for greeting in ("আসসালাম", "ওয়ালাইকুম", "ধন্যবাদ")
    ):
        return "safe"
    return "review"


def build_rule_reply(signals: RecruitmentSignals) -> str:
    fact = CANONICAL_FACTS.get(signals.focus, CANONICAL_FACTS["general"])

    if signals.needs_trust_repair:
        return (
            "ভাই, সন্দেহ হওয়া স্বাভাবিক। অফিসে সরাসরি এসে যাচাই করে সিদ্ধান্ত নিতে পারেন।\n"
            f"{CANONICAL_FACTS['office_location']}\n"
            f"আগ্রহী হলে {APPLICATION_FIELDS} পাঠান।"
        )

    if signals.focus == "fee":
        return (
            "যোগদান খরচ সম্পর্কে জানতে অফিসে সরাসরি যোগাযোগ করুন।\n"
            "অথবা নাম-বয়স-জেলা পাঠান, অফিস থেকে বিস্তারিত জানানো হবে।"
        )

    if signals.wants_application:
        return (
            "ধন্যবাদ ভাই। আবেদন শুরু করতে এই তথ্যগুলো পাঠান: "
            f"{APPLICATION_FIELDS}.\n"
            "নতুন হলেও আবেদন করা যাবে; অফিস যাচাই করে পরের ধাপ জানাবে।"
        )

    return f"{fact}\nআগ্রহী হলে {APPLICATION_FIELDS} পাঠান, অফিস থেকে পরের ধাপ জানানো হবে।"


# ── Faithfulness validator (Phase 2, 2026-08-06) ────────────────────────────
# Universal Responder's grounding check for Tier 15 -- parallel in spirit
# to social_auto_reply/intelligent_generator.py's _safe_polish(), but
# grounded against THIS query's own RAG citations (dynamic, per call)
# rather than a static numeric whitelist tuned for one topic. NOT a full
# semantic-entailment check -- that needs either a second LLM call or real
# NLP infrastructure, neither of which this function does. This is a
# crude but real, honest, deterministic proxy:
#   1. every number in the reply must appear somewhere in the retrieved
#      RAG text -- exactly _safe_polish()'s numeric-grounding idea,
#      generalized from "static whitelist" to "this query's own facts".
#   2. no poison words (the reply must not talk about being an AI, a
#      database, a model, a prompt).
#   3. if RAG found nothing at all, the reply MUST be the safe fallback
#      exactly -- there is nothing for the LLM to have grounded an answer
#      in, so any freeform text at that point is by definition ungrounded.

SAFE_FALLBACK_REPLY = "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"

_POISON_WORDS = ("chain_of_thought", "ai", "\u098f\u0986\u0987", "prompt", "\u09a1\u09c7\u099f\u09be\u09ac\u09c7\u099c", "database", "model", "reasoning")

_BANGLA_DIGITS_TRANS = str.maketrans("\u09e6\u09e7\u09e8\u09e9\u09ea\u09eb\u09ec\u09ed\u09ee\u09ef", "0123456789")


def _extract_numbers(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFC", text or "").translate(_BANGLA_DIGITS_TRANS)
    return set(re.findall(r"\b\d{2,6}\b", normalized))


def validate_faithfulness(
    reply: str,
    rag_answer: str | None,
    citations: list | None = None,
) -> tuple[bool, str | None]:
    """Returns (is_faithful, reason). reason is None when is_faithful is
    True, otherwise a human-readable explanation (matches
    _safe_polish()'s own (is_safe, reason) return-shape convention)."""
    if not reply:
        return False, "empty reply"
    if len(reply) > 1400:
        return False, f"reply too long ({len(reply)} chars, max 1400)"

    # Word-boundary matched, NOT raw substring (unlike social_auto_reply's
    # _safe_polish(), whose equivalent poison list is a plain `in` check --
    # harmless there since its replies are Bangla-first by construction,
    # but a real bug for this function: short English tokens like "ai"
    # match as bare substrings inside ordinary words -- "contain", "again",
    # "maintain" all contain "ai". Caught and fixed before shipping, not
    # copied from _safe_polish() as-is.
    lowered = reply.lower()
    hit = next(
        (p for p in _POISON_WORDS if re.search(r"\b" + re.escape(p.lower()) + r"\b", lowered)),
        None,
    )
    if hit:
        return False, f"reply contained a disallowed term: {hit!r}"

    if not rag_answer or not (citations or []):
        if reply.strip() != SAFE_FALLBACK_REPLY:
            return False, "no RAG facts were retrieved -- reply must be the safe fallback, not a freeform answer"
        return True, None

    reply_numbers = _extract_numbers(reply)
    grounding_numbers = _extract_numbers(rag_answer)
    unsupported = reply_numbers - grounding_numbers
    if unsupported:
        return False, f"reply contained number(s) not present in retrieved facts: {', '.join(sorted(unsupported))}"

    return True, None
