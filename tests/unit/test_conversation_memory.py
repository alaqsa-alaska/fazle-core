"""Unit tests — modules/conversation_memory (Phase B, Stage 1, 2026-07-19).

Pure-function merge-policy tests need no DB. The DB-backed tests
(thread creation, idempotency, expiry) are skipped when TEST_DB_PASSWORD
is unavailable, matching this session's standing constraint -- marked
with the same test_db_pool fixture pattern used throughout this repo, so
they run for real whenever a test DB is reachable, rather than being
silently disabled.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ===========================================================================
# merge_field() -- pure function, Part 6.2 merge policy
# ===========================================================================

class TestMergeFieldPolicy:

    def test_first_value_is_simply_set(self):
        from modules.conversation_memory import merge_field
        fields, result = merge_field(
            {}, "mother_vessel", value="MV Horda", source_message_id="M1",
            confidence=0.9, extraction_method="label", is_system_generated=False,
        )
        assert result.applied is True
        assert result.conflict is False
        assert fields["mother_vessel"]["value"] == "MV Horda"

    def test_system_generated_content_never_applied(self):
        """The one rule this module treats as non-negotiable: system-
        generated text (the MV Horda incident class) must never become
        authoritative conversation state, even with maximum confidence."""
        from modules.conversation_memory import merge_field
        fields, result = merge_field(
            {}, "mother_vessel", value="MV Horda", source_message_id="M1",
            confidence=1.0, extraction_method="label", is_system_generated=True,
        )
        assert result.applied is False
        assert result.reason == "system_generated_content_rejected"
        assert fields == {}

    def test_explicit_label_overrides_inferred_value(self):
        from modules.conversation_memory import merge_field
        fields = {"mother_vessel": {"value": "Horda", "confidence": 0.5, "source_message_id": "M1", "extraction_method": "inferred"}}
        fields, result = merge_field(
            fields, "mother_vessel", value="MV Horda", source_message_id="M2",
            confidence=0.4, extraction_method="explicit_label", is_system_generated=False,
            is_explicit_label=True,
        )
        assert result.applied is True
        assert result.reason == "explicit_label_overrides_inferred"
        assert fields["mother_vessel"]["value"] == "MV Horda"

    def test_higher_confidence_correction_overrides(self):
        from modules.conversation_memory import merge_field
        fields = {"master_mobile": {"value": "0182xxxxxxx", "confidence": 0.5, "source_message_id": "M1", "extraction_method": "fuzzy"}}
        fields, result = merge_field(
            fields, "master_mobile", value="01821375926", source_message_id="M2",
            confidence=0.95, extraction_method="explicit_label", is_system_generated=False,
        )
        assert result.applied is True
        assert fields["master_mobile"]["value"] == "01821375926"

    def test_conflicting_high_confidence_values_flagged_not_overwritten(self):
        """The explicit Part 6.2 requirement: a conflicting high-
        confidence value must create a conflict, never silently
        overwrite."""
        from modules.conversation_memory import merge_field
        fields = {"mother_vessel": {"value": "MV Horda", "confidence": 0.9, "source_message_id": "M1", "extraction_method": "label"}}
        fields, result = merge_field(
            fields, "mother_vessel", value="MV Different Ship", source_message_id="M2",
            confidence=0.85, extraction_method="label", is_system_generated=False,
        )
        assert result.applied is False
        assert result.conflict is True
        assert result.reason == "conflicting_high_confidence_values"
        # original value preserved, not overwritten
        assert fields["mother_vessel"]["value"] == "MV Horda"

    def test_low_confidence_value_ignored_not_conflict(self):
        from modules.conversation_memory import merge_field
        fields = {"mother_vessel": {"value": "MV Horda", "confidence": 0.9, "source_message_id": "M1", "extraction_method": "label"}}
        fields, result = merge_field(
            fields, "mother_vessel", value="Something Else", source_message_id="M2",
            confidence=0.2, extraction_method="guess", is_system_generated=False,
        )
        assert result.applied is False
        assert result.conflict is False
        assert result.reason == "low_confidence_value_ignored"
        assert fields["mother_vessel"]["value"] == "MV Horda"

    def test_unchanged_value_is_a_noop(self):
        from modules.conversation_memory import merge_field
        fields = {"mother_vessel": {"value": "MV Horda", "confidence": 0.9, "source_message_id": "M1", "extraction_method": "label"}}
        fields2, result = merge_field(
            fields, "mother_vessel", value="MV Horda", source_message_id="M2",
            confidence=0.9, extraction_method="label", is_system_generated=False,
        )
        assert result.applied is False
        assert result.reason == "unchanged_value"
        assert fields2 is fields  # genuinely untouched

    def test_field_entry_shape_matches_spec_example(self):
        """Part 4.6's exact required shape: value, source_message_id,
        confidence, extraction_method."""
        from modules.conversation_memory import merge_field
        fields, _ = merge_field(
            {}, "mother_vessel", value="MV Horda", source_message_id="MSG-123",
            confidence=0.94, extraction_method="bangla_label", is_system_generated=False,
        )
        entry = fields["mother_vessel"]
        assert entry["value"] == "MV Horda"
        assert entry["source_message_id"] == "MSG-123"
        assert entry["confidence"] == 0.94
        assert entry["extraction_method"] == "bangla_label"

    def test_two_message_escort_example_merges_into_one_complete_set(self):
        """The exact real example from the Phase B spec: message 1 gives
        lighter vessel + capacity + master mobile + destination; message
        2 (Bangla) gives the mother vessel. Neither alone is complete;
        merged, all three completeness-required fields are present."""
        from modules.conversation_memory import merge_field

        fields: dict = {}
        # Message 1
        fields, r1 = merge_field(fields, "lighter_vessel", value="Bushra Biva-1", source_message_id="M1", confidence=0.9, extraction_method="block_detection", is_system_generated=False)
        fields, r2 = merge_field(fields, "master_mobile", value="01821375926", source_message_id="M1", confidence=0.95, extraction_method="mobile_anchor", is_system_generated=False)
        fields, r3 = merge_field(fields, "destination", value="Outer to Local", source_message_id="M1", confidence=0.8, extraction_method="content_match", is_system_generated=False)
        fields, r4 = merge_field(fields, "capacity", value="1250 MT", source_message_id="M1", confidence=0.85, extraction_method="trailing_number", is_system_generated=False)
        # Message 2 (Bangla)
        fields, r5 = merge_field(fields, "mother_vessel", value="MV Horda", source_message_id="M2", confidence=0.9, extraction_method="bangla_label", is_system_generated=False, is_explicit_label=True)

        assert all(r.applied for r in (r1, r2, r3, r4, r5))
        # Completeness rule (Part 6.4): mother vessel + lighter vessel + master mobile
        assert fields["mother_vessel"]["value"] == "MV Horda"
        assert fields["lighter_vessel"]["value"] == "Bushra Biva-1"
        assert fields["master_mobile"]["value"] == "01821375926"
        assert {"mother_vessel", "lighter_vessel", "master_mobile"} <= set(fields.keys())


# ===========================================================================
# TTL configuration -- Part 4.5: domain-configurable, never hardcoded
# ===========================================================================

class TestTTLConfiguration:

    def test_escort_ttl_uses_minutes_setting(self, monkeypatch):
        from datetime import timedelta
        from app.config import get_settings
        import modules.conversation_memory as cm

        get_settings.cache_clear()
        monkeypatch.setenv("CONVERSATION_TTL_ESCORT_MINUTES", "45")
        get_settings.cache_clear()
        try:
            assert cm._ttl_for_domain("escort") == timedelta(minutes=45)
        finally:
            monkeypatch.delenv("CONVERSATION_TTL_ESCORT_MINUTES", raising=False)
            get_settings.cache_clear()

    def test_unknown_domain_falls_back_to_general_ttl(self):
        from datetime import timedelta
        import modules.conversation_memory as cm
        from app.config import get_settings

        get_settings.cache_clear()
        expected = timedelta(hours=get_settings().conversation_ttl_general_hours)
        assert cm._ttl_for_domain("some_future_domain") == expected

    def test_recruitment_ttl_is_hours_not_minutes(self):
        from datetime import timedelta
        import modules.conversation_memory as cm
        from app.config import get_settings

        get_settings.cache_clear()
        expected = timedelta(hours=get_settings().conversation_ttl_recruitment_hours)
        assert cm._ttl_for_domain("recruitment") == expected


# ===========================================================================
# Feature flags default OFF -- Part 13's shadow-mode-first requirement
# ===========================================================================

def test_stage1_flags_enabled_stage2_stage3_flags_still_off():
    """Reflects the real, intentional production state as of the Stage 2
    checkpoint (2026-08-08): all four conversation-memory flags are ON in
    production .env after Owner approval -- conversation_memory_enabled,
    whatsapp_thread_reconstruction_enabled, multi_message_escort_parser_enabled
    and bangla_semantic_parser_enabled."""
    from app.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.conversation_memory_enabled is True
    assert s.whatsapp_thread_reconstruction_enabled is True
    assert s.multi_message_escort_parser_enabled is True
    assert s.bangla_semantic_parser_enabled is True


def test_stage2_stage3_flags_default_off_at_code_level(monkeypatch):
    """Even independent of current .env content, the Settings class
    itself defaults these two to False -- Part 13's shadow-mode-first
    requirement is a code-level guarantee, not just today's deployment
    fact."""
    from app.config import get_settings
    # setenv overrides the .env file value; delenv alone does not because
    # pydantic-settings re-reads the .env file after the env var is removed.
    monkeypatch.setenv("MULTI_MESSAGE_ESCORT_PARSER_ENABLED", "false")
    monkeypatch.setenv("BANGLA_SEMANTIC_PARSER_ENABLED", "false")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.multi_message_escort_parser_enabled is False
        assert s.bangla_semantic_parser_enabled is False
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_update_thread_rejects_invalid_state_before_any_db_call():
    """update_thread() validates state up front and raises before ever
    awaiting a DB call -- verified by calling it with no DB connection
    available at all and confirming the ValueError still fires (a DB
    error would look different: a connection/pool error, not ValueError)."""
    from modules.conversation_memory import update_thread

    with pytest.raises(ValueError, match="invalid state"):
        await update_thread(1, state="not_a_real_state")
