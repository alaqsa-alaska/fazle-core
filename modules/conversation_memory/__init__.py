"""
Fazle Core — Conversation Memory Engine (Phase B, Stage 1, 2026-07-19).

Short-lived, persistent, restart-safe operational memory for ongoing
WhatsApp conversations -- lets the system understand several consecutive
messages as one business object (e.g. an escort order split across two
messages) instead of discarding each incomplete message in isolation.

This is explicitly NOT long-term personal memory (that's Phase C,
planning-only, not built here) and does NOT grant order-creation
authority to anyone who doesn't already have it -- conversation
reconstruction changes what the system *understands*, never who is
*authorized*. Every authorization gate this repo already has
(ESCORT_CLIENT_PHONES, _is_authoritative_bridge_confirmation(),
_is_system_generated_notification()) remains the caller's responsibility,
unchanged, upstream of anything in this module.

Reuses rather than duplicates:
- wbom_whatsapp_messages as the raw message source (no new message-event
  table -- see migration 053's own header comment).
- modules.escort._normalize_mobile for BD phone normalization (not a
  third normalization implementation).
- The processed_bridge_messages/processed_outgoing_escort_messages
  (message_id, bridge) dedup pattern, applied here as
  processed_conversation_messages.

Stage 1 scope: thread identity, field merge with provenance, TTL/expiry,
idempotency. Does NOT include: the escort multi-message parser itself
(Stage 2), Bangla semantic extraction (Stage 3), or message_router
integration beyond a shadow-mode hook (Stage 4). No business row
(draft/program/queue item) is ever created by this module directly --
callers own that decision entirely.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import get_settings
from app.database import execute, fetch_all, fetch_one, fetch_val

log = logging.getLogger("fazle.conversation_memory")

OPEN_STATES = ("open", "partial", "conflicted")
TERMINAL_STATES = ("complete", "draft_created", "awaiting_confirmation", "expired", "resolved", "abandoned")
ALL_STATES = OPEN_STATES + TERMINAL_STATES


def _ttl_for_domain(business_domain: str) -> timedelta:
    """Configurable per domain (Part 4.5) -- never hardcoded in parser
    functions. Unknown domains fall back to the general TTL."""
    settings = get_settings()
    if business_domain == "escort":
        return timedelta(minutes=settings.conversation_ttl_escort_minutes)
    if business_domain == "recruitment":
        return timedelta(hours=settings.conversation_ttl_recruitment_hours)
    return timedelta(hours=settings.conversation_ttl_general_hours)


async def is_message_processed(message_id: str, bridge: str) -> bool:
    """Idempotency check -- same (message_id, bridge) pattern as
    modules.bridge_poller's own processed_*_messages tables."""
    if not message_id:
        return False
    val = await fetch_val(
        "SELECT 1 FROM processed_conversation_messages WHERE message_id=$1 AND bridge=$2",
        message_id, bridge,
    )
    return val is not None


async def mark_message_processed(message_id: str, bridge: str, thread_id: Optional[int]) -> None:
    if not message_id:
        return
    await execute(
        """INSERT INTO processed_conversation_messages (message_id, bridge, thread_id)
           VALUES ($1, $2, $3) ON CONFLICT DO NOTHING""",
        message_id, bridge, thread_id,
    )


async def get_open_thread(
    *, bridge_id: str, counterparty_phone: str, business_domain: str,
) -> Optional[dict]:
    """Most recent open/partial/conflicted, NON-EXPIRED thread for this
    identity, if any. Conversation identity is platform+bridge+
    counterparty+domain (Part 4.2) -- deliberately NOT phone alone, so an
    escort-domain thread and a recruitment-domain thread for the same
    phone never collide, and a thread on bridge1 never merges with one on
    bridge2.

    Filters expires_at > NOW() explicitly rather than relying solely on
    expire_stale_threads() having already run -- Part 8's "after TTL
    expiry, must not silently reopen/merge" is a per-read guarantee, not
    just a post-sweep one; a message arriving in the gap between expiry
    and the next scheduler tick must not merge into a thread that is
    already, logically, expired."""
    row = await fetch_one(
        """SELECT * FROM fazle_conversation_threads
           WHERE bridge_id = $1 AND counterparty_phone = $2 AND business_domain = $3
             AND state = ANY($4) AND expires_at > NOW()
           ORDER BY last_message_at DESC LIMIT 1""",
        bridge_id, counterparty_phone, business_domain, list(OPEN_STATES),
    )
    return _deserialize(row) if row else None


