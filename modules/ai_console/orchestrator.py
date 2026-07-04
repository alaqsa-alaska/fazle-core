"""Mock orchestrator plus internal live-safe orchestration path."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

from modules.ai_console.live_adapters import (
    AIMemoryAdapter,
    CANONICAL_RUNTIME_INVENTORY_PATH,
    ReadonlyBusinessAdapter,
    ReadonlyRuntimeAdapter,
    UnifiedEvidenceBuilder,
    build_knowledge_result,
)

log = logging.getLogger("fazle.ai_console.orchestrator")
from modules.ai_console.mocks import (
    MockBusinessAdapter,
    MockEvidenceAdapter,
    MockMemoryAdapter,
    MockProviderAdapter,
    MockRuntimeAdapter,
)
from modules.ai_console.provider_registry import LLMProviderAdapter, ProviderRegistry
from modules.ai_console.registry import RegistryLoader, build_registry_loader


@dataclass
class MockOrchestrationResponse:
    request_id: str
    mode: str
    selected_tools: list[str]
    evidence: dict
    provider_result: dict
    traceability: list[str]


@dataclass
class ConsoleOrchestrationResponse:
    request_id: str
    mode: str
    selected_tools: list[str]
    evidence: dict
    provider_result: dict
    traceability: list[str]


def classify_message(message: str) -> str:
    q = message.lower()
    if any(token in q for token in ("ram", "cpu", "docker", "service", "health", "port", "postgres", "qdrant", "ollama", "bridge", "disk", "uptime")):
        return "runtime"
    if any(token in q for token in (
        "payroll", "employee", "salary", "attendance", "escort", "ledger", "recruitment",
        # English DB/data signals
        "database", " db ", "db'", "db ", "sql", "record", "vessel", "crew",
        "contact", "payment", "cash", "conversation", "message history",
        # Bengali business signals
        "কর্মচারী", "কর্মচারি", "মোবাইল", "নাম্বার", "রেকর্ড", "খুঁজে", "বেতন",
        "হাজিরা", "অ্যাটেন্ডেন্স", "এসকর্ট", "লেজার", "ক্যাশ", "নিয়োগ",
    )):
        return "business"
    if any(token in q for token in ("instruction", "note", "memory", "preference", "troubleshooting")):
        return "memory"
    # 'db' at start/end of message or as standalone token
    if " db" in q or q.startswith("db") or "the db" in q or "fazle-core" in q:
        return "business"
    return "general"


def select_mock_tools(mode: str) -> list[str]:
    if mode == "runtime":
        return ["RT-001", "RT-002", "RT-009", "RT-011", "RT-012", "RT-013"]
    if mode == "business":
        return ["BUS-001", "BUS-003", "BUS-005"]
    if mode == "memory":
        return ["MEM-001", "MEM-004"]
    return []


async def orchestrate_mock_request(
    message: str,
    *,
    registry: RegistryLoader | None = None,
) -> MockOrchestrationResponse:
    loader = registry or build_registry_loader()
    mode = classify_message(message)
    selected_tools = select_mock_tools(mode)

    runtime_adapter = MockRuntimeAdapter(loader)
    business_adapter = MockBusinessAdapter(loader)
    memory_adapter = MockMemoryAdapter(loader)
    evidence_adapter = MockEvidenceAdapter()
    provider_adapter = MockProviderAdapter()

    runtime_results = []
    business_results = []
    memory_results = []

    for tool_id in selected_tools:
        if tool_id.startswith("RT-"):
            runtime_results.append(await runtime_adapter.fetch(tool_id, {"message": message}))
        elif tool_id.startswith("BUS-"):
            business_results.append(await business_adapter.fetch(tool_id, {"message": message}))
        elif tool_id.startswith("MEM-"):
            memory_results.append(await memory_adapter.fetch(tool_id, {"message": message}))

    classification = {
        "mode": mode,
        "message": message,
        "canonical_runtime_inventory_path": CANONICAL_RUNTIME_INVENTORY_PATH,
    }
    request_id = f"mock-{uuid4().hex[:12]}"
    evidence = evidence_adapter.assemble(
        request_id=request_id,
        mode="mock_orchestration",
        classification=classification,
        runtime=runtime_results,
        business=business_results,
        memory=memory_results,
        knowledge=[],
        provider={},
        warnings=[],
        errors=[],
        traceability=["ORCH-005", "EVID-001"],
    )
    provider_result = await provider_adapter.respond(message, evidence)
    evidence.provider = provider_result
    evidence.response = provider_result["response"]

    return MockOrchestrationResponse(
        request_id=request_id,
        mode=mode,
        selected_tools=selected_tools,
        evidence=evidence.to_dict(),
        provider_result=provider_result,
        traceability=sorted(set(evidence.traceability + provider_result["traceability"])),
    )


def select_live_tools(mode: str) -> list[str]:
    if mode == "runtime":
        return [
            "RT-001",
            "RT-002",
            "RT-003",
            "RT-004",
            "RT-005",
            "RT-006",
            "RT-007",
            "RT-008",
            "RT-009",
            "RT-010",
            "RT-011",
            "RT-012",
            "RT-013",
            "RT-014",
        ]
    if mode == "business":
        return [
            "BUS-001",
            "BUS-002",
            "BUS-003",
            "BUS-004",
            "BUS-005",
            "BUS-006",
            "BUS-007",
            "BUS-008",
            "BUS-009",
        ]
    if mode == "memory":
        return ["MEM-001", "MEM-004", "MEM-005", "MEM-007"]
    # general / unclassified: run a broad baseline covering key areas
    return ["BUS-001", "BUS-003", "BUS-004", "MEM-001", "RT-001"]


async def orchestrate_console_request(
    message: str,
    *,
    requested_model: str | None = None,
    history: list[dict] | None = None,
    app_context: bool = True,
    db_context: bool = True,
    web_context: bool = True,
    include_provider_response: bool = False,
    registry: RegistryLoader | None = None,
) -> ConsoleOrchestrationResponse:
    loader = registry or build_registry_loader()
    mode = classify_message(message)
    selected_tools = select_live_tools(mode)

    runtime_adapter = ReadonlyRuntimeAdapter(loader)
    business_adapter = ReadonlyBusinessAdapter(loader)
    memory_adapter = AIMemoryAdapter(loader)
    evidence_adapter = UnifiedEvidenceBuilder()
    provider_registry = ProviderRegistry(loader)
    provider_adapter = LLMProviderAdapter(loader)

    runtime_results = []
    business_results = []
    memory_results = []
    log.info(
        "[ai_console.orchestrate] original_message=%r mode=%s selected_tools=%s db_context=%s",
        message,
        mode,
        selected_tools,
        db_context,
    )
    for tool_id in selected_tools:
        payload = {
            "message": message,
            "question": message,
            "requested_model": requested_model,
            "history": history or [],
        }
        # Force employee_search when the message contains employee/mobile/record signals.
        if tool_id == "BUS-001" and db_context:
            from modules.ai_console.live_adapters import _extract_phone
            phone = _extract_phone(message)
            if phone:
                payload["employee_mobile"] = phone
                payload["query"] = phone
                log.info("[ai_console.orchestrate] BUS-001 forced employee_mobile=%r", phone)
        if tool_id.startswith("RT-"):
            runtime_results.append(await runtime_adapter.fetch(tool_id, payload))
        elif tool_id.startswith("BUS-") and db_context:
            business_results.append(await business_adapter.fetch(tool_id, payload))
        elif tool_id.startswith("MEM-") and app_context:
            memory_results.append(await memory_adapter.fetch(tool_id, payload))
    log.info(
        "[ai_console.orchestrate] business_results_count=%d runtime_results_count=%d memory_results_count=%d",
        len(business_results),
        len(runtime_results),
        len(memory_results),
    )

    knowledge_results = [await build_knowledge_result(message, registry=loader)] if app_context else []
    provider_metadata = await provider_registry.list_models()
    request_id = f"live-{uuid4().hex[:12]}"
    classification = {
        "mode": mode,
        "message": message,
        "requested_model": requested_model,
        "history": history or [],
        "flags": {
            "app_context": app_context,
            "db_context": db_context,
            "web_context": web_context,
        },
        "canonical_runtime_inventory_path": CANONICAL_RUNTIME_INVENTORY_PATH,
        "selected_tools": selected_tools,
    }
    evidence = evidence_adapter.assemble(
        request_id=request_id,
        mode="live_safe_orchestration",
        classification=classification,
        runtime=runtime_results,
        business=business_results,
        memory=memory_results,
        knowledge=knowledge_results,
        provider=provider_metadata,
        warnings=[],
        errors=[],
        traceability=["ORCH-001", "ORCH-002", "ORCH-003", "ORCH-005"],
    )
    provider_result = {
        "provider": None,
        "model": requested_model,
        "response": None,
        "status": "not_called",
        "traceability": ["ORCH-005"],
    }
    if include_provider_response:
        provider_result = await provider_adapter.respond(message, evidence)
        evidence.provider = {**provider_metadata, "selected": provider_result}
        evidence.response = provider_result.get("response")

    return ConsoleOrchestrationResponse(
        request_id=request_id,
        mode=mode,
        selected_tools=selected_tools,
        evidence=evidence.to_dict(),
        provider_result=provider_result,
        traceability=sorted(set(evidence.traceability + provider_result["traceability"])),
    )
