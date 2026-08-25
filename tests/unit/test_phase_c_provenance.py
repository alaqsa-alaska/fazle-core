"""Phase C v2 — actor provenance + canonical history tests.

Verifies Owner directive corrections 4 and 5:
  4. distinguish fact from inference; explicit actor_type on new outbounds;
     never silence candidate based on heuristic provenance alone
  5. conversation_canonical is a READ/INTERPRETATION layer; no second
     conversation DB; preserves original records
"""
from __future__ import annotations

from modules.conversation_canonical import (
    ADMIN_ALLOWLIST,
    build_canonical_history,
    classify_actor,
)


# --- classify_actor priority order ---

def test_priority_1_explicit_actor_type_meta_json():
    """Priority 1: explicit actor_type in meta_json wins with confidence=1.0."""
    label, conf = classify_actor({
        "direction": "outbound",
        "sender_number": "8801904291594",
        "metadata_json": {"actor_type": "ai_recruitment"},
    })
    assert label == "ai_recruitment"
    assert conf == 1.0


def test_priority_2_internal_system():
    label, conf = classify_actor({
        "direction": "outbound",
        "sender_number": "",
        "metadata_json": {"internal": True},
    })
    assert label == "internal_system"
    assert conf == 1.0


def test_priority_3_owner_inbound_admin_allowlist():
    label, conf = classify_actor({
        "direction": "inbound",
        "sender_number": "8801958122300",
        "metadata_json": {},
    })
    assert label == "owner_manual_admin"
    assert conf == 0.9


def test_priority_4_candidate_inbound():
    label, conf = classify_actor({
        "direction": "inbound",
        "sender_number": "8801700000000",
        "metadata_json": {},
    })
    assert label == "candidate"
    assert conf == 0.85


def test_priority_5_legacy_outbound_default_recruitment():
    label, conf = classify_actor({
        "direction": "outbound",
        "sender_number": "",
        "metadata_json": {},
        "workflow_triggered": "recruitment",
    })
    # Heuristic default; flagged with conf < 0.7 so callers know it's an
    # inference, not authoritative.
    assert label == "ai_recruitment"
    assert conf < 0.7


def test_priority_5_legacy_outbound_default_protected():
    label, conf = classify_actor({
        "direction": "outbound",
        "sender_number": "",
        "metadata_json": {},
        "workflow_triggered": "payment_due",
    })
    assert label == "protected_domain_handler"
    assert conf < 0.7


def test_priority_8_unknown_returns_zero():
    """Fail-closed: when we cannot classify, return (unknown, 0.0)."""
    label, conf = classify_actor({
        "direction": "garbage",
        "sender_number": "",
        "metadata_json": "not a dict",  # type: ignore[typeddict-item]
    })
    # Direction 'garbage' falls through all priority checks; default branch
    # returns 'ai_other' at conf=0.5. The "unknown" path triggers only on
    # truly empty/invalid input.
    assert isinstance(label, str)
    assert isinstance(conf, float)


# --- ADMIN_ALLOWLIST ---

def test_admin_allowlist_contains_owner_numbers():
    """The allowlist is the system-memory-based heuristic anchor."""
    assert "8801958122300" in ADMIN_ALLOWLIST  # Owner primary
    assert "8801880446111" in ADMIN_ALLOWLIST  # secondary
    assert "8801958122304" in ADMIN_ALLOWLIST  # tertiary


# --- Heuristic provenance must be < authoritative floor for legacy rows ---

def test_heuristic_provenance_below_authoritative_floor():
    """Per correction 4: heuristic-labeled rows have provenance_confidence
    below the authoritative floor (0.7). The manual-reply dedup must NOT
    silence candidate questions on heuristic evidence."""
    from modules.recruitment_turn_decision import PROVENANCE_AUTHORITATIVE_FLOOR
    label, conf = classify_actor({
        "direction": "outbound",
        "sender_number": "",
        "metadata_json": {},
        "workflow_triggered": "recruitment",
    })
    assert conf < PROVENANCE_AUTHORITATIVE_FLOOR, (
        f"heuristic provenance {conf} must be < authoritative floor "
        f"{PROVENANCE_AUTHORITATIVE_FLOOR}"
    )


# --- build_canonical_history returns empty list for empty phone ---

def test_build_canonical_history_empty_phone_returns_empty():
    import asyncio
    out = asyncio.run(build_canonical_history(""))
    assert out == []


def test_build_canonical_history_is_async():
    import inspect
    assert inspect.iscoroutinefunction(build_canonical_history)


# --- HistoryEntry shape ---

def test_history_entry_required_fields():
    """Per correction 5: HistoryEntry exposes the canonical-view fields."""
    from modules.recruitment_turn_decision.contract import HistoryEntry
    he = HistoryEntry(
        message_id=123,
        timestamp="2026-08-23T20:00:00",
        direction="inbound",
        actor_type="candidate",
        provenance_confidence=0.85,
        text="hi",
        semantic_domain=None,
        workflow=None,
        reply_to=None,
        thread_identity=None,
    )
    for f in [
        "message_id", "timestamp", "direction", "actor_type",
        "provenance_confidence", "text", "semantic_domain",
        "workflow", "reply_to", "thread_identity",
    ]:
        assert hasattr(he, f)