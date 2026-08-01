"""
Unit tests — modules.escort multi-entry confirmation parsing/handling.

2026-08-01: parse_completed_draft() only ever extracted the FIRST lighter/
escort pair from an admin confirmation (every field regex used .search(),
never .finditer()). A real production confirmation with two numbered
lighter-vessel blocks (one escort per lighter) silently dropped the second
entry. These tests cover parse_completed_draft_entries() (the new multi-
entry parser), is_completed_escort_draft()'s updated completeness check,
and handle_admin_escort_completion()'s per-entry processing loop.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


def _set_escort_env(monkeypatch, clients: str = "8801670535255,8801955555555") -> None:
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", clients)
    monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
    monkeypatch.setenv("BRIDGE1_NUMBER", "8801958122300")
    monkeypatch.setenv("BRIDGE2_NUMBER", "8801880446111")
    from app.config import get_settings
    get_settings.cache_clear()


REAL_CONFIRMATION = """Mother Vessel: M.V. Elim

1. Lighter Vessel: M.V. Farhin Adib-0
Master Mobile: 01731034486
Destination: Rupshi
Escort Name: Md. Abdul Ajit Mullah
Escort Mobile: 01602300636

2. Lighter Vessel: M.V. Farhin Adib-3
Master Mobile: 01616527845
Destination: Rupshi
Escort Name: Helal Uddin
Escort Mobile: 01863377420

Start Date: 01.08.2026(D)

Al-Rifa'i Integrated Services"""

REAL_CONFIRMATION_BAD_PHONE_ENTRY_1 = REAL_CONFIRMATION.replace(
    "Escort Mobile: 01602300636", "Escort Mobile: 0160230063"
)


class TestParseCompletedDraftEntries:

    def test_two_entries_extracted_from_real_message(self):
        from modules.escort import parse_completed_draft_entries

        entries = parse_completed_draft_entries(REAL_CONFIRMATION)
        assert len(entries) == 2
        assert entries[0]["mother_vessel"] == "MV ELIM"
        assert entries[1]["mother_vessel"] == "MV ELIM"
        assert entries[0]["lighter_vessel"] == "M.V. Farhin Adib-0"
        assert entries[1]["lighter_vessel"] == "M.V. Farhin Adib-3"
        assert entries[0]["master_mobile"] == "01731034486"
        assert entries[1]["master_mobile"] == "01616527845"
        assert entries[0]["escort_mobile"] == "01602300636"
        assert entries[1]["escort_mobile"] == "01863377420"
        assert entries[1]["escort_name"] == "Helal Uddin"
        # shared fields identical across entries
        assert entries[0]["date_str"] == entries[1]["date_str"] == "01.08.2026"
        assert entries[0]["shift"] == entries[1]["shift"] == "D"

    def test_second_entry_survives_a_malformed_first_entry_phone(self):
        """The exact real incident: entry 1's escort mobile is one digit
        short. Entry 2 must still be fully extracted."""
        from modules.escort import parse_completed_draft_entries

        entries = parse_completed_draft_entries(REAL_CONFIRMATION_BAD_PHONE_ENTRY_1)
        assert len(entries) == 2
        assert entries[0]["escort_mobile"] is None
        assert entries[1]["escort_mobile"] == "01863377420"
        assert entries[1]["lighter_vessel"] == "M.V. Farhin Adib-3"

    def test_single_entry_message_delegates_unchanged(self):
        """No numbered blocks -> identical to parse_completed_draft()."""
        from modules.escort import parse_completed_draft_entries, parse_completed_draft

        text = (
            "Mother Vessel: MV TEST\nLighter Vessel: LV TEST\n"
            "Master Mobile: 01700000000\nEscort Name: Solo Escort\n"
            "Escort Mobile: 01711111111\nDate: 01.08.2026(D)\n"
            "Al-Rifa'i Integrated Services"
        )
        entries = parse_completed_draft_entries(text)
        assert len(entries) == 1
        assert entries[0] == parse_completed_draft(text)

    def test_three_entries(self):
        from modules.escort import parse_completed_draft_entries

        text = """Mother Vessel: MV THREE

1. Lighter Vessel: LV ONE
Master Mobile: 01711111111
Escort Name: Escort One
Escort Mobile: 01711111112

2. Lighter Vessel: LV TWO
Master Mobile: 01722222221
Escort Name: Escort Two
Escort Mobile: 01722222222

3. Lighter Vessel: LV THREE
Master Mobile: 01733333331
Escort Name: Escort Three
Escort Mobile: 01733333332

Start Date: 01.08.2026(N)
Al-Rifa'i Integrated Services"""
        entries = parse_completed_draft_entries(text)
        assert len(entries) == 3
        assert [e["lighter_vessel"] for e in entries] == ["LV ONE", "LV TWO", "LV THREE"]
        assert all(e["shift"] == "N" for e in entries)


