"""Actor provenance classifier (Phase C).

Per Owner directive correction 4: every NEW outbound message must
receive an explicit actor_type at creation time. Historical rows
(missing actor_type) are inferred heuristically; the inference is
labeled with provenance_confidence so callers can decide whether to
treat the entry as authoritative.

The classifier never makes up an authoritative answer when it doesn't
know. If a row is genuinely ambiguous, it returns ("unknown", 0.0) so
the Tier B decision can fail closed.

This module has NO database dependency. The caller passes the row dict
(typically a row from wbom_whatsapp_messages).
"""
from __future__ import annotations

from typing import Tuple


# === Admin / Owner allowlist ===
# Per system memory + Phase A evidence:
#   Owner Azim's primary WhatsApp:  8801958122300
#   Secondary admin / HERMES:       8801880446111
#   Tertiary admin / outreach:      8801958122304
# These are the ONLY phone numbers we treat as "owner_manual_admin"
# when they appear as senders in inbound messages. (This is heuristic;
# see Phase A plan for why provenance is inference-only until a
# schema-level actor_type column is added.)
ADMIN_ALLOWLIST = ("8801958122300", "8801880446111", "8801958122304")


def classify_actor(row: dict) -> Tuple[str, float]:
    """Returns (actor_type_label, provenance_confidence in [0, 1]).

    Priority order (deterministic, fail-closed):

      1. If the row carries an EXPLICIT actor_type in meta_json and it's
         in our KNOWN_ACTOR_TYPES, return it with confidence=1.0.

      2. If direction='outbound' and meta_json.internal is true (or its
         key equivalent), label as "internal_system" with confidence=1.0.

      3. If direction='inbound' and sender_number is in ADMIN_ALLOWLIST,
         return ("owner_manual_admin", 0.9).

      4. If direction='inbound' and sender_number is not in the
         admin allowlist, return ("candidate", 0.85).

      5. If direction='outbound' AND meta_json.actor_type is MISSING
         (legacy outbound rows that lack the new field), the heuristic
         default is ("ai_recruitment", 0.6). This is FLAGGED so callers
         know it's an inference; do NOT use it to silence candidate
         questions.

      6. If direction='outbound' AND meta_json.internal=true (system
         template), return ("system_template", 0.95).

      7. If direction='outbound' AND meta_json.actor_type is one of
         "ai_employee"|"ai_attendance"|"ai_escort"|..., return
         ("protected_domain_handler", 0.95).

      8. Anything ambiguous returns ("unknown", 0.0).
    """
    KNOWN_ACTOR_TYPES = {
        "candidate",
        "ai_recruitment",
        "ai_other",
        "owner_manual_admin",
        "system_template",
        "protected_domain_handler",
        "internal_system",
    }

    direction = (row.get("direction") or "").strip()
    sender = (row.get("sender_number") or "").strip()
    meta = row.get("metadata_json") or {}
    if not isinstance(meta, dict):
        meta = {}

    # 1. Explicit actor_type from meta_json
    explicit = meta.get("actor_type")
    if isinstance(explicit, str) and explicit in KNOWN_ACTOR_TYPES:
        return (explicit, 1.0)

    # 2. internal_system notification
    if direction == "outbound" and (meta.get("internal") is True or meta.get("internal") == "true"):
        return ("internal_system", 1.0)

    # 3. Owner inbound
    if direction == "inbound" and sender in ADMIN_ALLOWLIST:
        return ("owner_manual_admin", 0.9)

    # 4. Candidate inbound
    if direction == "inbound":
        return ("candidate", 0.85)

    # 5. Outbound without explicit actor_type (legacy / unfilled)
    if direction == "outbound":
        workflow = (row.get("workflow_triggered") or "").strip()
        if workflow == "recruitment":
            return ("ai_recruitment", 0.6)         # heuristic default; flagged
        if workflow in ("payment_due", "slip_submission", "attendance", "leave",
                         "client_order", "greeting", "join"):
            return ("protected_domain_handler", 0.6)  # heuristic default; flagged
        # 6 / 7 / 8 fall through
        return ("ai_other", 0.5)

    return ("unknown", 0.0)


__all__ = ["classify_actor", "ADMIN_ALLOWLIST"]