"""
Unit tests — Phase 1B.2 escort-client identity and routing convergence.

Covers the canonical resolver (modules.identity_brain.resolve_escort_client_identity
and its integration into detect_identity()), the verified/unverified routing
split in modules.message_router, and the escort module's unverified-path
labeling. See knowledge_base/00_governance/management_decisions.md, Session
21 Phase 1B.2, for the full design this asserts.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


# ── helpers ────────────────────────────────────────────────────────────────────

def _seed_row(role: str, phone: str = "8801811111111", priority: int = 90):
    return {"role": role, "confidence": 90, "source": "admin_api", "priority": priority, "notes": ""}


def _contact_row(display_name: str, relation: str = "unknown", phone: str = "8801811111111"):
    return {"contact_id": 1, "display_name": display_name, "relation_name": relation}


# ── Resolver: verified sources ──────────────────────────────────────────────────

class TestVerifiedIdentitySources:
    async def test_env_phone_match_is_verified(self, monkeypatch):
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: ["8801811111111"])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801811111111", "")

        assert result["identity_role"] == "client_escort_buyer"
        assert result["identity_source"] == "env_phone"

    async def test_stored_role_match_is_verified(self):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(
            return_value=_seed_row("client_escort_buyer"),
        )):
            result = await detect_identity("8801811111111", "")

        assert result["identity_role"] == "client_escort_buyer"
        # source is whatever the seed row's own `source` column says (existing
        # Step 2 behavior, unchanged) — "admin_api" in this fixture.
        assert result["identity_source"] == "admin_api"

    @pytest.mark.parametrize("name", [
        "Escort ABC Shipping",
        "ABC Escort Client",
        "MV Agent - Escort",
        "ESCORT Buyer Rahman",
        "Client (Escort)",
        "escort-client",
    ])
    async def test_contact_name_marker_match_is_verified(self, name):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None,                       # seed miss
            None,                       # employee wbom_employees miss
            None,                       # employee fpe_employees fallback miss
            None,                       # cash miss
            None,                       # attendance miss
            None,                       # escort_roster miss
            _contact_row(name),         # contact hit
        ])):
            result = await detect_identity("8801811111111", "")

        assert result["identity_role"] == "client_escort_buyer", f"name={name!r} should verify"
        assert result["identity_source"] == "contact_name_marker"

    @pytest.mark.parametrize("name", [
        "escorted driver",
        "escorting team",
        "Prescott Ltd",
        "MV Golden Star",
        "",
    ])
    async def test_unrelated_contact_name_not_verified(self, name):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None, None, None, None, None, None,
            _contact_row(name),
        ])):
            result = await detect_identity("8801811111111", "")

        assert result["identity_role"] != "client_escort_buyer", f"name={name!r} should not verify"

    async def test_inactive_stored_role_only_not_verified(self):
        """_lookup_seed()'s own query already filters is_active/status — an
        inactive row simply never returns from fetch_one, so this is
        equivalent to no seed row at all."""
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None,  # inactive seed row filtered out by _lookup_seed's own SQL
            None, None, None, None, None,
            None,  # no contact either
        ])):
            result = await detect_identity("8801900000000", "ordinary hello")

        assert result["identity_role"] != "client_escort_buyer"


# ── Resolver: priority order ─────────────────────────────────────────────────────

class TestSourcePriority:
    async def test_stored_role_wins_even_if_env_phone_also_matches(self, monkeypatch):
        """Seed rule (Step 2) always runs before the new env_phone check
        (Step 3.7) — a stored row, whatever it says, takes priority. Here the
        stored row itself says client_escort_buyer, so this also confirms
        env_phone doesn't need to fire for the same result."""
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: ["8801811111111"])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(
            return_value=_seed_row("vendor"),
        )):
            result = await detect_identity("8801811111111", "")

        # Stored seed row (vendor) wins outright — env_phone (Step 3.7) is
        # never reached because Step 2 already returned.
        assert result["identity_role"] == "vendor"

    async def test_env_phone_wins_over_contact_name_marker(self, monkeypatch):
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: ["8801811111111"])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801811111111", "")

        # env_phone fires at Step 3.7, before Step 4's contact lookup even runs.
        assert result["identity_source"] == "env_phone"


