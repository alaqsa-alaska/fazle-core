"""Unit tests — modules.bridge_poller._is_draft_always's new phone_block.{phone}
DB-flag check (Unified Admin AI Engine, Phase 3 Step 3).

Regression coverage for the previously-dead per-number auto-reply gate:
POST /api/wa/contacts/{phone}/block wrote phone_block.{phone} into
fazle_runtime_settings, but nothing ever read it -- _is_draft_always()
only checked static env-driven DRAFT_ALWAYS_PHONES/role/name lists. This
suite proves (a) the new DB check now short-circuits to True when the
flag is "true", and (b)/(c)/(d) every other case falls through to the
pre-existing env-list logic completely unchanged, including when the DB
read itself raises.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _empty_settings() -> SimpleNamespace:
    """A _settings stub with none of the static draft-always lists matching
    anything, so a False/absent/erroring DB flag deterministically falls
    through to a real (and here, predictably negative) evaluation of the
    pre-existing logic -- proving that logic still runs unmodified."""
    return SimpleNamespace(
        draft_always_phone_set=frozenset(),
        draft_always_role_set=frozenset(),
        draft_always_name_list=[],
        draft_name_prefix_list=[],
    )


class TestPhoneBlockGate:
    async def test_phone_block_true_returns_true_before_env_checks(self, monkeypatch):
        import modules.bridge_poller as bp

        monkeypatch.setattr(bp, "_settings", _empty_settings())

        async def _fake_get_runtime_setting(key, default="false"):
            assert key == "phone_block.8801700000001"
            return "true"

        monkeypatch.setattr(
            "modules.wa_chat_frontend.get_runtime_setting", _fake_get_runtime_setting
        )

        result = await bp._is_draft_always("8801700000001", "unknown", "Some Name")
        assert result is True

    async def test_phone_block_false_falls_through_to_env_logic(self, monkeypatch):
        import modules.bridge_poller as bp

        monkeypatch.setattr(bp, "_settings", _empty_settings())

        async def _fake_get_runtime_setting(key, default="false"):
            return "false"

        monkeypatch.setattr(
            "modules.wa_chat_frontend.get_runtime_setting", _fake_get_runtime_setting
        )

        # With every env-list empty, falling through must yield False, not True --
        # proves the new check did not short-circuit and existing logic ran.
        result = await bp._is_draft_always("8801700000002", "unknown", "Some Name")
        assert result is False

    async def test_phone_block_absent_falls_through_to_env_logic(self, monkeypatch):
        import modules.bridge_poller as bp

        monkeypatch.setattr(bp, "_settings", _empty_settings())

        async def _fake_get_runtime_setting(key, default="false"):
            # Simulates no row in fazle_runtime_settings: default is returned.
            return default

        monkeypatch.setattr(
            "modules.wa_chat_frontend.get_runtime_setting", _fake_get_runtime_setting
        )

        result = await bp._is_draft_always("8801700000003", "unknown", "Some Name")
        assert result is False

        # And existing env-list logic must still positively fire when it should --
        # proves the fallthrough path is fully intact, not just "returns False".
        settings_with_role_match = _empty_settings()
        settings_with_role_match.draft_always_role_set = frozenset({"accountant"})
        monkeypatch.setattr(bp, "_settings", settings_with_role_match)
        result2 = await bp._is_draft_always("8801700000003", "accountant", "Some Name")
        assert result2 is True

    async def test_get_runtime_setting_exception_falls_through_silently(self, monkeypatch):
        import modules.bridge_poller as bp

        monkeypatch.setattr(bp, "_settings", _empty_settings())

        async def _raising_get_runtime_setting(key, default="false"):
            raise RuntimeError("simulated DB outage")

        monkeypatch.setattr(
            "modules.wa_chat_frontend.get_runtime_setting", _raising_get_runtime_setting
        )

        # Must not raise -- must silently fall through to env-list logic.
        result = await bp._is_draft_always("8801700000004", "unknown", "Some Name")
        assert result is False

        # And existing logic must still work correctly after swallowing the error.
        settings_with_phone_match = _empty_settings()
        settings_with_phone_match.draft_always_phone_set = frozenset({"8801700000004"})
        monkeypatch.setattr(bp, "_settings", settings_with_phone_match)
        result2 = await bp._is_draft_always("8801700000004", "unknown", "Some Name")
        assert result2 is True
