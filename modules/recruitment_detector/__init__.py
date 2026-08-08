"""
modules/recruitment_detector — 3-layer recruitment intent detection.

Enhances (does not replace) modules.recruitment_flow.recruitment_eligibility()'s
existing single-keyword check (`is_recruitment_trigger`), which only catches an
exact substring from a flat keyword set and misses natural phrasing like
"ভাই সুযোগ দেন" or "আমি available".

Design rules (verified against real code before implementation, not guessed):
  - READ ONLY — never writes to any table.
  - Layer 2 does NOT make a second LLM call. message_router already computes
    intent once per message via ai.classify_intent_llm() before recruitment
    eligibility is ever checked (modules/message_router/__init__.py:569) —
    this layer just reuses that result. Firing a second LLM round-trip per
    message for the same classification would double LLM cost/latency for
    every inbound message system-wide, not just recruitment ones.
  - Layer 3 reuses two existing, already-tested functions instead of new raw
    SQL: recruitment_flow.get_active_session() (real semantics: funnel_stage
    in ('collecting','new'), TTL-expiring — verified by reading it directly,
    NOT the 'is_complete' column the original spec assumed, which does not
    exist on fazle_recruitment_sessions) and message_router.get_recent_history()
    (verified real columns: wbom_whatsapp_messages.sender_number/message_body/
    received_at/direction — NOT sender/message_text/created_at as originally
    assumed).
  - Never raises exceptions — returns is_recruitment=False on any error.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("fazle.recruitment_detector")

# ──────────────────────────────────────────────────────────────────────────
# LAYER 1: ENTITY SIGNAL DICTIONARY
# Bangla + English phrases grouped by recruitment-signal category. Each
# category match = 1 point. Threshold: 2 distinct categories = detected.
#
# self_introduction_for_work is a WEAK category — it only counts if at
# least one STRONG category also matched (prevents false positives from
# "আমার নাম রহিম" alone, which appears in all sorts of unrelated messages).
# ──────────────────────────────────────────────────────────────────────────

ENTITY_SIGNALS: dict[str, tuple[str, ...]] = {
    "seeking_work": (
        "কাজ খুঁজছি", "কাজ দরকার", "কাজ পাবো", "কাজ করতে চাই",
        "চাকরি খুঁজছি", "চাকরি দরকার", "চাকরি পাবো", "চাকরি করতে চাই",
        "job খুঁজছি", "job দরকার", "job করতে চাই",
        "কাজ লাগবে", "চাকরি লাগবে",
        "looking for work", "looking for job", "need work", "need job",
        "want to work", "want to join",
    ),
    "availability": (
        "আমি available", "এখন free", "কাজ নেই", "বেকার আছি",
        "ফ্রি আছি", "খালি আছি", "এখন বেকার",
        "currently free", "currently available", "no job now",
        "আমি বসে আছি", "কিছু করছি না",
    ),
    "experience_mention": (
        "বছর কাজ করেছি", "বছরের experience", "আগে কাজ করতাম",
        "আগে ছিলাম", "experience আছে", "অভিজ্ঞতা আছে",
        "trained আছি", "training আছে", "আমি trained",
        "security তে ছিলাম", "গার্ড ছিলাম", "আনসার ছিলাম",
        "army ছিলাম", "police ছিলাম",
        "i have experience", "years of experience", "worked before",
    ),
    "qualification_mention": (
        "ssc পাস", "hsc পাস", "certificate আছে",
        "সার্টিফিকেট আছে", "পাস করেছি",
        "ssc pass", "hsc pass", "have certificate",
    ),
    "opportunity_ask": (
        "সুযোগ আছে", "সুযোগ দিবেন", "সুযোগ পাবো", "সুযোগ দেন",
        "vacancy আছে", "opening আছে", "post আছে",
        "নেওয়া হচ্ছে", "recruit করছেন", "লোক নিচ্ছেন",
        "any vacancy", "any opening", "are you hiring",
        "do you need", "kono kaj ache", "kono chance ache",
    ),
    "reference_or_referral": (
        "পাঠিয়েছে", "বলেছে আসতে", "refer করেছে", "পরিচয় দিয়েছে",
        "ভাই বলেছে", "স্যার বলেছে", "আপনার নম্বর দিয়েছে",
        "someone referred", "was told to contact", "was asked to call",
    ),
    "salary_information": (
        "বেতন কত", "বেতন কতো", "salary কত", "salary কতো",
        "beton koto", "beton kotoo", "salary amount", "monthly salary",
    ),
    "duty_information": (
        "ডিউটি কী", "ডিউটি কি", "ডিউটি কতদিন", "ডিউটি কতোদিন",
        "duty কী", "duty কি", "duty কতদিন", "duty কতোদিন",
        "what is escort duty", "how long is escort duty",
    ),
    "self_introduction_for_work": (
        "আমার নাম", "আমি থাকি", "আমার বয়স",
        "আমার address", "আমার phone",
    ),
}

# DECISIVE categories are unambiguous enough on their own — a single match
# triggers detection (matches how the existing flat is_recruitment_trigger()
# keyword check already behaves: one keyword is enough). SUPPORTING
# categories are individually ambiguous (e.g. "আমার বয়স ২৫" alone appears in
# lots of non-recruitment contexts) and only count combined with something
# else. WEAK categories never trigger alone under any circumstance.
DECISIVE_CATEGORIES = {
    "seeking_work", "opportunity_ask", "reference_or_referral",
    "salary_information", "duty_information",
}
SUPPORTING_CATEGORIES = {"availability", "experience_mention", "qualification_mention"}
WEAK_CATEGORIES = {"self_introduction_for_work"}
ENTITY_THRESHOLD = 2  # combined SUPPORTING/WEAK categories needed when no DECISIVE match


def _score_entities(message: str) -> tuple[int, list[str]]:
    """
    Score message against entity signal categories.
      - Any single DECISIVE category match -> immediate detection.
      - Otherwise, SUPPORTING categories combine (>=2 needed); WEAK
        categories only count on top of at least one SUPPORTING match.
    Returns (score, matched_categories). A DECISIVE match always returns a
    score >= ENTITY_THRESHOLD so callers using a plain ">= ENTITY_THRESHOLD"
    check work correctly either way.
    """
    message_lower = (message or "").lower()
    matched: dict[str, list[str]] = {"decisive": [], "supporting": [], "weak": []}

    for category, signals in ENTITY_SIGNALS.items():
        for signal in signals:
            if signal.lower() in message_lower:
                if category in DECISIVE_CATEGORIES:
                    bucket = "decisive"
                elif category in WEAK_CATEGORIES:
                    bucket = "weak"
                else:
                    bucket = "supporting"
                if category not in matched[bucket]:
                    matched[bucket].append(category)
                break  # one match per category is enough

    if matched["decisive"]:
        effective = matched["decisive"] + matched["supporting"] + matched["weak"]
        return max(len(effective), ENTITY_THRESHOLD), effective

    effective = list(matched["supporting"])
    if effective:
        effective.extend(matched["weak"])
    return len(effective), effective


# ──────────────────────────────────────────────────────────────────────────
# LAYER 3: CONVERSATION CONTEXT — reuses existing helpers, no new SQL.
# ──────────────────────────────────────────────────────────────────────────

TREND_MIN_ENTITY_SCORE = 3


async def _check_context(phone: str) -> Optional[dict]:
    """Active session or recent-message trending. Never raises."""
    try:
        from modules.recruitment_flow import get_active_session

        session = await get_active_session(phone)
        if session:
            return {
                "is_recruitment": True,
                "confidence": 0.9,
                "reason": f"active_session:{session.get('collection_step')}",
            }

        from modules.message_router import get_recent_history

        recent = await get_recent_history(phone, limit=5)
        if not recent:
            return None

        combined = " ".join(recent)
        entity_score, categories = _score_entities(combined)
        if entity_score >= TREND_MIN_ENTITY_SCORE:
            return {
                "is_recruitment": True,
                "confidence": 0.75,
                "reason": f"trending_history: entity_score={entity_score} cats={categories}",
            }
        return None
    except Exception as exc:
        log.debug("recruitment_detector: context check failed for %s: %s", phone[-4:] if phone else "?", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────

async def detect_recruitment_intent(
    *,
    text: str,
    phone: str,
    intent: str = "unknown",
) -> dict:
    """
    3-layer recruitment detection, additive to the existing keyword trigger
    (modules.recruitment_flow.is_recruitment_trigger), which callers should
    still check first/instead where it already matches.

    intent: the ALREADY-COMPUTED classify_intent_llm() result for this
    message (message_router computes this once per message before
    recruitment eligibility is ever checked — pass that same value in,
    do not reclassify).

    Never raises. Always returns a dict:
      {"is_recruitment": bool, "confidence": float, "layer": str, "reason": str}
    """
    if not text or not text.strip():
        return {"is_recruitment": False, "confidence": 0.0, "layer": "none", "reason": "empty"}

    # Layer 1: entity signals (fast, sync, no DB/LLM call)
    entity_score, categories = _score_entities(text)
    if entity_score >= ENTITY_THRESHOLD:
        log.info("recruitment_detector L1 entity: score=%d cats=%s phone=%s",
                  entity_score, categories, phone[-4:] if phone else "?")
        return {
            "is_recruitment": True,
            "confidence": min(0.95, 0.70 + entity_score * 0.05),
            "layer": "entity",
            "reason": f"entities:{categories}",
        }

    # Layer 2: reuse the intent message_router already computed this turn.
    if (intent or "").strip().lower() == "recruitment":
        log.info("recruitment_detector L2 intent: phone=%s", phone[-4:] if phone else "?")
        return {"is_recruitment": True, "confidence": 0.8, "layer": "intent", "reason": "classify_intent_llm=recruitment"}

    # Layer 3: conversation context (active session, or trending history)
    ctx = await _check_context(phone)
    if ctx and ctx["is_recruitment"]:
        log.info("recruitment_detector L3 context: phone=%s reason=%s",
                  phone[-4:] if phone else "?", ctx["reason"])
        return {"is_recruitment": True, "confidence": ctx["confidence"], "layer": "context", "reason": ctx["reason"]}

    return {"is_recruitment": False, "confidence": 0.0, "layer": "none", "reason": "no_layer_matched"}
