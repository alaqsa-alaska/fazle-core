"""Shared envelopes for Phase 2C mock-only orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


ALLOWED_STATUSES = {"ok", "partial", "failed", "unavailable", "not_implemented"}
ALLOWED_CONFIDENCE = {"Verified", "Partially Verified", "Inference", "Unknown"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolResultEnvelope:
    tool_id: str
    tool_name: str
    status: str
    confidence: str
    source: str
    timestamp: str
    data: dict[str, Any]
    authority: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    traceability: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported status: {self.status}")
        if self.confidence not in ALLOWED_CONFIDENCE:
            raise ValueError(f"unsupported confidence: {self.confidence}")
        if not self.authority:
            raise ValueError("authority is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceEnvelope:
    request_id: str
    mode: str
    classification: dict[str, Any]
    runtime: list[dict[str, Any]] = field(default_factory=list)
    business: list[dict[str, Any]] = field(default_factory=list)
    memory: list[dict[str, Any]] = field(default_factory=list)
    knowledge: list[dict[str, Any]] = field(default_factory=list)
    provider: dict[str, Any] = field(default_factory=dict)
    response: str | None = None
    confidence: str = "Unknown"
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    traceability: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.confidence not in ALLOWED_CONFIDENCE:
            raise ValueError(f"unsupported confidence: {self.confidence}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