# ── Resolver: phone normalization ────────────────────────────────────────────────

class TestPhoneNormalizationEquivalence:
    @pytest.mark.parametrize("raw", [
        "01811111111",
        "8801811111111",
        "+8801811111111",
        "1811111111",
    ])
    async def test_equivalent_phone_forms_match_same_allowlist_entry(self, monkeypatch, raw):
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: ["8801811111111"])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity(raw, "")

        assert result["identity_role"] == "client_escort_buyer", f"{raw!r} should match"
        assert result["identity_source"] == "env_phone"


# ── Resolver: empty allowlist semantics ──────────────────────────────────────────

class TestEmptyAllowlistSemantics:
    async def test_empty_list_unknown_sender_not_verified(self, monkeypatch):
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: [])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801900000000", "ordinary hello")

        assert result["identity_role"] == "unknown"

    async def test_empty_list_stored_role_still_verified(self, monkeypatch):
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: [])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(
            return_value=_seed_row("client_escort_buyer"),
        )):
            result = await detect_identity("8801811111111", "")

        assert result["identity_role"] == "client_escort_buyer"

    async def test_empty_list_contact_marker_still_verified(self, monkeypatch):
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: [])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None, None, None, None, None, None,
            _contact_row("Escort ABC Shipping"),
        ])):
            result = await detect_identity("8801811111111", "")

        assert result["identity_role"] == "client_escort_buyer"
        assert result["identity_source"] == "contact_name_marker"

    async def test_empty_list_order_like_message_is_unverified_not_promoted(self, monkeypatch):
        """The core safety property: an empty ESCORT_CLIENT_PHONES list must
        never silently promote an unknown, never-before-seen sender to a
        persistent verified client_escort_buyer identity just because their
        message looks escort-shaped."""
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: [])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801900000000", "MV GOLDEN STAR lighter escort lagbe")

        assert result["identity_role"] != "client_escort_buyer"
        assert result["identity_role"] == "repeat_client"
        assert result["identity_source"] == "text_hint"


# ── Canonical resolver contract (resolve_escort_client_identity) ────────────────

class TestCanonicalResolverContract:
    async def test_verified_result_shape(self, monkeypatch):
        from modules.identity_brain import resolve_escort_client_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: ["8801811111111"])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await resolve_escort_client_identity("8801811111111", "", "8801811111111@s.whatsapp.net")

        assert result["is_escort_client"] is True
        assert result["canonical_role"] == "client_escort_buyer"
        assert result["is_verified"] is True
        assert result["source"] == "env_phone"
        assert result["normalized_phone"] == "8801811111111"
        assert result["original_jid"] == "8801811111111@s.whatsapp.net"

    async def test_unverified_message_content_result_shape(self):
        from modules.identity_brain import resolve_escort_client_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await resolve_escort_client_identity(
                "8801900000000", "MV GOLDEN STAR lighter escort lagbe",
            )

        assert result["is_escort_client"] is False
        assert result["canonical_role"] is None
        assert result["is_verified"] is False
        assert result["source"] == "unverified_message_content"

    async def test_ordinary_sender_result_shape(self):
        from modules.identity_brain import resolve_escort_client_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await resolve_escort_client_identity("8801900000000", "hello there")

        assert result["is_escort_client"] is False
        assert result["canonical_role"] is None
        assert result["is_verified"] is False
        assert result["source"] is None


# ── Authority isolation — escort identity grants nothing else ───────────────────

class TestAuthorityIsolation:
    async def test_env_phone_match_does_not_affect_admin_check(self, monkeypatch):
        """An escort-client env-phone match must never be confused with an
        admin — admin resolution (Step 1) runs first and is untouched."""
        from modules.identity_brain import detect_identity
        import modules.escort as escort_module

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: ["8801700000001"])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            with patch("app.config.get_settings") as mock_settings:
                mock_settings.return_value.admin_number_list = ["8801700000001"]
                mock_settings.return_value.admin_meta_number = ""
                mock_settings.return_value.admin_bridge1_number = ""
                mock_settings.return_value.admin_bridge2_number = ""
                result = await detect_identity("8801700000001", "")

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "admin"

    async def test_seed_accountant_still_wins_over_env_escort_phone(self, monkeypatch):
        import modules.escort as escort_module
        from modules.identity_brain import detect_identity

        monkeypatch.setattr(escort_module, "_escort_client_phone_list", lambda: ["8801811111111"])

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(
            return_value=_seed_row("accountant"),
        )):
            result = await detect_identity("8801811111111", "")

        assert result["identity_role"] == "accountant"


