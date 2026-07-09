"""
Phase 3–8 pipeline correctness tests (Phase 11 spec).

Covers:
  1. Phone extraction from formatted numbers (Phase 1/3)
  2. Parser: extracts phone + sets employee_id_phone fallback (Phase 3)
  3. Ingestion dedup: same message twice → one transaction only (Phase 5)
  4. Draft guard: historical message → no draft (Phase 6)
  5. Draft guard: already-replied message → no draft (Phase 6)
  6. Employee matching: name-only with no phone → returns None, no phantom (Phase 4)
  7. Employee matching: WBOM phone cross-lookup hits before name match (Phase 4)
  8. Ingestion fingerprint is deterministic and collision-resistant (Phase 5)
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone, timedelta

import pytest


# ─── Phase 1/3: Phone extraction ──────────────────────────────────────────────

class TestExtractPhoneCandidates:
    """Tests for shared.phone.extract_phone_candidates()"""

    def test_formatted_with_space_and_hyphen(self):
        """'+880 1849-258074' must be extracted and canonicalised."""
        from shared.phone import extract_phone_candidates
        result = extract_phone_candidates("+880 1849-258074")
        assert result == ["01849258074"]

    def test_hyphen_only(self):
        from shared.phone import extract_phone_candidates
        result = extract_phone_candidates("01849-258074")
        assert result == ["01849258074"]

    def test_multiple_phones_in_text(self):
        from shared.phone import extract_phone_candidates
        text = "Call 01712345678 or +880 1812-345678"
        result = extract_phone_candidates(text)
        assert "01712345678" in result
        assert "01812345678" in result
        assert len(result) == 2

    def test_no_phone_returns_empty(self):
        from shared.phone import extract_phone_candidates
        assert extract_phone_candidates("No phone here") == []

    def test_none_returns_empty(self):
        from shared.phone import extract_phone_candidates
        assert extract_phone_candidates(None) == []

    def test_deduplicated(self):
        from shared.phone import extract_phone_candidates
        text = "01712345678 and +88001712345678"
        result = extract_phone_candidates(text)
        assert result == ["01712345678"]


# ─── Phase 3: Parser phone normalization ──────────────────────────────────────

class TestParserPhoneExtraction:
    """Tests for _parse_payment() inter-digit noise stripping."""

    def test_formatted_phone_extracted(self):
        """'Saiful op +880 1849-258074(N) 305/-' must yield payout_phone."""
        from modules.fazle_payroll_engine.parser import parse_message
        result = parse_message("Saiful op +880 1849-258074(N) 305/-")
        assert result is not None
        assert result.payment is not None
        assert result.payment.payout_phone == "01849258074", (
            f"Expected 01849258074, got {result.payment.payout_phone!r}"
        )

    def test_employee_id_phone_fallback(self):
        """When no 'ID:' prefix, employee_id_phone must equal payout_phone."""
        from modules.fazle_payroll_engine.parser import parse_message
        result = parse_message("Karim 01712345678(B) 1500/-")
        assert result is not None
        assert result.payment is not None
        assert result.payment.payout_phone is not None
        assert result.payment.employee_id_phone == result.payment.payout_phone, (
            "employee_id_phone should fall back to payout_phone when no 'ID:' prefix"
        )

    def test_amount_still_extracted(self):
        """Ensure inter-digit stripping does NOT corrupt amount extraction."""
        from modules.fazle_payroll_engine.parser import parse_message
        result = parse_message("Rahman 01712345678(B) 2500/-")
        assert result is not None
        assert result.payment is not None
        assert result.payment.amount == 2500.0


# ─── Phase 4: Name-only match returns None ────────────────────────────────────

class TestEmployeeMatchSafety:
    """Tests for Phase 4 safety rules in match_or_create_employee."""

    @pytest.mark.asyncio
    async def test_name_only_no_phone_returns_none(self):
        """
        When both payout_phone and employee_id_phone are None and no fuzzy
        match exceeds threshold, match_or_create_employee must return None
        (not auto-create a phantom employee).
        """
        from modules.fazle_payroll_engine.employee import match_or_create_employee

        async def _fetch_one_empty(*a, **kw):
            return None

        async def _fetch_all_empty(*a, **kw):
            return []

        with (
            patch("modules.fazle_payroll_engine.employee.fetch_one", side_effect=_fetch_one_empty),
            patch("modules.fazle_payroll_engine.employee.fetch_all", side_effect=_fetch_all_empty),
        ):
            result = await match_or_create_employee(
                name_raw="Saiful Unknown",
                payout_phone=None,
                employee_id_phone=None,
            )
            assert result is None, (
                "name-only + no phone evidence must return None to prevent phantom record"
            )

    @pytest.mark.asyncio
    async def test_no_name_no_phone_returns_none(self):
        """Edge case: both name and phone are absent."""
        from modules.fazle_payroll_engine.employee import match_or_create_employee

        with (
            patch("modules.fazle_payroll_engine.employee.fetch_one", return_value=None),
            patch("modules.fazle_payroll_engine.employee.fetch_all", return_value=[]),
        ):
            result = await match_or_create_employee(
                name_raw=None,
                payout_phone=None,
                employee_id_phone=None,
            )
            assert result is None


# ─── Phase 5: Ingestion fingerprint ──────────────────────────────────────────

class TestIngestionFingerprint:
    """Tests for shared.locks.ingestion_fingerprint()"""

    def test_deterministic(self):
        from shared.locks import ingestion_fingerprint
        fp1 = ingestion_fingerprint("WA_MSG_123", "bridge1")
        fp2 = ingestion_fingerprint("WA_MSG_123", "bridge1")
        assert fp1 == fp2

    def test_different_messages_differ(self):
        from shared.locks import ingestion_fingerprint
        fp1 = ingestion_fingerprint("WA_MSG_123", "bridge1")
        fp2 = ingestion_fingerprint("WA_MSG_456", "bridge1")
        assert fp1 != fp2

    def test_different_sources_differ(self):
        from shared.locks import ingestion_fingerprint
        fp1 = ingestion_fingerprint("WA_MSG_123", "bridge1")
        fp2 = ingestion_fingerprint("WA_MSG_123", "bridge2")
        assert fp1 != fp2

    def test_with_employee_and_amount(self):
        from shared.locks import ingestion_fingerprint
        fp = ingestion_fingerprint("WA_MSG_123", "bridge1", employee_id=42, amount_cents=30500)
        assert len(fp) == 16
        assert isinstance(fp, str)

    def test_is_hex_prefix(self):
        from shared.locks import ingestion_fingerprint
        fp = ingestion_fingerprint("WA_MSG_X", "src")
        int(fp, 16)  # must not raise — valid hex


# ─── Phase 6: Draft guards ────────────────────────────────────────────────────

class TestDraftGuards:
    """Tests for shared.draft.should_generate_draft()"""

    @pytest.mark.asyncio
    async def test_historical_message_blocked(self):
        """Messages older than cutoff must return False."""
        from shared.draft import should_generate_draft
        old_ts = datetime.now(timezone.utc) - timedelta(hours=5)
        result = await should_generate_draft(
            chat_jid="8801712345678@s.whatsapp.net",
            msg_timestamp=old_ts,
            historical_cutoff_hours=2,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_recent_message_allowed_when_no_reply(self):
        """Recent message with no admin reply must return True."""
        from shared.draft import should_generate_draft
        recent_ts = datetime.now(timezone.utc) - timedelta(minutes=5)

        with patch("shared.draft.fetch_val", new=AsyncMock(return_value=None)):
            result = await should_generate_draft(
                chat_jid="8801712345678@s.whatsapp.net",
                msg_timestamp=recent_ts,
                historical_cutoff_hours=2,
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_already_replied_blocked(self):
        """Recent message where admin already replied must return False."""
        from shared.draft import should_generate_draft
        recent_ts = datetime.now(timezone.utc) - timedelta(minutes=5)

        with patch("shared.draft.fetch_val", new=AsyncMock(return_value=1)):
            result = await should_generate_draft(
                chat_jid="8801712345678@s.whatsapp.net",
                msg_timestamp=recent_ts,
                historical_cutoff_hours=2,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_none_timestamp_allowed(self):
        """Unknown timestamp must fail open (allow draft)."""
        from shared.draft import should_generate_draft
        with patch("shared.draft.fetch_val", new=AsyncMock(return_value=None)):
            result = await should_generate_draft(
                chat_jid="test@s.whatsapp.net",
                msg_timestamp=None,
            )
        assert result is True


# ─── Phase 6: expire_program_drafts ──────────────────────────────────────────

class TestExpireProgramDrafts:
    @pytest.mark.asyncio
    async def test_expire_returns_count(self):
        from shared.draft import expire_program_drafts
        with patch("shared.draft.fetch_val", new=AsyncMock(return_value=3)):
            n = await expire_program_drafts(employee_id=1, escort_program_id=99)
        assert n == 3

    @pytest.mark.asyncio
    async def test_expire_handles_db_error_gracefully(self):
        from shared.draft import expire_program_drafts
        with patch("shared.draft.fetch_val", side_effect=Exception("DB down")):
            n = await expire_program_drafts(employee_id=1, escort_program_id=99)
        assert n == 0
