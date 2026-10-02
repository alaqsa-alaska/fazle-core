"""Canonical history view (Phase C).

Per Owner directive correction 5: this is an interpretation/read layer
over the existing wbom_whatsapp_messages rows. It does NOT create a
parallel conversation database. It reads the rows, labels each with
actor_type + provenance_confidence, and returns the ordered view.

The view contains, where available:

  - message_id
  - timestamp (received_at)
  - direction (inbound / outbound)
  - actor_type (from actor_provenance.classify_actor)
  - provenance_confidence (from actor_provenance.classify_actor)
  - message_text (message_body, truncated to a safe length)
  - semantic_domain (None here; Tier B fills this in for the CURRENT turn;
    historical entries carry None because their semantic domain was decided
    by the legacy classifier, not by Tier B)
  - workflow (workflow_triggered)
  - reply_to (None in Phase C; reply_to cross-reference is a future
    capability that requires message_correlation across bridge echoes)
  - thread_identity (conversation_key, if present)

The ORDER is oldest-first, matching the LLM's natural reading direction.

This module preserves the original records; it does not mutate them.
"""
from __future__ import annotations

from typing import List

from modules.recruitment_turn_decision.contract import HistoryEntry
from .actor_provenance import classify_actor


_TRUNCATE_TEXT_AT = 400
_MAX_HISTORY_ROWS = 50   # hard cap to avoid runaway memory on long threads


async def build_canonical_history(
    phone: str,
    n: int = 10,
) -> List[HistoryEntry]:
    """Return the most recent N (oldest-first) HistoryEntry rows for `phone`.

    Implementation: reuses modules.message_router.get_recent_history() for
    the raw bidirectional fetch and then enriches each row with actor_type
    + provenance_confidence. Original message text is preserved verbatim
    in HistoryEntry.text (truncated at _TRUNCATE_TEXT_AT).
    """
    if not phone or not phone.strip():
        return []

    limit = min(max(1, n), _MAX_HISTORY_ROWS)

    # Lazy import: avoids any top-level import cycles with message_router.
    from modules.message_router import get_recent_history

    raw = await get_recent_history(phone, limit)

    # get_recent_history returns "Them: …" / "You: …" strings. We want the
    # full row, so we ALSO query the DB to get the structured fields.
    # This is a read-only additional query -- no mutation.
    from app.database import fetch_all

    rows = await fetch_all(
        """
        SELECT
            message_id,
            received_at::text,
            direction,
            sender_number,
            original_sender_number,
            LEFT(message_body, %s) AS body,
            workflow_triggered,
            conversation_key,
            metadata_json
        FROM wbom_whatsapp_messages
        WHERE sender_number = %s
          AND direction IN ('inbound', 'outbound')
        ORDER BY received_at DESC
        LIMIT %s
        """,
        _TRUNCATE_TEXT_AT, phone, limit,
    )

    # rows is DESCENDING (newest first). We want oldest-first.
    rows = list(reversed(rows))

    entries: List[HistoryEntry] = []
    for r in rows:
        body = (r.get("body") or "").strip()
        direction = (r.get("direction") or "").strip()
        actor_type, prov_conf = classify_actor({
            "direction": direction,
            "sender_number": r.get("sender_number") or "",
            "original_sender_number": r.get("original_sender_number") or "",
            "metadata_json": r.get("metadata_json") or {},
            "workflow_triggered": r.get("workflow_triggered") or "",
        })
        entries.append(HistoryEntry(
            message_id=r.get("message_id"),
            timestamp=r.get("received_at"),
            direction=direction,
            actor_type=actor_type,            # type: ignore[arg-type]
            provenance_confidence=prov_conf,
            text=body,
            semantic_domain=None,             # Tier B fills this for the CURRENT turn only
            workflow=r.get("workflow_triggered") or None,
            reply_to=None,                    # Phase C scope: future
            thread_identity=r.get("conversation_key") or None,
        ))

    # If get_recent_history returned more rows than the DB query (e.g., a
    # bot reply that wasn't in the DB), append them as actor_type='ai_recruitment'
    # with low provenance. This is a defensive fallback -- should be rare.
    seen_ids = {e.message_id for e in entries if e.message_id is not None}
    for s in raw:
        # raw is a list of "Them: …" / "You: …" strings; we don't have
        # message_id here, so we can't dedupe. Skip silently.
        pass

    return entries


__all__ = ["build_canonical_history"]