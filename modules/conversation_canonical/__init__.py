"""Conversation canonical interpretation layer (Phase C).

Per Owner directive correction 5: this module is a READ/INTERPRETATION
layer over the existing wbom_whatsapp_messages rows. It does NOT
create a second conversation database or duplicate message history.

Public surface:

  build_canonical_history(phone, n=10)  ->  list[HistoryEntry]
  classify_actor(wbom_row)              ->  (actor_type_label, provenance_confidence)

HistoryEntry is defined in modules.recruitment_turn_decision.contract
to keep the dataclass shape authoritative in one place; this package
imports it for convenience.

This module is intentionally lightweight: it reads the DB once, labels
each row with actor_type + provenance_confidence, and returns. It does
not cache state across calls.
"""
from .actor_provenance import classify_actor, ADMIN_ALLOWLIST
from .canonical_history import build_canonical_history

__all__ = [
    "build_canonical_history",
    "classify_actor",
    "ADMIN_ALLOWLIST",
]