class TestIsCompletedEscortDraftMultiEntry:

    def test_complete_multi_entry_recognized(self):
        from modules.escort import is_completed_escort_draft
        assert is_completed_escort_draft(REAL_CONFIRMATION) is True

    def test_one_bad_entry_does_not_block_the_message(self):
        """Owner requirement: one bad phone number in a multi-entry
        confirmation must not block the other, valid entries."""
        from modules.escort import is_completed_escort_draft
        assert is_completed_escort_draft(REAL_CONFIRMATION_BAD_PHONE_ENTRY_1) is True

    def test_all_entries_incomplete_rejected(self):
        from modules.escort import is_completed_escort_draft
        text = """Mother Vessel: MV X

1. Lighter Vessel: LV A
Master Mobile: 01711111111

2. Lighter Vessel: LV B
Master Mobile: 01722222222

Al-Rifa'i Integrated Services"""
        assert is_completed_escort_draft(text) is False


class TestHandleAdminEscortCompletionMultiEntry:

    async def test_both_entries_matched_and_updated(self, monkeypatch):
        _set_escort_env(monkeypatch)
        import modules.escort as escort_module
        from modules.escort import handle_admin_escort_completion

        programs_by_lighter = {
            "M.V. Farhin Adib-0": {
                "program_id": 701,
                "mother_vessel": "MV ELIM",
                "lighter_vessel": "M.V. Farhin Adib-0",
                "destination": "Old",
                "remarks": json.dumps({"sender_phone": "8801670535255", "source_bridge": "bridge2"}),
            },
            "M.V. Farhin Adib-3": {
                "program_id": 702,
                "mother_vessel": "MV ELIM",
                "lighter_vessel": "M.V. Farhin Adib-3",
                "destination": "Old",
                "remarks": json.dumps({"sender_phone": "8801670535255", "source_bridge": "bridge2"}),
            },
        }

        updated = []
        synced = []

        async def _fake_find_pending_program(mv, lv, **kwargs):
            return programs_by_lighter.get(lv)

        async def _fake_resolve(*a, **kw):
            return 99

        async def _fake_update_program_confirmed(program_id, escort_name, escort_mobile, **kwargs):
            updated.append((program_id, escort_name, escort_mobile))
            return True

        async def _fake_sync(program_id):
            synced.append(program_id)

        monkeypatch.setattr(escort_module, "_find_pending_program", _fake_find_pending_program)
        monkeypatch.setattr(escort_module, "_resolve_escort_employee_id", _fake_resolve)
        monkeypatch.setattr(escort_module, "_update_program_confirmed", _fake_update_program_confirmed)
        monkeypatch.setattr(escort_module, "_sync_roster_after_confirm", _fake_sync)

        reply, note = await handle_admin_escort_completion(
            REAL_CONFIRMATION, "8801880446111", "bridge2", recipient_phone="8801670535255",
        )

        assert len(updated) == 2
        assert updated[0] == (701, "Md", "01602300636")
        assert updated[1] == (702, "Helal Uddin", "01863377420")
        assert synced == [701, 702]
        assert note is not None
        assert "Sent to 8801670535255" in reply
        # Combined slip must mention both lighters
        assert "Farhin Adib-0" in note["text"]
        assert "Farhin Adib-3" in note["text"]

    async def test_one_matched_one_queued_for_review(self, monkeypatch):
        """Entry 1 matches an existing draft; entry 2 has no match at all
        -- must queue for review, never auto-create (Owner decision kept)."""
        _set_escort_env(monkeypatch)
        import modules.escort as escort_module
        from modules.escort import handle_admin_escort_completion

        async def _fake_find_pending_program(mv, lv, **kwargs):
            if lv == "M.V. Farhin Adib-0":
                return {
                    "program_id": 801,
                    "mother_vessel": "MV ELIM",
                    "lighter_vessel": lv,
                    "destination": None,
                    "remarks": json.dumps({"sender_phone": "8801670535255", "source_bridge": "bridge2"}),
                }
            return None

        async def _fake_score(*a, **kw):
            return []

        create_calls = []

        async def _fake_create(*a, **kw):
            create_calls.append((a, kw))
            return 9999

        async def _fake_resolve(*a, **kw):
            return 99

        async def _fake_update_program_confirmed(*a, **kw):
            return True

        async def _fake_sync(program_id):
            pass

        queue_calls = []

        async def _fake_enqueue_unmatched(**kwargs):
            queue_calls.append(kwargs)
            return 55

        monkeypatch.setattr(escort_module, "_find_pending_program", _fake_find_pending_program)
        monkeypatch.setattr(escort_module, "_score_pending_program_candidates", _fake_score)
        monkeypatch.setattr(escort_module, "_create_confirmed_from_admin", _fake_create)
        monkeypatch.setattr(escort_module, "_resolve_escort_employee_id", _fake_resolve)
        monkeypatch.setattr(escort_module, "_update_program_confirmed", _fake_update_program_confirmed)
        monkeypatch.setattr(escort_module, "_sync_roster_after_confirm", _fake_sync)
        monkeypatch.setattr(escort_module, "_enqueue_unmatched_confirmation", _fake_enqueue_unmatched)

        reply, note = await handle_admin_escort_completion(
            REAL_CONFIRMATION, "8801880446111", "bridge2", recipient_phone="8801670535255",
        )

        assert create_calls == [], "no-match entry must never auto-create a program"
        assert len(queue_calls) == 1
        assert "Queued for review (#55)" in reply
        assert "confirmed" in reply.lower()
        assert note is not None  # the matched entry still notifies the client
