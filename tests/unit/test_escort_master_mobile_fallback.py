"""
Unit tests — modules.escort._find_pending_program()'s 2026-08-01
exact-master-mobile fallback.

Owner instruction: an exact match on the lighter master's mobile number
between a draft and its confirmation proves it's the same order — but only
as a *fallback* when the normal fuzzy score is inconclusive (never instead
of it), and only among drafts from the last 7 days. Two or more drafts
sharing the same master mobile stays genuinely ambiguous.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import modules.escort as escort_module

pytestmark = pytest.mark.unit

NOW = datetime.now(timezone.utc)


def _row(program_id, master_mobile="01700000000", mv="MV TEST", lv="LV TEST",
         program_date=None, assignment_time=None, remarks="{}"):
    return {
        "program_id": program_id,
        "remarks": remarks,
        "mother_vessel": mv,
        "lighter_vessel": lv,
        "destination": None,
        "master_mobile": master_mobile,
        "program_date": program_date,
        "assignment_time": assignment_time or NOW,
    }


class TestFindPendingProgramFallback:

    async def test_normal_threshold_match_unaffected(self):
        """Regression: a normal high-confidence match still wins outright,
        fallback logic never even needs to trigger."""
        row = _row(101, mv="MV ELIM", lv="FARHIN ADIB", program_date=NOW.date())
        with patch.object(escort_module, "fetch_all", new=AsyncMock(return_value=[row])), \
             patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=row)):
            result = await escort_module._find_pending_program(
                "MV ELIM", "FARHIN ADIB", master_mobile="01700000000", program_date=NOW.date(),
            )
        assert result is not None
        assert result["program_id"] == 101

    async def test_low_score_single_exact_mobile_match_within_7_days_wins(self):
        """The exact incident this fix targets: weak fuzzy score (verified
        below the 5.5 threshold: master_mobile-only match scores exactly
        5.0), but the master mobile matches exactly and the draft is
        recent."""
        row = _row(202, master_mobile="01731034486", mv="MV ZZZZZZZZZZZ",
                    lv="QQQQQQQQQQQQQQQQ", assignment_time=NOW - timedelta(days=2))
        with patch.object(escort_module, "fetch_all", new=AsyncMock(return_value=[row])), \
             patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=row)):
            result = await escort_module._find_pending_program(
                "MV ELIM", "FARHIN ADIB-0", master_mobile="01731034486", program_date=None,
            )
        assert result is not None
        assert result["program_id"] == 202

    async def test_exact_mobile_match_older_than_7_days_does_not_fall_back(self):
        row = _row(303, master_mobile="01731034486", mv="MV ZZZZZZZZZZZ",
                    lv="QQQQQQQQQQQQQQQQ", assignment_time=NOW - timedelta(days=10))
        with patch.object(escort_module, "fetch_all", new=AsyncMock(return_value=[row])), \
             patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=row)):
            result = await escort_module._find_pending_program(
                "MV ELIM", "FARHIN ADIB-0", master_mobile="01731034486", program_date=None,
            )
        assert result is None

    async def test_two_candidates_sharing_master_mobile_stays_ambiguous(self):
        row1 = _row(404, master_mobile="01731034486", mv="MV A", assignment_time=NOW - timedelta(days=1))
        row2 = _row(505, master_mobile="01731034486", mv="MV B", assignment_time=NOW - timedelta(days=2))
        with patch.object(escort_module, "fetch_all", new=AsyncMock(return_value=[row1, row2])), \
             patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=row1)):
            result = await escort_module._find_pending_program(
                "MV ELIM", "FARHIN ADIB-0", master_mobile="01731034486", program_date=None,
            )
        assert result is None

    async def test_no_master_mobile_match_at_all_stays_none(self):
        row = _row(606, master_mobile="01999999999", mv="MV UNRELATED",
                    assignment_time=NOW - timedelta(days=1))
        with patch.object(escort_module, "fetch_all", new=AsyncMock(return_value=[row])), \
             patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=row)):
            result = await escort_module._find_pending_program(
                "MV ELIM", "FARHIN ADIB-0", master_mobile="01731034486", program_date=None,
            )
        assert result is None
