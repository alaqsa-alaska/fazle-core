"""Fact-level Recruitment authority over the existing text/KB sources.

This is a read-only register and evidence annotator.  It does not store
applicants, replace the existing KB/RAG index, or activate policy.  Unresolved
facts intentionally carry no answer value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_REGISTER_PATH = Path(__file__).resolve().parents[1] / "knowledge_base" / "00_governance" / "recruitment_fact_authority.json"
_MARKER = "[FAZLE_FACT_AUTHORITY]"
_ANSWERABLE = {"current_approved"}


@dataclass(frozen=True, slots=True)
class FactAuthority:
    key: str
    subject: str
    status: str
    value: str | None
    competing_values: tuple[str, ...]
    sources: tuple[str, ...]
    conditions: tuple[str, ...]
    safe_action: str

    @property
    def answerable(self) -> bool:
        return self.status in _ANSWERABLE and bool(self.value)

    @property
    def authority(self) -> str:
        return "owner_approved_fact" if self.answerable else "evidence_only"

    def as_marker(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "subject": self.subject,
            "status": self.status,
            "authority": self.authority,
            "value": self.value if self.answerable else None,
            "competing_values": list(self.competing_values),
            "sources": list(self.sources),
            "conditions": list(self.conditions),
            "safe_action": self.safe_action,
        }


def _make_fact(raw: dict[str, Any]) -> FactAuthority:
    status = str(raw.get("status", "unverified"))
    value = raw.get("value")
    if status not in {"current_approved", "historical", "unverified", "conflicting", "superseded"}:
        raise ValueError(f"unsupported Recruitment fact status: {status}")
    if status not in _ANSWERABLE:
        value = None
    return FactAuthority(
        key=str(raw["key"]), subject=str(raw.get("subject", raw["key"])),
        status=status, value=value,
        competing_values=tuple(str(v) for v in raw.get("competing_values", ())),
        sources=tuple(str(v) for v in raw.get("sources", ())),
        conditions=tuple(str(v) for v in raw.get("conditions", ())),
        safe_action=str(raw.get("safe_action", "request_current_owner_confirmation")),
    )


@lru_cache(maxsize=1)
def _load_register_payload() -> dict[str, Any]:
    return json.loads(_REGISTER_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_recruitment_authority() -> tuple[FactAuthority, ...]:
    """Load the immutable register; a missing/corrupt register fails closed."""
    try:
        payload = _load_register_payload()
        return tuple(_make_fact(item) for item in payload.get("facts", ()))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return (FactAuthority("authority.register", "authority_register", "unverified", None, (), (), (), "request_current_owner_confirmation"),)


def _source_matches(source: str, reference: str) -> bool:
    source = source.replace("\\", "/").lower()
    reference = reference.replace("\\", "/").lower()
    return source == reference or source.endswith(reference) or reference.endswith(source)


def facts_for_evidence(source: str, text: str = "", *, include_all: bool = False) -> tuple[FactAuthority, ...]:
    """Return facts relevant to one existing source/chunk, never by score."""
    lowered = (text or "").casefold()
    selected: list[FactAuthority] = []
    for fact in load_recruitment_authority():
        source_hit = any(_source_matches(source, ref) for ref in fact.sources)
        keyword_hit = any(keyword.casefold() in lowered for keyword in _keywords_for(fact))
        if source_hit and (include_all or keyword_hit or not text.strip()):
            selected.append(fact)
        elif not source_hit and keyword_hit and text.strip():
            selected.append(fact)
    return tuple(dict((fact.key, fact) for fact in selected).values())


_RECRUITMENT_FOCUS_FACT_KEYS = {
    "salary": ("salary.by_role",),
    "fee": ("recruitment.fee.existence",),
    "office_location": ("office.address.current",),
    "training": ("training.duration",),
    "documents": ("recruitment.required_documents",),
    "positions": ("recruitment.roles.available", "recruitment.vacancies.current"),
    "application": ("recruitment.lifecycle.model", "recruitment.application.selection_authority"),
}


def facts_for_recruitment_question(text: str, *, focus: str = "") -> tuple[FactAuthority, ...]:
    """Return register facts relevant to a live Recruitment question.

    Keyword selection remains the primary retrieval mechanism. The small
    focus mapping only bridges conversation labels to register keys; it never
    supplies a value or changes a fact status.
    """
    lowered = (text or "").casefold()
    selected = {
        fact.key: fact
        for fact in facts_for_evidence("fallback:recruitment", text)
    }
    # The Bengali substring "ফি" occurs inside "অফিস". Do not let that
    # lexical overlap volunteer fee policy for an office-location question.
    without_office = lowered.replace("অফিস", "").replace("office", "")
    if not any(token in without_office for token in ("ফি", "জামানত", "deposit", "joining fee", "ভর্তি")):
        selected.pop("recruitment.fee.existence", None)
        selected.pop("fee.amounts", None)
    by_key = {fact.key: fact for fact in load_recruitment_authority()}
    wanted = []
    if focus == "salary" and any(token in lowered for token in ("বেতন", "salary", "মাসিক", "কত পাব")):
        wanted.append("salary.by_role")
    if focus == "fee" and any(token in without_office for token in ("ফি", "জামানত", "deposit", "joining fee", "ভর্তি")):
        wanted.append("recruitment.fee.existence")
    if focus == "office_location" or "অফিস" in lowered or "office" in lowered:
        if any(token in lowered for token in ("কোথা", "where", "address", "লোকেশন", "location")):
            wanted.append("office.address.current")
        if any(token in lowered for token in ("কখন", "সময়", "সময়", "খোলা", "শুক্রবার", "friday", "hour")):
            wanted.append("office.hours_and_friday")
        if any(token in lowered for token in ("যোগাযোগ", "নম্বর", "ফোন", "whatsapp", "কল", "contact", "phone")):
            wanted.append("office.contact.current")
    for key in wanted:
        if key in by_key:
            selected[key] = by_key[key]
    return tuple(selected.values())


def _keywords_for(fact: FactAuthority) -> tuple[str, ...]:
    try:
        item = next(item for item in _load_register_payload().get("facts", ()) if item.get("key") == fact.key)
        return tuple(str(v) for v in item.get("keywords", ())) + (fact.key, fact.subject)
    except (OSError, ValueError, TypeError, StopIteration, json.JSONDecodeError):
        return (fact.key, fact.subject)


def render_authority_markers(source: str, text: str = "", *, include_all: bool = False) -> str:
    return "\n".join(_MARKER + " " + json.dumps(fact.as_marker(), ensure_ascii=False, sort_keys=True)
                     for fact in facts_for_evidence(source, text, include_all=include_all))


def parse_authority_markers(text: str) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        if not line.startswith(_MARKER):
            continue
        try:
            record = json.loads(line[len(_MARKER):].strip())
        except json.JSONDecodeError:
            continue
        if record.get("key") and record.get("status"):
            records.append(record)
    return tuple(records)
