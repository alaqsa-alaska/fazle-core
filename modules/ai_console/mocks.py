"""Mock adapters for Phase 2C registry-driven skeleton."""

from __future__ import annotations

from typing import Any

from modules.ai_console.adapters import (
    BusinessAdapter,
    EvidenceAdapter,
    MemoryAdapter,
    ProviderAdapter,
    RuntimeAdapter,
)
from modules.ai_console.envelopes import EvidenceEnvelope, ToolResultEnvelope, utc_now_iso
from modules.ai_console.registry import RegistryLoader


class MockRuntimeAdapter(RuntimeAdapter):
    def __init__(self, registry: RegistryLoader) -> None:
        self.registry = registry

    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        entry = self.registry.get(tool_id)
        fixtures = {
            "RT-001": {"services": [{"name": "fazle-core", "status": "active"}]},
            "RT-002": {"containers": [{"name": "qdrant", "status": "running"}]},
            "RT-003": {"processes": [{"name": "bridge-worker", "status": "online"}]},
            "RT-009": {"health": {"status": "ok", "latency_ms": 3}},
            "RT-011": {"health": {"status": "ok"}, "collections": [{"name": "kb", "points": 120}]},
            "RT-012": {"health": {"status": "ok"}, "models": ["qwen3:8b", "qwen2.5:3b"]},
            "RT-013": {"bridges": [{"name": "bridge1", "status": "healthy"}]},
        }
        data = fixtures.get(tool_id, {})
        status = "ok" if data else "not_implemented"
        confidence = entry.confidence_default if data else "Unknown"
        warnings = [] if data else ["mock_runtime_fixture_missing"]
        return ToolResultEnvelope(
            tool_id=tool_id,
            tool_name=entry.name,
            status=status,
            confidence=confidence,
            source="mock",
            timestamp=utc_now_iso(),
            data=data,
            authority=entry.authority,
            warnings=warnings,
            errors=[],
            traceability=entry.traceability_refs,
        )


class MockBusinessAdapter(BusinessAdapter):
    def __init__(self, registry: RegistryLoader) -> None:
        self.registry = registry

    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        entry = self.registry.get(tool_id)
        fixtures = {
            "BUS-001": {"matches": [{"employee_name": "Manik", "employee_mobile": "01700000000"}], "similar_matches": []},
            "BUS-003": {"rows": [{"employee_name": "Manik", "net_salary": 15000, "period_month": 6, "period_year": 2026}]},
            "BUS-005": {"rows": [{"mother_vessel": "MV Test", "status": "Assigned"}]},
        }
        data = fixtures.get(tool_id, {})
        status = "ok" if data else "not_implemented"
        confidence = entry.confidence_default if data else "Unknown"
        warnings = [] if data else ["mock_business_fixture_missing"]
        return ToolResultEnvelope(
            tool_id=tool_id,
            tool_name=entry.name,
            status=status,
            confidence=confidence,
            source="mock",
            timestamp=utc_now_iso(),
            data=data,
            authority=entry.authority,
            warnings=warnings,
            errors=[],
            traceability=entry.traceability_refs,
        )


class MockMemoryAdapter(MemoryAdapter):
    def __init__(self, registry: RegistryLoader) -> None:
        self.registry = registry

    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        entry = self.registry.get(tool_id)
        fixtures = {
            "MEM-001": {"records": [{"instruction": "Always answer in Bangla", "priority": "highest"}]},
            "MEM-004": {"records": [{"service": "fazle-core", "note": "Restart only after owner approval"}]},
        }
        data = fixtures.get(tool_id, {})
        status = "ok" if data else "not_implemented"
        confidence = entry.confidence_default if data else "Unknown"
        warnings = [] if data else ["mock_memory_fixture_missing"]
        return ToolResultEnvelope(
            tool_id=tool_id,
            tool_name=entry.name,
            status=status,
            confidence=confidence,
            source="mock",
            timestamp=utc_now_iso(),
            data=data,
            authority=entry.authority,
            warnings=warnings,
            errors=[],
            traceability=entry.traceability_refs,
        )


class MockEvidenceAdapter(EvidenceAdapter):
    def assemble(
        self,
        *,
        request_id: str,
        mode: str,
        classification: dict[str, Any],
        runtime: list[ToolResultEnvelope],
        business: list[ToolResultEnvelope],
        memory: list[ToolResultEnvelope],
        knowledge: list[ToolResultEnvelope],
        provider: dict[str, Any],
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        traceability: list[str] | None = None,
    ) -> EvidenceEnvelope:
        all_results = runtime + business + memory + knowledge
        derived_confidence = "Unknown"
        if all(result.status == "ok" for result in all_results if all_results):
            derived_confidence = "Verified"
        elif any(result.status == "ok" for result in all_results):
            derived_confidence = "Partially Verified"
        if not all_results:
            derived_confidence = "Inference"

        final_traceability = list(traceability or [])
        final_traceability.extend(["ORCH-005", "EVID-001"])
        for result in all_results:
            final_traceability.extend(result.traceability)

        return EvidenceEnvelope(
            request_id=request_id,
            mode=mode,
            classification=classification,
            runtime=[item.to_dict() for item in runtime],
            business=[item.to_dict() for item in business],
            memory=[item.to_dict() for item in memory],
            knowledge=[item.to_dict() for item in knowledge],
            provider=provider,
            confidence=derived_confidence,
            warnings=list(warnings or []),
            errors=list(errors or []),
            traceability=sorted(set(final_traceability)),
        )


class MockProviderAdapter(ProviderAdapter):
    async def respond(self, prompt: str, evidence: EvidenceEnvelope) -> dict[str, Any]:
        return {
            "provider": "mock_local_provider",
            "model": "mock-local",
            "response": f"Mock response for: {prompt}",
            "confidence": evidence.confidence,
            "traceability": sorted(set(evidence.traceability + ["PROV-001"])),
        }
