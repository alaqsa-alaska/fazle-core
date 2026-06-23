"""Context-aware reply planning with strict business fact boundaries."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass

from app.database import execute

from . import reply_rules as rules
from .classifier import Classification, classify_comment, classify_message
from .conversation_history import recent_thread_history, thread_state
from .payment_issue_handler import initial_payment_reply
from .reply_generator import generate_comment_reply, generate_reply
from .risk_flagger import risk_reason

_BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _extract_age(text: str) -> int | None:
    """Extract a numeric age from text. Returns None if no clear age found."""
    normalized = unicodedata.normalize("NFC", text)
    for pattern in (
        r"(?:বয়স|age)[:\s]*([0-9০-৯]+)",
        r"\b([0-9০-৯]{2})\s*(?:বছর|years?|yr)\b",
        r"\b([6-9][0-9])\b",
    ):
        m = re.search(pattern, normalized, re.IGNORECASE)
        if m:
            try:
                age = int(m.group(1).translate(_BANGLA_DIGITS))
                if 10 <= age <= 80:
                    return age
            except ValueError:
                pass
    return None


@dataclass(frozen=True)
class ReplyPlan:
    reply_text: str
    intent: str
    auto_send: bool
    flag_reason: str | None
    event_ids: list[int]
    target_id: str
    reply_to_comment_id: str | None = None


_INTENT_PRIORITY = [
    "payment_issue",
    "complaint",
    "legal_issue",
    "scam_allegation",
    "abuse",
    "fees",
    "applicant_info_complete",
    "interested",
    "salary",
    "salary_objection",
    "location",
    "documents",
    "age_issue",
    "job_details",
    "career_growth",
    "training",
    "accommodation",
    "difficulty",
    "greeting",
]

_SAFE_FREEFORM = {
    "career_growth",
    "training",
    "accommodation",
    "difficulty",
    "experience",
}


async def plan_reply(platform: str, target_id: str, events: list[dict]) -> ReplyPlan | None:
    if not events:
        return None
    combined_text = "\n".join(str(e.get("message_text") or "").strip() for e in events if e.get("message_text"))
    event_ids = [int(e["id"]) for e in events]
    classifications = [_classify(platform, str(e.get("message_text") or ""), bool(e.get("media_flag"))) for e in events]
    intents = _ordered_unique([_expand_freeform_intent(c.intent, str(e.get("message_text") or "")) for c, e in zip(classifications, events)])
    history = await recent_thread_history(platform, target_id)
    state = await thread_state(platform, target_id)
    risk = _thread_risk(classifications, events, intents)
    if risk:
        return ReplyPlan("", "+".join(intents) or "manual_review", False, risk, event_ids, target_id, _comment_id(events))
    reply = _combined_rule_reply(platform, combined_text, intents, history, state)
    if not reply:
        return ReplyPlan("", "+".join(intents) or "manual_review", False, "no_safe_reply", event_ids, target_id, _comment_id(events))
    polished = await _polish_with_ai(reply, combined_text, history, intents)
    final_reply = polished if _safe_polish(polished) else reply
    await _update_thread_state(platform, target_id, intents, final_reply)
    return ReplyPlan(final_reply, "+".join(intents), True, None, event_ids, target_id, _comment_id(events))


def _classify(platform: str, text: str, media_flag: bool) -> Classification:
    if platform == "facebook_comment":
        return classify_comment(text)
    return classify_message(text, media_flag=media_flag, platform=platform)


def _expand_freeform_intent(intent: str, text: str) -> str:
    lower = text.lower()
    if any(x in lower for x in ("training", "ট্রেনিং", "শিখ", "experience nai", "অভিজ্ঞতা নাই", "new")):
        return "training"
    if any(x in lower for x in ("থাকতে", "থাকা", "accommodation", "mess", "মেস")):
        return "accommodation"
    if any(x in lower for x in ("কষ্ট", "hard", "difficult", "পারবো")):
        return "difficulty"
    if any(x in lower for x in ("promotion", "প্রমোশন", "বেতন বাড়", "future", "office job", "supervisor")):
        return "career_growth"
    if any(x in lower for x in ("experience", "অভিজ্ঞতা")):
        return "experience"
    return intent


def _ordered_unique(intents: list[str]) -> list[str]:
    values = []
    for intent in _INTENT_PRIORITY:
        if intent in intents and intent not in values:
            values.append(intent)
    for intent in intents:
        if intent not in values:
            values.append(intent)
    return values


def _thread_risk(classifications: list[Classification], events: list[dict], intents: list[str]) -> str | None:
    for cls, event in zip(classifications, events):
        reason = risk_reason(cls, media_flag=bool(event.get("media_flag")), text=str(event.get("message_text") or ""))
        if reason and reason not in _SAFE_FREEFORM:
            return reason
    if "payment_issue" in intents:
        return "payment_issue"
    return None


def _combined_rule_reply(platform: str, text: str, intents: list[str], history: list[dict], state: dict | None) -> str:
    already = set((state or {}).get("answered_intents") or [])
    parts: list[str] = []
    if "greeting" in intents and not already:
        parts.append("আসসালামু আলাইকুম। আল-আকসা সিকিউরিটি সার্ভিসে যোগাযোগ করার জন্য ধন্যবাদ।")
    if "interested" in intents and "applicant_info_complete" not in intents and "interested" not in already:
        parts.append(rules.INTERESTED_REPLY)
    if "applicant_info_complete" in intents:
        parts.append(rules.APPLICANT_INFO_RECEIVED_REPLY)
    if "salary" in intents and "salary" not in already:
        parts.append(rules.SALARY_REPLY)
    if "salary_objection" in intents and "salary_objection" not in already:
        parts.append("এটি প্রশিক্ষণকালীন বেতন। অভিজ্ঞতা ও দক্ষতা বাড়লে বেতনও বৃদ্ধি পায়। ভবিষ্যতে অফিসিয়াল ও উচ্চ পদে কাজের সুযোগ থাকে।")
    if "location" in intents and "location" not in already:
        parts.append(rules.LOCATION_REPLY)
    if "job_details" in intents and "job_details" not in already:
        lower = text.lower()
        parts.append(rules.SHIP_CLARIFICATION_REPLY if any(x in lower for x in ("ship", "জাহাজ", "সাগর", "পানি")) else rules.JOB_DETAILS_REPLY)
    if "join_process" in intents and "join_process" not in already:
        parts.append(rules.INTERESTED_REPLY)
    if "documents" in intents and "documents" not in already:
        parts.append(rules.DOCUMENTS_REPLY)
    if "fees" in intents and "fees" not in already:
        parts.append(rules.FEES_REPLY)
    if "age_issue" in intents and "age_issue" not in already:
        extracted_age = _extract_age(text)
        if extracted_age is not None and (
            extracted_age < rules.MIN_AGE or extracted_age > rules.MAX_AGE
        ):
            parts.append(rules.AGE_OUT_OF_RANGE_REPLY)
        else:
            parts.append(rules.AGE_ISSUE_REPLY)
    if "training" in intents or "experience" in intents:
        parts.append("অভিজ্ঞতা না থাকলেও সমস্যা নেই। কাজের নিয়ম, দায়িত্ব ও নিরাপত্তা বিষয়ে প্রয়োজন অনুযায়ী ট্রেনিং/গাইডলাইন দেওয়া হয়, তাই মনোযোগ দিয়ে শিখলে কাজ বুঝে নেওয়া সম্ভব।")
    if "recruitment_follow_up" in intents and "recruitment_follow_up" not in already:
        parts.append("চাকরির বিষয়ে আপনার আগ্রহের জন্য ধন্যবাদ। কোন তথ্যটি জানতে চান লিখলে আমরা নিয়োগ সংক্রান্ত তথ্য দিয়ে সহায়তা করব।")
    if "accommodation" in intents:
        parts.append("কাজের ধরন অনুযায়ী কোম্পানি যেখানে থাকার ব্যবস্থা করবে সেখানে থাকতে হতে পারে। নিজের প্রয়োজনীয় কাপড়চোপড় ও কাগজপত্র নিয়ে আসা ভালো।")
    if "difficulty" in intents:
        parts.append("কাজে দায়িত্বশীলতা ও নিয়ম মানা জরুরি। প্রথম দিকে নতুন মনে হতে পারে, তবে ট্রেনিং ও অভ্যাসের মাধ্যমে ধীরে ধীরে কাজ সহজ হয়ে যায়।")
    if "career_growth" in intents:
        parts.append("ভালো পারফরম্যান্স, নিয়মিত দায়িত্ব পালন ও অভিজ্ঞতা বাড়লে ভবিষ্যতে ভালো সুযোগ তৈরি হতে পারে। তবে পদোন্নতি বা বেতন বৃদ্ধি কাজের মান, প্রয়োজন ও কর্তৃপক্ষের সিদ্ধান্তের ওপর নির্ভর করে।")
    if not parts and platform == "facebook_comment":
        cls = classify_comment(text)
        parts.append(generate_comment_reply(cls, text=text))
    if not parts and len(history) > 1:
        parts.append("আপনার আগের কথার ধারাবাহিকতায় বলছি, কোন বিষয়টি জানতে চান একটু নির্দিষ্ট করে লিখলে আমরা সঠিকভাবে জানাতে পারব।")
    if not parts:
        cls = classify_message(text, platform=platform)
        parts.append(generate_reply(cls, text=text, platform=platform))
    reply = _dedupe_paragraphs("\n\n".join(p for p in parts if p).strip())
    if already and reply:
        reply = _shorten_repeat(reply, already, intents)
    return reply


async def _polish_with_ai(rule_reply: str, current_text: str, history: list[dict], intents: list[str]) -> str:
    if os.getenv("SOCIAL_AI_REPLY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return ""
    history_text = "\n".join(f"{row.get('direction')}: {row.get('text')}" for row in reversed(history[-12:]))
    business_facts = (
        f"Company: {rules.COMPANY_NAME}\n"
        f"Office: {rules.OFFICE_ADDRESS}\n"
        f"Office time: {rules.OFFICE_TIME}\n"
        "Salary: training 10,000-15,000 taka; probation/afterward 12,000-18,000 taka depending on role/responsibility.\n"
        "Fees: processing 350 taka; training/joining 3,500 taka; mess/food advance may be 1,000 taka.\n"
        f"Contacts: {rules.OFFICE_NUMBERS}\n"
    )
    prompt_context = (
        "Approved business facts only:\n" + business_facts +
        "Communication guidance: answer all current questions together, avoid repeating full answers already given, be respectful, honest, positive, and never guarantee job/salary/promotion.\n"
        f"Detected intents: {', '.join(intents)}\n"
        f"Thread history:\n{history_text}\n\n"
        f"Current messages:\n{current_text[:800]}\n\n"
        f"Safe draft to improve without changing facts:\n{rule_reply[:1200]}"
    )
    try:
        from app.ollama import generate_reply as ai_generate_reply
        return await ai_generate_reply(
            user_message=current_text,
            intent="recruitment",
            db_context=prompt_context,
            history=history_text,
            role="new_lead",
        )
    except (RuntimeError, ValueError, OSError):
        return ""
    except Exception:
        return ""


def _safe_polish(text: str) -> bool:
    if not text or len(text) > 1400:
        return False
    if "আপনার বার্তা পেয়েছি" in text:
        return False
    poison = ("chain_of_thought", "AI", "এআই", "prompt", "ডেটাবেস", "model", "reasoning")
    if any(p.lower() in text.lower() for p in poison):
        return False
    numbers = set(re.findall(r"\b\d{3,6}\b", text.replace(",", "")))
    allowed = {"350", "1000", "3500", "10000", "12000", "15000", "18000", "01958", "122311", "122327", "011004669"}
    return numbers.issubset(allowed)


def _dedupe_paragraphs(text: str) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        key = re.sub(r"\s+", " ", para.lower())[:120]
        if key not in seen:
            seen.add(key)
            out.append(para)
    return "\n\n".join(out)


def _shorten_repeat(reply: str, already: set[str], intents: list[str]) -> str:
    if all(intent in already for intent in intents if intent not in {"greeting"}):
        return "আগের তথ্যের ধারাবাহিকতায় বলছি, বিস্তারিত একই থাকবে। নতুন কোনো নির্দিষ্ট প্রশ্ন থাকলে লিখুন, আমরা সেই অনুযায়ী জানাব।"
    return reply


async def _update_thread_state(platform: str, target_id: str, intents: list[str], reply: str) -> None:
    await execute(
        """
        INSERT INTO social_thread_state (platform, target_id, answered_intents, last_reply_text, context_summary, updated_at)
        VALUES ($1,$2,$3::text[],$4,$5::jsonb,NOW())
        ON CONFLICT (platform, target_id) DO UPDATE SET
            answered_intents = ARRAY(SELECT DISTINCT unnest(social_thread_state.answered_intents || EXCLUDED.answered_intents)),
            last_reply_text = EXCLUDED.last_reply_text,
            context_summary = EXCLUDED.context_summary,
            updated_at = NOW()
        """,
        platform,
        target_id,
        intents,
        reply[:1200],
        json.dumps({"last_intents": intents}),
    )


def _comment_id(events: list[dict]) -> str | None:
    first = events[0]
    return first.get("comment_id") if first.get("platform") == "facebook_comment" else None
