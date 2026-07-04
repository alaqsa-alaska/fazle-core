from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.asyncio


async def test_runtime_adapter_parses_systemd_output(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyRuntimeAdapter

    async def fake_run_command(args, timeout=5.0):
        return (
            0,
            "\n".join(
                [
                    "fazle-core.service loaded active running Fazle Core API",
                    "nginx.service loaded active running nginx web server",
                    "ssh.service loaded active running OpenSSH server",
                ]
            ),
            "",
        )

    monkeypatch.setattr("modules.ai_console.live_adapters._run_command", fake_run_command)

    result = await ReadonlyRuntimeAdapter().fetch("RT-001", {})

    assert result.status == "ok"
    assert result.data["count"] == 2
    assert result.data["services"][0]["unit"] == "fazle-core.service"


async def test_runtime_adapter_uses_runtime_truth_helpers(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyRuntimeAdapter

    async def fake_diag():
        return {"bridges": [{"name": "bridge1", "status": "ok"}]}

    async def fake_nodes():
        return {"ok": True, "count": 2, "nodes": [{"node_id": "fazle-core"}]}

    monkeypatch.setattr("modules.ai_console.live_adapters.get_bridge_diagnostics", fake_diag)
    monkeypatch.setattr("modules.ai_console.live_adapters.get_current_runtime_nodes", fake_nodes)

    result = await ReadonlyRuntimeAdapter().fetch("RT-013", {})

    assert result.status == "ok"
    assert result.data["canonical_runtime_inventory_path"] == "/api/runtime/nodes/current"
    assert result.data["diagnostics"]["bridges"][0]["status"] == "ok"


async def test_business_employee_search_returns_suggestions(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    async def fake_employee_list(status="active"):
        return [
            {"employee_name": "Manik", "employee_mobile": "01711111111"},
            {"employee_name": "Monir", "employee_mobile": "01722222222"},
        ]

    monkeypatch.setattr("modules.ai_console.live_adapters.ai_readonly_tools.get_employee_list", fake_employee_list)

    result = await ReadonlyBusinessAdapter().fetch("BUS-001", {"query": "Maneek"})

    assert result.status == "partial"
    assert "Manik" in result.data["suggestions"]


async def test_business_employee_summary_aggregates_readonly_sources(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    async def fake_employee_list(status="active"):
        return [{"employee_name": "Manik", "employee_mobile": "01711111111", "designation": "Guard"}]

    async def fake_payment_summary(employee_name=None, month=None, year=None):
        return [{"employee_name": employee_name, "net_salary": 12000}]

    async def fake_attendance_summary(employee_name=None, month=None):
        return [{"employee_name": employee_name, "status": "present"}]

    async def fake_contact_summary(phone):
        return {"found": True, "display_name": "Manik Contact"}

    monkeypatch.setattr("modules.ai_console.live_adapters.ai_readonly_tools.get_employee_list", fake_employee_list)
    monkeypatch.setattr("modules.ai_console.live_adapters.ai_readonly_tools.get_payment_summary", fake_payment_summary)
    monkeypatch.setattr("modules.ai_console.live_adapters.ai_readonly_tools.get_attendance_summary", fake_attendance_summary)
    monkeypatch.setattr("modules.ai_console.live_adapters.ai_readonly_tools.get_contact_summary", fake_contact_summary)

    result = await ReadonlyBusinessAdapter().fetch("BUS-002", {"query": "Manik"})

    assert result.status == "ok"
    assert result.data["employee"]["employee_name"] == "Manik"
    assert result.data["payroll_recent"][0]["net_salary"] == 12000
    assert result.data["contact"]["display_name"] == "Manik Contact"


async def test_business_cash_ledger_summary_uses_daily_payments(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    async def fake_daily(question):
        return {"transaction_count": 2, "total_amount": 1000, "answer": "ok"}

    monkeypatch.setattr("modules.ai_console.live_adapters.ai_readonly_tools.get_daily_payments", fake_daily)

    result = await ReadonlyBusinessAdapter().fetch("BUS-006", {"question": "today payments"})

    assert result.status == "ok"
    assert result.data["transaction_count"] == 2


async def test_memory_adapter_can_save_and_recall_owner_instruction(monkeypatch):
    from modules.ai_console.live_adapters import AIMemoryAdapter

    observed: dict = {}

    async def fake_remember_fact(subject_type, subject_key, fact_type, fact_text, source_ref="", confidence=0.8):
        observed["saved"] = (subject_type, subject_key, fact_type, fact_text)
        return 77

    async def fake_recall_facts(subject_type, subject_key, fact_type=None, limit=10):
        return [{"id": 77, "fact_text": "Always answer in Bangla"}]

    monkeypatch.setattr("modules.ai_console.live_adapters.ollama_memory.remember_fact", fake_remember_fact)
    monkeypatch.setattr("modules.ai_console.live_adapters.ollama_memory.recall_facts", fake_recall_facts)

    adapter = AIMemoryAdapter()
    saved = await adapter.fetch("MEM-001", {"action": "save", "text": "Always answer in Bangla"})
    recalled = await adapter.fetch("MEM-001", {"action": "recall"})

    assert saved.status == "ok"
    assert saved.data["fact_id"] == 77
    assert observed["saved"][0] == "owner"
    assert recalled.status == "ok"
    assert recalled.data["records"][0]["fact_text"] == "Always answer in Bangla"


async def test_memory_adapter_supports_task_status_update(monkeypatch):
    from modules.ai_console.live_adapters import AIMemoryAdapter

    observed: dict = {}

    async def fake_update_task_status(task_id, status, notes=None):
        observed["task"] = (task_id, status, notes)

    monkeypatch.setattr("modules.ai_console.live_adapters.ollama_memory.update_task_status", fake_update_task_status)

    result = await AIMemoryAdapter().fetch(
        "MEM-005",
        {"action": "set_status", "task_id": 5, "status": "done", "notes": "completed"},
    )

    assert result.status == "ok"
    assert observed["task"] == (5, "done", "completed")


async def test_evidence_builder_combines_families_and_confidence():
    from modules.ai_console.envelopes import ToolResultEnvelope
    from modules.ai_console.live_adapters import UnifiedEvidenceBuilder

    runtime = ToolResultEnvelope(
        tool_id="RT-001",
        tool_name="systemd",
        status="ok",
        confidence="Verified",
        source="test",
        timestamp="2026-06-30T00:00:00+00:00",
        data={"services": []},
        authority="runtime_tool",
        traceability=["RT-001"],
    )
    knowledge = ToolResultEnvelope(
        tool_id="BUS-008",
        tool_name="knowledge_search",
        status="partial",
        confidence="Partially Verified",
        source="test",
        timestamp="2026-06-30T00:00:00+00:00",
        data={"rows": []},
        authority="knowledge_base",
        traceability=["BUS-008"],
    )

    envelope = UnifiedEvidenceBuilder().assemble(
        request_id="req-1",
        mode="live_safe_orchestration",
        classification={"mode": "runtime"},
        runtime=[runtime],
        business=[],
        memory=[],
        knowledge=[knowledge],
        provider={"models": []},
        traceability=["ORCH-005"],
    )

    assert envelope.confidence == "Partially Verified"
    assert envelope.runtime[0]["tool_id"] == "RT-001"
    assert envelope.knowledge[0]["tool_id"] == "BUS-008"
    assert "EVID-001" in envelope.traceability


async def test_provider_registry_reports_models_without_secrets(monkeypatch):
    from modules.ai_console.provider_registry import ProviderRegistry

    async def fake_check_health():
        return {
            "primary_provider": "ollama",
            "ollama": {"status": "ok", "models": ["qwen3:8b"], "active_model": "qwen3:8b"},
            "github_models": {"status": "ok"},
            "groq": {"status": "disabled"},
        }

    monkeypatch.setattr("modules.ai_console.provider_registry.ai.check_health", fake_check_health)

    data = await ProviderRegistry().list_models()

    assert data["default_model"]
    assert any(item["provider"] == "ollama" for item in data["models"])
    assert not any("key" in str(item).lower() and "api" in str(item).lower() for item in data["models"])


async def test_internal_orchestrator_preserves_backward_safe_integration(monkeypatch):
    from modules.ai_console.envelopes import ToolResultEnvelope
    from modules.ai_console.orchestrator import orchestrate_console_request

    async def fake_runtime_fetch(self, tool_id, payload):
        return ToolResultEnvelope(
            tool_id=tool_id,
            tool_name="runtime",
            status="ok",
            confidence="Verified",
            source="test",
            timestamp="2026-06-30T00:00:00+00:00",
            data={"ok": True},
            authority="runtime_tool",
            traceability=[tool_id],
        )

    async def fake_business_fetch(self, tool_id, payload):
        return ToolResultEnvelope(
            tool_id=tool_id,
            tool_name="business",
            status="ok",
            confidence="Verified",
            source="test",
            timestamp="2026-06-30T00:00:00+00:00",
            data={"ok": True},
            authority="postgres_readonly_view",
            traceability=[tool_id],
        )

    async def fake_memory_fetch(self, tool_id, payload):
        return ToolResultEnvelope(
            tool_id=tool_id,
            tool_name="memory",
            status="ok",
            confidence="Partially Verified",
            source="test",
            timestamp="2026-06-30T00:00:00+00:00",
            data={"ok": True},
            authority="ai_memory",
            traceability=[tool_id],
        )

    async def fake_knowledge(question, registry=None, limit=5):
        return ToolResultEnvelope(
            tool_id="BUS-008",
            tool_name="knowledge_search",
            status="ok",
            confidence="Partially Verified",
            source="test",
            timestamp="2026-06-30T00:00:00+00:00",
            data={"rows": []},
            authority="knowledge_base",
            traceability=["BUS-008"],
        )

    async def fake_list_models(self):
        return {"models": [{"provider": "ollama", "model_name": "qwen3:8b"}]}

    monkeypatch.setattr("modules.ai_console.orchestrator.ReadonlyRuntimeAdapter.fetch", fake_runtime_fetch)
    monkeypatch.setattr("modules.ai_console.orchestrator.ReadonlyBusinessAdapter.fetch", fake_business_fetch)
    monkeypatch.setattr("modules.ai_console.orchestrator.AIMemoryAdapter.fetch", fake_memory_fetch)
    monkeypatch.setattr("modules.ai_console.orchestrator.build_knowledge_result", fake_knowledge)
    monkeypatch.setattr("modules.ai_console.orchestrator.ProviderRegistry.list_models", fake_list_models)

    result = await orchestrate_console_request("docker health", include_provider_response=False)

    assert result.mode == "runtime"
    assert result.provider_result["status"] == "not_called"
    assert result.evidence["classification"]["canonical_runtime_inventory_path"] == "/api/runtime/nodes/current"


async def test_phase3_to_7_sources_avoid_forbidden_runtime_patterns():
    root = Path("/home/azim/core/modules/ai_console")
    forbidden_imports = {
        "modules.payroll",
        "modules.escort_roster",
        "modules.attendance",
        "modules.message_router",
        "modules.outbound",
        "subprocess",
    }
    forbidden_tokens = [
        "shell=True",
        "send_whatsapp",
        "send_message(",
    ]
    for path in root.glob("*.py"):
        text = path.read_text()
        for token in forbidden_tokens:
            assert token not in text
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
                assert forbidden_imports.isdisjoint(names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module not in forbidden_imports
