"""Generate approved replies from deterministic classification results."""
from __future__ import annotations

import re
import unicodedata

from .classifier import Classification
from . import reply_rules as rules

_BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _extract_age_simple(text: str) -> int | None:
    """Extract a numeric age from text for BR-25 enforcement."""
    normalized = unicodedata.normalize("NFC", text or "")
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


def generate_reply(classification: Classification, *, text: str, platform: str = "") -> str:
    intent = classification.intent
    if intent == "greeting":
        return rules.WELCOME_REPLY
    if intent == "interested":
        return rules.INTERESTED_REPLY
    if intent == "salary":
        return rules.SALARY_REPLY
    if intent == "location":
        return rules.LOCATION_REPLY
    if intent == "age_issue":
        age = _extract_age_simple(text)
        if age is not None and (age < rules.MIN_AGE or age > rules.MAX_AGE):
            return rules.AGE_OUT_OF_RANGE_REPLY
        return rules.AGE_ISSUE_REPLY
    if intent == "documents":
        return rules.DOCUMENTS_REPLY
    if intent == "fees":
        return rules.FEES_REPLY
    if intent == "applicant_info_complete":
        return rules.APPLICANT_INFO_RECEIVED_REPLY
    if intent == "job_details":
        lower = (text or "").lower()
        if any(term in lower for term in ("জাহাজে নাকি", "ship e naki", "পানিতে", "সাগরে", "ship না office", "ship e", "office e")):
            return rules.SHIP_CLARIFICATION_REPLY
        return rules.JOB_DETAILS_REPLY
    return ""


def generate_comment_reply(classification: Classification, *, text: str) -> str:
    if classification.intent == "interested":
        return rules.COMMENT_INTERESTED_REPLY
    if classification.intent == "salary":
        return rules.SALARY_REPLY
    if classification.intent == "location":
        return rules.LOCATION_REPLY
    if classification.intent == "job_details":
        return rules.SHIP_CLARIFICATION_REPLY
    return generate_reply(classification, text=text, platform="facebook_comment")
