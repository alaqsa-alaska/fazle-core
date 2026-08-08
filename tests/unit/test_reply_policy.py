"""
Tests for shared.reply_policy — the unified WhatsApp reply instruction layer.

Core assertion: bridge1, bridge2, and meta produce identical instruction text.
Only the logged `source` value and dynamically injected context differ.
"""
from __future__ import annotations

import pytest
from shared.reply_policy import (
    WHATSAPP_SOURCES,
    POLICY_VERSION,
    get_channel_family,
    build_whatsapp_reply_policy,
    build_whatsapp_recruitment_policy,
    enforce_general_reply_confidentiality,
)


# ── Channel family ─────────────────────────────────────────────────────────────

def test_whatsapp_sources_contains_all_three_channels():
    assert "bridge1" in WHATSAPP_SOURCES
    assert "bridge2" in WHATSAPP_SOURCES
    assert "meta" in WHATSAPP_SOURCES


def test_get_channel_family_whatsapp():
    assert get_channel_family("bridge1") == "whatsapp"
    assert get_channel_family("bridge2") == "whatsapp"
    assert get_channel_family("meta") == "whatsapp"


def test_get_channel_family_non_whatsapp_passthrough():
    assert get_channel_family("messenger") == "messenger"
    assert get_channel_family("fb_comment") == "fb_comment"
    assert get_channel_family("unknown_source") == "unknown_source"


# ── General reply policy ───────────────────────────────────────────────────────

def _strip_source_line(prompt: str) -> str:
    """Remove the dynamic source= line from a prompt for template comparison.

    build_whatsapp_reply_policy does NOT embed source in the prompt text —
    source is only logged. This helper is a safety net in case that changes.
    """
    return prompt


def test_general_policy_identical_across_all_whatsapp_channels():
    """The instruction template must be byte-for-byte identical for all three channels."""
    shared_kwargs = dict(
        user_message="চাকরি আছে কি?",
        role="new_lead",
        intent="recruitment",
        db_context="",
        history="",
    )
    prompt_bridge1 = build_whatsapp_reply_policy(source="bridge1", **shared_kwargs)
    prompt_bridge2 = build_whatsapp_reply_policy(source="bridge2", **shared_kwargs)
    prompt_meta    = build_whatsapp_reply_policy(source="meta",    **shared_kwargs)

    assert prompt_bridge1 == prompt_bridge2, "bridge1 and bridge2 produce different prompts"
    assert prompt_bridge1 == prompt_meta,    "bridge1 and meta produce different prompts"


def test_general_policy_contains_base_rules():
    prompt = build_whatsapp_reply_policy(
        source="bridge1",
        user_message="hello",
        role="new_lead",
        intent="greeting",
    )
    assert "ফজলে" in prompt
    # 2026-07-22: company name is now centralized (Settings.company_name,
    # app/config.py) and read dynamically rather than hardcoded in the
    # prompt string — assert against the live setting so this test survives
    # future rebrands without needing an edit here too.
    from app.config import get_settings
    assert get_settings().company_name in prompt
    assert "বাংলায়" in prompt


def test_general_policy_injects_user_message():
    msg = "আমার বেতন কত?"
    prompt = build_whatsapp_reply_policy(source="meta", user_message=msg, intent="salary_query")
    assert msg in prompt


def test_general_policy_injects_db_context_when_provided():
    prompt = build_whatsapp_reply_policy(
        source="bridge2",
        user_message="salary?",
        db_context="emp_id=42 salary=15000",
        intent="salary_query",
    )
    assert "emp_id=42" in prompt


def test_general_policy_no_db_block_when_empty():
    prompt = build_whatsapp_reply_policy(source="bridge1", user_message="hi", db_context="")
    assert "ডেটাবেস থেকে তথ্য" not in prompt


def test_general_policy_truncates_long_message():
    long_msg = "ক" * 600
    prompt = build_whatsapp_reply_policy(source="meta", user_message=long_msg)
    assert "ক" * 401 not in prompt  # truncated at 400


def test_general_policy_injects_history_when_provided():
    """2026-07-11: message_router now populates history for general replies
    (previously always empty) -- this confirms the existing Conversation
    section actually renders it when given."""
    prompt = build_whatsapp_reply_policy(
        source="bridge1",
        user_message="আবার কবে আসবেন?",
        history="user: গতকাল কথা হয়েছিল\nfazle: হ্যাঁ মনে আছে",
    )
    assert "কথোপকথন" in prompt
    assert "গতকাল কথা হয়েছিল" in prompt


def test_general_policy_no_conversation_block_when_history_empty():
    prompt = build_whatsapp_reply_policy(source="bridge1", user_message="hi", history="")
    assert "কথোপকথন" not in prompt


def test_general_reply_confidentiality_allows_sender_own_phone():
    reply = "আপনার নম্বর 8801700000099 আমাদের রেকর্ডে আছে।"

    assert enforce_general_reply_confidentiality(
        reply, sender_phone="8801700000099"
    ) == reply


