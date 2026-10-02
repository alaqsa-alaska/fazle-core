"""
Fazle Core — Media placeholder vs. genuine text.
================================================

``modules.bridge_poller._fetch_new_messages`` fills a synthetic placeholder
``text = "[media:<type>]"`` (e.g. ``[media:audio]``, ``[media:image]``,
``[media:document]``, ``[media:video]``) for an inbound media message that
carries no real caption/``content`` and no bridge-side OCR/STT
(``processed_text``). That string is a transport marker, **not** something the
sender wrote — it must never be routed to the intent classifier, RAG, the
semantic gate, Hermes, or business-reply generation, and it must not satisfy a
``if text:`` "we already have text" check.

``has_meaningful_text`` is the one predicate callers should use to decide
"does this message carry genuine, actionable text?" — genuine sender text or
real extracted OCR/STT text both count; a bare ``[media:*]`` placeholder does
not.
"""

import re

# Matches ONLY the exact synthetic marker _fetch_new_messages emits:
# f"[media:{media_type}]" with media_type already lower-cased/stripped.
_MEDIA_PLACEHOLDER_RE = re.compile(r"^\[media:[a-z0-9_-]+\]$", re.IGNORECASE)


def is_media_placeholder(text: str | None) -> bool:
    """True iff ``text`` is exactly a synthetic ``[media:<type>]`` transport
    placeholder (nothing else on the line)."""
    if not text:
        return False
    return bool(_MEDIA_PLACEHOLDER_RE.fullmatch(text.strip()))


def has_meaningful_text(text: str | None) -> bool:
    """True iff ``text`` carries genuine, routable content — real sender text
    or real extracted OCR/STT text. A blank string or a bare ``[media:*]``
    placeholder is not meaningful."""
    return bool(text and text.strip()) and not is_media_placeholder(text)
