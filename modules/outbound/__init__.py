"""
Fazle Core — Persistent Outbound Queue (Batch 15.1 + 15.2)

Tables: fazle_outbound_queue
- enqueue() with idempotency_key dedup
- sweep_once() polls due rows, sends via bridge, retries with exp backoff, → DLQ at max_attempts
- start_background_worker() lifespan task
- Circuit breaker integration: opening enqueues a one-per-minute alert to admin
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional

from app.database import execute, fetch_one, fetch_all, fetch_val, get_pool
from app.error_log import record_error
from modules.outbound_authorization import (
    QueueRevalidationResult,
    QueueAuthorizationSnapshotRecord,
    issue_transport_authorization,
    is_transport_authorized,
    revalidate_queued_outbound,
    snapshot_from_queue_row,
)
from modules.identity_role_authorization import AUTHORIZATION_LOOKUP_FAILED, AuthorizationDecision

log = logging.getLogger("fazle.outbound")

_worker_task: Optional[asyncio.Task] = None


def _outbound_enabled() -> bool:
    return os.getenv("OUTBOUND_ENABLED", "false").lower() in ("1", "true", "yes")


def _admin_number() -> Optional[str]:
    raw = os.getenv("ADMIN_NUMBERS", "").strip()
    if not raw:
        return None
    return raw.split(",")[0].strip() or None


_META_CHANNELS = {"meta", "meta_whatsapp", "messenger", "instagram", "facebook_comment"}


def _meta_outbound_log(event: str, *, source_bridge: str, **fields: Any) -> None:
    """Meta-channel-scoped observability event (Owner directive,
    2026-08-27, Section 1) -- channel-gated so bridge1/bridge2/bridge3
    sends (the vast majority of this generic worker's traffic) stay
    completely unaffected; only messenger/instagram/meta* rows emit
    anything here. Matches app.main._meta_log_event's format
    ("[META_EVENT] event=... channel=...") so both are grep-able the same
    way. Never logs recipient/body/tokens -- queue id, status, and error
    class only."""
    if source_bridge not in _META_CHANNELS:
        return
    parts = [f"event={event}", f"channel={source_bridge}"]
    parts += [f"{k}={v}" for k, v in fields.items() if v not in (None, "")]
    log.info("[META_EVENT] " + " ".join(parts))


async def enqueue(
    recipient: str,
    body: str,
    *,
    source_bridge: str = "bridge2",
    fallback_channel: Optional[str] = None,
    purpose: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    authorization: Optional[QueueAuthorizationSnapshotRecord] = None,
) -> Optional[int]:
    """Insert into queue. Returns id, or None if dedup'd by idempotency_key.

    BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02: if `source_bridge` is in a
    non-sending pairing state, the enqueue is REFUSED here (returns None,
    logged) — the queue-dispatch hold in sweep_once() is the second net for
    rows enqueued by any path that bypasses this check.
    """
    import json
    import hashlib
    from modules.message_router import inbound_reference, record_inbound_action
    reference = inbound_reference()
    meta = {**reference, **(meta or {})}
    if reference and not idempotency_key:
        action = hashlib.sha256(
            json.dumps([recipient, source_bridge, purpose, body], ensure_ascii=False).encode()
        ).hexdigest()
        idempotency_key = f"inbound:{reference['core_message_id']}:{action}"

    _hs_bridge = source_bridge or ""
    if _hs_bridge:
        _hheld, _hstate = await _pairing_held(_hs_bridge)
        if _hheld:
            record_inbound_action("outbound", outcome="refused", reason="pairing_hold",
                                  source_bridge=source_bridge, purpose=purpose)
            log.warning(
                "[OUTBOUND_ENQUEUE] REFUSED bridge=%s pairing_state=%s recipient=%s purpose=%s "
                "key=%s — bridge is in a non-sending pairing state",
                _hs_bridge, _hstate, recipient, purpose, idempotency_key,
            )
            return None
    # Phase 7 (2026-08-26): Generic no-duplicate-answer invariant.
    # Read-only check for a substantially similar recent reply to the same
    # recipient + same purpose within the dedup window. Emits a structured
    # warning log so the caller (Phase 5/6 informational handlers, Hermes
    # dispatch) can include "you already said this" in the LLM prompt on
    # the next iteration. Does NOT block the current send — the existing
    # idempotency_key dedup is the authoritative gate. Falls closed to
    # "no recent reply" on any DB error.
    try:
        if purpose and await _was_recently_replied(recipient, purpose, body):
            log.warning(
                "[OUTBOUND_PHASE7] similar recent reply recipient=%s purpose=%s "
                "dedup_window_minutes=%s (informational, NOT blocking send)",
                recipient, purpose, _PHASE7_DEDUP_MINUTES,
            )
    except Exception as _phase7_exc:  # noqa: BLE001
        log.debug("[OUTBOUND_PHASE7] similar-reply check skipped (non-fatal): %s", _phase7_exc)

    meta_json = json.dumps(meta or {})
    try:
        if authorization is None:
            # Keep legacy callers compatible while migration 088 is pending.
            row = await fetch_one(
                """INSERT INTO fazle_outbound_queue
                  (recipient, body, source_bridge, fallback_channel, purpose, idempotency_key, meta_json)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
               ON CONFLICT (idempotency_key) DO NOTHING
                   RETURNING id""",
                recipient, body, source_bridge, fallback_channel, purpose, idempotency_key, meta_json,
            )
        else:
            snapshot = authorization.db_values()
            row = await fetch_one(
                """INSERT INTO fazle_outbound_queue
                  (recipient, body, source_bridge, fallback_channel, purpose, idempotency_key, meta_json,
                   authorization_identity_id, authorization_version, authorization_conversation_key,
                   authorization_domain, authorization_scope_type, authorization_scope_key,
                   authorization_role_names, authorization_captured_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb,
                       $8, $9, $10, $11, $12, $13, $14::jsonb, $15)
               ON CONFLICT (idempotency_key) DO NOTHING
                   RETURNING id""",
                recipient, body, source_bridge, fallback_channel, purpose, idempotency_key, meta_json,
                *snapshot,
            )
    except Exception as exc:
        record_inbound_action("outbound", outcome="enqueue_failed", error_type=type(exc).__name__,
                              purpose=purpose, source_bridge=source_bridge)
        raise
    qid = row["id"] if row else None
    if reference or meta.get("core_message_id"):
        linked_id = qid
        if linked_id is None and idempotency_key:
            existing = await fetch_one(
                "SELECT id FROM fazle_outbound_queue WHERE idempotency_key=$1",
                idempotency_key,
            )
            linked_id = existing["id"] if existing else None
        record_inbound_action("outbound", queue_id=linked_id,
                              outcome="queued" if qid else "duplicate_or_refused",
                              purpose=purpose, source_bridge=source_bridge)
    if qid:
        log.info(f"[OUTBOUND_ENQUEUE] id={qid} recipient={recipient} bridge={source_bridge} purpose={purpose} key={idempotency_key} body={body[:60]!r}")
    else:
        log.info(f"[OUTBOUND_ENQUEUE] dedup_skip recipient={recipient} key={idempotency_key}")
    return qid


# Phase 7 (2026-08-26): reply-invariant helper. Window is intentionally
# generous so genuinely different replies aren't flagged; tighten only if
# the structured-log noise becomes operationally annoying.
_PHASE7_DEDUP_MINUTES = 10


def _normalize_reply_for_compare(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation. Used only for
    similarity comparison — never reaches candidate-facing text."""
    import re as _re
    s = (text or "").strip().lower()
    s = _re.sub(r"\s+", " ", s)
    s = _re.sub(r"[।.,?!:;]", "", s)
    return s.strip()


async def _was_recently_replied(recipient: str, purpose: str, candidate_body: str) -> bool:
    """Return True iff a substantially similar outbound reply was queued
    to the same recipient + same purpose within the dedup window.

    Read-only: queries fazle_outbound_queue + fazle_draft_replies
    history. Never blocks; caller decides whether to act on the warning.
    Returns False (no recent reply) on any error.
    """
    try:
        # Look at the last few outbound replies to this recipient + purpose
        rows = await fetch_all(
            """SELECT body, created_at FROM fazle_outbound_queue
               WHERE recipient = $1 AND purpose = $2
                 AND created_at > NOW() - make_interval(mins => $3)
               ORDER BY created_at DESC LIMIT 5""",
            recipient, purpose, _PHASE7_DEDUP_MINUTES,
        )
        norm_candidate = _normalize_reply_for_compare(candidate_body)
        if not norm_candidate:
            return False
        for r in rows:
            if _normalize_reply_for_compare(r.get("body") or "") == norm_candidate:
                return True
        return False
    except Exception:  # noqa: BLE001 — fail closed to False
        return False


async def pending_count() -> int:
    return int(await fetch_val(
        "SELECT COUNT(*) FROM fazle_outbound_queue WHERE status IN ('pending','failed')"
    ) or 0)


def delivery_details(row: dict) -> dict:
    """Queue 'sent' means transport acceptance, not recipient delivery."""
    import json
    meta = row.get("meta_json") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    receipt = meta.get("delivery_receipt") or {}
    delivered = receipt.get("status") in ("delivered", "read")
    status = row.get("status")
    failed = not delivered and (receipt.get("status") == "failed" or status == "dlq" or (
        status == "failed" and (row.get("attempts") or 0) >= (row.get("max_attempts") or 3)
    ))
    return {
        "send_accepted": status == "sent" or receipt.get("status") == "sent" or delivered,
        "delivered": delivered,
        "delivery_outcome": receipt.get("status") or {
            "pending": "queued", "sending": "send_attempted", "sent": "unconfirmed",
            "failed": "failed", "dlq": "dead_lettered",
        }.get(status, "unknown"),
        "in_flight": not delivered and not failed and status in ("pending", "sending", "failed"),
        "definitively_failed": failed,
    }


async def record_meta_receipt(receipt: dict) -> None:
    """Use one exact provider row; reject zero or ambiguous matches."""
    import json
    ranks = {"sent": 1, "failed": 2, "delivered": 3, "read": 4}
    state = receipt.get("status")
    if not receipt.get("id") or state not in ranks:
        return
    value = {"status": state, "rank": ranks[state],
             "external_id": receipt["id"], "timestamp": receipt.get("timestamp")}

    matches = await fetch_all(
        """SELECT id FROM fazle_outbound_queue
              WHERE source_bridge IN ('meta','meta_whatsapp') AND external_id=$1
              ORDER BY id""",
        receipt["id"],
    )
    if len(matches) != 1:
        if len(matches) > 1:
            log.warning("[META_EVENT] receipt_ambiguous external_id=%s matches=%d; ignored",
                        receipt["id"], len(matches))
        return
    await execute(
        """UPDATE fazle_outbound_queue
           SET meta_json=COALESCE(meta_json, '{}'::jsonb) ||
               jsonb_build_object('delivery_receipt', $2::jsonb)
           WHERE id=$1
             AND COALESCE((meta_json->'delivery_receipt'->>'rank')::int, 0) < $3""",
        matches[0]["id"], json.dumps(value), ranks[state],
    )


async def attach_message_provenance(rows: list[dict]) -> list[dict]:
    """Read existing one-to-many draft/queue relations without recipient guesses."""
    import json
    result = [dict(row) for row in rows]
    for row in result:
        if isinstance(row.get("provenance"), str):
            row["provenance"] = json.loads(row["provenance"])
    ids = [str(r.get("message_id", r.get("id"))) for r in result if r.get("provenance")]
    actions = [a for r in result for a in (r.get("provenance") or {}).get("actions", [])]
    draft_ids = [a["draft_id"] for a in actions if a.get("draft_id")]
    queue_ids = [a["queue_id"] for a in actions if a.get("queue_id")]
    drafts = queues = []
    if ids:
        drafts = await fetch_all(
            """SELECT id, status, approved_at, sent_at, meta->>'core_message_id' AS inbound_id
               FROM fazle_draft_replies WHERE meta->>'core_message_id'=ANY($1::text[])
                  OR id=ANY($2::bigint[])""", ids, draft_ids,
        )
        queues = await fetch_all(
            """SELECT id, status, purpose, source_bridge, attempts, max_attempts,
                      last_error, external_id, sent_at, meta_json,
                      meta_json->>'core_message_id' AS inbound_id
               FROM fazle_outbound_queue WHERE meta_json->>'core_message_id'=ANY($1::text[])
                  OR id=ANY($2::bigint[])""", ids, queue_ids,
        )
    for row in result:
        provenance = row.get("provenance")
        if isinstance(provenance, str):
            provenance = json.loads(provenance)
        mid = str(row.get("message_id", row.get("id")))
        row["provenance"] = provenance or {"processing_outcome": "unknown", "actions": []}
        own_actions = row["provenance"].get("actions", [])
        own_drafts = {a["draft_id"] for a in own_actions if a.get("draft_id")}
        own_queues = {a["queue_id"] for a in own_actions if a.get("queue_id")}
        row["drafts"] = [dict(d) for d in drafts if d["inbound_id"] == mid or d["id"] in own_drafts]
        row["outbound"] = [{**{k: v for k, v in q.items() if k != "meta_json"}, **delivery_details(q)}
                           for q in queues if q["inbound_id"] == mid or q["id"] in own_queues]
    return result


async def dlq_count() -> int:
    return int(await fetch_val(
        "SELECT COUNT(*) FROM fazle_outbound_queue WHERE status = 'dlq'"
    ) or 0)


async def actionable_dlq_count() -> int:
    """Recent business DLQ only; terminal alert/history rows are informational."""
    return int(await fetch_val(
        """SELECT COUNT(*) FROM fazle_outbound_queue
           WHERE status='dlq'
             AND created_at >= NOW() - INTERVAL '24 hours'
             AND COALESCE(purpose, '') NOT IN ('dlq-alert', 'health-summary', 'circuit-alert')"""
    ) or 0)


_ALERT_PIPELINE_PURPOSES = ("dlq-alert", "health-summary", "circuit-alert")


async def list_dlq_entries(limit: int = 20) -> list[dict]:
    """Real dead-lettered outbound WhatsApp sends -- the same table
    dlq_count()/actionable_dlq_count() above already summarize, now with
    actual rows for diagnosis (2026-08-20 fix: fazle-mcp's get_dlq_status
    has called GET /api/outbound/dead-letters since 2026-08-15 expecting
    exactly this, but no fazle-core route backed it -- 404 every time).
    `actionable` flags the same "recent, not an alert-pipeline artifact"
    distinction actionable_dlq_count() already uses, per-row, so a caller
    can tell a real stuck business message apart from the alert pipeline's
    own historical noise without re-deriving the filter itself."""
    rows = await fetch_all(
        """SELECT id, recipient, purpose, source_bridge, attempts, max_attempts,
                  last_error, created_at, updated_at,
                  (created_at >= NOW() - INTERVAL '24 hours'
                   AND COALESCE(purpose, '') NOT IN ($1, $2, $3)) AS actionable
           FROM fazle_outbound_queue
           WHERE status = 'dlq'
           ORDER BY updated_at DESC
           LIMIT $4""",
        *_ALERT_PIPELINE_PURPOSES, limit,
    )
    return [dict(r) for r in rows]


def _authorization_context(row: dict):
    try:
        snapshot = snapshot_from_queue_row(row)
    except Exception:
        return None, None, "global", None
    if snapshot is None:
        return None, None, "global", None
    return snapshot.conversation_key, snapshot.domain, snapshot.scope_type, snapshot.scope_key


def _purpose_authorized(row: dict) -> bool:
    meta = row.get("meta_json") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            return False
    return isinstance(meta, dict) and meta.get("purpose_authorized") is True


async def _authorize_claimed_row(conn, row: dict, *, channel: str):
    conversation_key, domain, scope_type, scope_key = _authorization_context(row)
    try:
        return await revalidate_queued_outbound(
            conn, row["id"], conversation_key=conversation_key, domain=domain,
            scope_type=scope_type, scope_key=scope_key, channel_identifier=channel,
            global_outbound_enabled=True, purpose_authorized=_purpose_authorized(row),
            claimed=True,
        )
    except Exception as exc:
        log.warning("[OUTBOUND_AUTH_LOOKUP_FAILED] queue_id=%s error_type=%s",
                    row["id"], type(exc).__name__)
        decision = AuthorizationDecision(
            allowed=False, reason_code=AUTHORIZATION_LOOKUP_FAILED,
            authorization_version=None, manual_review_required=True,
            restriction_category="authorization",
        )
        return QueueRevalidationResult(row["id"], row.get("status"), decision, None, False)


async def _record_authorization_review(conn, row: dict, result) -> None:
    decision = result.decision
    reason = f"authorization_denied:{decision.reason_code}"
    await conn.execute(
        """UPDATE fazle_outbound_queue
              SET status='failed', next_retry_at=NOW() + INTERVAL '1 day',
                  last_error=$2, locked_at=NULL, updated_at=NOW(),
                  meta_json=COALESCE(meta_json, '{}'::jsonb) ||
                      jsonb_build_object('authorization_review', jsonb_build_object(
                          'reason_code', $3::text,
                          'restriction_category', $4::text,
                          'manual_review_required', $5::boolean,
                          'authorization_version', $6::bigint
                      ))
            WHERE id=$1""",
        row["id"], reason, decision.reason_code, decision.restriction_category,
        decision.manual_review_required, decision.authorization_version,
    )
    log.warning("[OUTBOUND_AUTH_DENIED] queue_id=%s reason=%s category=%s",
                row["id"], decision.reason_code, decision.restriction_category)


async def _send_with_channel(source_bridge: str, recipient: str, body: str, *, authorization=None) -> str:
    if not is_transport_authorized(authorization):
        raise PermissionError("transport authorization required")
    """Send through a supported channel and return an external delivery id."""
    if source_bridge in {"meta", "meta_whatsapp", "messenger", "instagram", "facebook_comment"}:
        from modules.social_auto_reply.send_queue import (
            _send_comment, _send_instagram, _send_messenger, _send_meta_whatsapp,
        )
        if source_bridge in {"meta", "meta_whatsapp"}:
            return await _send_meta_whatsapp(recipient, body, authorization=authorization)
        if source_bridge == "messenger":
            return await _send_messenger(recipient, body, authorization=authorization)
        if source_bridge == "instagram":
            return await _send_instagram(recipient, body, authorization=authorization)
        return await _send_comment(recipient, body, authorization=authorization)

    from app.bridge import get_bridge1, get_bridge2, get_bridge3, BridgeSendError
    if source_bridge == "bridge2":
        client = get_bridge2()
    elif source_bridge == "bridge3":
        client = get_bridge3()
    else:
        client = get_bridge1()

    if not client.breaker.allow():
        log.warning(f"[OUTBOUND_SEND_FAIL] circuit_open bridge={source_bridge} recipient={recipient}")
        raise BridgeSendError(f"circuit_open:{source_bridge}")

    log.info(f"[OUTBOUND_SEND_START] bridge={source_bridge} recipient={recipient} body={body[:60]!r}")
    try:
        await client.send_strict(recipient, body, authorization=authorization)
        client.breaker.record_success()
        log.info(f"[OUTBOUND_SEND_SUCCESS] bridge={source_bridge} recipient={recipient}")
        return f"{source_bridge}:{recipient}"
    except BridgeSendError as exc:
        just_opened = client.breaker.record_failure()
        log.error(f"[OUTBOUND_SEND_FAIL] bridge={source_bridge} recipient={recipient} error={exc}")
        if just_opened:
            await _alert_circuit_open(source_bridge)
        raise


async def _alert_circuit_open(source_bridge: str) -> None:
    """Enqueue a one-per-minute alert to admin when a breaker opens."""
    admin = _admin_number()
    if not admin:
        return
    from datetime import datetime
    minute = datetime.utcnow().strftime("%Y%m%d%H%M")
    key = f"circuit-open-{source_bridge}-{minute}"
    other = "bridge1" if source_bridge == "bridge2" else "bridge2"
    body = f"⚠️ ALERT: {source_bridge} circuit OPEN. Outbound paused 60s. Investigate."
    try:
        await enqueue(admin, body,
                      source_bridge=other,  # send via the other bridge
                      purpose="circuit-alert",
                      idempotency_key=key)
    except Exception as e:
        log.warning(f"[outbound] failed to enqueue circuit alert: {e}")


async def _pairing_held(bridge: str) -> tuple[bool, str]:
    """Thin wrapper over modules.bridge_poller._bridge_outbound_held (lazy import
    — bridge_poller imports this module, never the reverse). Fail-closed:
    ANYTHING that goes wrong resolving the state holds a fail-closed bridge.
    BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02: the queue dispatcher must block
    ALREADY-queued Bridge3 rows while the bridge is in a non-sending pairing
    state, not just prevent new enqueues."""
    try:
        from modules.bridge_poller import _bridge_outbound_held
        return await _bridge_outbound_held(bridge)
    except Exception as exc:  # noqa: BLE001
        if bridge == "bridge3":
            log.warning("[OUTBOUND_HOLD] bridge3 hold-check failed (%s) — holding fail-closed", exc)
            return (True, "(hold-check-failed)")
        return (False, "")


async def sweep_once(limit: int = 20) -> dict:
    """Pick up to N due rows, send them, update status. Returns counts."""
    if not _outbound_enabled():
        return {"picked": 0, "sent": 0, "failed": 0, "dlq": 0, "paused": True}
    pool = get_pool()
    sent = failed = dlq = held = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, recipient, body, source_bridge, fallback_channel, attempts, max_attempts,
                          purpose, meta_json, idempotency_key,
                          authorization_identity_id, authorization_version,
                          authorization_conversation_key, authorization_domain,
                          authorization_scope_type, authorization_scope_key,
                          authorization_role_names, authorization_captured_at
                     FROM fazle_outbound_queue
                    WHERE status IN ('pending','failed')
                      AND next_retry_at <= NOW()
                    ORDER BY next_retry_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1""",
                limit,
            )
            ids = [r["id"] for r in rows]
            if ids:
                await conn.execute(
                    "UPDATE fazle_outbound_queue SET status='sending', locked_at=NOW(), updated_at=NOW() "
                    "WHERE id = ANY($1::bigint[])",
                    ids,
                )

        # Process outside the lock transaction; each result is its own update
        for r in rows:
            rid = r["id"]
            attempts = r["attempts"] + 1

            # Pairing-state hold (BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02):
            # a bridge in a non-sending pairing state must not have its queued
            # work dispatched. Leave the row PENDING (no attempt consumed, no
            # DLQ) so it dispatches normally once the owner releases the bridge.
            # The fallback route is guarded separately below.
            _src = r["source_bridge"] or ""
            _held_src, _hs = await _pairing_held(_src) if _src else (False, "")
            if _held_src:
                await pool.execute(
                    """UPDATE fazle_outbound_queue
                          SET status='pending', locked_at=NULL, updated_at=NOW(),
                              next_retry_at = NOW() + INTERVAL '5 minutes',
                              last_error=$2
                        WHERE id=$1""",
                    rid, f"bridge_pairing_hold:{_src}:{_hs}",
                )
                held += 1
                log.info(
                    "[OUTBOUND_HOLD] queue id=%s bridge=%s held (pairing_state=%s) — "
                    "left pending, not dispatched",
                    rid, _src, _hs,
                )
                continue

            auth_result = await _authorize_claimed_row(conn, r, channel=_src)
            if not auth_result.decision.allowed:
                await _record_authorization_review(conn, r, auth_result)
                held += 1
                continue
            auth_token = issue_transport_authorization(auth_result.decision)

            try:
                try:
                    external_id = await _send_with_channel(
                        r["source_bridge"], r["recipient"], r["body"],
                        authorization=auth_token,
                    )
                except Exception:
                    fallback = r.get("fallback_channel")
                    if not fallback:
                        raise
                    _held_fb, _hf = await _pairing_held(fallback)
                    if _held_fb:
                        # never fail a message over to a bridge that is in a
                        # non-sending pairing state — re-raise so the row goes
                        # through normal failed/backoff handling instead.
                        log.warning(
                            "[OUTBOUND_HOLD] id=%s fallback=%s is pairing-held (%s) — "
                            "not failing over", rid, fallback, _hf,
                        )
                        raise
                    log.warning(
                        "[OUTBOUND_FAILOVER] id=%s from=%s to=%s",
                        rid, r["source_bridge"], fallback,
                    )
                    fallback_result = await _authorize_claimed_row(conn, r, channel=fallback)
                    if not fallback_result.decision.allowed:
                        await _record_authorization_review(conn, r, fallback_result)
                        held += 1
                        continue
                    fallback_token = issue_transport_authorization(fallback_result.decision)
                    external_id = await _send_with_channel(
                        fallback, r["recipient"], r["body"],
                        authorization=fallback_token,
                    )
                await conn.execute(
                    """UPDATE fazle_outbound_queue
                          SET status='sent', attempts=$2, sent_at=NOW(), last_error=NULL,
                              external_id=$3, locked_at=NULL, updated_at=NOW()
                        WHERE id=$1""",
                    rid, attempts, external_id,
                )
                sent += 1
                # A draft reaches legacy 'sent' only after transport acceptance.
                # Failure to update its projection must never resend the message.
                try:
                    import json
                    queue_meta = r.get("meta_json") or {}
                    if isinstance(queue_meta, str):
                        queue_meta = json.loads(queue_meta)
                    if queue_meta.get("draft_id"):
                        await conn.execute(
                            """UPDATE fazle_draft_replies SET status='sent', sent_at=NOW(),
                               meta=COALESCE(meta, '{}'::jsonb) ||
                                 jsonb_build_object('send_accepted', true, 'outbound_queue_id', $2::bigint)
                               WHERE id=$1 AND status IN ('approved','edited')""",
                            queue_meta["draft_id"], rid,
                        )
                except Exception as projection_error:
                    log.error("Accepted queue id=%s; draft projection failed: %s", rid, projection_error)
                _meta_outbound_log("meta_outbound_sent", source_bridge=r["source_bridge"], queue_id=rid, attempts=attempts)

                # Owner-authoritative accounting boundary (2026-08-30): a
                # successful WhatsApp delivery of an authorized owner→accountant
                # payment-relay message IS the completed payment event. Book it
                # to FPE here, at the send-success boundary — do NOT wait for an
                # eventual bridge SQLite self-echo. Never lets an accounting
                # error undo/retry the (already successful) send; the reconcile
                # pass in the FPE accounting worker retries any that slip
                # through. Only fires for purpose == "owner-accountant-payment-relay".
                if (r.get("purpose") or "") == "owner-accountant-payment-relay":
                    try:
                        from modules.payment_relay_accounting import on_relay_send_success
                        await on_relay_send_success(dict(r), external_id)
                    except Exception as _acct_exc:  # noqa: BLE001
                        log.error(
                            "[OUTBOUND_RELAY_ACCT] queue id=%s send OK but accounting hook "
                            "raised (reconcile will retry): %s", rid, _acct_exc,
                        )
            except Exception as e:
                err = str(e)[:500]
                if attempts >= r["max_attempts"]:
                    await conn.execute(
                        """UPDATE fazle_outbound_queue
                              SET status='dlq', attempts=$2, last_error=$3,
                                  locked_at=NULL, updated_at=NOW()
                            WHERE id=$1""",
                        rid, attempts, err,
                    )
                    dlq += 1
                    await record_error("outbound.dlq", e)
                    _meta_outbound_log("meta_outbound_dlq", source_bridge=r["source_bridge"], queue_id=rid, attempts=attempts, error_class=type(e).__name__)
                else:
                    # Exponential backoff: 1m, 2m, 4m, ...
                    await conn.execute(
                        """UPDATE fazle_outbound_queue
                              SET status='failed', attempts=$2::integer, last_error=$3,
                                  next_retry_at = NOW() + (
                                      INTERVAL '1 minute' * power(2::numeric, $2::integer)
                                  )
                                  , locked_at=NULL, updated_at=NOW()
                            WHERE id=$1""",
                        rid, attempts, err,
                    )
                    failed += 1
                    _meta_outbound_log("meta_outbound_failed", source_bridge=r["source_bridge"], queue_id=rid, attempts=attempts, error_class=type(e).__name__)
    return {"picked": len(rows), "sent": sent, "failed": failed, "dlq": dlq, "held": held}


async def _worker_loop(interval_seconds: int) -> None:
    log.info(f"[outbound] worker started (interval={interval_seconds}s, enabled={_outbound_enabled()})")
    while True:
        try:
            # Never replay an old `sending` row automatically. A prior process
            # may have delivered it before losing its acknowledgement.
            await sweep_once(limit=20)
        except asyncio.CancelledError:
            log.info("[outbound] worker cancelled")
            raise
        except Exception as e:
            log.exception(f"[outbound] sweep error: {e}")
            await record_error("outbound.sweep", e)
        await asyncio.sleep(interval_seconds)


def start_background_worker(interval_seconds: int = 10) -> asyncio.Task:
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    interval = int(os.getenv("OUTBOUND_SWEEP_INTERVAL_S", str(interval_seconds)))
    _worker_task = asyncio.create_task(_worker_loop(interval), name="outbound-worker")
    return _worker_task


async def stop_background_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except (asyncio.CancelledError, Exception):
        pass
    _worker_task = None