# ── message_router: verified/unverified branch helper ───────────────────────────

class TestMessageRouterVerifiedHelper:
    def test_seed_sourced_repeat_client_is_verified(self):
        from modules.message_router import _is_verified_escort_identity

        identity = {"identity_source": "db_contact"}
        assert _is_verified_escort_identity(identity, "repeat_client") is True

    def test_text_hint_repeat_client_is_unverified(self):
        from modules.message_router import _is_verified_escort_identity

        identity = {"identity_source": "text_hint"}
        assert _is_verified_escort_identity(identity, "repeat_client") is False

    def test_non_escort_role_is_never_verified_escort(self):
        from modules.message_router import _is_verified_escort_identity

        identity = {"identity_source": "db_employee"}
        assert _is_verified_escort_identity(identity, "employee") is False

    def test_env_phone_sourced_client_escort_buyer_is_verified(self):
        from modules.message_router import _is_verified_escort_identity

        identity = {"identity_source": "env_phone"}
        assert _is_verified_escort_identity(identity, "client_escort_buyer") is True


# ── escort module: unverified-path labeling ──────────────────────────────────────

class TestUnverifiedPathLabeling:
    async def test_unverified_admin_message_is_labeled(self, monkeypatch):
        from modules.escort import handle_escort_client_message
        import modules.escort as escort_module
        import modules.message_router as router_module

        sender = "8801955555599"
        text = """
        MV GOLDEN STAR
        A/C Test Client
        Wheat
        01 AMENA-3 01933333333 Narayanganj 1000 MT
        06/05/2026 Day
        """

        saved = {}

        async def _fake_save(*args, **kwargs):
            saved["is_verified"] = kwargs.get("is_verified")
            return [1]

        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        from app.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
        monkeypatch.setattr(router_module, "get_primary_admin", lambda: "8801880446111")
        monkeypatch.setattr(escort_module, "is_minimum_escort_order", lambda order: True)

        _, admin_note = await handle_escort_client_message(text, sender, source="bridge1", is_verified=False)

        assert admin_note is not None
        assert "UNVERIFIED SENDER" in admin_note["text"]
        assert saved.get("is_verified") is False

    async def test_verified_admin_message_is_not_labeled(self, monkeypatch):
        from modules.escort import handle_escort_client_message
        import modules.escort as escort_module
        import modules.message_router as router_module

        sender = "8801955555598"
        text = """
        MV GOLDEN STAR
        A/C Test Client
        Wheat
        01 AMENA-3 01933333333 Narayanganj 1000 MT
        06/05/2026 Day
        """

        saved = {}

        async def _fake_save(*args, **kwargs):
            saved["is_verified"] = kwargs.get("is_verified")
            return [1]

        monkeypatch.setenv("ESCORT_CLIENT_PHONES", sender)
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        from app.config import get_settings
        get_settings.cache_clear()
        monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
        monkeypatch.setattr(router_module, "get_primary_admin", lambda: "8801880446111")
        monkeypatch.setattr(escort_module, "is_minimum_escort_order", lambda order: True)

        _, admin_note = await handle_escort_client_message(text, sender, source="bridge1", is_verified=True)

        assert admin_note is not None
        assert "UNVERIFIED SENDER" not in admin_note["text"]
        assert saved.get("is_verified") is True

    async def test_verified_sender_not_in_allowlist_still_allowed(self, monkeypatch):
        """The real gap this convergence fix closes: a sender verified via a
        stored contact-role or contact-name marker (not ESCORT_CLIENT_PHONES)
        must not be rejected by the phone-allowlist gate, which only knows
        about .env membership."""
        from modules.escort import handle_escort_client_message
        import modules.escort as escort_module
        import modules.message_router as router_module

        sender = "8801955555597"
        text = "MV STAR lighter AMENA 01711273432 escort lagbe 07/05/2026 Night"

        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")  # sender NOT in allowlist
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        from app.config import get_settings
        get_settings.cache_clear()

        async def _fake_save(*args, **kwargs):
            return [1]

        monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
        monkeypatch.setattr(router_module, "get_primary_admin", lambda: "8801880446111")

        # is_verified=True simulates a sender verified via stored role or
        # contact-name marker — the phone-list gate must be bypassed.
        result = await handle_escort_client_message(text, sender, source="bridge1", is_verified=True)

        assert result != ("", None), "verified sender must not be rejected by the phone-allowlist gate"

    async def test_unverified_sender_still_gated_by_allowlist_when_closed(self, monkeypatch):
        """Unchanged pre-1B.2 behavior: an unverified sender not in a
        non-empty allowlist is still rejected — this fix only changes the
        verified path."""
        from modules.escort import handle_escort_client_message
        import modules.escort as escort_module

        sender = "8801955555596"
        text = "MV STAR lighter AMENA 01711273432 escort lagbe 07/05/2026 Night"

        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "8801700000099")  # non-empty, sender not in it
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        from app.config import get_settings
        get_settings.cache_clear()

        result = await handle_escort_client_message(text, sender, source="bridge1", is_verified=False)

        assert result == ("", None)


