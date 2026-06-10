"""
Fazle Core — Batch 25 (v1.0.1 hotfix)
Draft Quality Gate.

Single source of truth for all `fazle_draft_replies` insert callsites.
Returns (ok, reason) — when not ok, the caller stores the draft with
status='rejected_quality' (or 'rejected_fallback') and meta.quality_reason
instead of leaving it 'pending', so it never reaches an admin's APPROVE list.

Kill-switch: env DRAFT_QUALITY_GATE=false disables all checks (gate passes
everything). Default: enabled.

Adjustments per owner review (Batch 25):
  - LLM fallback match is EXACT string equality, not LIKE wildcard.
  - Numbered-list replies like "[1] ..." are NOT bad signals on their own.
  - Path/dev artifact patterns are conservative and only target obvious leaks.
"""
from __future__ import annotations

import os
import re
from typing import Optional

_EMOJI_RE = re.compile(
    r'[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA9F☀-➿]+'
)


def strip_reply_emoji(text: str) -> str:
    """Remove emoji from LLM-generated reply text before saving or sending."""
    return _EMOJI_RE.sub('', text).strip()

# Exact LLM fallback string emitted by app/ollama.py when generation fails.
# Match must be EXACT (after .strip()) — never a substring/LIKE — so that a
# legitimate reply quoting the phrase is not rejected.
LLM_FALLBACK_EXACT = (
    "আমি এই মুহূর্তে সাড়া দিতে পারছি না। "
    "অনুগ্রহ করে কিছুক্ষণ পরে আবার চেষ্টা করুন।"
)
# Newer (v1.0.1) fallback — also exact-match.
LLM_FALLBACK_EXACT_V2 = "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।"

# Bad patterns indicate developer/path/tool leakage. Conservative list.
# NOTE: `[1]`, `[2]`, etc. (numbered list / RAG citations) are NOT included —
# those are legitimate output formats.
BAD_PATTERNS: tuple[str, ...] = (
    "file://",
    "/home/azim",
    "Created [](",
    "Traceback (most recent call last)",
    "```",
    "<|",
    "/scripts/",
    "/venv/",
)

MAX_DRAFT_LEN = 4000


def _gate_enabled() -> bool:
    v = os.getenv("DRAFT_QUALITY_GATE", "true").strip().lower()
    return v not in ("false", "0", "off", "no")


def check_draft_quality(reply_text: Optional[str]) -> tuple[bool, Optional[str]]:
    """Return (ok, reason). reason is None when ok=True.

    Reason codes (stable; used by metrics + meta.quality_reason):
      - empty
      - llm_fallback
      - bad_pattern:<pattern_prefix>
      - too_long
    """
    if not _gate_enabled():
        return True, None

    if reply_text is None:
        return False, "empty"
    text = reply_text.strip()
    if not text:
        return False, "empty"

    text = strip_reply_emoji(text)
    if not text:
        return False, "empty"

    if text == LLM_FALLBACK_EXACT or text == LLM_FALLBACK_EXACT_V2:
        return False, "llm_fallback"

    for pat in BAD_PATTERNS:
        if pat in text:
            # Truncate pattern in reason for readability + label cardinality
            return False, f"bad_pattern:{pat[:24]}"

    if len(text) > MAX_DRAFT_LEN:
        return False, "too_long"

    return True, None
