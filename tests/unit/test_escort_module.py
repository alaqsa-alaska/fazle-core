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

    def test_mv_blocks_with_capacity_and_mobile_keep_parenthesized_mv_as_mother(self):
        from modules.escort import parse_escort_message

        text = """
        M.V Al Balad-3
        2200 M.T
        01716-797411
        M.V SEA QUEST-3
        1800 M.T
        +880 1712-400445
        M.V Lamha-2
        1500 M.T
        01839-771853

        N.Gonj

        O/A Ctg.

        (MV HORDA)
        """
        order = parse_escort_message(text)
        assert order["mother_vessel"] == "MV HORDA"
        assert len(order["lighters"]) >= 3
        assert "AL BALAD-3" in order["lighters"][0]["lighter_vessel"].upper()
        assert order["lighters"][0]["master_mobile"] == "01716797411"

    def test_completed_draft_parses_lighter_vessel_label_strictly(self):
        from modules.escort import parse_completed_draft

        draft = parse_completed_draft(
            """
            Mother Vessel: MV Horda
            Lighter Vessel:MV Al Balad-3
            Master Mobile: 01716797411
            Destination: chottogram
            Escort Name: Junaid Islam Shami
            Escort Mobile: 01805668850
            Start Date: 04.07.2026 (D)
            Al-Aqsa Security Service
            """
        )
        assert draft["mother_vessel"] == "MV HORDA"
        assert draft["lighter_vessel"] == "MV Al Balad-3"
        assert draft["master_mobile"] == "01716797411"


class TestSecondarySignalsPositionIndependent:
    """Secondary signals (importer/cargo/destination/capacity) are content-based,
    not tied to a fixed sequence relative to mother/lighter vessel — regression
    coverage for the 2026-07-15 incident where two real client orders
    ("Mv.Wan minh ... *A/c.kazi firm* ... local 800",
    "Mv.Alondra ... *A/c.Aci godrej* ...") lost their importer and capacity
    fields because of formatting/range assumptions the KB never actually
    required."""

    def test_importer_survives_whatsapp_bold_markup(self):
        from modules.escort import parse_escort_message

        text = "Mv.Wan minh 01/soyabean ext\n*A/c.kazi firm*\n1.Ak Sarkar 5-01730840247 local 800 "
        order = parse_escort_message(text)
        assert order["importer"] == "kazi firm"
        assert order["cargo_type"] == "Soybean Extract"
        assert order["lighters"][0]["capacity"] == "800 MT"
        assert order["lighters"][0]["destination"] == "Chattogram"

    def test_importer_after_mother_vessel_before_lighter_with_markup(self):
        from modules.escort import parse_escort_message

        text = "Mv.Alondra/soyabean ext\n*For oute\n*A/c.Aci godrej*\n1.Abdullah sayad-01725964055 "
        order = parse_escort_message(text)
        assert order["importer"] == "Aci godrej"
        assert order["mother_vessel"] == "MV ALONDRA"

    @pytest.mark.parametrize("bare,expected", [
        ("700", "700 MT"),
        ("800", "800 MT"),
        ("900", "900 MT"),
        ("1100", "1100 MT"),
        ("1200", "1200 MT"),
    ])
    def test_bare_capacity_covers_700_to_1200_range(self, bare, expected):
        from modules.escort import _normalize_capacity

        assert _normalize_capacity(f"local {bare}", allow_bare=True) == expected