# ── webhook LID resolution parity ────────────────────────────────────────────────

class TestWebhookLidResolutionParity:
    async def test_resolved_lid_sender_saved_with_real_phone(
        self, client, test_db_pool, monkeypatch, mock_all_services,
    ):
        """A webhook event whose sender is an @lid identifier must resolve to
        the real phone via the same whatsmeow_lid_map mechanism the SQLite
        poller already uses — previously the webhook path never attempted
        this at all and always classified such senders as unknown.

        mock_all_services: an "unknown"-role generic message falls through
        to the AI reply path (Ollama, falling back to Groq) — without
        mocking that, this test attempts a real network call and can hang/
        timeout depending on unrelated test ordering/state. Unrelated to the
        LID resolution behavior itself, just test hygiene."""
        import modules.bridge_poller as poller_module

        monkeypatch.setattr(
            poller_module, "_load_lid_map",
            lambda whatsapp_db: {"999888777": "8801844444444"},
        )

        unique_text = "hello there — lid-resolved-test-marker-001"
        payload = {
            "sender": "999888777@lid",
            "message": unique_text,
            "timestamp": "2026-07-17T10:00:00Z",
            "message_id": "test-lid-resolved-001",
            "source": "bridge1",
        }
        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT sender_number FROM wbom_whatsapp_messages WHERE message_body = $1",
                unique_text,
            )
        assert row is not None
        assert row["sender_number"] == "8801844444444"

    async def test_unresolved_lid_sender_not_misclassified_as_a_phone(
        self, client, test_db_pool, monkeypatch, mock_all_services,
    ):
        """An @lid sender with no entry in the lid map must not have a phone
        number invented for it — it gets the same 'unresolved:<lid>'
        sentinel the poller uses, so identity resolution safely falls
        through to unknown rather than misclassifying or colliding two
        distinct LIDs into the same identity.

        mock_all_services: see the note on the sibling test above — an
        unresolved/unknown sender's generic text otherwise reaches the real
        AI reply path."""
        import modules.bridge_poller as poller_module

        monkeypatch.setattr(poller_module, "_load_lid_map", lambda whatsapp_db: {})

        unique_text = "hello there — lid-unresolved-test-marker-001"
        payload = {
            "sender": "111222333@lid",
            "message": unique_text,
            "timestamp": "2026-07-17T10:00:00Z",
            "message_id": "test-lid-unresolved-001",
            "source": "bridge1",
        }
        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT sender_number FROM wbom_whatsapp_messages WHERE message_body = $1",
                unique_text,
            )
        assert row is not None
        assert row["sender_number"] == "unresolved:111222333"