async def get_open_threads(
    *, bridge_id: str, counterparty_phone: str, business_domain: str,
) -> list[dict]:
    """All open/partial/conflicted, non-expired threads for this identity,
    most recently active first -- Stage 2's grouping/interleaving
    protection (Part 5) needs to see every live candidate thread, not just
    the most recent one, to decide whether an incoming message continues
    an existing thread, starts a new one, or is ambiguous between several."""
    rows = await fetch_all(
        """SELECT * FROM fazle_conversation_threads
           WHERE bridge_id = $1 AND counterparty_phone = $2 AND business_domain = $3
             AND state = ANY($4) AND expires_at > NOW()
           ORDER BY last_message_at DESC""",
        bridge_id, counterparty_phone, business_domain, list(OPEN_STATES),
    )
    return [_deserialize(r) for r in rows]


async def create_thread(
    *,
    platform: str,
    bridge_id: str,
    counterparty_phone: str,
    business_domain: str,
    sender_role_at_open: Optional[str] = None,
    business_intent: Optional[str] = None,
    first_message_id: Optional[str] = None,
) -> dict:
    """Always creates a new thread -- callers wanting "find existing or
    create" should call get_open_thread() first (kept as two explicit
    steps rather than one upsert-style function, so a caller can decide
    what "no open thread" should mean for their own domain: e.g. escort's
    Stage 2 multi-message parser will call get_open_thread() first, but a
    domain wanting one-thread-per-message could skip that call
    entirely)."""
    now = datetime.now(timezone.utc)
    expires_at = now + _ttl_for_domain(business_domain)
    row = await fetch_one(
        """INSERT INTO fazle_conversation_threads
               (platform, bridge_id, counterparty_phone, sender_role_at_open,
                business_domain, business_intent, state, fields,
                first_message_id, first_message_at, last_message_id, last_message_at,
                expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, 'open', '{}'::jsonb, $7, $8, $7, $8, $9)
           RETURNING *""",
        platform, bridge_id, counterparty_phone, sender_role_at_open,
        business_domain, business_intent, first_message_id, now, expires_at,
    )
    log.info(
        "[conversation_memory] thread created id=%s bridge=%s counterparty=%s domain=%s expires_at=%s",
        row["id"], bridge_id, counterparty_phone, business_domain, expires_at,
    )
    return _deserialize(row)


def _deserialize(row: dict) -> dict:
    d = dict(row)
    for key in ("fields", "conflict_flags"):
        if isinstance(d.get(key), str):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


class FieldMergeResult:
    """Outcome of merging one extracted field into a thread -- callers
    use this to decide whether to log/surface a conflict, never silently
    swallow it (Part 6.2's explicit "conflicting high-confidence value
    must create a conflict state rather than silent overwrite")."""

    def __init__(self, applied: bool, conflict: bool, reason: str):
        self.applied = applied
        self.conflict = conflict
        self.reason = reason

    def __repr__(self) -> str:
        return f"FieldMergeResult(applied={self.applied}, conflict={self.conflict}, reason={self.reason!r})"


_CONFLICT_CONFIDENCE_THRESHOLD = 0.6