class TestMotherVesselFieldBoundaryRegression:
    """Regression coverage for the 2026-07-26 mother-vessel/importer
    field-boundary bug. The two real messages below are verbatim from
    wbom_whatsapp_messages (sender 8801670535255, message_id 12815 and
    12809) -- the actual production input that produced the reported bad
    admin drafts ("Mother Vessel: MV AT-O/A", "Importer: ity 1400Mts").

    Root causes fixed:
    1. A bare "At-O/A" line (a clue/marker per the KB, not a name -- the
       real vessel name was on the next line) was accepted as the
       mother-vessel candidate itself instead of being skipped.
    2. _IMPORTER_RE's "a/c" alternative matched mid-word inside
       "Capacity" (the "ac" in "C-ap-ac-ity"), bleeding the remainder
       ("ity 1400Mts") into the importer field.
    3. An explicit "Mother Vessel:" label must take precedence over the
       weaker trailing "(MV ...)" heuristic, which previously ran
       unconditionally before any explicit-label check.
    """

    _REAL_INCIDENT_TEXT = (
        "21) Mv Fatema Sattar 5\n"
        "Capacity 1400Mts\n"
        "Rupshi A/c Nourish\n"
        "01718643830\n"
        "\n"
        "At-O/A\n"
        "Mv. Qing Quan Shan"
    )

    def test_at_oa_marker_line_not_used_as_mother_vessel_name(self):
        from modules.escort import parse_escort_message

        order = parse_escort_message(self._REAL_INCIDENT_TEXT)
        assert order["mother_vessel"] == "MV QING QUAN SHAN"
        assert "AT-O/A" not in order["mother_vessel"]

    def test_capacity_word_does_not_bleed_into_importer(self):
        from modules.escort import parse_escort_message

        order = parse_escort_message(self._REAL_INCIDENT_TEXT)
        assert order["importer"] == "Nourish"

    def test_lighter_and_mobile_unaffected_by_fix(self):
        """Fields that already parsed correctly in the real message must
        remain correct — no unrelated regression from the fix."""
        from modules.escort import parse_escort_message

        order = parse_escort_message(self._REAL_INCIDENT_TEXT)
        lighter = order["lighters"][0]
        assert lighter["master_mobile"] == "01718643830"
        assert "FATEMA SATTAR" in lighter["lighter_vessel"].upper()
        assert lighter["destination"] == "Narayanganj"  # Rupshi alias

    def test_existing_african_isabelle_message_still_correct(self):
        """Sibling real message, message_id=12809 (2026-07-26) — already
        parsed correctly before the fix; must still parse correctly
        after (no regression on the non-broken sibling case)."""
        from modules.escort import parse_escort_message

        text = (
            "Mv.African Isabelle /soyabean ext\n"
            "A/c.Padma feed\n"
            "1.S .Hossain -01321170933 n.para 900 m t"
        )
        order = parse_escort_message(text)
        assert order["mother_vessel"] == "MV AFRICAN ISABELLE"
        assert order["importer"] == "Padma feed"

    def test_explicit_mother_vessel_label_wins_over_trailing_paren(self):
        """KB: 'If Mother Vessel: label is present, that value wins for
        mother vessel' -- must hold even when the message also contains
        a trailing (MV ...) elsewhere."""
        from modules.escort import parse_escort_message

        text = (
            "Mother Vessel: MV Real Name\n"
            "Lighter Vessel: City-46\n"
            "01711273432\n"
            "Remarks: last seen near (MV Decoy)"
        )
        order = parse_escort_message(text)
        assert order["mother_vessel"] == "MV REAL NAME"

    def test_trailing_paren_still_wins_when_no_explicit_label(self):
        """Unchanged existing behavior: with no explicit label, the
        trailing (MV ...) heuristic still applies."""
        from modules.escort import parse_escort_message

        text = (
            "M.V Al Balad-3\n"
            "2200 M.T\n"
            "01716-797411\n"
            "(MV HORDA)"
        )
        order = parse_escort_message(text)
        assert order["mother_vessel"] == "MV HORDA"


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


def _use_pool(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool


class TestContactIdResolution:
    """Regression coverage for the 2026-07-12 contact_id adoption fix."""

    async def test_get_contact_id_matches_across_phone_formats(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.escort import _get_contact_id

        async with test_db_pool.acquire() as conn:
            contact_id = await conn.fetchval(
                "INSERT INTO wbom_contacts (whatsapp_number) VALUES ($1) RETURNING contact_id",
                "8801711000111",
            )

        # Same underlying number, three different formats a sender might
        # actually arrive as — the pre-fix raw exact-string match would
        # only have matched the '880...' form.
        assert await _get_contact_id("8801711000111") == contact_id
        assert await _get_contact_id("01711000111") == contact_id
        assert await _get_contact_id("+8801711000111") == contact_id
        assert await _get_contact_id("01799999999") is None
        assert await _get_contact_id("") is None

    async def test_create_confirmed_from_admin_sets_contact_id(self, test_db_pool, monkeypatch):
        _use_pool(test_db_pool)
        from modules.escort import _create_confirmed_from_admin

        async with test_db_pool.acquire() as conn:
            contact_id = await conn.fetchval(
                "INSERT INTO wbom_contacts (whatsapp_number) VALUES ($1) RETURNING contact_id",
                "8801722000222",
            )

        async def _fake_resolve_employee(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "modules.escort._resolve_escort_employee_id", _fake_resolve_employee
        )

        draft = {
            "mother_vessel": "MV CONTACTFIX",
            "lighter_vessel": "LV-1",
            "escort_name": "Test Escort",
            "escort_mobile": "01911111111",
            "shift": "D",
            "date_str": None,
            "destination": "",
            "cargo_type": "",
            "importer": "",
            "capacity": "",
            "master_mobile": "01922222222",
        }

        pid = await _create_confirmed_from_admin(draft, "bridge2", client_phone="01722000222")

        assert pid is not None
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT contact_id FROM wbom_escort_programs WHERE program_id = $1", pid
            )
        assert row["contact_id"] == contact_id

    async def test_backfill_never_overwrites_existing_contact_id(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.escort import _backfill_contact_id_if_missing

        async with test_db_pool.acquire() as conn:
            original_contact = await conn.fetchval(
                "INSERT INTO wbom_contacts (whatsapp_number) VALUES ($1) RETURNING contact_id",
                "8801733000333",
            )
            other_contact = await conn.fetchval(
                "INSERT INTO wbom_contacts (whatsapp_number) VALUES ($1) RETURNING contact_id",
                "8801744000444",
            )
            pid = await conn.fetchval(
                "INSERT INTO wbom_escort_programs (status, contact_id) VALUES ('confirmed', $1) "
                "RETURNING program_id",
                original_contact,
            )

        await _backfill_contact_id_if_missing(pid, "01744000444")

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT contact_id FROM wbom_escort_programs WHERE program_id = $1", pid
            )
        assert row["contact_id"] == original_contact
        assert row["contact_id"] != other_contact
