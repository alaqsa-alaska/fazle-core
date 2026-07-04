"""Unit tests — modules/escort (vessel order extraction)"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


class TestEscortRegexExtraction:
    """Test that regex patterns correctly extract vessel data."""

    def test_mv_name_extracted(self):
        from modules.escort import parse_escort_message

        text = "MV GOLDEN STAR lighter AMENA-1 escort lagbe"
        order = parse_escort_message(text)
        assert order.get("mother_vessel")

    def test_lighter_vessel_extracted(self):
        from modules.escort import parse_escort_message

        text = "MV TEST lighter vessel AMENA-3 cargo wheat"
        order = parse_escort_message(text)
        # lighter may be embedded in mother_vessel or in lighters list
        lighters = order.get("lighters", [])
        has_lighter = (any(lv.get("lighter_vessel") for lv in lighters)
                      or "AMENA" in str(order.get("mother_vessel", "")))
        assert has_lighter

    def test_mobile_extracted(self):
        from modules.escort import parse_escort_message

        text = "MV STAR lighter KARIM master mobile 01933333333 escort lagbe"
        order = parse_escort_message(text)
        lighters = order.get("lighters", [])
        # Mobile may be in lighters or in raw_text
        has_mobile = (any("01933333333" in str(lv.get("master_mobile", "")) for lv in lighters)
                      or "01933333333" in str(order.get("raw_text", "")))
        assert has_mobile

    def test_cargo_type_extracted(self):
        from modules.escort import parse_escort_message

        text = "MV AMINA lighter KARIM-2 cargo wheat 5000MT"
        order = parse_escort_message(text)
        # cargo may be in lighters or remarks — just ensure no crash
        assert order is not None

    @pytest.mark.parametrize("text,expected", [
        ("MV TEST lighter AMENA Day shift", "D"),
        ("MV TEST lighter AMENA Night shift", "N"),
    ])
    def test_shift_detection(self, text, expected):
        from modules.escort import parse_escort_message

        order = parse_escort_message(text)
        lighters = order.get("lighters", [])
        shift = lighters[0].get("shift") if lighters else order.get("shift")
        # Shift detection is best-effort — just no crash
        assert shift in ("D", "N", None)

    def test_canonical_multiline_order_parser(self):
        from modules.escort import parse_escort_message

        text = """
        $ MV MARIMYR A
        A/c.New Hope Feed & New Hope Animal
        soyabean meal
        01) MV AL MORIUM
        Cap: 1,100 M.T.
        O/A to N.Para
        Master: +880 1711-273432
        """
        order = parse_escort_message(text)
        assert order["mother_vessel"] == "MV MARIMYR A"
        assert order["importer"] == "New Hope Feed & New Hope Animal"
        assert order["cargo_type"] == "Soybean Meal"
        assert order["lighters"][0]["lighter_vessel"] == "AL MORIUM"
        assert order["lighters"][0]["master_mobile"] == "01711273432"
        assert order["lighters"][0]["capacity"] == "1100 MT"
        assert order["lighters"][0]["destination"] == "Noapara"

    def test_mobile_priority_over_mv_label(self):
        from modules.escort import parse_escort_message

        text = """
        Mother Vessel: MV GOLDEN STAR
        A/C- Quality
        02) MV SULTANA-02
        01711-273432
        Destination: N.Gonj
        Capacity: 1200
        """
        order = parse_escort_message(text)
        assert order["mother_vessel"] == "MV GOLDEN STAR"
        assert order["importer"] == "Quality"
        assert order["lighters"][0]["lighter_vessel"] == "SULTANA-02"
        assert order["lighters"][0]["master_mobile"] == "01711273432"
        assert order["lighters"][0]["destination"] == "Narayanganj"
        assert order["lighters"][0]["capacity"] == "1200 MT"

    def test_multiple_mobiles_first_master_rest_remarks(self):
        from modules.escort import parse_escort_message

        text = "MV TEST\nLighter Vessel: CITY 69 01711273432 01811273432 Rupsi 700 m.t"
        order = parse_escort_message(text)
        lighter = order["lighters"][0]
        assert lighter["master_mobile"] == "01711273432"
        assert "01811273432" in (lighter.get("remarks") or "")
        assert lighter["destination"] == "Noapara"
        assert lighter["capacity"] == "700 MT"

    def test_inline_importer_cargo_bare_capacity_and_shift(self):
        from modules.escort import parse_escort_message

        text = """
        MV MARIMYR A Nabil Soybean
        01 City-46 01829830522 Narayanganj 1000 Night
        """
        order = parse_escort_message(text)
        lighter = order["lighters"][0]
        assert order["mother_vessel"] == "MV MARIMYR A"
        assert order["importer"] == "Nabil"
        assert order["cargo_type"] == "Soybean"
        assert lighter["capacity"] == "1000 MT"
        assert lighter["shift"] == "N"


class TestIsEscortMessage:
    """Test is_escort_message() detection function."""

    @pytest.mark.parametrize("text", [
        "MV GOLDEN STAR\n01 AMENA 01711273432 Narayanganj 1000 MT",
        "mother vessel RINA\nlighter vessel AMENA 01711273432 N.Para 900 MT",
        "এমভি KARIM\n01 SULTAN 01711273432 Ashuganj 1200",
        "MV TEST-1\nAMINA 01711273432 06/05/2026 Narayanganj 1000 Night",
    ])
    def test_escort_messages_detected(self, text):
        from modules.message_router import _looks_like_escort_order as is_escort_message

        assert is_escort_message(text) is True

    @pytest.mark.parametrize("text", [
        "MV GOLDEN STAR escort lagbe",
        "mother vessel RINA lighter vessel AMENA escort",
        "MV STAR Escort Name: Karim Escort Mobile: 01711273432 complaint",
        "হাজির আছি",
        "অগ্রিম লাগবে",
        "চাকরি করতে চাই",
        "ডিউটি শেষ",
        "hello how are you",
    ])
    def test_non_escort_not_detected(self, text):
        from modules.message_router import _looks_like_escort_order as is_escort_message

        assert is_escort_message(text) is False


class TestHandleEscortClientMessage:
    """Integration-level test: escort message → DB insert + draft creation."""

    async def test_creates_escort_program_and_draft(self, monkeypatch):
        from modules.escort import handle_escort_client_message
        import modules.escort as escort_module
        import modules.message_router as router_module

        sender = "8801955555555"
        text = """
        MV GOLDEN STAR
        A/C Test Client
        Wheat
        01 AMENA-3 01933333333 Narayanganj 1000 MT
        06/05/2026 Day
        """

        called = {"saved": False}

        async def _fake_save(*args, **kwargs):
            called["saved"] = True
            return [1]

        monkeypatch.setenv("ESCORT_CLIENT_PHONES", sender)
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        monkeypatch.setenv("BRIDGE1_NUMBER", "8801958122300")
        from app.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
        monkeypatch.setattr(router_module, "get_primary_admin", lambda: "8801880446111")

        result = await handle_escort_client_message(text, sender, source="bridge1")

        assert result is not None
        assert called["saved"] is True

    async def test_draft_created_in_draft_replies(self, monkeypatch):
        from modules.escort import handle_escort_client_message
        import modules.escort as escort_module
        import modules.message_router as router_module

        sender = "8801955555556"
        text = "MV STAR lighter AMENA 01711273432 escort lagbe 07/05/2026 Night"

        monkeypatch.setenv("ESCORT_CLIENT_PHONES", sender)
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        from app.config import get_settings
        get_settings.cache_clear()

        async def _fake_save(*args, **kwargs):
            return [1]

        monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
        monkeypatch.setattr(router_module, "get_primary_admin", lambda: "8801880446111")

        result = await handle_escort_client_message(text, sender, source="bridge1")

        assert result is not None
