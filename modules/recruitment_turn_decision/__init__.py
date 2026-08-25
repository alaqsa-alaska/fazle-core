"""Recruitment Turn Decision v2 -- Phase C (2026-08-23).

RecruitmentTurnDecision is the semantic decision contract for any inbound
turn that has been admitted into the Recruitment funnel by upstream
identity + recruitment_detector.

Phase B (deterministic) parses the candidate's text into IntakeProposal
objects. Phase C (this module) decides what those proposals mean in the
candidate's conversation. The decision is *descriptive*, never *factitious*.

Architectural invariants (per Owner directive 2026-08-23):

  - Tier A (Phase B parsers) proposes. Tier B (this module) decides.
  - This module NEVER writes to fazle_recruitment_sessions directly.
  - This module NEVER fabricates salary/fee/duty/address facts.
  - This module NEVER routes to payroll/attendance/slip/escort.
  - cf_W001 wrong-domain routing remains Phase D.

Public entry points:

  decide(phone, text, current_step, session, identity, history, intake_proposals, source_of_truth)
      -> RecruitmentTurnDecision

Activation flags (default OFF in app/config.py):

  recruitment_turn_decision_v2_enabled       (False)
  recruitment_turn_decision_v2_real_llm      (False, WS1, 2026-08-25)

When BOTH flags are True, decide() resolves a real provider-chain
LLM call via llm_bridge.make_decision_llm_call() unless the caller
explicitly supplied an llm_call. Either flag off -> byte-identical
pre-WS1 behavior.
"""
from .contract import (
    RecruitmentTurnDecision,
    IntakeProposal,
    Question,
    ReasoningEvidence,
    SemanticDomain,
    TurnType,
    AMBIGUITY_CONFIDENCE_FLOOR,
    PROVENANCE_AUTHORITATIVE_FLOOR,
)
from .extract import propose
from .decide import decide
from .llm_bridge import (
    DecisionLLMCall,
    make_decision_llm_call,
    is_real_llm_enabled,
)
from .state_rules import apply_state_rules, validate_decision

__all__ = [
    "RecruitmentTurnDecision",
    "IntakeProposal",
    "Question",
    "ReasoningEvidence",
    "SemanticDomain",
    "TurnType",
    "AMBIGUITY_CONFIDENCE_FLOOR",
    "PROVENANCE_AUTHORITATIVE_FLOOR",
    "propose",
    "decide",
    "DecisionLLMCall",
    "make_decision_llm_call",
    "is_real_llm_enabled",
    "apply_state_rules",
    "validate_decision",
]