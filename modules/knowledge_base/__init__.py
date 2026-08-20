"""
Fazle Core — Knowledge Base
Provides instant auto-replies for common intents without LLM overhead.

Priority:
  1. DB lookup (fazle_knowledge_base table) — dynamic, admin-editable
  2. Hardcoded fallback constants — always works even if table missing

Source: extracted from resources/ txt files.
"""
import logging
import re
from typing import Optional

from app.database import fetch_one, fetch_all

log = logging.getLogger("fazle.knowledge_base")

# ── Hardcoded fallback templates ───────────────────────────────────────────────
# Each entry: (trigger_keywords, reply_text)
_FALLBACK: list[tuple[list[str], str]] = [
    (
        ["চাকরি কি", "কাজ কী", "কাজ কি", "details", "ডিউটি কত", "কত ঘণ্টা", "বিস্তারিত জান", "survey scout"],
        "ধন্যবাদ আমাদের সাথে যোগাযোগ করার জন্য \n\n"
        " পদ: Survey Scout (সার্ভে স্কট)\n"
        " কাজ: লাইটার জাহাজে মালামাল তদারকি, লোড–আনলোড হিসাব, চুরি প্রতিরোধ\n"
        " ডিউটি: গড়ে ৬–৮ ঘণ্টা | থাকা: জাহাজেই (ফ্রি)\n"
        " বেতন: ১০,০০০–১৭,০০০ টাকা |  অভিজ্ঞতা লাগবে না\n\n"
        " আবেদন করতে পাঠান: নাম, বয়স, শিক্ষা, ঠিকানা\n"
        " WhatsApp: 01958 122300",
    ),
    (
        ["ঠিকানা", "লোকেশন", "address", "অফিস কোথায়", "কোথায় অফিস",
         "হেড অফিস কই", "হেড অপিশ কই", "কোথায় যেতে হবে", "কোথায় আসতে হবে",
         "office address", "office location", "একে খান", "google map",
         "কোথায় যাব", "অফিসের ঠিকানা", "আগ্রপাড়া", "পাহাড়তলী"],
        # 2026-07-21: single canonical office text (Owner-approved), also the
        # DB row b11_office_address — kept in sync as the DB-unavailable fallback.
        "আমাদের অফিস:\n"
        "Al-Rifa'i Integrated Services\n"
        "আগ্রপাড়া, ভিক্টোরিয়া গেইট নং ১, খোকনের বিল্ডিং (২য় তলা)\n"
        "পোস্ট অফিস: উত্তর কাট্টলি (4217), থানা: পাহাড়তলী, চট্টগ্রাম সিটি কর্পোরেশন\n\n"
        "অফিস সময়: সকাল ১০টা – বিকাল ৫টা (শুক্রবার বন্ধ)\n"
        "যোগাযোগ: 01958 122300\n"
        "সাথে আনুন: NID ফটোকপি (নিজের ও অভিভাবকের) + ৪ কপি পাসপোর্ট ও ২ কপি স্ট্যাম্প সাইজ ছবি "
        "+ প্রয়োজনীয় কাপড়-চোপড়, কাঁথা, মশারি, বিছানার চাদর (১ টি করে)।",
    ),
    (
        ["করতে চাই", "আগ্রহী", "interested", "আমাকে নিবেন", "জয়েন করতে চাই", "apply"],
        "ধন্যবাদ আগ্রহ দেখানোর জন্য \n\n"
        "আবেদন করতে নিচের তথ্যগুলো পাঠান:\n"
        "১. পূর্ণ নাম\n২. বয়স\n৩. শিক্ষাগত যোগ্যতা\n"
        "৪. বর্তমান ঠিকানা (জেলাসহ)\n৫. মোবাইল নম্বর\n\n"
        " যোগাযোগ: 01958 122300\n"
        " ফি সংক্রান্ত: কোনো অগ্রীম জামানত নয় — মাসিক বেতন থেকে কিস্তিতে কাটা হয়।",
    ),
    (
        ["বাটপার", "ভুয়া", "fake", "প্রতারক", "ধোঁকাবাজ", "fraud"],
        "Al-Rifa'i Integrated Services একটি নিবন্ধিত প্রতিষ্ঠান।\n"
        " কোনো ঘুষ বা অতিরিক্ত ফি নেওয়া হয় না\n"
        " বেতন প্রতি মাসে নিয়মিত প্রদান করা হয়\n"
        " ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n"
        " 01958 122300 — সরাসরি অফিসে এসে যাচাই করুন।",
    ),
    (
        ["কী কী লাগবে", "কাগজপত্র", "NID", "ছবি", "বয়সসীমা", "certificate", "কী নিয়ে আসব"],
        " আবেদনের জন্য যা লাগবে:\n"
        "বয়স: ১৮–৫৫ বছর | শিক্ষা: ন্যূনতম অষ্টম শ্রেণি\n"
        "অভিজ্ঞতা লাগবে না — ৪৫ দিন ট্রেনিং দেওয়া হবে\n\n"
        " অফিসে আনুন:\n1. NID / জন্ম নিবন্ধন (ফটোকপি)\n"
        "2. পাসপোর্ট সাইজ ছবি (২ কপি)\n"
        " সার্টিফিকেট না থাকলেও আবেদন করা যাবে",
    ),
    (
        ["মাদ্রাসা", "সার্টিফিকেট নেই", "দাখিল", "আলিম", "ক্লাস ৮", "পড়তে পারি"],
        "জি ভাই, আবেদন করতে পারবেন \n"
        " মাদ্রাসার ছাত্র — চলবে |  দাখিল/আলিম — চলবে\n"
        " শুধু পড়তে ও লিখতে পারলেই যথেষ্ট\n"
        " পাঠান: নাম, বয়স, শিক্ষা (যা আছে), ঠিকানা\n"
        " WhatsApp: 01958 122300",
    ),
    (
        ["vacancy", "আসন আছে", "এখনো নিচ্ছেন", "লোক নিচ্ছেন", "আর কত জন"],
        " সীমিত আসন — এখনো নিয়োগ চলছে\n"
        " আগ্রহী হলে দেরি না করে এখনই আবেদন করুন\n"
        " পাঠান: নাম, বয়স, শিক্ষা, ঠিকানা\n"
        " WhatsApp: 01958 122300\n"
        "আগে এলে আগে সুযোগ",
    ),
    (
        ["টাকা লাগবে", "ভর্তি ফি", "জামানত", "joining fee", "deposit", "ট্রেনিং ফি"],
        # 2026-07-21: single canonical deposit/fee text (Owner-approved),
        # also the DB row b11_no_fee — kept in sync as the DB-unavailable fallback.
        "চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না।\n\n"
        "যদিও প্রসেসিং ফি, জয়েনিং ফি ইত্যাদি অন্যান্য প্রতিষ্ঠানের মত আমাদের এখানেও আছে। "
        "তবুও আমরা অগ্রীম গ্রহনের পরিবর্তে মাসিক বেতনের সাথে অর্থ্যাৎ বেতন থেকে কিস্তিতে কাটার ব্যবস্থা রেখেছি।\n\n"
        "আর্থিক সমস্যার বিষয়ে আলোচনা করা যায়। টাকা নেই - এজন্য কারো চাকরিতে জয়েন বাঁধা হবে না।\n"
        "প্রতিষ্ঠানের নির্দিষ্ট মানি-রিসিট বা টাকা জমা দেয়ার রশিদ ছাড়া কেউ টাকা চাইলে তা প্রতারণা "
        "— সরাসরি অফিসে যাচাই করুন বা কল দিন 01958 122300 নাম্বারে।",
    ),
    (
        ["বেতন কত", "salary", "মাসে কত", "কত পাব", "বেতন কাঠামো"],
        " বেতন পদ অনুযায়ী আলাদা:\n\n"
        " সার্ভে স্কট / এস্কর্ট পদ:\n"
        "-  ট্রেনিং (৪৫ দিন): ৳১০,০০০–১৫,০০০/মাস\n"
        "-  ট্রেনিং পরে: ৳১২,০০০–১৭,০০০ (ডিউটিভিত্তিক)\n\n"
        " সিকিউরিটি গার্ড পদ:\n"
        "-  প্রবেশন (৩ মাস): মোট প্যাকেজ ~৳১৭,০০০/মাস\n"
        "-  স্থায়ী হলে: মোট প্যাকেজ ~৳২৪,৭০০/মাস\n\n"
        "আপনি কোন পদে আগ্রহী? বিস্তারিত জানাই।",
    ),
    (
        ["বেতন মেরে", "বেতন পাই না", "পাওনা দেয়নি"],
        "আমরা আপনার অভিযোগ গুরুত্বের সাথে নিচ্ছি।\n"
        " WhatsApp: 01958 122300\n"
        " ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n"
        " সব বৈধ পাওনা মিটিয়ে দিতে আমরা প্রতিশ্রুতিবদ্ধ",
    ),
    (
        ["সালাম", "আস্সালামু", "hello", "hi", "হ্যালো", "menu", "মেনু", "start"],
        "আমি ফজলে — Al-Rifa'i Integrated Services-এর ডিজিটাল সহকারী।\n\n"
        "কীভাবে সাহায্য করতে পারি?\n"
        "১. চাকরি সম্পর্কে জানতে\n"
        "২. অফিসের ঠিকানা\n"
        "৩. বেতন সম্পর্কে\n"
        "৪. ডিউটি/পেমেন্ট\n\n"
        "যেকোনো প্রশ্ন করুন ",
    ),
    # ── Extended KB: operation officer, marketing, incharge, ghat supervisor ──
    (
        ["অপারেশন অফিসার", "operation officer", "অপারেশন অফিসার বেতন"],
        "অপারেশন অফিসার মাঠ পর্যায়ের নিরাপত্তা পরিচালনা, রোস্টার ও ডিউটি প্ল্যান করেন।\n"
        " যোগ্যতা: ন্যূনতম স্নাতক ডিগ্রি + কম্পিউটার দক্ষতা\n"
        " ট্রায়াল: ১২,০০০-১৮,০০০ টাকা | স্থায়ী: ১৮,০০০-৩৫,০০০+ টাকা\n"
        " যোগাযোগ: 01958-122300",
    ),
    (
        ["মার্কেটিং অফিসার", "marketing officer", "মার্কেটিং বেতন"],
        "মার্কেটিং অফিসার নতুন ক্লায়েন্ট সংগ্রহ ও কোম্পানি প্রমোশনের কাজ করেন।\n"
        " বেতন: ১৫,০০০-৩০,০০০+ টাকা + কমিশন/ইনসেন্টিভ\n"
        " যোগাযোগ: 01958-122300",
    ),
    (
        ["সিকিউরিটি ইনচার্জ", "security incharge", "ইনচার্জ বেতন"],
        "সিকিউরিটি ইনচার্জ নির্দিষ্ট সাইটের নিরাপত্তা ব্যবস্থাপনা পরিচালনা করেন।\n"
        " যোগ্যতা: HSC/স্নাতক + ২-৩ বছর অভিজ্ঞতা\n"
        " বেতন: ১৬,০০০-৩২,০০০+ টাকা",
    ),
    (
        ["ঘাট সুপারভাইজার", "ghat supervisor", "ঘাটের কাজ"],
        "ঘাট সুপারভাইজার জাহাজ, লাইটার ভেসেল ও পণ্য পরিবহন তদারকি করেন।\n"
        "কাজ ফিল্ডভিত্তিক, শিফট রোটেশন আছে।\n"
        " বেতন: ১৫,০০০-৩০,০০০+ টাকা",
    ),
    (
        ["এসকর্ট কতদিন", "কতদিন থাকতে হয়", "জাহাজে কতদিন", "প্রোগ্রাম কতদিন"],
        "এসকর্ট প্রোগ্রামে সাধারণত ৭ থেকে ২০ দিন সময় লাগে।\n"
        "কাজের ধরন ও জাহাজের গন্তব্য অনুযায়ী সময় কম বা বেশি হতে পারে।",
    ),
    (
        ["প্রবেশন", "ট্রায়াল পিরিয়ড", "মূল্যায়ন সময়", "trial period"],
        "প্রশিক্ষণ শেষে সাধারণ পদে ৩-৬ মাস মূল্যায়ন বা প্রবেশন সময় থাকে।\n"
        "সার্ভে স্কাউটের ক্ষেত্রে প্রায় ৪৫ দিন। ভালো পারফরম্যান্সে স্থায়ী হওয়ার সুযোগ আছে।",
    ),
    (
        ["রিজাইন", "চাকরি ছাড়", "নোটিশ কতদিন", "resignation"],
        "চাকরি ছাড়তে হলে সাধারণত ৩০ দিনের লিখিত নোটিশ দিতে হবে।\n"
        "দায়িত্ব হস্তান্তর করে প্রশাসনিক প্রক্রিয়া অনুসরণ করতে হবে।",
    ),
    (
        ["ছুটি", "সাপ্তাহিক ছুটি", "বার্ষিক ছুটি", "চিকিৎসা ছুটি"],
        "স্থায়ী কর্মীদের জন্য সাপ্তাহিক ছুটি, সরকারি ছুটি,\n"
        "বার্ষিক ছুটি ও চিকিৎসা ছুটির সুযোগ রয়েছে।\n"
        "ছুটি পেতে অফিসের নিয়ম অনুযায়ী আবেদন করতে হবে।",
    ),
    (
        ["প্রভিডেন্ট ফান্ড", "PF সুবিধা", "পিএফ", "provident fund"],
        "স্থায়ী কর্মীদের জন্য প্রভিডেন্ট ফান্ড সুবিধা রয়েছে।\n"
        "চাকরির মেয়াদ ২ বছর পূর্ণ হওয়ার পর দাবি বা উত্তোলনের সুযোগ থাকতে পারে।",
    ),
    (
        ["ডিউটির নিয়ম", "ইউনিফর্ম নিয়ম", "পোস্টে ঘুমানো", "নিষিদ্ধ"],
        "ডিউটির সময় পরিষ্কার ইউনিফর্ম ও পালিশ করা জুতা বাধ্যতামূলক।\n"
        "পোস্টে ঘুমানো, অনুমতি ছাড়া পোস্ট ত্যাগ ও অসদাচরণ নিষিদ্ধ।\n"
        "ডিউটি শুরুর ১৫ মিনিট আগে পোস্টে উপস্থিত থাকতে হবে।",
    ),
    (
        ["অপারেশন অফিসার ক্যারিয়ার", "ইনচার্জ প্রমোশন", "ক্যারিয়ার পথ", "উন্নতির সুযোগ"],
        "ক্যারিয়ার পথ:\n"
        "গার্ড → সুপারভাইজার → ইনচার্জ → অপারেশন অফিসার → অপারেশন ম্যানেজার।\n"
        "ভালো পারফরম্যান্স ও সততায় দ্রুত প্রমোশনের সুযোগ রয়েছে।",
    ),
    (
        ["অগ্রিম", "ইমার্জেন্সি অগ্রিম", "advance", "জরুরি টাকা", "salary advance"],
        "বিশেষ পরিস্থিতিতে সীমিত অগ্রিম সুবিধা পাওয়া যেতে পারে।\n"
        "এটি অনুমোদন, ডিউটি স্ট্যাটাস ও অফিসের নীতিমালার উপর নির্ভর করে।",
    ),
    (
        ["bKash", "Nagad", "মোবাইল ব্যাংকিং", "bkash নম্বর", "nagad নম্বর"],
        "বেতন ও পেমেন্ট নগদ, bKash, Nagad বা ব্যাংক ট্রান্সফারে দেওয়া হয়।\n"
        "পেমেন্টের নির্দিষ্ট নম্বর অফিস থেকে জানানো হবে।\n"
        "অপরিচিত নম্বরে টাকা পাঠাবেন না। যোগাযোগ: 01958-122300",
    ),
    (
        ["যাতায়াত ভাতা", "কনভেয়েন্স", "conveyance", "transport allowance"],
        "কিছু পদে যাতায়াত ভাতা বা কনভেয়েন্স অ্যালাওয়েন্স থাকতে পারে।\n"
        "পদ ও কাজের ধরন অনুযায়ী ভিন্ন। অফিস থেকে বিস্তারিত জানানো হবে।",
    ),
]


