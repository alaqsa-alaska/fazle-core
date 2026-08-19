"""
Hermes Kernel — data classes for the AI Operating System scaffolding layer.

These are pure data containers. No DB calls, no imports from business modules,
no side effects. They are the canonical shape for DecisionRecord, TrustEvaluation,
and KernelEvent as defined in:
  - HERMES_AI_DECISION_CONTRACT_2026-08-06.md
  - HERMES_TRUST_ENGINE_2026-08-06.md
  - HERMES_EVENT_MODEL_2026-08-06.md

Discovery summary:
  Existing owner:  None — no equivalent data class exists in the codebase.
  Existing runtime: None.
  Files reused:    None — pure data structures.
  Risk level:      Zero — no side effects, no imports from business modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


@dataclass
class TrustEvaluation:
    """Weighted trust score from seven dimensions per HERMES_TRUST_ENGINE."""
    trace_id: UUID
    timestamp: datetime
    t01_identity: float           # KS-001 identity confidence (0.0-1.0)
    t02_intent: float             # intent classification confidence (0.0-1.0)
    t03_evidence: float           # tool/evidence coverage (0.0-1.0)
    t04_memory_freshness: float   # conversation memory freshness (0.0-1.0)
    t05_knowledge_freshness: float
    t06_tool_availability: float  # tool success rate (0.0-1.0)
    t07_risk_modifier: float      # action class risk modifier (0.6-1.0)
    base_trust_score: float
    final_trust_score: float
    trust_band: str               # verified | partial | inference | unknown
    allowed_actions: list[str]
    recommended_action: str


@dataclass
class DecisionRecord:
    """One record per process_message() turn per HERMES_AI_DECISION_CONTRACT."""
    trace_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sender_phone: str = ""
    identity_role: str = ""
    identity_confidence: Optional[float] = None  # 0.0-1.0; from identity["confidence"]
    identity_source: str = ""
    intent: str = ""
    intent_method: str = ""        # regex | keyword | llm | unknown
    evidence_sources: list[str] = field(default_factory=list)
    evidence_coverage: Optional[float] = None
    memory_context: str = ""
    knowledge_context: str = ""
    tool_results_summary: str = ""
    reasoning_summary: str = ""
    confidence_score: Optional[float] = None
    confidence_band: str = ""
    chosen_action: str = ""        # reply | draft | clarification | escalation | no_action
    explanation: str = ""
    escalation_reason: str = ""
    provider_used: str = ""
    provider_fallback_path: str = ""
    source: str = ""
    raw_input_text: str = ""
    source_message_id: str = ""    # the bridge's own message id, when known
                                    # (see modules.message_router.process_message's
                                    # own message_id param) -- 2026-08-19
    trust_evaluation: Optional[TrustEvaluation] = None


@dataclass
class KernelEvent:
    """One record per standard kernel event per HERMES_EVENT_MODEL."""
    event_id: UUID = field(default_factory=uuid4)
    trace_id: UUID = field(default_factory=uuid4)
    event_name: str = ""           # EVT-001 message.received, etc.
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict = field(default_factory=dict)
