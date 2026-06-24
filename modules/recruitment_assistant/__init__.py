"""Knowledge-driven recruitment assistant safety layer."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RecruitmentGuidance:
    answer: str
    required_documents: list[str] = field(default_factory=list)
    admin_escalation: bool = False
    safety_notes: list[str] = field(default_factory=list)


def candidate_intake_summary(name: str = "", phone: str = "", area: str = "", position: str = "") -> dict:
    return {"name": name, "phone": phone, "area": area, "position": position, "status": "intake_started"}


def guidance_for_position(position: str) -> RecruitmentGuidance:
    normalized = position.strip().lower()
    docs = ["NID copy", "recent photo", "mobile number", "address"]
    if "guard" in normalized or "security" in normalized:
        docs.append("previous security experience, if any")
    return RecruitmentGuidance(
        answer="Share role requirements from the Knowledge Base and collect candidate details. Do not make a direct hiring decision.",
        required_documents=docs,
        admin_escalation=False,
        safety_notes=["No direct hiring decisions", "Escalate salary exceptions to admin"],
    )
