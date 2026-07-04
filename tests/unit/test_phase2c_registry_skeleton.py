from __future__ import annotations

import ast
from pathlib import Path

import pytest


def test_registry_loader_returns_expected_ids():
    from modules.ai_console.registry import build_registry_loader

    loader = build_registry_loader()

    for entry_id in (
        "RT-001",
        "RT-009",
        "BUS-001",
        "BUS-003",
        "MEM-001",
        "EVID-001",
        "STD-009",
        "ORCH-005",
    ):
        assert loader.exists(entry_id)


def test_every_registry_entry_has_required_fields():
    from modules.ai_console.registry import build_registry_loader

    loader = build_registry_loader()
    for entry in loader.list_entries():
        assert entry.id
        assert entry.name
        assert entry.family
        assert entry.purpose
        assert entry.authority
        assert entry.allowed_mode
        assert entry.output_schema_ref
        assert entry.confidence_default
        assert entry.traceability_refs


@pytest.mark.asyncio
async def test_mock_runtime_adapter_returns_standard_envelope():
    from modules.ai_console.mocks import MockRuntimeAdapter
    from modules.ai_console.registry import build_registry_loader

    adapter = MockRuntimeAdapter(build_registry_loader())
    result = await adapter.fetch("RT-001", {})

    assert result.status == "ok"
    assert result.confidence == "Verified"
    assert result.source == "mock"
    assert result.authority == "runtime_tool"
    assert "RT-001" in result.traceability


@pytest.mark.asyncio
async def test_mock_business_adapter_returns_standard_envelope():
    from modules.ai_console.mocks import MockBusinessAdapter
    from modules.ai_console.registry import build_registry_loader

    adapter = MockBusinessAdapter(build_registry_loader())
    result = await adapter.fetch("BUS-003", {})

    assert result.status == "ok"
    assert result.confidence == "Verified"
    assert result.source == "mock"
    assert result.authority == "postgres_readonly_view"
    assert "rows" in result.data


@pytest.mark.asyncio
async def test_mock_memory_adapter_returns_standard_envelope():
    from modules.ai_console.mocks import MockMemoryAdapter
    from modules.ai_console.registry import build_registry_loader

    adapter = MockMemoryAdapter(build_registry_loader())
    result = await adapter.fetch("MEM-001", {})

    assert result.status == "ok"
    assert result.confidence == "Partially Verified"
    assert result.source == "mock"
    assert result.authority == "ai_memory"
    assert "records" in result.data


def test_tool_result_envelope_rejects_invalid_values():
    from modules.ai_console.envelopes import ToolResultEnvelope

    with pytest.raises(ValueError):
        ToolResultEnvelope(
            tool_id="RT-001",
            tool_name="systemd",
            status="bad",
            confidence="Verified",
            source="mock",
            timestamp="2026-06-30T00:00:00Z",
            data={},
            authority="runtime_tool",
        )

    with pytest.raises(ValueError):
        ToolResultEnvelope(
            tool_id="RT-001",
            tool_name="systemd",
            status="ok",
            confidence="Bad",
            source="mock",
            timestamp="2026-06-30T00:00:00Z",
            data={},
            authority="runtime_tool",
        )


def test_evidence_envelope_assembles_correctly():
    from modules.ai_console.envelopes import ToolResultEnvelope
    from modules.ai_console.mocks import MockEvidenceAdapter

    runtime_result = ToolResultEnvelope(
        tool_id="RT-001",
        tool_name="systemd",
        status="ok",
        confidence="Verified",
        source="mock",
        timestamp="2026-06-30T00:00:00Z",
        data={"services": []},
        authority="runtime_tool",
        traceability=["RT-001", "ORCH-001"],
    )

    envelope = MockEvidenceAdapter().assemble(
        request_id="req-1",
        mode="mock_orchestration",
        classification={"mode": "runtime"},
        runtime=[runtime_result],
        business=[],
        memory=[],
        knowledge=[],
        provider={},
        traceability=["ORCH-005"],
    )

    assert envelope.confidence == "Verified"
    assert envelope.runtime[0]["tool_id"] == "RT-001"
    assert "EVID-001" in envelope.traceability


@pytest.mark.asyncio
async def test_orchestrator_selects_correct_mock_tool_family():
    from modules.ai_console.orchestrator import orchestrate_mock_request

    runtime_response = await orchestrate_mock_request("docker health check")
    business_response = await orchestrate_mock_request("payroll summary")
    memory_response = await orchestrate_mock_request("last instruction note")
    general_response = await orchestrate_mock_request("summarize this")

    assert runtime_response.mode == "runtime"
    assert runtime_response.selected_tools[0].startswith("RT-")
    assert business_response.mode == "business"
    assert business_response.selected_tools[0].startswith("BUS-")
    assert memory_response.mode == "memory"
    assert memory_response.selected_tools[0].startswith("MEM-")
    assert general_response.mode == "general"
    assert general_response.selected_tools == []


@pytest.mark.asyncio
async def test_orchestrator_traceability_ids_are_present():
    from modules.ai_console.orchestrator import orchestrate_mock_request

    response = await orchestrate_mock_request("postgres health")

    assert "ORCH-005" in response.traceability
    assert "EVID-001" in response.traceability
    assert any(item.startswith("RT-") for item in response.traceability)


def test_no_prohibited_side_effects_exist():
    from modules.ai_console import adapters

    assert "unrestricted_shell" in adapters.RuntimeAdapter.prohibited_side_effects
    assert "business_write" in adapters.BusinessAdapter.prohibited_side_effects
    assert "destructive_delete_without_owner_approval" in adapters.MemoryAdapter.prohibited_side_effects
    assert "direct_db_access" in adapters.ProviderAdapter.prohibited_side_effects


def test_phase2c_skeleton_does_not_import_prohibited_modules():
    root = Path("/home/azim/core/modules/ai_console")
    forbidden = {
        "modules.payroll",
        "modules.escort_roster",
        "modules.attendance",
        "modules.message_router",
        "modules.outbound",
        "subprocess",
    }
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
                assert forbidden.isdisjoint(names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in forbidden


def test_no_production_db_write_path_is_introduced():
    root = Path("/home/azim/core/modules/ai_console")
    forbidden_tokens = [
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "TRUNCATE ",
        "DROP TABLE",
        "send_message",
        "send_whatsapp",
    ]
    for path in root.glob("*.py"):
        text = path.read_text()
        for token in forbidden_tokens:
            assert token not in text


def test_canonical_runtime_inventory_source_is_preserved():
    from modules.ai_console.orchestrator import CANONICAL_RUNTIME_INVENTORY_PATH

    assert CANONICAL_RUNTIME_INVENTORY_PATH == "/api/runtime/nodes/current"
