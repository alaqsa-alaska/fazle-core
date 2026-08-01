"""
Unit tests — modules.escort Step 5 (2026-08-01): admin-reminder flow for a
confirmation entry with a present-but-malformed mobile number.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def _set_escort_env(monkeypatch, clients: str = "8801670535255,8801955555555") -> None:
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", clients)
    monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
    monkeypatch.setenv("BRIDGE1_NUMBER", "8801958122300")
    monkeypatch.setenv("BRIDGE2_NUMBER", "8801880446111")
    monkeypatch.setenv("ADMIN_NUMBERS", "8801880446111")
    from app.config import get_settings
    get_settings.cache_clear()


REAL_CONFIRMATION_BAD_PHONE_ENTRY_1 = """Mother Vessel: M.V. Elim

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


class TestMalformedMobileDetection:

    def test_detects_malformed_escort_mobile(self):
        from modules.escort import _malformed_mobile_raw
        block = "Escort Mobile: 0160230063"
        assert _malformed_mobile_raw(block, field="escort") == "0160230063"

    def test_valid_mobile_not_flagged(self):
        from modules.escort import _malformed_mobile_raw
        block = "Escort Mobile: 01602300636"
        assert _malformed_mobile_raw(block, field="escort") is None

    def test_genuinely_absent_field_not_flagged(self):
        from modules.escort import _malformed_mobile_raw
        block = "Lighter Vessel: X\nMaster Mobile: 01700000000"
        assert _malformed_mobile_raw(block, field="escort") is None


class TestHandleAdminEscortCompletionWithMalformedMobile:

    async def test_bad_entry_creates_review_draft_good_entry_still_confirmed(self, monkeypatch):
        _set_escort_env(monkeypatch)
        import modules.escort as escort_module
        from modules.escort import handle_admin_escort_completion

        async def _fake_find_pending_program(mv, lv, **kwargs):
            if lv == "M.V. Farhin Adib-3":
                return {
                    "program_id": 901,
                    "mother_vessel": "MV ELIM",
                    "lighter_vessel": lv,
                    "destination": None,
                    "remarks": json.dumps({"sender_phone": "8801670535255", "source_bridge": "bridge2"}),
                }
            return None  # entry 1 should never even reach matching

        find_calls = []

        async def _tracking_find(mv, lv, **kwargs):
            find_calls.append(lv)
            return await _fake_find_pending_program(mv, lv, **kwargs)

        async def _fake_resolve(*a, **kw):
            return 1

        async def _fake_update_program_confirmed(*a, **kw):
            return True

        async def _fake_sync(program_id):
            pass

        review_calls = []

        async def _fake_create_review(block_text, **kwargs):
            review_calls.append((block_text, kwargs))
            return 321

        monkeypatch.setattr(escort_module, "_find_pending_program", _tracking_find)
        monkeypatch.setattr(escort_module, "_resolve_escort_employee_id", _fake_resolve)
        monkeypatch.setattr(escort_module, "_update_program_confirmed", _fake_update_program_confirmed)
        monkeypatch.setattr(escort_module, "_sync_roster_after_confirm", _fake_sync)
        monkeypatch.setattr(escort_module, "_create_escort_confirmation_review_draft", _fake_create_review)

        reply, note = await handle_admin_escort_completion(
            REAL_CONFIRMATION_BAD_PHONE_ENTRY_1, "8801880446111", "bridge2",
            recipient_phone="8801670535255",
        )

        assert "M.V. Farhin Adib-0" not in find_calls, "malformed entry must never reach draft matching"
        assert find_calls == ["M.V. Farhin Adib-3"]
        assert len(review_calls) == 1
        assert "0160230063" in review_calls[0][0]
        assert "#321" in reply
        assert note is not None  # the good entry still confirms and notifies the client

    async def test_review_draft_pushed_via_outbound_queue(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import _create_escort_confirmation_review_draft

        with patch("shared.draft_reply.create_draft_reply", new=AsyncMock(return_value=555)), \
             patch("modules.escort.execute", new=AsyncMock(return_value="UPDATE 1")), \
             patch("modules.escort.fetch_one", new=AsyncMock(return_value={"display_name": "Md Nurul Huda Hridoy"})), \
             patch("modules.outbound.enqueue", new=AsyncMock(return_value=1)) as mock_enqueue:
            fake_draft = {
                "mother_vessel": "MV ELIM", "lighter_vessel": "M.V. Farhin Adib-0",
                "master_mobile": "01731034486", "cargo_type": None, "importer": None,
                "capacity": None, "escort_name": "Md. Abdul Ajit Mullah", "escort_mobile": None,
                "date_str": "01.08.2026", "shift": "D", "destination": "Narayanganj",
            }
            draft_id = await _create_escort_confirmation_review_draft(
                "Escort Mobile: 0160230063", draft=fake_draft,
                recipient_phone="8801670535255", source="bridge2",
            )

        assert draft_id == 555
        mock_enqueue.assert_awaited_once()
        call = mock_enqueue.await_args
        assert call.args[0] == "8801880446111"
        assert "Admin REMINDER 555" in call.args[1]
        assert "Md Nurul Huda Hridoy" in call.args[1]
        assert "Do you think to review this message?" in call.args[1]
        assert call.kwargs["purpose"] == "escort-confirmation-review"


_FAKE_DRAFT = {
    "mother_vessel": "MV ELIM", "lighter_vessel": "M.V. Farhin Adib-0",
    "master_mobile": "01731034486", "cargo_type": None, "importer": None,
    "capacity": None, "escort_name": "Md. Abdul Ajit Mullah", "escort_mobile": "0160230063",
    "date_str": "01.08.2026", "shift": "D", "destination": "Narayanganj",
}


def _fake_review_row(status="pending"):
    return {
        "id": 321, "status": status, "intent": "escort_confirmation_review",
        "meta": json.dumps({"context": json.dumps({
            "draft": _FAKE_DRAFT,
            "recipient_phone": "8801670535255",
            "source": "bridge2",
            "raw_block": "Escort Mobile: 0160230063",
        })}),
    }


class TestApproveEscortConfirmationReview:

    async def test_approve_saves_as_originally_parsed(self, monkeypatch):
        import modules.escort as escort_module
        from modules.escort import approve_escort_confirmation_review

        with patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=_fake_review_row())), \
             patch.object(escort_module, "execute", new=AsyncMock(return_value="UPDATE 1")), \
             patch.object(escort_module, "_process_one_confirmation_entry", new=AsyncMock(return_value={
                 "status": "confirmed", "lighter_vessel": "M.V. Farhin Adib-0",
                 "final_slip": "slip text", "client_phone": "8801670535255", "client_bridge": "bridge2",
             })) as mock_process, \
             patch("modules.outbound.enqueue", new=AsyncMock(return_value=1)):
            reply = await approve_escort_confirmation_review(321, "8801880446111")

        assert "approved" in reply.lower()
        assert "M.V. Farhin Adib-0" in reply
        # The exact originally-parsed draft (with the malformed number) is
        # what gets saved -- never re-validated/corrected.
        assert mock_process.await_args.args[0]["escort_mobile"] == "0160230063"

    async def test_approve_already_reviewed_is_rejected(self, monkeypatch):
        import modules.escort as escort_module
        from modules.escort import approve_escort_confirmation_review

        with patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=_fake_review_row(status="approved"))):
            reply = await approve_escort_confirmation_review(321, "8801880446111")

        assert "approved" in reply.lower() or "ইতিমধ্যে" in reply

    async def test_approve_unknown_draft(self, monkeypatch):
        import modules.escort as escort_module
        from modules.escort import approve_escort_confirmation_review

        with patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=None)):
            reply = await approve_escort_confirmation_review(999, "8801880446111")

        assert "999" in reply


