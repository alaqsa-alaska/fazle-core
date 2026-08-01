"""
Unit tests — modules.escort.is_completed_escort_draft() / _matches_company_name()

2026-08-01 incident: admin sent a fully-formed escort-order confirmation
(Mother Vessel, both Lighter Vessels, both Master Mobiles, both Escort
Names, both Escort Mobiles, Start Date all present and correctly labeled)
signed off "Al-Rifa'i Integrated Services" — the company's actual current
name (COMPANY_NAME in .env, also used by the system's own AI-generated
greeting text). is_completed_escort_draft() requires a recognized company
signature, but _AL_AQSA_RE was a hardcoded regex still only listing
"al-aqsa" (pre-2026-07-21) and "al-manaratayn" (the 2026-07-21 rebrand) —
it had never been updated for this name, so the confirmation was silently
never detected: no wbom_escort_programs row was ever confirmed, despite
every other field being well-formed.

Fix: is_completed_escort_draft() now also accepts a message that mentions
the currently-configured settings.company_name (tolerant of punctuation/
spacing), so the next rebrand only requires updating COMPANY_NAME in .env.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from modules.escort import is_completed_escort_draft, _matches_company_name

pytestmark = pytest.mark.unit

REAL_CONFIRMATION = """Mother Vessel: M.V. Elim

1. Lighter Vessel: M.V. Farhin Adib-0
Master Mobile: 01731034486
Destination: Rupshi
Escort Name: Md. Abdul Ajit Mullah
Escort Mobile: 0160230063

2. Lighter Vessel: M.V. Farhin Adib-3
Master Mobile: 01616527845
Destination: Rupshi
Escort Name: Helal Uddin
Escort Mobile: 01863377420

Start Date: 01.08.2026(D)

Al-Rifa'i Integrated Services"""


def _with_company_name(name: str):
    return patch("modules.escort.get_settings", return_value=type(
        "S", (), {"company_name": name}
    )())


class TestMatchesCompanyName:

    def test_exact_current_name(self):
        with _with_company_name("Al-Rifa'i Integrated Services"):
            assert _matches_company_name("... Al-Rifa'i Integrated Services") is True

    def test_tolerant_of_missing_apostrophe_and_hyphen(self):
        with _with_company_name("Al-Rifa'i Integrated Services"):
            assert _matches_company_name("Signed, Al Rifai") is True
            assert _matches_company_name("AlRifai Services") is True

    def test_no_match_for_unrelated_text(self):
        with _with_company_name("Al-Rifa'i Integrated Services"):
            assert _matches_company_name("Please send the release slip") is False

    def test_empty_company_name_never_matches(self):
        with _with_company_name(""):
            assert _matches_company_name("Al-Rifa'i Integrated Services") is False


class TestIsCompletedEscortDraftRecognizesCurrentBrand:

    def test_real_confirmation_message_recognized(self):
        """The exact production message that was silently skipped."""
        with _with_company_name("Al-Rifa'i Integrated Services"):
            assert is_completed_escort_draft(REAL_CONFIRMATION) is True

    def test_legacy_al_aqsa_signature_still_recognized(self):
        """Regression guard: historical/legacy-brand messages must still work."""
        text = (
            "Mother Vessel: MV TEST\n"
            "Lighter Vessel: MV LIGHTER\n"
            "Escort Name: Test Escort\n"
            "Escort Mobile: 01700000000\n"
            "Date: 01.08.2026(D)\n"
            "Al-Aqsa Security & Logistics Services"
        )
        with _with_company_name("Al-Rifa'i Integrated Services"):
            assert is_completed_escort_draft(text) is True

    def test_missing_company_signature_still_rejected(self):
        """A message with no recognizable company name at all must still
        fail — the gate isn't being removed, just fixed."""
        text = (
            "Mother Vessel: MV TEST\n"
            "Lighter Vessel: MV LIGHTER\n"
            "Escort Name: Test Escort\n"
            "Escort Mobile: 01700000000\n"
            "Date: 01.08.2026(D)"
        )
        with _with_company_name("Al-Rifa'i Integrated Services"):
            assert is_completed_escort_draft(text) is False

    def test_incomplete_draft_with_correct_signature_still_rejected(self):
        """Company name alone isn't sufficient — all draft fields are still
        required."""
        text = "Mother Vessel: MV TEST\nAl-Rifa'i Integrated Services"
        with _with_company_name("Al-Rifa'i Integrated Services"):
            assert is_completed_escort_draft(text) is False
