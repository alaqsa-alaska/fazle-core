"""Context-aware reply planning with strict business fact boundaries."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass

from app.database import execute

from modules.identity_brain import detect_identity

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
    # 2026-08-03 (Owner decision, Phase 4): True when reply_text came from
    # the AI-polish step (not the rule-based fallback) — the caller
    # (planner_worker.py) must route this to the existing draft/admin-
    # approval mechanism instead of a real auto-send, same as it already
    # does for DRAFT_ALWAYS_* senders. auto_send stays True either way —
    # auto_send=False means "skip/ignore entirely" (a different, existing
    # meaning), not "needs review."
    needs_review: bool = False
    # 2026-08-04 (Owner decision): human-readable reason this plan needs a
    # decision -- either _safe_polish()'s exact rejection reason, or "why
    # no safe automated reply exists" for the no-safe-reply/risk-flagged
    # paths. None when no review is needed. Carried through to the admin
    # notification so the Admin sees *why*, not just *that*, review is
    # needed -- see planner_worker.py's _notify_admin_flagged / the
    # extended _draft_instead_of_send.
    validation_reason: str | None = None
    # 2026-08-05 (Owner decision, provider-chain change): the AI-generated
    # text that _safe_polish() rejected (None if polish never ran, e.g.
    # never_ai_polish intents, or if it succeeded). The Admin's review
    # notification shows this alongside the safe rule-based reply_text
    # that actually gets sent, so a reviewer can judge whether the
    # rejection was correct.
    rejected_ai_reply: str | None = None
    # 2026-08-05: raw per-provider attempt trace (app.llm.get_last_attempts())
    # captured whenever polish was attempted, success or failure -- lets the
    # admin notification show exactly which providers were tried and their
    # outcome, not just a summary reason string.
    provider_attempts: list[dict] | None = None


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
    # 2026-08-03 (Owner-approved persona proposal): resolve sender identity
    # for persona selection, reusing the same identity_brain.detect_identity()
    # message_router already uses for bridge1/bridge2 — no new detection
    # logic, just wiring this previously identity-blind path to it.
    # target_id is a real phone for meta_whatsapp; for messenger/
    # facebook_comment it's a PSID/comment id that won't match any
    # phone-based evidence, so this safely resolves to role="unknown" for
    # those rather than erroring — detect_identity() never raises for a
    # non-phone input, but the try/except is defense-in-depth anyway since
    # this is a new dependency added to a previously self-contained,
    # safety-critical path (mirrors the fire-and-forget-safe pattern
    # modules/memory_extractor already uses elsewhere in this codebase).
    try:
        identity = await detect_identity(target_id, combined_text)
        resolved_role = identity.get("role", "unknown")
    except Exception:
        resolved_role = "unknown"
    persona_text = rules.persona_for_role(resolved_role)

    # 2026-08-03 (Owner decision, Phase 4): topics that must never be sent
    # as AI-polished text, regardless of _safe_polish() outcome — skip the
    # AI-polish attempt entirely and use the pre-approved rule-based reply
    # directly (Owner's explicit "fallback to safe-rules" option). Mapped
    # from the Owner's 3 named categories to real classifier intents:
    # Salary/Compensation -> salary, salary_objection; Cash transactions/
    # Financials -> fees, payment_issue (payment_issue already never
    # reaches this function auto-send-wise, listed for completeness).
    # "Final hiring decisions / job offers" has NO current matching intent
    # in this classifier's 30+ categories (checked directly, not assumed)
    # — this recruitment flow only ever informs/collects info, never
    # announces a hiring decision, so there's nothing to gate here yet.
    # Flagged, not silently ignored.
    never_ai_polish = {"salary", "salary_objection", "fees", "payment_issue"}
    if never_ai_polish.intersection(intents):
        await _update_thread_state(platform, target_id, intents, reply)
        return ReplyPlan(reply, "+".join(intents), True, None, event_ids, target_id, _comment_id(events), needs_review=False)

    polished = await _polish_with_ai(reply, combined_text, history, intents, persona_text, resolved_role)
    # Dynamic office-numbers (2026-08-04, Owner decision — see
    # reply_rules.get_effective_office_numbers()'s docstring): whatever the
    # AI was actually told in business_facts (static default, or a live
    # admin override) must also be what _safe_polish() accepts, or a
    # correct reply quoting the real current number would be wrongly
    # rejected — exactly the bug found during investigation (the old
    # whitelist's 122311/122327 never matched OFFICE_NUMBERS' 122300).
    dynamic_office_numbers = await rules.get_effective_office_numbers()
    is_safe, fail_reason = _safe_polish(polished, extra_allowed=_extract_number_tokens(dynamic_office_numbers))
    # 2026-08-05 (Owner decision, provider-chain change): capture the raw
    # per-provider attempt trace unconditionally, success or failure — the
    # admin-review notification (planner_worker.py) can show exactly which
    # providers were tried and their outcome whenever polish was attempted.
    try:
        from app.llm import get_last_attempts
        provider_attempts = get_last_attempts() or None
    except Exception:
        provider_attempts = None
    if is_safe:
        final_reply = polished
        # 2026-08-03 (Owner decision, Phase 4): AI-polished text must go to
        # the existing admin draft/approval queue, never straight to a real
        # send — SOCIAL_AI_POLISH_REVIEW_REQUIRED (default true) is the
        # per-channel toggle; message_router is a separate, unrelated flag
        # (see shared/reply_policy.py's module docstring) with no
        # equivalent polish/validate step to gate today.
        needs_review = os.getenv("SOCIAL_AI_POLISH_REVIEW_REQUIRED", "true").lower() in {"1", "true", "yes"}
        validation_reason = None
        rejected_ai_reply = None
    else:
        # 2026-08-04 (Owner decision): a validation failure must never be a
        # silent substitution — the safe rule-based reply is still what
        # gets sent (never an unvalidated AI reply), but it now ALWAYS
        # goes through admin review with the exact reason attached,
        # instead of auto-sending unreviewed as before.
        final_reply = reply
        needs_review = True
        validation_reason = fail_reason
        rejected_ai_reply = polished or None
        # 2026-08-05: _safe_polish() rejects app.llm.generate_reply()'s own
        # all-providers-exhausted boilerplate the same way it rejects any
        # other unsafe text (it IS the same literal string check) — give
        # that specific case a clearer summary reason (the raw trace is
        # still separately available via provider_attempts above).
        if fail_reason and "generic acknowledgement phrase" in fail_reason and provider_attempts:
            validation_reason = "All AI providers exhausted — " + "; ".join(
                f"{a['provider']}({a['model']}): {a['outcome']}" + (f" [{a['detail']}]" if a['detail'] else "")
                for a in provider_attempts
            )
    await _update_thread_state(platform, target_id, intents, final_reply)
    return ReplyPlan(
        final_reply, "+".join(intents), True, None, event_ids, target_id, _comment_id(events),
        needs_review=needs_review, validation_reason=validation_reason,
        rejected_ai_reply=rejected_ai_reply, provider_attempts=provider_attempts,
    )


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
        parts.append("আসসালামু আলাইকুম। Al-Rifa'i Integrated Services-এ যোগাযোগ করার জন্য ধন্যবাদ।")
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


# Maps an identity_brain role to a shared/reply_policy.py ROLE_PROMPTS key,
# so the generic role_hint line that builder injects (its own
# "## ভূমিকা (Role)" section) stays sensible rather than silently falling
# back to its "new_lead" default for everything (same vocabulary-mismatch
# bug fixed directly in reply_policy.py's own ROLE_PROMPTS — see the
# 2026-08-03 proposal doc). Now mostly 1:1 since reply_policy.py's
# ROLE_PROMPTS was expanded to cover identity_brain's full vocabulary, kept
# as an explicit local dict (not imported from reply_policy.py, which this
# module must not import per its own docstring boundary) so this module
# doesn't silently break if that module's key set ever changes.
_REPLY_POLICY_COMPAT_ROLE: dict[str, str] = {
    "vip_client": "vip_client",
    "client_escort_buyer": "client_escort_buyer",
    "repeat_client": "repeat_client",
    "vendor": "vendor",
    "employee": "employee",
    "supervisor": "supervisor",
    "accountant": "accountant",
    "internal_coordinator": "internal_coordinator",
    "escort": "escort",
    "security_guard": "security_guard",
    "admin": "admin",
    "candidate": "candidate",
    "family": "known_contact",
    "unknown": "new_lead",
}


async def _polish_with_ai(
    rule_reply: str, current_text: str, history: list[dict], intents: list[str],
    persona_text: str = "", resolved_role: str = "unknown",
) -> str:
    if os.getenv("SOCIAL_AI_REPLY_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        return ""
    history_text = "\n".join(f"{row.get('direction')}: {row.get('text')}" for row in reversed(history[-12:]))
    # 2026-08-04 (Owner decision): dynamic office-numbers lookup — see
    # reply_rules.get_effective_office_numbers()'s docstring. Whatever this
    # tells the model is also what plan_reply()'s _safe_polish() call
    # accepts (passed as extra_allowed there), so the two never disagree.
    office_numbers = await rules.get_effective_office_numbers()
    business_facts = (
        f"Company: {rules.COMPANY_NAME}\n"
        f"Office: {rules.OFFICE_ADDRESS}\n"
        f"Office time: {rules.OFFICE_TIME}\n"
        "Salary: training 10,000-15,000 taka; probation/afterward 12,000-18,000 taka depending on role/responsibility.\n"
        "Fees: processing 350 taka; training/joining 3,500 taka; mess/food advance may be 1,000 taka.\n"
        f"Contacts: {office_numbers}\n"
    )
    # 2026-08-03 (Phase 3, Owner-approved proposal): additive KB enrichment —
    # same safe_search() call modules/message_router already uses for its
    # own AI-fallback path (modules/message_router/__init__.py, "Phase 4 —
    # Hybrid RAG enrichment"), not a new retrieval system. business_facts
    # above stays as the floor; KB hits are appended on top, never replace
    # it. safe_search() is timeout-wrapped and exception-safe (empty list
    # on failure/timeout), so this can never block or crash reply
    # generation — same defensive pattern as that existing call site.
    kb_context = ""
    try:
        from modules.rag import safe_search
        kb_hits = await safe_search(current_text, k=3, role=resolved_role)
        kb_lines = [
            f"- {h.get('title', '')}: {(h.get('text') or '').strip()[:300]}"
            for h in kb_hits
            if (h.get("text") or "").strip()
        ]
        if kb_lines:
            kb_context = "\nKB Context:\n" + "\n".join(kb_lines) + "\n"
    except Exception:
        kb_context = ""
    persona_line = f"Persona (how to speak): {persona_text}\n" if persona_text else ""
    prompt_context = (
        "Approved business facts only:\n" + business_facts + kb_context +
        persona_line +
        "Communication guidance: answer all current questions together, avoid repeating full answers already given, be honest and positive, and never guarantee job/salary/promotion.\n"
        f"Detected intents: {', '.join(intents)}\n"
        f"Thread history:\n{history_text}\n\n"
        f"Current messages:\n{current_text[:800]}\n\n"
        f"Safe draft to improve without changing facts:\n{rule_reply[:1200]}"
    )
    try:
        # 2026-08-05 (Owner decision): routed through app.llm's full
        # provider chain (OmniRoute -> Groq -> local Ollama/Hermes -> Kimi)
        # instead of calling app.ollama directly, so AI-polish inherits the
        # new chain automatically with no duplicated fallback logic here.
        # app.llm.generate_reply() never raises (catches everything
        # internally) -- this try/except is kept as defense-in-depth only.
        from app.llm import generate_reply as ai_generate_reply
        return await ai_generate_reply(
            user_message=current_text,
            intent="recruitment",
            db_context=prompt_context,
            history=history_text,
            role=_REPLY_POLICY_COMPAT_ROLE.get(resolved_role, "new_lead"),
        )
    except (RuntimeError, ValueError, OSError):
        return ""
    except Exception:
        return ""


# Salary/fee figures the AI is explicitly given in _polish_with_ai()'s
# business_facts (kept in sync with that string, and independently
# verified 2026-08-04 against knowledge_base/04_business_rules/
# salary_business_rules.md — these are correct, unlike the office-number
# entries that used to live in this set; see reply_rules.OFFICE_NUMBERS's
# comment for that history). ADM_WHATSAPP (011004669) kept as before —
# not part of business_facts, provenance predates this investigation,
# left untouched since the Owner's 2026-08-04 decision only addressed
# office numbers, not this one.
_ALLOWED_POLISH_NUMBERS = frozenset({"350", "1000", "3500", "10000", "12000", "15000", "18000", "011004669"})


def _extract_number_tokens(text: str) -> set[str]:
    """3-6 digit runs from `text`, Bangla-script numerals (০-৯) normalized
    to ASCII first. Shared by _safe_polish() and plan_reply()'s dynamic-
    office-number lookup so both use identical tokenization."""
    normalized = (text or "").translate(_BANGLA_DIGITS)
    return set(re.findall(r"\b\d{3,6}\b", normalized.replace(",", "")))


def _safe_polish(text: str, extra_allowed: set[str] | None = None) -> tuple[bool, str | None]:
    """Returns (is_safe, reason). reason is None when is_safe is True,
    otherwise a human-readable explanation suitable for an admin
    notification (2026-08-04 Owner decision — see plan_reply()'s caller
    for how this reason now reaches the Admin instead of being silently
    discarded).

    extra_allowed: additional allowed number tokens beyond
    _ALLOWED_POLISH_NUMBERS (e.g. the current dynamic office-numbers list)
    — merged in by the caller so this function itself doesn't need to
    know how numbers are sourced.

    2026-08-04 fix: numbers are now Bangla-numeral-aware (via
    _extract_number_tokens's translation) — the previous ASCII-only regex
    never matched ০-৯ digits at all, silently letting any Bangla-script
    number through unchecked (which is what every rule-based reply
    template in reply_rules.py actually uses), while being strict about
    ASCII digits the model might emit instead.
    """
    if not text:
        return False, "AI-polish returned empty text"
    if len(text) > 1400:
        return False, f"AI-polished reply too long ({len(text)} chars, max 1400)"
    if "আপনার বার্তা পেয়েছি" in text:
        return False, "AI-polished reply repeated the generic acknowledgement phrase"
    poison = ("chain_of_thought", "AI", "এআই", "prompt", "ডেটাবেস", "model", "reasoning")
    lowered = text.lower()
    hit = next((p for p in poison if p.lower() in lowered), None)
    if hit:
        return False, f"AI-polished reply contained a disallowed term: {hit!r}"
    numbers = _extract_number_tokens(text)
    allowed = set(_ALLOWED_POLISH_NUMBERS)
    if extra_allowed:
        allowed |= extra_allowed
    unexpected = numbers - allowed
    if unexpected:
        return False, f"AI-polished reply contained unapproved number(s): {', '.join(sorted(unexpected))}"
    return True, None


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
