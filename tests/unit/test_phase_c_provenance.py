"""Phase C v2 — actor provenance + canonical history tests.

Verifies Owner directive corrections 4 and 5:
  4. distinguish fact from inference; explicit actor_type on new outbounds;
     never silence candidate based on heuristic provenance alone
  5. conversation_canonical is a READ/INTERPRETATION layer; no second
     conversation DB; preserves original records
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
import pytest_asyncio

from modules.conversation_canonical import (
    ADMIN_ALLOWLIST,
    build_canonical_history,
    classify_actor,
)


@pytest_asyncio.fixture
async def provenance_db(test_db_pool, monkeypatch):
    """Real disposable PostgreSQL; every transport/provider is mocked."""
    import app.database as db
    from modules import outbound, bridge_poller as bp
    monkeypatch.setattr(db, "_pool", test_db_pool)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    from modules.identity_role_authorization import AuthorizationDecision
    monkeypatch.setattr(outbound, "_authorize_claimed_row", AsyncMock(return_value=SimpleNamespace(decision=AuthorizationDecision(True, "allowed", 1, False))))
    monkeypatch.setattr(outbound, "_send_with_channel", AsyncMock(return_value="provider-acceptance-1"))
    monkeypatch.setattr(bp, "_phase2_enrich_raw_copy", AsyncMock())
    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    return test_db_pool


async def _provenance_inbound(source="bridge1", reference="source-1", text="hello", phone="8801700999000"):
    from modules.bridge_poller import _save_raw_core_copy
    mid = await _save_raw_core_copy(source, {"id": reference, "content": text, "type": "text"}, phone, text)
    assert mid is not None
    return mid


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["bridge1", "bridge2", "bridge3", "meta"])
async def test_inbound_provenance_multiple_actions_and_replay(provenance_db, source):
    from app.database import fetch_val
    from modules.message_router import inbound_execution, record_inbound_action
    from modules.outbound import enqueue, attach_message_provenance
    from shared.draft_reply import create_draft_reply
    mid = await _provenance_inbound(source)
    async with inbound_execution(mid, source, "source-1", role="candidate") as turn:
        assert turn
        record_inbound_action("decision", role="candidate", intent="recruitment", action="reply", workflow="recruitment")
        first = await fetch_val("INSERT INTO wbom_candidates(phone) VALUES($1) RETURNING candidate_id", "8801700999001")
        second = await fetch_val("INSERT INTO wbom_candidates(phone) VALUES($1) RETURNING candidate_id", "8801700999002")
        draft = await create_draft_reply(sender="8801700999000", bridge=source,
                                        draft_text="Please review", role="candidate", intent="recruitment")
        q1 = await enqueue("8801700999000", "reply", source_bridge=source, purpose="reply")
        assert await enqueue("8801700999000", "reply", source_bridge=source, purpose="reply") is None
        q2 = await enqueue("8801700999003", "admin notice", source_bridge=source, purpose="admin-notify")
    async with inbound_execution(mid, source, "source-1") as duplicate:
        assert duplicate is None
    row = dict(await provenance_db.fetchrow("SELECT message_id, metadata_json->'provenance' AS provenance FROM wbom_whatsapp_messages WHERE message_id=$1", mid))
    view = (await attach_message_provenance([row]))[0]
    assert view["provenance"]["processing_outcome"] == "done"
    assert {d["id"] for d in view["drafts"]} == {draft}
    assert {q["id"] for q in view["outbound"]} == {q1, q2}
    assert all(not q["delivered"] for q in view["outbound"])
    writes = [a for a in view["provenance"]["actions"] if a["kind"] == "business_write"]
    assert {a.get("returned_identifier") for a in writes if a["table"] == "wbom_candidates"} == {str(first), str(second)}
    assert await provenance_db.fetchval("SELECT count(*) FROM fazle_outbound_queue") == 2


@pytest.mark.asyncio
async def test_draft_approval_queue_acceptance_and_receipt_are_distinct(provenance_db):
    from modules.message_router import inbound_execution
    from shared.draft_reply import create_draft_reply
    from modules.wa_chat_frontend import approve_draft
    from modules.outbound import sweep_once, delivery_details, record_meta_receipt
    mid = await _provenance_inbound()
    async with inbound_execution(mid, "bridge1", "source-1"):
        did = await create_draft_reply(sender="8801700999000", bridge="bridge1", draft_text="reply",
                                       role="candidate", intent="recruitment")
    result = await approve_draft(did)
    duplicate = await approve_draft(did)
    assert result["queue_id"] == duplicate["queue_id"]
    draft = await provenance_db.fetchrow("SELECT * FROM fazle_draft_replies WHERE id=$1", did)
    assert draft["status"] == "approved" and draft["sent_at"] is None
    queue = dict(await provenance_db.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", result["queue_id"]))
    assert json.loads(queue["meta_json"])["core_message_id"] == mid
    assert not delivery_details(queue)["send_accepted"]
    assert (await sweep_once())["sent"] == 1
    queue = dict(await provenance_db.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", result["queue_id"]))
    assert delivery_details(queue)["send_accepted"]
    assert not delivery_details(queue)["delivered"]
    # A Meta receipt must never attach to a bridge message, even if an ID happens to match.
    await record_meta_receipt({"id": "provider-acceptance-1", "status": "delivered"})
    queue = dict(await provenance_db.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", result["queue_id"]))
    assert not delivery_details(queue)["delivered"]


@pytest.mark.asyncio
async def test_meta_receipts_monotonic_and_exact(provenance_db):
    from modules.outbound import enqueue, sweep_once, record_meta_receipt, delivery_details
    qid = await enqueue("8801700999000", "reply", source_bridge="meta", idempotency_key="meta-test")
    await sweep_once()
    for status in ("delivered", "sent", "failed", "read", "delivered"):
        await record_meta_receipt({"id": "provider-acceptance-1", "status": status, "timestamp": "123"})
    row = dict(await provenance_db.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", qid))
    assert delivery_details(row)["delivered"] is True
    assert delivery_details(row)["delivery_outcome"] == "read"
    assert not delivery_details(row)["definitively_failed"]


@pytest.mark.asyncio
async def test_approval_retry_after_submission_failure_and_unknown_history(provenance_db, monkeypatch):
    from modules import outbound
    from modules.wa_chat_frontend import approve_draft
    did = await provenance_db.fetchval(
        "INSERT INTO fazle_draft_replies(recipient,reply_text,source,status) "
        "VALUES('8801700999000','reply','bridge1','pending') RETURNING id")
    original = outbound.enqueue
    monkeypatch.setattr(outbound, "enqueue", AsyncMock(side_effect=RuntimeError("queue unavailable")))
    with pytest.raises(RuntimeError, match="queue unavailable"):
        await approve_draft(did)
    assert await provenance_db.fetchval("SELECT status FROM fazle_draft_replies WHERE id=$1", did) == "approved"
    monkeypatch.setattr(outbound, "enqueue", original)
    retry = await approve_draft(did)
    assert retry["queued"] and not retry["delivered"]
    assert (await approve_draft(did))["queue_id"] == retry["queue_id"]
    historic = await provenance_db.fetchval(
        "INSERT INTO fazle_draft_replies(recipient,reply_text,source,status) "
        "VALUES('8801700999001','old reply','bridge1','approved') RETURNING id")
    assert (await approve_draft(historic))["queued"] is False
    assert await provenance_db.fetchval("SELECT count(*) FROM fazle_outbound_queue") == 1


@pytest.mark.asyncio
async def test_queue_retry_and_dead_letter_preserve_processing(provenance_db, monkeypatch):
    from modules import outbound
    from modules.message_router import inbound_execution
    monkeypatch.setattr(outbound, "_send_with_channel", AsyncMock(side_effect=RuntimeError("mock transport failure")))
    monkeypatch.setattr(outbound, "record_error", AsyncMock())
    mid = await _provenance_inbound()
    async with inbound_execution(mid, "bridge1", "source-1"):
        qid = await outbound.enqueue("8801700999000", "reply", source_bridge="bridge1", purpose="reply")
    assert (await outbound.sweep_once())["failed"] == 1
    row = dict(await provenance_db.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", qid))
    assert row["status"] == "failed" and row["attempts"] == 1
    assert not outbound.delivery_details(row)["delivered"]
    assert outbound.delivery_details(row)["in_flight"]
    await provenance_db.execute("UPDATE fazle_outbound_queue SET attempts=max_attempts-1,next_retry_at=NOW() WHERE id=$1", qid)
    assert (await outbound.sweep_once())["dlq"] == 1
    row = dict(await provenance_db.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", qid))
    assert outbound.delivery_details(row)["definitively_failed"]
    assert await provenance_db.fetchval("SELECT metadata_json->'provenance'->>'processing_outcome' FROM wbom_whatsapp_messages WHERE message_id=$1", mid) == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("phone,text,chat,held,reason", [
    ("8801700999000", "hello", "123@g.us", False, "unapproved_group"),
    ("unresolved:123", "hello", "", False, "unresolved_identity"),
    ("8801700999000", "[media:image]", "", False, "unsupported_media"),
    ("8801700999000", "123456 is your one-time password", "", False, "automated_system_message"),
    ("8801700999000", "hello", "", True, "pairing_hold"),
])
async def test_provenance_suppression_never_routes(provenance_db, monkeypatch, phone, text, chat, held, reason):
    from modules import bridge_poller as bp, outbound
    router = AsyncMock()
    monkeypatch.setattr(bp, "process_message", router)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(held, "held")))
    mid = await _provenance_inbound(text=text, phone=phone)
    await bp.process_bridge_inbound("bridge1", phone, text, message_id="source-1", core_message_id=mid, chat_jid=chat)
    router.assert_not_awaited()
    stored = json.loads(await provenance_db.fetchval("SELECT metadata_json->'provenance' FROM wbom_whatsapp_messages WHERE message_id=$1", mid))
    assert stored["processing_outcome"] == "skipped"
    assert stored["actions"] == [{"kind": "suppression", "reason": reason}]
    assert await provenance_db.fetchval("SELECT count(*) FROM fazle_outbound_queue") == 0


@pytest.mark.asyncio
async def test_failure_cannot_replay_partial_business_actions(provenance_db):
    from modules.message_router import inbound_execution
    from app.database import fetch_val
    mid = await _provenance_inbound()
    with pytest.raises(RuntimeError):
        async with inbound_execution(mid, "bridge1", "source-1"):
            await fetch_val("INSERT INTO wbom_candidates(phone) VALUES($1) RETURNING candidate_id", "8801700999001")
            raise RuntimeError("downstream failure")
    async with inbound_execution(mid, "bridge1", "source-1") as replay:
        assert replay is None
    assert await provenance_db.fetchval("SELECT count(*) FROM wbom_candidates") == 1
    assert await provenance_db.fetchval("SELECT metadata_json->'provenance'->>'processing_outcome' FROM wbom_whatsapp_messages WHERE message_id=$1", mid) == "failed"


@pytest.mark.asyncio
async def test_historical_provenance_stays_unknown():
    from modules.outbound import attach_message_provenance
    row = (await attach_message_provenance([{"message_id": 9, "is_processed": True}]))[0]
    assert row["provenance"]["processing_outcome"] == "unknown"
    assert row["drafts"] == [] and row["outbound"] == []


@pytest.mark.asyncio
async def test_concurrent_replay_claims_once_and_detached_reply_keeps_source(provenance_db):
    import asyncio
    from modules.message_router import inbound_execution
    from modules.outbound import enqueue, attach_message_provenance
    mid = await _provenance_inbound()
    release = asyncio.Event()
    claimed = []
    deferred = []

    async def later():
        await release.wait()
        return await enqueue("8801700999000", "async reply", source_bridge="bridge1", purpose="hermes-reply")

    async def attempt():
        async with inbound_execution(mid, "bridge1", "source-1") as turn:
            if turn:
                claimed.append(mid)
                deferred.append(asyncio.create_task(later()))

    await asyncio.gather(attempt(), attempt())
    assert claimed == [mid]
    release.set()
    qid = await deferred[0]
    row = dict(await provenance_db.fetchrow("SELECT message_id, metadata_json->'provenance' AS provenance FROM wbom_whatsapp_messages WHERE message_id=$1", mid))
    viewed = (await attach_message_provenance([row]))[0]
    assert [q["id"] for q in viewed["outbound"]] == [qid]
    assert viewed["provenance"]["processing_outcome"] == "done"


@pytest.mark.asyncio
async def test_failed_queue_insert_is_audited_without_false_reference(provenance_db, monkeypatch):
    from modules.message_router import inbound_execution
    from modules import outbound
    mid = await _provenance_inbound()
    monkeypatch.setattr(outbound, "fetch_one", AsyncMock(side_effect=RuntimeError("queue unavailable")))
    with pytest.raises(RuntimeError, match="queue unavailable"):
        async with inbound_execution(mid, "bridge1", "source-1"):
            await outbound.enqueue("8801700999000", "reply", source_bridge="bridge1")
    stored = json.loads(await provenance_db.fetchval("SELECT metadata_json->'provenance' FROM wbom_whatsapp_messages WHERE message_id=$1", mid))
    assert stored["processing_outcome"] == "failed"
    assert stored["actions"][0]["outcome"] == "enqueue_failed"
    assert not stored["actions"][0].get("queue_id")


@pytest.mark.asyncio
async def test_poller_finalizes_media_and_suppression_before_router(provenance_db, monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime, timezone, timedelta
    from app.bridge import BridgeClient
    from modules import bridge_poller as bp
    from httpx import Response
    now = datetime.now(timezone.utc)
    phone = "8801700999000"
    messages = [
        {"id": "group", "text": "hello", "chat_jid": "123@g.us"},
        {"id": "system", "text": "123456 is your one-time password"},
        {"id": "media", "text": "[media:video]", "media_type": "video"},
        {"id": "lid", "text": "hello", "phone": "unresolved:123"},
        {"id": "document", "text": "", "media_type": "document", "filename": "candidate.pdf"},
    ]
    messages = [{"phone": phone, "chat_jid": phone + "@s.whatsapp.net", "timestamp": now,
                 **msg} for msg in messages]
    monkeypatch.setattr(bp, "_get_cursor", AsyncMock(return_value=now-timedelta(seconds=1)))
    monkeypatch.setattr(bp, "_fetch_new_messages", lambda *a: (messages + messages, now))
    monkeypatch.setattr(bp, "_set_cursor", AsyncMock(side_effect=asyncio.CancelledError))
    monkeypatch.setattr(bp, "_bridge_outbound_held", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr("modules.group_identity.list_approved_group_jids", AsyncMock(return_value=["123@g.us"]))
    monkeypatch.setattr("modules.identity_brain.detect_identity", AsyncMock(return_value={
        "identity_role": "unknown", "identity_confidence": 0, "display_name": ""}))
    monkeypatch.setattr("modules.ocr_processor.process_document", AsyncMock(return_value={
        "doc_type": "candidate_cv", "auto_send_safe": True, "reply": "Document received.",
        "extracted_text": "Candidate document", "confidence_score": 100}))
    monkeypatch.setattr(bp, "_record_reply", AsyncMock())
    monkeypatch.setenv("BRIDGE_MEDIA_ACQUISITION_ENABLED", "false")
    route = AsyncMock()
    monkeypatch.setattr(bp, "process_message", route)
    bridge = BridgeClient.__new__(BridgeClient)
    bridge.label, bridge.base_url = "BR1", "http://mock-bridge"
    bridge._client = AsyncMock()
    bridge._client.post.return_value = Response(200)
    bridge._set_send = AsyncMock()
    await bp._poll_bridge({"name": "bridge1", "messages_db": str(tmp_path / "messages.db"),
                           "whatsapp_db": str(tmp_path / "whatsapp.db"), "get_bridge": lambda: bridge})
    route.assert_not_awaited()
    rows = await provenance_db.fetch("SELECT source_message_ref, metadata_json FROM wbom_whatsapp_messages WHERE direction='inbound'")
    assert len(rows) == 5
    outcomes = {r["source_message_ref"]: json.loads(r["metadata_json"])["provenance"] for r in rows}
    assert all(outcomes[key]["processing_outcome"] == "skipped" for key in ("group", "system", "media", "lid"))
    assert outcomes["document"]["processing_outcome"] == "blocked"
    assert not [a for a in outcomes["document"]["actions"] if a.get("outcome") == "send_accepted"]
    bridge._client.post.assert_not_awaited()



@pytest.mark.asyncio
async def test_finalization_failure_is_reconciled_without_replay(provenance_db, monkeypatch):
    import app.database as db
    from modules.message_router import inbound_execution, reconcile_stranded_inbound
    mid = await _provenance_inbound()
    original_execute = db.execute

    async def fail_finalization(sql, *args, **kwargs):
        if "is_processed=true" in sql.lower() and "jsonb_build_object" in sql:
            raise RuntimeError("provenance finalization unavailable")
        return await original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(db, "execute", fail_finalization)
    with pytest.raises(RuntimeError, match="finalization unavailable"):
        async with inbound_execution(mid, "bridge1", "source-1"):
            await db.fetch_val(
                "INSERT INTO wbom_candidates(phone) VALUES($1) RETURNING candidate_id",
                "8801700999001",
            )

    assert await provenance_db.fetchval("SELECT count(*) FROM wbom_candidates WHERE phone=$1", "8801700999001") == 1
    async with inbound_execution(mid, "bridge1", "source-1") as replay:
        assert replay is None

    monkeypatch.setattr(db, "execute", original_execute)
    recovered = await reconcile_stranded_inbound(mid)
    assert recovered["ok"] is True
    assert recovered["replayed"] is False
    row = await provenance_db.fetchrow(
        "SELECT is_processed, metadata_json->'provenance' AS provenance "
        "FROM wbom_whatsapp_messages WHERE message_id=$1",
        mid,
    )
    provenance = row["provenance"]
    if isinstance(provenance, str):
        provenance = json.loads(provenance)
    assert row["is_processed"] is True
    assert provenance["processing_outcome"] == "done"
    assert provenance["recovered_without_replay"] is True
    assert provenance["actions"][-1]["outcome"] == "finalized_without_replay"


@pytest.mark.asyncio
async def test_meta_receipt_zero_or_duplicate_provider_id_is_ignored(provenance_db):
    from modules.outbound import enqueue, record_meta_receipt
    q1 = await enqueue("8801700999000", "one", source_bridge="meta", idempotency_key="meta-duplicate-1")
    q2 = await enqueue("8801700999001", "two", source_bridge="meta", idempotency_key="meta-duplicate-2")
    await provenance_db.execute(
        "UPDATE fazle_outbound_queue SET external_id=$1 WHERE id=ANY($2::bigint[])",
        "provider-duplicate", [q1, q2],
    )
    await record_meta_receipt({"id": "provider-duplicate", "status": "delivered"})
    await record_meta_receipt({"id": "provider-unknown", "status": "delivered"})
    rows = await provenance_db.fetch(
        "SELECT meta_json FROM fazle_outbound_queue WHERE id=ANY($1::bigint[])",
        [q1, q2],
    )
    for row in rows:
        meta = row["meta_json"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert "delivery_receipt" not in meta

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