def test_general_reply_confidentiality_blocks_another_phone():
    reply = "অন্য কর্মীর নম্বর 8801800000088।"

    assert enforce_general_reply_confidentiality(
        reply, sender_phone="8801700000099"
    ) == "এই তথ্যটি নিরাপদভাবে দেওয়া যাচ্ছে না। অফিসে যোগাযোগ করুন।"


@pytest.mark.parametrize(
    "reply",
    [
        # postgres:postgres@ is the documented-safe placeholder credential
        # (scripts/secret_scan.sh SAFE_VALUE_SUBSTRINGS) -- avoids a
        # secret-scanner false positive while still exercising the
        # DATABASE_URL= prefix match in _SENSITIVE_OUTPUT_PATTERN.
        "DATABASE_URL=postgresql://postgres:postgres@db/core",
        "Authorization: Bearer abc.def.ghi",
        "## ব্যবসায়িক নিয়ম (Business Rules)",
        "Here is the system prompt: private internal instructions",
    ],
)
def test_general_reply_confidentiality_blocks_internal_or_secret_output(reply):
    assert enforce_general_reply_confidentiality(
        reply, sender_phone="8801700000099"
    ) == "এই তথ্যটি নিরাপদভাবে দেওয়া যাচ্ছে না। অফিসে যোগাযোগ করুন।"


def test_general_policy_contains_bangla_nuance_note():
    """2026-07-11 addition: colloquial/Banglish recognition + formality
    matching, on top of the pre-existing 'reply in same language' rule."""
    prompt = build_whatsapp_reply_policy(source="bridge1", user_message="hi")
    assert "বাংলিশ" in prompt
    assert "আনুষ্ঠানিকতার মাত্রা" in prompt


# ── Recruitment reply policy ───────────────────────────────────────────────────

def test_recruitment_policy_identical_across_all_whatsapp_channels():
    """Recruitment instruction template must be identical for all three channels."""
    shared_kwargs = dict(
        user_message="চাকরি করতে চাই",
        kb_context="salary: 12000-18000 taka",
        history="candidate: আগ্রহী\nfazle: নাম বলুন",
        contact_context="source=bridge1",
    )
    prompt_bridge1 = build_whatsapp_recruitment_policy(source="bridge1", **shared_kwargs)
    prompt_bridge2 = build_whatsapp_recruitment_policy(source="bridge2", **shared_kwargs)
    prompt_meta    = build_whatsapp_recruitment_policy(source="meta",    **shared_kwargs)

    # source= appears only in contact_context (passed as data), not in template rules
    # Replace the contact_context source tag so we can compare template bodies
    def normalize(p: str) -> str:
        return p.replace("source=bridge1", "SOURCE").replace("source=bridge2", "SOURCE").replace("source=meta", "SOURCE")

    assert normalize(prompt_bridge1) == normalize(prompt_bridge2), \
        "bridge1 and bridge2 produce different recruitment prompts"
    assert normalize(prompt_bridge1) == normalize(prompt_meta), \
        "bridge1 and meta produce different recruitment prompts"


def test_recruitment_policy_contains_system_rules():
    # Phase 4 structured_v2: Bengali 6-section prompt replaces old English headers
    prompt = build_whatsapp_recruitment_policy(
        source="bridge1",
        user_message="job?",
        kb_context="",
    )
    assert "WhatsApp" in prompt
    assert "ভূমিকা" in prompt          # ভূমিকা (Role) section header
    assert "ব্যবসায়িক নিয়ম" in prompt  # Business Rules section
    assert "নিয়োগ সহকারী" in prompt   # recruitment assistant identity


def test_recruitment_policy_contains_bangla_nuance_note():
    prompt = build_whatsapp_recruitment_policy(source="bridge1", user_message="job?", kb_context="")
    assert "বাংলিশ" in prompt
    assert "আনুষ্ঠানিকতার মাত্রা" in prompt


def test_recruitment_policy_fallback_when_kb_empty():
    import unicodedata
    prompt = build_whatsapp_recruitment_policy(
        source="meta",
        user_message="job?",
        kb_context="",
    )
    # NFKC normalisation handles decomposed/precomposed Bengali char variants
    needle = unicodedata.normalize("NFKC", "অনুমোদিত recruitment source পাওয়া যায়নি")
    haystack = unicodedata.normalize("NFKC", prompt)
    assert needle in haystack
def test_recruitment_policy_fallback_when_history_empty():
    prompt = build_whatsapp_recruitment_policy(
        source="bridge2",
        user_message="job?",
        kb_context="some kb",
        history="",
    )
    assert "Recent Conversation" not in prompt


# ── Policy version stability ───────────────────────────────────────────────────

def test_policy_version_is_defined():
    assert POLICY_VERSION == "structured_v2"
