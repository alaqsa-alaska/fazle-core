"""Detect automated OTP / login-code / platform system notifications.

BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02 (owner amendment): the re-pair
incident had a Facebook confirmation code ("99015 is your Facebook
confirmation code") and a WhatsApp/Instagram account-linking notice
("Your WhatsApp account is successfully connected …") turned into a
recruitment session + a wbom_candidates row + an auto-reply, because on
Bridge3 an unknown sender defaults to the recruitment funnel.

These messages are never a job applicant, an employee, or a business
action. `is_automated_system_message()` recognises them so the bridge
poller can preserve them as raw forensic rows (provenance intact) while
skipping identity resolution, routing, session/candidate creation and any
reply.

Design: a deterministic, deliberately TIGHT allowlist. Prefer a
false-negative (let an odd message route normally) over a false-positive
(suppress a real person). Every pattern is anchored to phrasing that only
ever appears in an automated transactional/system message. No LLM, no
network, pure function.
"""
from __future__ import annotations

import re

# Bengali-digit tolerance: transliterate so "৯৯০১৫ is your ..." also matches.
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# Each entry is a compiled, case-insensitive regex. A match anywhere in the
# (digit-normalised, whitespace-collapsed) message body suppresses it.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "<code> is your <Service> (confirmation|verification|login|security) code"
    # — Facebook / Instagram / WhatsApp / Google / Microsoft / generic.
    re.compile(r"\b\d{3,8}\b[^.\n]{0,40}\bis your\b[^.\n]{0,40}\bcode\b", re.I),
    # "your <Service> code is <code>" / "verification code: <code>"
    re.compile(r"\b(verification|confirmation|login|security|one[- ]?time)\b[^.\n]{0,25}\bcode\b[^.\n]{0,15}\b\d{3,8}\b", re.I),
    re.compile(r"\byour\b[^.\n]{0,30}\bcode is\b[^.\n]{0,10}\b\d{3,8}\b", re.I),
    # "<code> is your one-time password" / OTP
    re.compile(r"\b\d{3,8}\b[^.\n]{0,30}\bis your\b[^.\n]{0,20}\b(one[- ]?time password|otp)\b", re.I),
    re.compile(r"\b(otp|one[- ]?time password)\b[^.\n]{0,20}\b\d{3,8}\b", re.I),
    # WhatsApp / Instagram / Facebook account-link & security notifications
    re.compile(r"\byour whatsapp account is (successfully )?(connected|linked|registered)\b", re.I),
    re.compile(r"\byour (facebook|instagram|meta) account\b[^.\n]{0,40}\b(connected|linked|logged in|new login)\b", re.I),
    re.compile(r"\bdon'?t share (it|this code)\b.*\b(code|password)\b", re.I),
    re.compile(r"\b(we noticed a new login|new login to your account|was your login attempt)\b", re.I),
    # Explicit "do not reply / automated message" self-labels paired with a code
    re.compile(r"\bthis is an automated message\b.*\b\d{3,6}\b", re.I),
)

def is_automated_system_message(text: str | None) -> bool:
    """True when `text` is an automated OTP / login-code / platform system
    notification that must never create a candidate/session or get a reply.

    Deliberately does NOT treat a bare numeric string ("1998", "25") as a
    system message — that could be a real person's answer. Only the explicit
    "<code> is your … code" / account-notification phrasings match.
    """
    if not text:
        return False
    norm = text.translate(_BN_DIGITS)
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return False
    return any(p.search(norm) for p in _PATTERNS)
