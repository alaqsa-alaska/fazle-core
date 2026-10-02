"""Tier A: deterministic IntakeProposal extraction (Phase C).

Wraps the Phase B parsers in modules.recruitment_flow into
IntakeProposal objects for Tier B to judge. NO new Bengali keywords are
added in this layer; every natural-language interpretation responsibility
belongs to Tier B (decide.py).

What this layer does:
  - Runs Phase B parsers on the candidate's text.
  - Marks whether each proposal is high-confidence (accepts the value
    clearly) or requires semantic review (Tier B must judge).
  - Honors the locked-field contract from Phase B: a proposal whose value
    matches a locked field is reported as requires_semantic_review=True
    so Tier B can decide whether to acknowledge or treat as new info.

What this layer does NOT do (Phase C directive correction 2):
  - Does not expand Bengali keyword lists.
  - Does not add regex rules to interpret natural meaning.
  - Does not introduce sender/phone-specific exceptions.
  - Does not perform semantic classification.
"""
from __future__ import annotations

from typing import List, Optional

from modules.recruitment_flow import (
    _parse_age,
    _parse_experience,
    _parse_job_preference,
    _parse_phone,
    STEP_QUESTIONS,
)

from .contract import IntakeField, IntakeProposal


# Field names known to the intake state machine.
KNOWN_FIELDS = (
    "name",
    "age",
    "dob",
    "area",
    "job_preference",
    "experience",
    "phone_confirm",
)


def _proposal(
    field: IntakeField,
    value,
    confidence: float,
    evidence: str,
    requires_semantic_review: bool,
) -> IntakeProposal:
    return IntakeProposal(
        field=field,
        value=value,
        confidence=confidence,
        evidence=evidence,
        requires_semantic_review=requires_semantic_review,
    )


def _age_proposal(text: str, locked_age: Optional[int]) -> Optional[IntakeProposal]:
    """Phase B _parse_age already returns None for DOB-flagged text. The
    parser is unchanged. We annotate confidence and whether the parsed
    value would be a re-assertion of an already-locked field."""
    val = _parse_age(text, (18, 55))
    if val is None:
        return None
    return _proposal(
        field="age",
        value=val,
        confidence=0.9,
        evidence=f"_parse_age returned {val} from text",
        requires_semantic_review=(val == locked_age),  # re-assertion of locked value
    )


def _experience_proposal(text: str, locked_experience: Optional[int]) -> Optional[IntakeProposal]:
    # Phase B's _parse_experience returns int or 0; treat 0 as valid (new/experienced=0)
    val = _parse_experience(text)
    if val is None:
        return None
    return _proposal(
        field="experience",
        value=val,
        confidence=0.7,
        evidence=f"_parse_experience returned {val}",
        requires_semantic_review=(val == locked_experience),
    )


def _job_preference_proposal(text: str, locked_job: Optional[str]) -> Optional[IntakeProposal]:
    val = _parse_job_preference(text)
    if val is None:
        return None
    return _proposal(
        field="job_preference",
        value=val,
        confidence=0.85,
        evidence=f"_parse_job_preference matched '{val}'",
        requires_semantic_review=(val == locked_job),
    )


def _phone_proposal(text: str, locked_phone: Optional[str]) -> Optional[IntakeProposal]:
    val = _parse_phone(text)
    if val is None:
        return None
    return _proposal(
        field="phone_confirm",
        value=val,
        confidence=0.9,
        evidence=f"_parse_phone matched {val}",
        requires_semantic_review=(val == locked_phone),
    )


def _name_proposal(text: str, locked_name: Optional[str]) -> Optional[IntakeProposal]:
    """The name field is special: Phase B's _looks_like_name is a hard
    gate (returns False for question-marked or off-vocab text). We do
    not call it here; Tier B is responsible for the semantic
    accept/reject decision on a name proposal.

    Instead, this layer only proposes a name value when the text is
    short and doesn't look like a question. Tier B judges the rest.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    if len(stripped) > 60:
        return None
    if "?" in stripped or "؟" in stripped:
        # surface the rejection candidate but mark it requires semantic review
        return _proposal(
            field="name",
            value=None,
            confidence=0.0,
            evidence="text contains question mark",
            requires_semantic_review=True,
        )
    return _proposal(
        field="name",
        value=stripped[:100],
        confidence=0.6,                # Phase B semantics decides accept
        evidence="text passes length/structure heuristic; semantic accept deferred to Tier B",
        requires_semantic_review=True,  # ALWAYS for name; Tier B is the only authority
    )


def _area_proposal(text: str, locked_area: Optional[str]) -> Optional[IntakeProposal]:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if len(stripped) > 60:
        return None
    if "?" in stripped or "؟" in stripped:
        return None
    return _proposal(
        field="area",
        value=stripped[:100],
        confidence=0.5,
        evidence="text passes length/structure heuristic; semantic accept deferred to Tier B",
        requires_semantic_review=True,
    )


def _dob_proposal(text: str) -> Optional[IntakeProposal]:
    """Phase B _looks_like_dob is exposed via the age parser; we surface
    a DOB proposal separately so Tier B can record 'DOB != age' and
    block extraction."""
    from modules.recruitment_flow import _looks_like_dob
    if not _looks_like_dob(text or ""):
        return None
    return _proposal(
        field="dob",
        value=text.strip(),
        confidence=0.95,
        evidence="_looks_like_dob returned True",
        requires_semantic_review=False,
    )


def propose(
    text: str,
    current_step: Optional[str],
    locked_fields: dict,
) -> List[IntakeProposal]:
    """Run every Phase B parser that applies to current_step and return
    the resulting IntakeProposal list.

    Order is by field; Tier B judges accepted vs rejected.
    """
    if not text or not text.strip():
        return []

    out: List[IntakeProposal] = []
    locked_name = locked_fields.get("full_name")
    locked_age = locked_fields.get("age")
    locked_area = locked_fields.get("area")
    locked_job = locked_fields.get("job_preference")
    locked_exp = locked_fields.get("experience_years")
    locked_phone = locked_fields.get("confirmed_phone")

    # DOB is checked regardless of step (it's a guard).
    dob = _dob_proposal(text)
    if dob:
        out.append(dob)

    if current_step == "name":
        n = _name_proposal(text, locked_name)
        if n:
            out.append(n)
    elif current_step == "age":
        a = _age_proposal(text, locked_age)
        if a:
            out.append(a)
    elif current_step == "area":
        ar = _area_proposal(text, locked_area)
        if ar:
            out.append(ar)
    elif current_step == "job_preference":
        j = _job_preposal(text, locked_job) if False else _job_preference_proposal(text, locked_job)
        if j:
            out.append(j)
    elif current_step == "experience":
        e = _experience_proposal(text, locked_exp)
        if e:
            out.append(e)
    elif current_step == "phone_confirm":
        p = _phone_proposal(text, locked_phone)
        if p:
            out.append(p)

    return out


__all__ = ["propose", "KNOWN_FIELDS", "STEP_QUESTIONS"]