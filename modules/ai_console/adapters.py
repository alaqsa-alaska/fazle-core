"""Adapter interfaces for Phase 2C registry-driven skeleton."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from modules.ai_console.envelopes import EvidenceEnvelope, ToolResultEnvelope


class RuntimeAdapter(ABC):
    allowed_side_effects = ("readonly_inspection",)
    prohibited_side_effects = (
        "unrestricted_shell",
        "automatic_restart",
        "file_deletion",
        "firewall_change",
    )

    @abstractmethod
    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        raise NotImplementedError


class BusinessAdapter(ABC):
    allowed_side_effects = ("readonly_query",)
    prohibited_side_effects = (
        "business_write",
        "unrestricted_sql",
        "whatsapp_send",
    )

    @abstractmethod
    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        raise NotImplementedError


class MemoryAdapter(ABC):
    allowed_side_effects = ("ai_memory_read", "ai_memory_write")
    prohibited_side_effects = (
        "business_table_write",
        "truth_override",
        "destructive_delete_without_owner_approval",
    )

    @abstractmethod
    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        raise NotImplementedError


class EvidenceAdapter(ABC):
    allowed_side_effects = ("evidence_assembly",)
    prohibited_side_effects = ("provider_bypass", "source_erasure")

    @abstractmethod
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
        raise NotImplementedError


class ProviderAdapter(ABC):
    allowed_side_effects = ("provider_invocation",)
    prohibited_side_effects = (
        "secret_leakage",
        "direct_runtime_access",
        "direct_db_access",
    )

    @abstractmethod
    async def respond(self, prompt: str, evidence: EvidenceEnvelope) -> dict[str, Any]:
        raise NotImplementedError