class TestRejectEscortConfirmationReview:

    async def test_reject_saves_nothing(self, monkeypatch):
        import modules.escort as escort_module
        from modules.escort import reject_escort_confirmation_review

        with patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=_fake_review_row())), \
             patch.object(escort_module, "execute", new=AsyncMock(return_value="UPDATE 1")) as mock_execute:
            reply = await reject_escort_confirmation_review(321, "8801880446111")

        assert "rejected" in reply.lower()
        assert "rejected" in mock_execute.await_args.args[0].lower()


class TestEditEscortConfirmationReview:

    async def test_edit_requires_complete_message(self, monkeypatch):
        import modules.escort as escort_module
        from modules.escort import edit_escort_confirmation_review

        with patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=_fake_review_row())):
            reply = await edit_escort_confirmation_review(321, "Escort Mobile: 01602300636", "8801880446111")

        assert "সম্পূর্ণ" in reply or "complete" in reply.lower()

    async def test_edit_with_complete_message_reparsed_and_saved(self, monkeypatch):
        import modules.escort as escort_module
        from modules.escort import edit_escort_confirmation_review

        corrected = (
            "Mother Vessel: M.V. Elim\n"
            "Lighter Vessel: M.V. Farhin Adib-0\n"
            "Master Mobile: 01731034486\n"
            "Escort Name: Md. Abdul Ajit Mullah\n"
            "Escort Mobile: 01602300636\n"
            "Start Date: 01.08.2026(D)\n"
            "Al-Rifa'i Integrated Services"
        )
        with patch.object(escort_module, "fetch_one", new=AsyncMock(return_value=_fake_review_row())), \
             patch.object(escort_module, "execute", new=AsyncMock(return_value="UPDATE 1")), \
             patch.object(escort_module, "_process_one_confirmation_entry", new=AsyncMock(return_value={
                 "status": "confirmed", "lighter_vessel": "M.V. Farhin Adib-0",
                 "final_slip": "slip", "client_phone": "8801670535255", "client_bridge": "bridge2",
             })) as mock_process, \
             patch("modules.outbound.enqueue", new=AsyncMock(return_value=1)):
            reply = await edit_escort_confirmation_review(321, corrected, "8801880446111")

        assert "edited" in reply.lower() or "✏️" in reply
        assert mock_process.await_args.args[0]["escort_mobile"] == "01602300636"