async def get_reply(text: str, intent: Optional[str] = None) -> Optional[str]:
    """
    Return a knowledge-base reply for the given message text.
    Tries DB first, falls back to hardcoded constants.
    Returns None if nothing matches — caller should use LLM.
    """
    text_lower = text.lower().strip()

    # CV / recruitment content detection: long messages from job applicants contain
    # "ঠিকানা", "address" as data fields, not as questions.  Skip short ambiguous
    # keywords when the text is clearly a document (> 300 chars) or intent is recruitment.
    is_cv_like = len(text) > 300 or intent == "recruitment"
    _CV_SKIP_KEYWORDS = {"address", "ঠিকানা", "লোকেশন"}

    def _should_skip(kw: str) -> bool:
        return is_cv_like and kw.lower() in _CV_SKIP_KEYWORDS

    # 1. Try DB
    try:
        rows = await fetch_all(
            "SELECT key, trigger_keywords, reply_text FROM fazle_knowledge_base WHERE is_active = true",
        )
        for row in rows:
            keywords = row.get("trigger_keywords") or []
            for kw in keywords:
                if _should_skip(kw):
                    continue
                if kw.lower() in text_lower:
                    log.info(f"[KB] DB match: key={row['key']} kw={kw!r}")
                    return row["reply_text"]
    except Exception as e:
        log.debug(f"[KB] DB lookup failed (table may not exist yet): {e}")

    # 2. Fallback to hardcoded
    for keywords, reply in _FALLBACK:
        for kw in keywords:
            if _should_skip(kw):
                continue
            if kw.lower() in text_lower:
                log.info(f"[KB] fallback match: kw={kw!r}")
                return reply

    # 3. Batch 21 — RAG semantic fallback (skip CV-like blobs)
    # PATCH 3: RAG results are CONTEXT ONLY — LLM generates a clean reply, never raw chunks
    if not is_cv_like:
        try:
            from modules import rag
            from app.ollama import generate_reply as _llm_reply
            res = await rag.safe_answer(text, k=2, min_score=4.0)
            if res and res.get("top_score", 0) >= 4.0:
                raw_context = res.get("answer", "")
                _POISON_PATTERNS = (
                    "এআই-এর বিশ্লেষণ", "এআই-এর ইনটেন্ট",
                    "| :--- |", "chain_of_thought", "Intent)",
                    "প্রার্থীর মেসেজ", "প্রার্থীর সম্ভাব্য প্রশ্ন",
                )
                if any(p in raw_context for p in _POISON_PATTERNS):
                    src = (res.get("citations") or [{}])[0].get("source", "?")
                    log.warning(
                        f"[KB] [RAG_POISON_BLOCKED] analysis text in RAG chunk, "
                        f"score={res['top_score']}, source={src}"
                    )
                else:
                    # Safe context: let LLM generate a short conversational reply
                    src = (res.get("citations") or [{}])[0].get("source", "?")
                    log.info(
                        f"[KB] [RAG_CONTEXT_LLM] score={res['top_score']} "
                        f"source={src!r} — generating clean reply via LLM"
                    )
                    rag_reply = await _llm_reply(
                        user_message=text,
                        intent=intent or "general",
                        db_context=raw_context,
                    )
                    return rag_reply
        except Exception as e:
            log.debug(f"[KB] RAG fallback failed: {e}")

    return None


async def get_recruitment_reply(text: str) -> Optional[str]:
    """Shortcut: match only recruitment-category KB entries."""
    text_lower = text.lower().strip()
    try:
        rows = await fetch_all(
            "SELECT trigger_keywords, reply_text FROM fazle_knowledge_base "
            "WHERE is_active = true AND category = 'recruitment'",
        )
        for row in rows:
            for kw in (row.get("trigger_keywords") or []):
                if kw.lower() in text_lower:
                    return row["reply_text"]
    except Exception as _e:
        from app.error_log import record_error
        await record_error("knowledge_base.recruitment_lookup", _e)
    # Fallback subset
    for keywords, reply in _FALLBACK:
        for kw in keywords:
            if kw.lower() in text_lower:
                return reply
    return None