def merge_field(
    fields: dict,
    field_name: str,
    *,
    value: Any,
    source_message_id: Optional[str],
    confidence: float,
    extraction_method: str,
    is_system_generated: bool,
    is_explicit_label: bool = False,
) -> tuple[dict, FieldMergeResult]:
    """Pure function: given a thread's current `fields` dict and one
    newly-extracted field, returns the (possibly updated) fields dict and
    a FieldMergeResult explaining what happened. Does not touch the
    database -- callers persist the returned fields dict themselves,
    inside whatever transaction/lock scope their own domain needs (this
    module deliberately doesn't impose one, matching this session's own
    established preference for advisory-lock-scoped callers over a
    one-size-fits-all internal transaction).

    Merge policy (Part 6.2), in order:
    1. System-generated content never overrides human-originated data,
       and never even gets recorded as if authoritative -- rejected
       outright.
    2. A field with no existing value is simply set.
    3. An explicit labelled value (e.g. "Mother Vessel: X") overrides a
       previously inferred value for the same field.
    4. A later, equal-or-higher-confidence correction may override an
       earlier lower-confidence value.
    5. Otherwise: if the new value differs from the existing one and
       both are reasonably confident, this is a conflict -- flagged, not
       silently overwritten. If the values are the same, no-op.
    """
    if is_system_generated:
        return fields, FieldMergeResult(False, False, "system_generated_content_rejected")

    existing = fields.get(field_name)
    new_entry = {
        "value": value,
        "source_message_id": source_message_id,
        "confidence": confidence,
        "extraction_method": extraction_method,
    }

    if existing is None:
        updated = {**fields, field_name: new_entry}
        return updated, FieldMergeResult(True, False, "field_set_first_time")

    if existing.get("value") == value:
        return fields, FieldMergeResult(False, False, "unchanged_value")

    # Part 4's "normalized-equivalent values are not conflicts" -- e.g.
    # the same vessel name with different whitespace/case, or the same
    # phone number in a different textual form that the upstream parser
    # didn't already normalize away. Treated identically to a raw match:
    # a no-op, not a correction and not a conflict.
    from modules.conversation_memory.grouping import normalize_for_comparison
    if normalize_for_comparison(field_name, existing.get("value")) == normalize_for_comparison(field_name, value):
        return fields, FieldMergeResult(False, False, "normalized_equivalent_value")

    if is_explicit_label and not existing.get("is_explicit_label"):
        new_entry["is_explicit_label"] = True
        updated = {**fields, field_name: new_entry}
        return updated, FieldMergeResult(True, False, "explicit_label_overrides_inferred")

    existing_confidence = float(existing.get("confidence") or 0.0)
    # Strictly greater, not >=: two equally-confident but DIFFERENT values
    # (e.g. two explicit "Mother Vessel: X" labels naming different ships)
    # must not silently overwrite one another -- Part 4's "a later message
    # must not silently overwrite a different high-confidence explicit
    # value" is exactly the equal-confidence case, so it has to fall
    # through to the conflict check below rather than being treated as a
    # strictly-better correction.
    if confidence > existing_confidence and confidence >= _CONFLICT_CONFIDENCE_THRESHOLD:
        updated = {**fields, field_name: new_entry}
        return updated, FieldMergeResult(True, False, "higher_confidence_correction")

    if confidence >= _CONFLICT_CONFIDENCE_THRESHOLD and existing_confidence >= _CONFLICT_CONFIDENCE_THRESHOLD:
        return fields, FieldMergeResult(False, True, "conflicting_high_confidence_values")

    return fields, FieldMergeResult(False, False, "low_confidence_value_ignored")


async def update_thread(
    thread_id: int,
    *,
    fields: Optional[dict] = None,
    state: Optional[str] = None,
    last_message_id: Optional[str] = None,
    last_system_action: Optional[str] = None,
    related_object_type: Optional[str] = None,
    related_object_id: Optional[int] = None,
    conflict_flags: Optional[list] = None,
    ambiguity_reason: Optional[str] = None,
) -> Optional[dict]:
    """Partial update -- only supplied kwargs are written. Always bumps
    last_message_at/updated_at when called with a new last_message_id."""
    if state is not None and state not in ALL_STATES:
        raise ValueError(f"invalid state: {state!r}")

    set_clauses: list[str] = ["updated_at = NOW()"]
    args: list[Any] = []

    def push(sql_fragment: str, value: Any) -> None:
        args.append(value)
        set_clauses.append(f"{sql_fragment} = ${len(args)}")

    if fields is not None:
        push("fields", json.dumps(fields, default=str))
    if state is not None:
        push("state", state)
    if last_message_id is not None:
        push("last_message_id", last_message_id)
        set_clauses.append("last_message_at = NOW()")
    if last_system_action is not None:
        push("last_system_action", last_system_action)
    if related_object_type is not None:
        push("related_object_type", related_object_type)
    if related_object_id is not None:
        push("related_object_id", related_object_id)
    if conflict_flags is not None:
        push("conflict_flags", json.dumps(conflict_flags, default=str))
    if ambiguity_reason is not None:
        push("ambiguity_reason", ambiguity_reason)

    args.append(thread_id)
    row = await fetch_one(
        f"UPDATE fazle_conversation_threads SET {', '.join(set_clauses)} WHERE id = ${len(args)} RETURNING *",
        *args,
    )
    return _deserialize(row) if row else None


EXCLUSION_GROUPING_ACTIONS = ("unauthorized_excluded", "system_generated_excluded", "duplicate_replay")


