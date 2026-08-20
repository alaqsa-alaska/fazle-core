"""
Shared salam-detection helper (2026-08-20, Owner-directed template audit).

Root cause of a real regression (draft 8801...1596 reply to "Uthlam ghum
theke", plus historical repeat_client/social_auto_reply drafts): several
reply-generation paths prepended a salam greeting unconditionally whenever
a message was classified "greeting"/"family-personal", rather than gating
it on whether the sender's own message actually contained a salam pattern
first. This one small helper is the single source of truth for that gate
-- used by modules/message_router (family-role deflection reply) and
modules/social_auto_reply/intelligent_generator (recruitment greeting
reply) so the two paths can't drift out of sync with each other again.

Deliberately narrow: this is a content check, not a template. It answers
one question -- "did the sender say something salam-shaped?" -- and lets
each caller decide what to do with that answer. It does not itself choose
or generate a reply.
"""
from __future__ import annotations

_SALAM_PATTERNS = (
    "আসসালামু আলাইকুম",
    "আস্সালামু আলাইকুম",
    "আসসালামুয়ালাইকুম",
    "আস্সালামুয়ালাইকুম",
    "আস-সালামু",
    "আসসালামু",
    "আসসালাম",
    "সালামু আলাইকুম",
    "assalamu alaikum",
    "assalamualaikum",
    "assalam",
    "salam",
)


def sender_used_salam(text: str) -> bool:
    """True if `text` (the sender's own raw message, not any generated
    reply) contains a salam-shaped greeting. Empty/None text -> False, same
    as every existing "no signal -> don't force it" convention in this
    codebase (e.g. modules/recruitment_flow's age-gate). Case-insensitive;
    substring match, not a full-message match, since real messages mix a
    salam with other content ("আসসালামু আলাইকুম ভাই, চাকরির খবর কি")."""
    if not text:
        return False
    lower = text.lower()
    return any(pattern.lower() in lower for pattern in _SALAM_PATTERNS)
