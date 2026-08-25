"""RecruitmentTurnDecision contract — Phase C (2026-08-23).

The decision object is descriptive*: it tells downstream code what the
candidate meant and what the system is permitted to do. It never invents
business facts. It never mutates state.

Tier A (Phase B parsers) emits IntakeProposal objects. Tier B (decide.py)
judges which proposals are valid in this conversation context and
populates the decision.

Invariants enforced by this module's types and the validators in
state_rules.py:
  - semantic_domain: only {"recruitment","non_recruitment","ambiguous"}.
  - turn_type: only the 8 owner-mandated categories.
  - confidence in [0, 1]. Below AMBIGUITY_CONFIDENCE_FLOOR -> AMBIGUOUS
    with needs_human_review=True and state_write=NONE.
  - provenance_confidence in [0, 1]. The manual-reply dedup logic only
    treats historical entries as authoritative when this is
    >= PROVENANCE_AUTHORITATIVE_FLOOR (default 0.7).
  - manual_reply_already_answered can only be True when provenance_confidence
    >= PROVENANCE_AUTHORITATIVE_FLOOR (validated in state_rules.validate_decision).
  - questions[].must_answer_from_source_of_truth=True for fee topics.
  - The decision NEVER fabricates a salary, fee, duty, or address value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Literal

# === Confidence floors ===

# Phase C directive correction 3: ambiguity fallback is conservative.
# Below this confidence we set turn_type=AMBIGUOUS, needs_human_review=True,
# state_write=NONE, and the reply path must NOT volunteer office contact
# info automatically.
AMBIGUITY_CONFIDENCE_FLOOR: float = 0.60

# Phase C directive correction 4: actor provenance must be sufficiently
# explicit to be treated as authoritative. Heuristic-labeled history
# entries (provenance_confidence < this floor) are CARRIED in canonical
# history but CANNOT trigger manual_reply_already_answered.
PROVENANCE_AUTHORITATIVE_FLOOR: float = 0.70

# Phase C directive correction 1: the safe-fee override literal lives in
# modules.recruitment_flow.SAFE_FEE_PENDING_OWNER_DECISION. This module
# deliberately does NOT re-export it -- downstream code imports it directly
# from recruitment_flow so the value can never drift.


# === Type literals ===

SemanticDomain = Literal[
    "recruitment",       # confirmed recruitment-domain turn
    "non_recruitment",   # confirmed not a recruitment question
    "ambiguous",         # domain uncertain; treat with caution
]

TurnType = Literal[
    "PURE_INTAKE",
    "PURE_QUESTION",
    "MIXED",
    "CORRECTION",
    "FRUSTRATION",
    "FOLLOW_UP_CONTEXTUAL",
    "NON_RECRUITMENT",
    "AMBIGUOUS",
]

IntakeField = Literal[
    "name",
    "age",
    "dob",
    "area",
    "job_preference",
    "experience",
    "phone_confirm",
]

ActorType = Literal[
    "candidate",
    "ai_recruitment",
    "ai_other",
    "owner_manual_admin",
    "system_template",
    "protected_domain_handler",
    "internal_system",
    "unknown",
]


# === Dataclasses ===

@dataclass
class IntakeProposal:
    """Tier A deterministic extraction outcome. Phase B produces these;
    Tier B (this module) judges whether to accept them in this turn."""
    field: IntakeField
    value: Any                    # string, int, None
    confidence: float             # 0.0-1.0 from the parser
    evidence: str                 # matched regex / span / keyword
    requires_semantic_review: bool # True when Tier B must judge


@dataclass
class Question:
    """A question detected in this turn, scoped to recruitment-domain topics."""
    topic: str                    # free-form topic, e.g. "available_posts"
    must_answer_from_source_of_truth: bool
    evidence: str                 # exact message span that triggered


@dataclass
class ReasoningEvidence:
    """A single contributing piece of evidence used by Tier B to reach
    the final decision. Optional debug aid; not required for correctness."""
    rule: str                     # short rule id
    span: str                     # matched text span in current message
    history_ref: Optional[str]    # e.g. "last_outbound_id=16901"
    source_ref: Optional[str]     # e.g. "source_of_truth.txt#line=24"
    weight: float                 # 0.0-1.0


@dataclass
class HistoryEntry:
    """Read-only interpretation view of a single wbom_whatsapp_messages
    row. Built by modules.conversation_canonical.canonical_history.

    Phase C directive correction 5: this is an *interpretation* layer.
    It does not create a second conversation database; it reads existing
    rows and annotates them.
    """
    message_id: Optional[int]
    timestamp: Optional[str]
    direction: str                # "inbound" | "outbound"
    actor_type: ActorType         # explicit label, not a free-form string
    provenance_confidence: float  # 0.0-1.0
    text: str
    semantic_domain: Optional[str] # "recruitment" | None; populated by Tier B
    workflow: Optional[str]       # workflow_triggered at insertion
    reply_to: Optional[int]       # future; not used in Phase C
    thread_identity: Optional[str] # future; conversation_key


@dataclass
class RecruitmentTurnDecision:
    """The semantic decision object for one inbound recruitment turn.

    Per Phase C directive corrections 6, 7, 8, 9:
      - The decision describes meaning + allowed actions. It does NOT
        fabricate business facts.
      - MIXED turns are first-class (intake_fields != {} AND questions != []
        simultaneously).
      - candidate_correction=True is a *permission to evaluate*, not an
        automatic overwrite. Phase-B field-locking remains authoritative;
        apply_state_rules() decides whether the proposed correction is
        written, requires confirmation, or requires human review.
      - manual_reply_already_answered=True is only reachable when the
        upstream history entry's provenance_confidence >=
        PROVENANCE_AUTHORITATIVE_FLOOR. Otherwise state_rules raises.
    """
    # --- core classification ---
    semantic_domain: SemanticDomain
    turn_type: TurnType
    confidence: float                                 # 0.0-1.0

    # --- intake fields (Tier A proposals, post Tier B judgement) ---
    accepted_proposals: List[IntakeProposal] = field(default_factory=list)
    rejected_proposals: List[IntakeProposal] = field(default_factory=list)

    # --- questions in this turn ---
    questions: List[Question] = field(default_factory=list)

    # --- contextual reasoning ---
    references_previous_answer: bool = False
    manual_reply_already_answered: bool = False
    candidate_correction: bool = False
    candidate_frustration: bool = False
    needs_clarification: bool = False
    needs_human_review: bool = False

    # --- provenance (Phase C directive correction 4) ---
    provenance_actor_type: ActorType = "unknown"
    provenance_confidence: float = 0.0   # for the *current* inbound
    # The history-side provenance of the latest outbound (if any) is in
    # the HistoryEntry that drove `manual_reply_already_answered=True`.
    # That entry is checked against PROVENANCE_AUTHORITATIVE_FLOOR by
    # state_rules.validate_decision.

    # --- protected-domain boundary (Phase C directive 11; cf_W001 Phase D) ---
    multi_domain_review_required: bool = False

    # --- audit ---
    reason_codes: Tuple[str, ...] = field(default_factory=tuple)
    evidence: List[ReasoningEvidence] = field(default_factory=list)

    # --- deterministic side effects (state_rules applies them) ---
    should_advance_intake: bool = False
    should_answer_question: bool = False
    intake_step_after: Optional[str] = None    # None = no advance
    state_write: Literal["APPLY", "NONE"] = "NONE"
    clarification_question: Optional[str] = None  # short, source-of-truth-grounded

    # --- self-rollback diagnostic fields (Phase C directive 16) ---
    rollback_trigger: bool = False
    rollback_reason: Optional[str] = None

    def to_log_dict(self) -> dict:
        return {
            "semantic_domain": self.semantic_domain,
            "turn_type": self.turn_type,
            "confidence": round(self.confidence, 3),
            "accepted": len(self.accepted_proposals),
            "rejected": len(self.rejected_proposals),
            "questions": len(self.questions),
            "should_advance_intake": self.should_advance_intake,
            "should_answer_question": self.should_answer_question,
            "needs_human_review": self.needs_human_review,
            "needs_clarification": self.needs_clarification,
            "multi_domain_review_required": self.multi_domain_review_required,
            "candidate_correction": self.candidate_correction,
            "candidate_frustration": self.candidate_frustration,
            "references_previous_answer": self.references_previous_answer,
            "manual_reply_already_answered": self.manual_reply_already_answered,
            "intake_step_after": self.intake_step_after,
            "state_write": self.state_write,
            "provenance_actor_type": self.provenance_actor_type,
            "provenance_confidence": round(self.provenance_confidence, 3),
            "reason_codes": list(self.reason_codes),
            "rollback_trigger": self.rollback_trigger,
        }