async def record_shadow_comparison(
    *,
    thread_id: Optional[int],
    bridge_id: str,
    counterparty_phone: str,
    business_domain: str,
    message_id: Optional[str],
    current_parser_result: dict,
    conversation_aware_result: dict,
    field_provenance: dict,
    completion_state: str,
    grouping_action: str,
    grouping_reason: str,
    conflicts: list,
    would_create_draft: bool,
    reason: str,
    existing_draft_relation: Optional[str] = None,
    existing_draft_relation_detail: Optional[dict] = None,
    parser_missed_candidate: bool = False,
    parser_would_have_merged_incorrectly: bool = False,
    processing_latency_ms: Optional[int] = None,
) -> None:
    """Stage 2 Part 9 -- one structured row per observed message while
    multi_message_escort_parser_enabled is on. Never read by anything that
    creates a business row; exists purely for the required aggregate
    metrics, all computed on demand via GROUP BY over this table."""
    await execute(
        """INSERT INTO fazle_conversation_shadow_comparisons
               (thread_id, bridge_id, counterparty_phone, business_domain, message_id,
                current_parser_result, conversation_aware_result, field_provenance,
                completion_state, grouping_action, grouping_reason, conflicts,
                would_create_draft, reason, existing_draft_relation,
                existing_draft_relation_detail, parser_missed_candidate,
                parser_would_have_merged_incorrectly, processing_latency_ms)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)""",
        thread_id, bridge_id, counterparty_phone, business_domain, message_id,
        json.dumps(current_parser_result, default=str),
        json.dumps(conversation_aware_result, default=str),
        json.dumps(field_provenance, default=str),
        completion_state, grouping_action, grouping_reason,
        json.dumps(conflicts, default=str),
        would_create_draft, reason, existing_draft_relation,
        json.dumps(existing_draft_relation_detail, default=str) if existing_draft_relation_detail is not None else None,
        parser_missed_candidate, parser_would_have_merged_incorrectly, processing_latency_ms,
    )


async def record_shadow_exclusion(
    *, bridge_id: str, counterparty_phone: str, business_domain: str,
    message_id: Optional[str], exclusion_kind: str,
) -> None:
    """Section 6's "system-generated messages excluded" / "unauthorized
    senders excluded" / "duplicate message replays" counts need a row to
    count -- these cases return early in _observe() before any candidate
    logic runs, so without this they'd be invisible to
    shadow_comparison_metrics(). exclusion_kind must be one of
    EXCLUSION_GROUPING_ACTIONS. Only called while
    multi_message_escort_parser_enabled is on, same scope boundary as
    record_shadow_comparison()."""
    if exclusion_kind not in EXCLUSION_GROUPING_ACTIONS:
        raise ValueError(f"invalid exclusion_kind: {exclusion_kind!r}")
    await execute(
        """INSERT INTO fazle_conversation_shadow_comparisons
               (thread_id, bridge_id, counterparty_phone, business_domain, message_id,
                current_parser_result, conversation_aware_result, field_provenance,
                completion_state, grouping_action, grouping_reason, conflicts,
                would_create_draft, reason)
           VALUES (NULL,$1,$2,$3,$4,'{}'::jsonb,'{}'::jsonb,'{}'::jsonb,
                   'excluded',$5,$5,'[]'::jsonb,FALSE,$5)""",
        bridge_id, counterparty_phone, business_domain, message_id, exclusion_kind,
    )


_REQUIRED_FIELDS_ARRAY = "array['mother_vessel','lighter_vessel','master_mobile']"


