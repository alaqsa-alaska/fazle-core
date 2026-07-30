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
            "BUS-001", "BUS-002", "BUS-003", "BUS-004", "BUS-005", "BUS-006",
            "BUS-007", "BUS-008", "BUS-009", "BUS-010", "BUS-011", "BUS-012",
            "BUS-013", "BUS-014",
        ]
    if mode == "memory":
        return ["MEM-001", "MEM-004", "MEM-005", "MEM-007"]
    # general / unclassified: run a broad baseline covering key areas
    return ["BUS-001", "BUS-003", "BUS-004", "MEM-001", "RT-001"]


# Keyword groups for narrowing "business" mode to a relevant subset of tools
# instead of always running all 14 -- keeps per-question latency/DB load
# bounded while still satisfying "choose one or more approved tools."
_BUS_TOOL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("BUS-013", ("missing end date", "no end date", "without end date", "open ended", "end date not recorded")),
    ("BUS-014", ("advance", "advances", "অগ্রিম")),
    ("BUS-010", ("available", "availability", "free tomorrow", "who is free")),
    ("BUS-011", ("conflict", "double book", "double-book", "clash")),
    ("BUS-012", ("suggest", "suggestion", "recommend", "who should", "rank")),
    ("BUS-003", ("payroll", "salary", "বেতন")),
    ("BUS-004", ("attendance", "হাজিরা", "অ্যাটেন্ডেন্স")),
    ("BUS-005", ("escort", "vessel", "program", "এসকর্ট")),
    ("BUS-006", ("cash", "ledger", "transaction", "ক্যাশ", "লেজার")),
    ("BUS-007", ("recruitment", "candidate", "নিয়োগ")),
    ("BUS-008", ("knowledge", "policy", "how to", "how do i", "rule")),
    ("BUS-009", ("conversation history", "message history", "past messages")),
    ("BUS-017", ("payment", "পেমেন্ট", "transaction", "টাকা পাঠানো", "আজ কয়জন", "paid", "daily payment", "আজকে পেমেন্ট", "আজকের পেমেন্ট", "টাকা দেওয়া")),
    ("BUS-018", ("roster", "escort list", "escort roster", "এসকর্ট রোস্টার", "roster summary", "escort status", "active escort", "রোস্টার", "escort count", "কতজন এসকর্ট", "escort program list")),
]


def select_business_tools_for_message(message: str) -> list[str]:
    """Narrow business-mode tool selection to what the question actually
    signals, falling back to a small general-purpose set (not all 14) when
    nothing specific matches. BUS-001/BUS-002 (employee search/summary) are
    always included when any employee-ish signal (name/mobile) is present,
    matching the existing BUS-001 phone-forcing behavior below."""
    q = (message or "").lower()
    selected: list[str] = []
    for tool_id, keywords in _BUS_TOOL_KEYWORDS:
        if any(kw in q for kw in keywords):
            selected.append(tool_id)
    has_employee_signal = bool(_extract_phone_hint(q)) or any(
        kw in q for kw in ("employee", "mobile", "phone", "name", "কর্মচারী", "মোবাইল", "কর্মচারি", "কতজন", "নামে", "খুঁজে দাও", "search")
    )
    if has_employee_signal and "BUS-001" not in selected:
        selected.append("BUS-001")
    if not selected:
        # No specific signal — safe, bounded general business default.
        selected = ["BUS-001", "BUS-003", "BUS-004", "BUS-005"]
    return selected


def _extract_phone_hint(text: str) -> str | None:
    import re
    digits = re.sub(r"\D+", "", text or "")
    return digits if len(digits) >= 8 else None


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
    actor_role_level: int = 100,
) -> ConsoleOrchestrationResponse:
    loader = registry or build_registry_loader()
    mode = classify_message(message)
    selected_tools = (
        select_business_tools_for_message(message) if mode == "business" else select_live_tools(mode)
    )

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
            business_results.append(
                await business_adapter.fetch(tool_id, payload, actor_role_level=actor_role_level)
            )
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
