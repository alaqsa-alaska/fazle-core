"""Unit tests -- Unified Admin AI Engine, Phase 3 Step 4.

Covers: new registry entries (RT-015, BUS-015, BUS-016) resolve correctly
and carry the right min_role/prohibited_behavior, and their live_adapters
handlers behave correctly (read filtering, write dispatch, and the
action-value guard on _admin_directive_write raising ValueError before
any DB call).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules.ai_console.live_adapters import ReadonlyBusinessAdapter, ReadonlyRuntimeAdapter
from modules.ai_console.registry import build_registry_loader

pytestmark = pytest.mark.unit


@pytest.fixture()
def loader():
    return build_registry_loader()


class TestRegistryEntries:
    def test_all_three_tool_ids_present(self, loader):
        for tool_id in ("RT-015", "BUS-015", "BUS-016"):
            assert loader.exists(tool_id), f"{tool_id} missing from registry"

    def test_rt015_min_role_metadata_is_office_assistant(self, loader):
        entry = loader.get("RT-015")
        assert entry.metadata.get("min_role") == "office_assistant"
        # Documented-not-yet-enforced per Flag 1 -- ReadonlyRuntimeAdapter.fetch()
        # takes no actor_role_level parameter, confirmed by signature inspection.
        import inspect

        sig = inspect.signature(ReadonlyRuntimeAdapter.fetch)
        assert "actor_role_level" not in sig.parameters

    def test_bus015_and_bus016_min_role_is_admin(self, loader):
        assert loader.get("BUS-015").metadata.get("min_role") == "admin"
        assert loader.get("BUS-016").metadata.get("min_role") == "admin"

    def test_bus016_prohibited_behavior_includes_new_items(self, loader):
        prohibited = loader.get("BUS-016").prohibited_behavior
        assert "direct_whatsapp_send" in prohibited
        assert "unrestricted_sql_write" in prohibited
        # Base BUS-* set must still be present, per "same set... PLUS".
        assert "business_write" in prohibited
        assert "unrestricted_sql" in prohibited
        assert "secret_exposure" in prohibited

    def test_no_existing_entry_disturbed(self, loader):
        rt001 = loader.get("RT-001")
        assert rt001.name == "systemd"
        assert rt001.metadata == {}
        bus001 = loader.get("BUS-001")
        assert bus001.metadata == {"min_role": "viewer"}
        bus014 = loader.get("BUS-014")
        assert bus014.metadata == {"min_role": "accountant"}


class TestAdminDirectiveReadHandler:
    async def test_returns_structured_rows(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-015")
        fake_rows = [
            {"id": 1, "directive_text": "always confirm advances over 5000tk", "category": "general"},
            {"id": 2, "directive_text": "note about this client", "category": "contact_note"},
        ]
        with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=fake_rows)):
            envelope = await adapter._admin_directive_read(entry, {"subject_key": None})
        assert envelope.status == "ok"
        assert envelope.data["rows"] == fake_rows
        assert envelope.data["count"] == 2

    async def test_category_filter_applied_in_handler(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-015")
        fake_rows = [
            {"id": 1, "category": "general"},
            {"id": 2, "category": "autoreply_policy"},
        ]
        with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=fake_rows)):
            envelope = await adapter._admin_directive_read(entry, {"category": "autoreply_policy"})
        assert envelope.data["rows"] == [{"id": 2, "category": "autoreply_policy"}]

    async def test_exception_returns_failed_envelope_not_raise(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-015")
        with patch("modules.admin_directives.list_directives", new=AsyncMock(side_effect=RuntimeError("db down"))):
            envelope = await adapter._admin_directive_read(entry, {})
        assert envelope.status == "failed"
        assert envelope.errors


class TestAdminDirectiveWriteHandler:
    async def test_save_action_calls_save_directive(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-016")
        with patch("modules.admin_directives.save_directive", new=AsyncMock(return_value=7)) as mock_save:
            envelope = await adapter._admin_directive_write(
                entry,
                {
                    "action": "save",
                    "directive_text": "remember this",
                    "created_by_phone": "8801700000001",
                    "created_by_channel": "whatsapp",
                },
            )
        mock_save.assert_awaited_once()
        assert envelope.status == "ok"
        assert envelope.data == {"action": "save", "id": 7}

    async def test_revoke_action_calls_revoke_directive(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-016")
        with patch("modules.admin_directives.revoke_directive", new=AsyncMock(return_value=True)) as mock_revoke:
            envelope = await adapter._admin_directive_write(
                entry,
                {"action": "revoke", "directive_id": 7, "revoked_by_phone": "8801700000001"},
            )
        mock_revoke.assert_awaited_once()
        assert envelope.status == "ok"
        assert envelope.data == {"action": "revoke", "id": 7, "revoked": True}

    async def test_invalid_action_raises_value_error_immediately(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-016")
        with patch("modules.admin_directives.save_directive", new=AsyncMock()) as mock_save, \
             patch("modules.admin_directives.revoke_directive", new=AsyncMock()) as mock_revoke:
            with pytest.raises(ValueError, match="Invalid action"):
                await adapter._admin_directive_write(entry, {"action": "delete_everything"})
        # The guard must fire before any DB call -- neither function may have been touched.
        mock_save.assert_not_awaited()
        mock_revoke.assert_not_awaited()

    def test_no_raw_sql_in_handler_source(self):
        """Structural proof, matching the Stage E pattern: the write handler
        never issues SQL itself -- it only calls modules.admin_directives'
        already-reviewed functions."""
        import inspect

        source = inspect.getsource(ReadonlyBusinessAdapter._admin_directive_write)
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "fetch_val(", "fetch_all(", "execute("):
            assert forbidden not in source, f"raw SQL / direct DB call found: {forbidden!r}"