async def shadow_comparison_metrics(business_domain: str = "escort") -> dict:
    """Stage 4 Section 6's full required metric set, computed on demand
    from fazle_conversation_shadow_comparisons + fazle_conversation_threads
    -- never a running counter that could drift from the underlying rows.
    "Shadow exceptions" are intentionally not included here -- they're
    logged (`log.error("[shadow] conversation memory observation
    failed...")`) rather than persisted, since an exception can occur
    before any row would exist to attach it to; read via
    `grep -c '\\[shadow\\] conversation memory observation' logs/fazle-core.log`."""
    row = await fetch_one(
        f"""SELECT
               count(*) FILTER (WHERE grouping_action NOT IN ('unauthorized_excluded','system_generated_excluded','duplicate_replay'))
                   AS authorized_client_messages_observed,
               count(DISTINCT thread_id) FILTER (WHERE thread_id IS NOT NULL)
                   AS reconstructed_conversation_threads,
               count(*) FILTER (WHERE would_create_draft AND NOT parser_missed_candidate)
                   AS single_message_complete_orders,
               count(*) FILTER (WHERE would_create_draft AND parser_missed_candidate)
                   AS multi_message_complete_candidates,
               count(*) FILTER (WHERE grouping_reason = 'conflicting_identity_fields_treated_as_new_order')
                   AS possible_interleaved_orders,
               count(*) FILTER (WHERE grouping_action = 'duplicate_replay')
                   AS duplicate_message_replays,
               count(*) FILTER (WHERE grouping_action = 'system_generated_excluded')
                   AS system_generated_messages_excluded,
               count(*) FILTER (WHERE grouping_action = 'unauthorized_excluded')
                   AS unauthorized_senders_excluded,
               count(*) FILTER (WHERE current_parser_result ?& {_REQUIRED_FIELDS_ARRAY})
                   AS current_parser_drafts,
               count(*) FILTER (WHERE would_create_draft)
                   AS shadow_parser_would_create_draft_results,
               count(*) FILTER (WHERE parser_missed_candidate)
                   AS current_parser_misses_recovered_by_shadow_parsing,
               count(*) FILTER (WHERE (current_parser_result ?& {_REQUIRED_FIELDS_ARRAY}) AND NOT would_create_draft)
                   AS current_parser_successes_missed_by_shadow_parsing,
               count(*) FILTER (WHERE jsonb_array_length(conflicts) > 0)
                   AS field_level_disagreements,
               count(*) FILTER (WHERE grouping_action = 'merge_existing' AND grouping_reason = 'single_open_thread_no_identity_conflict')
                   AS possible_false_merges,
               count(*) FILTER (WHERE grouping_action = 'needs_review')
                   AS possible_missed_merges,
               count(*) FILTER (WHERE field_provenance::text LIKE '%bangla_label%')
                   AS bangla_labelled_fields_extracted,
               count(*) FILTER (
                   WHERE EXISTS (
                       SELECT 1 FROM jsonb_each(conversation_aware_result) f(key, value)
                       WHERE f.key IN ('mother_vessel','lighter_vessel','master_mobile')
                         AND (f.value->>'confidence')::numeric < 0.6
                   )
               ) AS low_confidence_required_fields,
               round(avg(processing_latency_ms) FILTER (WHERE processing_latency_ms IS NOT NULL))
                   AS avg_processing_latency_ms,
               max(processing_latency_ms) AS max_processing_latency_ms
           FROM fazle_conversation_shadow_comparisons
           WHERE business_domain = $1""",
        business_domain,
    )
    metrics = dict(row) if row else {}

    thread_row = await fetch_one(
        """SELECT
               count(*) FILTER (WHERE state IN ('open','partial')) AS incomplete_threads,
               count(*) FILTER (WHERE state = 'conflicted') AS conflicted_threads,
               count(*) FILTER (
                   WHERE state = 'expired'
                     AND NOT EXISTS (
                         SELECT 1 FROM fazle_conversation_shadow_comparisons c
                         WHERE c.thread_id = fazle_conversation_threads.id AND c.would_create_draft
                     )
               ) AS expired_incomplete_threads
           FROM fazle_conversation_threads
           WHERE business_domain = $1""",
        business_domain,
    )
    metrics.update(dict(thread_row) if thread_row else {})
    return metrics


async def expire_stale_threads(actor: str = "scheduler") -> dict:
    """Flips overdue open/partial/conflicted threads to 'expired' -- does
    NOT hard-delete (Part 4.5: "must preserve minimal audit evidence"),
    unlike the unrelated escort-draft TTL sweep
    (modules.escort_roster.db.expire_stale_drafts(), which does delete --
    different retention requirement, not a pattern to copy here). Never
    touches any business table -- expiry here means "stop trying to
    complete this conversation," nothing else."""
    rows = await fetch_all(
        """UPDATE fazle_conversation_threads
           SET state = 'expired', last_system_action = 'auto_expired', updated_at = NOW()
           WHERE state = ANY($1) AND expires_at < NOW()
           RETURNING id, business_domain, bridge_id, counterparty_phone""",
        list(OPEN_STATES),
    )
    if rows:
        log.info("[conversation_memory] expired %d stale thread(s), actor=%s", len(rows), actor)
    return {"expired_count": len(rows), "expired_ids": [r["id"] for r in rows]}
