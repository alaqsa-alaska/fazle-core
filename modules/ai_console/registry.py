"""Static registries for Phase 2C skeleton execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    name: str
    family: str
    purpose: str
    authority: str
    allowed_mode: str
    output_schema_ref: str
    confidence_default: str
    prohibited_behavior: list[str] = field(default_factory=list)
    traceability_refs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _runtime_entries() -> list[RegistryEntry]:
    rows = [
        ("RT-001", "systemd", "Inspect systemd service state", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-001"]),
        ("RT-002", "docker", "Inspect Docker container state", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-002"]),
        ("RT-003", "pm2", "Inspect PM2 process state", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-003"]),
        ("RT-004", "nginx", "Inspect Nginx route and vhost state", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-004"]),
        ("RT-005", "ports", "Inspect listening port inventory", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-005"]),
        ("RT-006", "disk", "Inspect disk metrics", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-006"]),
        ("RT-007", "ram", "Inspect RAM metrics", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-007"]),
        ("RT-008", "cpu", "Inspect CPU metrics", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-008"]),
        ("RT-009", "postgres", "Inspect PostgreSQL health", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-009"]),
        ("RT-010", "redis", "Inspect Redis health", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-010"]),
        ("RT-011", "qdrant", "Inspect Qdrant health", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-011"]),
        ("RT-012", "ollama", "Inspect Ollama health and model inventory", "runtime_tool", "runtime", "Verified", ["ORCH-008", "RT-012"]),
        ("RT-013", "bridges", "Inspect WhatsApp bridge diagnostics", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-013"]),
        ("RT-014", "media_processor", "Inspect media processor diagnostics", "runtime_tool", "runtime", "Verified", ["ORCH-001", "RT-014"]),
    ]
    entries: list[RegistryEntry] = []
    for rid, name, purpose, authority, mode, confidence, refs in rows:
        entries.append(
            RegistryEntry(
                id=rid,
                name=name,
                family="runtime",
                purpose=purpose,
                authority=authority,
                allowed_mode=mode,
                output_schema_ref="PHASE_2B_6_PER_TOOL_OUTPUT_SCHEMA_MATRIX",
                confidence_default=confidence,
                prohibited_behavior=[
                    "unrestricted_shell",
                    "automatic_restart",
                    "file_deletion",
                    "firewall_change",
                    "secret_exposure",
                ],
                traceability_refs=refs + ["STD-008", "EVID-011"],
            )
        )
    # RT-015 is added directly (not via the rows/loop above) because it needs
    # a per-entry `metadata={"min_role": ...}` the shared loop does not set
    # for RT-001..014 -- see Phase 2 Step 4 notes: min_role is descriptive
    # only until ReadonlyRuntimeAdapter.fetch() gains an RBAC check to match
    # ReadonlyBusinessAdapter's (not part of this additive-only change).
    entries.append(
        RegistryEntry(
            id="RT-015",
            name="error_log",
            family="runtime",
            purpose="Read recent application error log entries for diagnostics. Read-only.",
            authority="runtime_tool",
            allowed_mode="runtime",
            output_schema_ref="PHASE_2B_6_PER_TOOL_OUTPUT_SCHEMA_MATRIX",
            confidence_default="Verified",
            prohibited_behavior=[
                "unrestricted_shell",
                "automatic_restart",
                "file_deletion",
                "firewall_change",
                "secret_exposure",
            ],
            traceability_refs=["ORCH-001", "RT-015", "STD-008", "EVID-011"],
            metadata={"min_role": "office_assistant"},
        )
    )
    return entries


_BUS_MIN_ROLE: dict[str, str] = {
    # viewer: basic employee/program search, Dispatch availability/conflict
    "BUS-001": "viewer",
    "BUS-005": "viewer",
    "BUS-008": "viewer",
    "BUS-010": "viewer",
    "BUS-011": "viewer",
    # office_assistant: operational attendance/program/dispatch detail
    "BUS-002": "office_assistant",
    "BUS-004": "office_assistant",
    "BUS-007": "office_assistant",
    "BUS-009": "office_assistant",
    "BUS-012": "office_assistant",
    "BUS-013": "office_assistant",
    # accountant: payroll/salary/advance/cash summaries
    "BUS-003": "accountant",
    "BUS-006": "accountant",
    "BUS-014": "accountant",
    # admin: admin directive/action memory (BUS-016's own min_role is set
    # directly on its standalone RegistryEntry below, not via this dict,
    # since it also needs non-uniform prohibited_behavior -- listed here
    # only for BUS-015, which does fit the uniform rows/loop shape)
    "BUS-015": "admin",
    # admin: payroll page-truth adapter -- reads fpe_cash_transactions
    # directly (the real ledger), not the empty/draft-only wbom_payroll_runs
    # view BUS-003 uses -- kept at the same tier as other financial reads.
    "BUS-017": "admin",
    # admin: escort roster page-truth adapter -- reads escort_roster_entries
    # directly (the same computed projection the roster page shows).
    "BUS-018": "admin",
}


def _business_entries() -> list[RegistryEntry]:
    rows = [
        ("BUS-001", "employee_search", "Search employees by name or mobile", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-001"]),
        ("BUS-002", "employee_summary", "Summarize employee record", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-002"]),
        ("BUS-003", "payroll_summary", "Summarize payroll facts", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-003"]),
        ("BUS-004", "attendance_summary", "Summarize attendance facts", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-004"]),
        ("BUS-005", "escort_summary", "Summarize escort program facts", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-005"]),
        ("BUS-006", "cash_ledger_summary", "Summarize cash ledger facts", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-006"]),
        ("BUS-007", "recruitment_summary", "Summarize recruitment facts", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-007"]),
        ("BUS-008", "knowledge_search", "Search knowledge base content", "knowledge_base", "knowledge", "Partially Verified", ["ORCH-032", "BUS-008"]),
        ("BUS-009", "conversation_history", "Summarize conversation history", "conversation_context", "conversation", "Partially Verified", ["ORCH-035", "BUS-009"]),
        ("BUS-010", "dispatch_availability", "List escort worker availability", "dispatch_readonly_service", "business", "Verified", ["ORCH-002", "BUS-010"]),
        ("BUS-011", "dispatch_conflict_check", "Check one escort's proposed window for conflicts", "dispatch_readonly_service", "business", "Verified", ["ORCH-002", "BUS-011"]),
        ("BUS-012", "dispatch_suggestions", "Rank available escorts for a proposed window", "dispatch_readonly_service", "business", "Verified", ["ORCH-002", "BUS-012"]),
        ("BUS-013", "missing_end_date_programs", "List escort programs missing an end date (review signal, not a conflict)", "dispatch_readonly_service", "business", "Partially Verified", ["ORCH-002", "BUS-013"]),
        ("BUS-014", "employee_advances", "Summarize recorded payroll-run advances", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-014"]),
        ("BUS-015", "admin_directive_read", "Read saved admin directives/instructions from memory. Filter by subject_key or category. Read-only.", "postgres_readonly_view", "business", "Verified", ["ORCH-002", "BUS-015"]),
        ("BUS-017", "payroll_page_summary", "Read real payroll/payment data from the live transaction ledger (fpe_cash_transactions). This is the same source used by the payroll page. Read-only.", "fpe_readonly_view", "business", "Verified", ["ORCH-002", "BUS-017"]),
        ("BUS-018", "escort_roster_page_summary", "Read live escort roster summary and active roster list data from escort_roster_entries. This is the same source used by the escort roster page. Read-only.", "escort_roster_readonly_view", "business", "Verified", ["ORCH-002", "BUS-018"]),
    ]
    entries: list[RegistryEntry] = []
    for rid, name, purpose, authority, mode, confidence, refs in rows:
        entries.append(
            RegistryEntry(
                id=rid,
                name=name,
                family="business",
                purpose=purpose,
                authority=authority,
                allowed_mode=mode,
                output_schema_ref="PHASE_2B_6_PER_TOOL_OUTPUT_SCHEMA_MATRIX",
                confidence_default=confidence,
                prohibited_behavior=[
                    "business_write",
                    "unrestricted_sql",
                    "secret_exposure",
                ],
                traceability_refs=refs + ["STD-008", "EVID-011"],
                metadata={"min_role": _BUS_MIN_ROLE.get(rid, "admin")},
            )
        )
    # BUS-016 is added directly (not via the rows/loop above) because it is
    # the first business-family tool that performs a write (to the narrow,
    # non-Critical fazle_admin_directives table only -- never to any table
    # ReadonlyBusinessAdapter's other tools touch). It needs a non-uniform
    # prohibited_behavior list and an authority string that honestly says
    # "write service", not the "*_readonly_*" naming every other BUS-*
    # entry uses -- reusing that naming here would misrepresent it.
    entries.append(
        RegistryEntry(
            id="BUS-016",
            name="admin_directive_write",
            family="business",
            purpose="Save or revoke an admin directive/instruction in memory. Write operation — requires admin role.",
            authority="admin_directive_service",
            allowed_mode="business",
            output_schema_ref="PHASE_2B_6_PER_TOOL_OUTPUT_SCHEMA_MATRIX",
            confidence_default="Verified",
            prohibited_behavior=[
                "business_write",
                "unrestricted_sql",
                "secret_exposure",
                "direct_whatsapp_send",
                "unrestricted_sql_write",
            ],
            traceability_refs=["ORCH-002", "BUS-016", "STD-008", "EVID-011"],
            metadata={"min_role": "admin"},
        )
    )
    return entries


def _memory_entries() -> list[RegistryEntry]:
    rows = [
        ("MEM-001", "owner_instruction", "Recall owner instruction memory", "ai_memory", "memory", "Partially Verified", ["ORCH-031", "MEM-001"]),
        ("MEM-002", "business_note", "Recall assistant business note", "ai_memory", "memory", "Partially Verified", ["ORCH-031", "MEM-002"]),
        ("MEM-003", "architecture_decision", "Recall architecture decisions", "ai_memory", "memory", "Partially Verified", ["ORCH-031", "MEM-003"]),
        ("MEM-004", "troubleshooting_note", "Recall troubleshooting notes", "ai_memory", "memory", "Partially Verified", ["ORCH-031", "MEM-004"]),
        ("MEM-005", "task_memory", "Recall task memory", "ai_memory", "memory", "Partially Verified", ["ORCH-031", "MEM-005"]),
        ("MEM-006", "preference_memory", "Recall preference memory", "ai_memory", "memory", "Partially Verified", ["ORCH-031", "MEM-006"]),
        ("MEM-007", "conversation_memory", "Recall bounded conversation memory", "ai_memory", "memory", "Partially Verified", ["ORCH-031", "MEM-007"]),
    ]
    entries: list[RegistryEntry] = []
    for rid, name, purpose, authority, mode, confidence, refs in rows:
        entries.append(
            RegistryEntry(
                id=rid,
                name=name,
                family="memory",
                purpose=purpose,
                authority=authority,
                allowed_mode=mode,
                output_schema_ref="PHASE_2B_6_PER_TOOL_OUTPUT_SCHEMA_MATRIX",
                confidence_default=confidence,
                prohibited_behavior=[
                    "business_truth_override",
                    "destructive_delete_without_owner_approval",
                    "secret_exposure",
                ],
                traceability_refs=refs + ["STD-008", "EVID-011"],
            )
        )
    return entries


def _evidence_entries() -> list[RegistryEntry]:
    return [
        RegistryEntry(
            id="EVID-001",
            name="evidence_package",
            family="evidence",
            purpose="Assemble canonical evidence package before provider invocation",
            authority="evidence_adapter",
            allowed_mode="evidence",
            output_schema_ref="PHASE_2B_4_EVIDENCE_REGISTRY",
            confidence_default="Verified",
            prohibited_behavior=["provider_bypass", "source_erasure", "secret_exposure"],
            traceability_refs=["ORCH-005", "EVID-001", "EVID-004", "EVID-006"],
        ),
        RegistryEntry(
            id="EVID-011",
            name="tool_output_contract",
            family="evidence",
            purpose="Standardize tool output shape across families",
            authority="evidence_adapter",
            allowed_mode="evidence",
            output_schema_ref="PHASE_2B_6_PER_TOOL_OUTPUT_SCHEMA_MATRIX",
            confidence_default="Verified",
            prohibited_behavior=["secret_exposure"],
            traceability_refs=["STD-008", "EVID-011"],
        ),
    ]


def _standard_entries() -> list[RegistryEntry]:
    return [
        RegistryEntry(
            id="STD-004",
            name="merge_traceability_requirement",
            family="standard",
            purpose="Require implementation documents to cite reference IDs",
            authority="development_standard",
            allowed_mode="governance",
            output_schema_ref="FAZLE_AI_OPERATING_CONSOLE_DEVELOPMENT_STANDARD",
            confidence_default="Verified",
            prohibited_behavior=[],
            traceability_refs=["STD-004"],
        ),
        RegistryEntry(
            id="STD-009",
            name="adapter_boundary_requirement",
            family="standard",
            purpose="Require explicit adapter boundaries",
            authority="development_standard",
            allowed_mode="governance",
            output_schema_ref="PHASE_2B_7_ADAPTER_BOUNDARY_TABLE",
            confidence_default="Verified",
            prohibited_behavior=[],
            traceability_refs=["STD-009", "STD-010", "STD-011"],
        ),
    ]


def _orchestration_entries() -> list[RegistryEntry]:
    rows = [
        ("ORCH-001", "runtime_questions_use_runtime_tools", "VPS/runtime questions must prefer runtime tools"),
        ("ORCH-002", "business_facts_use_business_tools", "Business facts must come from readonly business tools"),
        ("ORCH-003", "memory_cannot_override_truth", "AI memory cannot override business or runtime truth"),
        ("ORCH-004", "no_unrestricted_shell", "LLM must never receive unrestricted shell capability"),
        ("ORCH-005", "assemble_evidence_before_provider", "Evidence must be assembled before provider invocation"),
        ("ORCH-061", "tool_failure_degrades_safely", "Tool or provider failure must degrade safely"),
        ("ORCH-076", "tool_schema_rules_apply", "Tools must not bypass authority, confidence, or failure rules"),
    ]
    return [
        RegistryEntry(
            id=oid,
            name=name,
            family="orchestration",
            purpose=purpose,
            authority="orchestration_policy",
            allowed_mode="governance",
            output_schema_ref="PHASE_2B_RUNTIME_TOOL_REGISTRY_DESIGN_REVIEW",
            confidence_default="Verified",
            prohibited_behavior=[],
            traceability_refs=[oid],
        )
        for oid, name, purpose in rows
    ]


def load_all_registry_entries() -> list[RegistryEntry]:
    return (
        _runtime_entries()
        + _business_entries()
        + _memory_entries()
        + _evidence_entries()
        + _standard_entries()
        + _orchestration_entries()
    )


class RegistryLoader:
    """Static registry loader for Phase 2C mock-only orchestration."""

    def __init__(self, entries: list[RegistryEntry]) -> None:
        self._entries = {entry.id: entry for entry in entries}

    def list_entries(self) -> list[RegistryEntry]:
        return list(self._entries.values())

    def get(self, entry_id: str) -> RegistryEntry:
        return self._entries[entry_id]

    def exists(self, entry_id: str) -> bool:
        return entry_id in self._entries

    def by_family(self, family: str) -> list[RegistryEntry]:
        return [entry for entry in self._entries.values() if entry.family == family]


def build_registry_loader() -> RegistryLoader:
    return RegistryLoader(load_all_registry_entries())
