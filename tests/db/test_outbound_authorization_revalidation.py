"""IR-2D1 queue snapshots and non-sending transactional revalidation."""
import json

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from modules.identity_role_authorization import (
    ALLOWED,
    ALLOWED_PROTECTED_PURPOSE,
    AUTHORIZATION_LOOKUP_FAILED,
    CONTEXT_MISMATCH,
    CONVERSATION_HOLD,
    CUSTOMER_AUTO_REPLY,
    DOMAIN_RESTRICTED,
    GLOBAL_MAINTENANCE,
    IDENTITY_HOLD,
    LEGACY_QUEUE_CONTEXT,
    PROTECTED_ADMIN_ALERT,
    SYSTEM_ALERT,
    VERSION_MISMATCH,
    RoleGrant,
)
from modules.identity_role_authorization_adapter import (
    AuthorizationEvidence,
    BridgeAuthorizationState,
)
from modules.outbound_authorization import (
    AuthorizationSnapshotError,
    capture_authorization_snapshot,
    revalidate_queued_outbound,
)


MIGRATION_SQL = (
    Path(__file__).parents[2] / "db" / "migrations" / "088_outbound_authorization_snapshot.sql"
).read_text(encoding="utf-8")


async def _settings(conn, *, recruitment=True, bridge1=None, bridge3=None, phone=None,
                    extra=None):
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fazle_runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    values = {
        "auto_reply.all": "true",
        "auto_reply.recruitment": "true" if recruitment else "false",
    }
    if bridge1 is not None:
        values["bridge1.pairing_state"] = bridge1
    if bridge3 is not None:
        values["bridge3.pairing_state"] = bridge3
    if phone is not None:
        values[f"phone_block.{phone}"] = "true"
    if extra:
        values.update(extra)
    for key, value in values.items():
        await conn.execute(
            """
            INSERT INTO fazle_runtime_settings (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
            """,
            key, value,
        )


async def _subject(conn, number: int, roles=("candidate",), domain="recruitment"):
    phone = f"8801707{number:04d}"
    identity_id = await conn.fetchval(
        """
        INSERT INTO fazle_identity_subjects
            (identity_kind, display_name, verification_state)
        VALUES ('person', $1, 'verified')
        RETURNING identity_id
        """,
        f"Synthetic IR2D1 {number}",
    )
    await conn.execute(
        """
        INSERT INTO wbom_identity_links
            (identity_key, platform, platform_user_id, identity_id,
             identifier_type, verification_state, evidence_source)
        VALUES ($1, 'whatsapp', $2, $3, 'verified_mobile', 'verified', 'ir2d1-test')
        """,
        f"subject:{identity_id}", phone, identity_id,
    )
    grants = []
    version = 1
    for role_name in roles:
        async with conn.transaction():
            version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                INSERT INTO fazle_identity_role_assignments
                    (identity_id, role_name, business_domain,
                     authorization_version_at_change, assignment_reason)
                VALUES ($1, $2, $3, $4, 'ir2d1-test')
                """,
                identity_id, role_name, domain, version,
            )
        grants.append(RoleGrant(role_name, frozenset({domain})))
    return identity_id, phone, version, tuple(grants)


def _evidence(identity_id, phone, version, grants, *, conversation="conversation-a",
              domain="recruitment", purpose=CUSTOMER_AUTO_REPLY,
              purpose_authorized=False, scope_type="global", scope_key=None):
    return AuthorizationEvidence(
        identity={"identity_id": str(identity_id), "verification_state": "verified"},
        channel_identifier="bridge1",
        conversation_key=conversation,
        domain=domain,
        purpose=purpose,
        current_roles=grants,
        authorization_version=version,
        global_outbound_enabled=True,
        bridge=BridgeAuthorizationState(),
        domain_auto_reply_enabled=True,
        purpose_authorized=purpose_authorized,
        scope_type=scope_type,
        scope_key=scope_key,
    )


async def _enqueue(test_db_pool, monkeypatch, phone, evidence, *, key, purpose=None):
    from app import database
    from modules import outbound

    monkeypatch.setattr(database, "_pool", test_db_pool)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    snapshot = capture_authorization_snapshot(evidence)
    queue_id = await outbound.enqueue(
        phone, "synthetic reply", source_bridge="bridge1",
        purpose=purpose or evidence.purpose, idempotency_key=key,
        authorization=snapshot,
    )
    assert queue_id is not None
    return queue_id, snapshot


async def _revalidate(conn, queue_id, *, conversation="conversation-a", domain="recruitment",
                      scope_type="global", scope_key=None, channel="bridge1",
                      global_enabled=True, purpose_authorized=False):
    return await revalidate_queued_outbound(
        conn, queue_id, conversation_key=conversation, domain=domain,
        scope_type=scope_type, scope_key=scope_key,
        channel_identifier=channel, global_outbound_enabled=global_enabled,
        purpose_authorized=purpose_authorized,
    )


@pytest.mark.asyncio
async def test_snapshot_schema_enqueue_and_revalidation_denials(test_db_pool, monkeypatch):
    async with test_db_pool.acquire() as conn:
        await conn.execute(MIGRATION_SQL)
        identity_id, phone, version, grants = await _subject(conn, 1, ("operator", "candidate"))
        await _settings(conn)
        evidence = _evidence(identity_id, phone, version, grants)

        queue_id, snapshot = await _enqueue(test_db_pool, monkeypatch, phone, evidence, key="ir2d1-valid")
        row = await conn.fetchrow(
            """
            SELECT authorization_identity_id, authorization_version,
                   authorization_conversation_key, authorization_domain,
                   authorization_scope_type, authorization_role_names,
                   authorization_captured_at, status
              FROM fazle_outbound_queue WHERE id=$1
            """,
            queue_id,
        )
        assert row["authorization_identity_id"] == identity_id
        assert row["authorization_version"] == version
        assert row["authorization_conversation_key"] == "conversation-a"
        assert row["authorization_domain"] == "recruitment"
        assert row["authorization_scope_type"] == "global"
        assert json.loads(row["authorization_role_names"]) == ["candidate", "operator"]
        assert row["authorization_captured_at"] is not None
        assert row["status"] == "pending"
        assert list(snapshot.role_names) == ["candidate", "operator"]
        valid = await _revalidate(conn, queue_id)
        assert valid.decision.allowed and valid.decision.reason_code == ALLOWED
        assert valid.queue_locked is True

        # Existing callers remain legacy rows; current role assignment cannot
        # make such a row eligible.
        from modules import outbound
        legacy_id = await outbound.enqueue(
            phone, "legacy synthetic reply", source_bridge="bridge1",
            purpose=CUSTOMER_AUTO_REPLY, idempotency_key="ir2d1-legacy",
        )
        legacy = await _revalidate(conn, legacy_id)
        assert legacy.decision.reason_code == LEGACY_QUEUE_CONTEXT
        assert legacy.decision.manual_review_required is True
        assert await conn.fetchval("SELECT status FROM fazle_outbound_queue WHERE id=$1", legacy_id) == "pending"

        with pytest.raises(AuthorizationSnapshotError):
            capture_authorization_snapshot(
                _evidence(identity_id, phone, version, (), conversation="conversation-invalid")
            )

        # The queue snapshot remains bound to the original identity/context.
        other_id, other_phone, other_version, other_grants = await _subject(conn, 2)
        mismatch_id, _ = await _enqueue(
            test_db_pool, monkeypatch, other_phone,
            _evidence(identity_id, phone, version, grants), key="ir2d1-identity-mismatch",
        )
        assert (await _revalidate(conn, mismatch_id)).decision.reason_code == CONTEXT_MISMATCH
        assert (await _revalidate(conn, queue_id, conversation="conversation-other")).decision.reason_code == CONTEXT_MISMATCH
        assert (await _revalidate(conn, queue_id, domain="escort")).decision.reason_code == CONTEXT_MISMATCH
        assert (await _revalidate(conn, queue_id, scope_type="project", scope_key="p1")).decision.reason_code == CONTEXT_MISMATCH

        # A role mutation bumps the subject version in the same transaction;
        # both revocation and newly-added roles stale the old queue snapshot.
        async with conn.transaction():
            new_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                UPDATE fazle_identity_role_assignments
                   SET active=false, revoked_at=NOW(), revoked_by='ir2d1-test',
                       revocation_reason='ir2d1 revoke',
                       authorization_version_at_change=$1
                 WHERE identity_id=$2 AND role_name='candidate'
                """,
                new_version, identity_id,
            )
        revoked = await _revalidate(conn, queue_id)
        assert revoked.decision.reason_code == VERSION_MISMATCH

        async with conn.transaction():
            new_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                INSERT INTO fazle_identity_role_assignments
                    (identity_id, role_name, business_domain,
                     authorization_version_at_change, assignment_reason)
                VALUES ($1, 'new_role', 'recruitment', $2, 'ir2d1-test')
                """,
                identity_id, new_version,
            )
        new_role = await _revalidate(conn, queue_id)
        assert new_role.decision.reason_code == VERSION_MISMATCH

        # Identity hold placement/release also changes the identity version;
        # release does not mutate the queue or silently revive this row.
        async with conn.transaction():
            hold_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            hold_id = await conn.fetchval(
                """
                INSERT INTO fazle_outbound_holds
                    (target_type, identity_id, reason, placed_by)
                VALUES ('identity', $1, 'ir2d1 hold', 'ir2d1-test')
                RETURNING hold_id
                """,
                identity_id,
            )
        held = await _revalidate(conn, queue_id)
        assert held.decision.reason_code == IDENTITY_HOLD
        async with conn.transaction():
            release_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                UPDATE fazle_outbound_holds
                   SET active=false, released_at=NOW(), released_by='ir2d1-test',
                       release_reason='ir2d1 release', state_version=state_version+1
                 WHERE hold_id=$1
                """,
                hold_id,
            )
        released = await _revalidate(conn, queue_id)
        assert release_version != version
        assert released.decision.reason_code == VERSION_MISMATCH
        assert await conn.fetchval("SELECT status FROM fazle_outbound_queue WHERE id=$1", queue_id) == "pending"

        # Conversation holds are narrower and do not change identity version.
        identity_b, phone_b, version_b, grants_b = await _subject(conn, 3)
        await _settings(conn)
        queue_a, _ = await _enqueue(
            test_db_pool, monkeypatch, phone_b,
            _evidence(identity_b, phone_b, version_b, grants_b, conversation="conversation-a"),
            key="ir2d1-conversation-a",
        )
        queue_b, _ = await _enqueue(
            test_db_pool, monkeypatch, phone_b,
            _evidence(identity_b, phone_b, version_b, grants_b, conversation="conversation-b"),
            key="ir2d1-conversation-b",
        )
        await conn.execute(
            """
            INSERT INTO fazle_outbound_holds
                (target_type, conversation_key, reason, placed_by)
            VALUES ('conversation', 'conversation-a', 'ir2d1 conversation hold', 'ir2d1-test')
            """
        )
        held_a = await _revalidate(conn, queue_a, conversation="conversation-a")
        clear_b = await _revalidate(conn, queue_b, conversation="conversation-b")
        assert held_a.decision.reason_code == CONVERSATION_HOLD
        assert clear_b.decision.allowed
        assert await conn.fetchval(
            "SELECT authorization_version FROM fazle_identity_subjects WHERE identity_id=$1", identity_b
        ) == version_b
        await conn.execute(
            """
            UPDATE fazle_outbound_holds
               SET active=false, released_at=NOW(), released_by='ir2d1-test',
                   release_reason='ir2d1 release', state_version=state_version+1
             WHERE target_type='conversation' AND conversation_key='conversation-a'
            """
        )
        released_a = await _revalidate(conn, queue_a, conversation="conversation-a")
        assert released_a.decision.allowed
        assert await conn.fetchval("SELECT status FROM fazle_outbound_queue WHERE id=$1", queue_a) == "pending"

        # Existing phone blocks, global maintenance, bridge restriction, and
        # domain restriction remain independent gates.
        identity_c, phone_c, version_c, grants_c = await _subject(conn, 4)
        await _settings(conn, phone=phone_c)
        q_c, _ = await _enqueue(
            test_db_pool, monkeypatch, phone_c,
            _evidence(identity_c, phone_c, version_c, grants_c), key="ir2d1-gates",
        )
        assert (await _revalidate(conn, q_c)).decision.reason_code == IDENTITY_HOLD
        assert (await _revalidate(conn, q_c, global_enabled=False)).decision.reason_code == GLOBAL_MAINTENANCE
        await _settings(conn, bridge1="HISTORY_SYNC_QUARANTINE")
        assert (await _revalidate(conn, q_c)).decision.reason_code == "bridge_pairing_hold"
        await conn.execute("UPDATE fazle_runtime_settings SET value='false' WHERE key=$1", f"phone_block.{phone_c}")
        await _settings(conn, bridge1="OUTBOUND_OWNER_APPROVED", recruitment=False)
        assert (await _revalidate(conn, q_c)).decision.reason_code == DOMAIN_RESTRICTED

        # Protected purposes still require explicit current authorization and
        # all normal queue/context/hold/channel/version gates.
        identity_d, phone_d, version_d, grants_d = await _subject(conn, 5, ())
        await _settings(conn)
        protected_evidence = _evidence(
            identity_d, phone_d, version_d, grants_d,
            purpose=SYSTEM_ALERT, purpose_authorized=True,
        )
        protected_id, _ = await _enqueue(
            test_db_pool, monkeypatch, phone_d, protected_evidence,
            key="ir2d1-protected", purpose=SYSTEM_ALERT,
        )
        assert (await _revalidate(conn, protected_id, purpose_authorized=True)).decision.reason_code == ALLOWED_PROTECTED_PURPOSE
        assert (await _revalidate(conn, protected_id, purpose_authorized=False)).decision.reason_code != ALLOWED_PROTECTED_PURPOSE
        admin_evidence = _evidence(
            identity_d, phone_d, version_d, grants_d,
            purpose=PROTECTED_ADMIN_ALERT, purpose_authorized=True,
        )
        admin_id, _ = await _enqueue(
            test_db_pool, monkeypatch, phone_d, admin_evidence,
            key="ir2d1-protected-admin", purpose=PROTECTED_ADMIN_ALERT,
        )
        assert (await _revalidate(conn, admin_id, purpose_authorized=True)).decision.reason_code == ALLOWED_PROTECTED_PURPOSE


@pytest.mark.asyncio
async def test_authorization_mutations_and_concurrency_version_are_atomic(test_db_pool):
    async with test_db_pool.acquire() as conn:
        identity_id, phone, version, grants = await _subject(conn, 6)
        await _settings(conn)

        async with conn.transaction():
            assigned_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                INSERT INTO fazle_identity_role_assignments
                    (identity_id, role_name, business_domain,
                     authorization_version_at_change, assignment_reason)
                VALUES ($1, 'second_role', 'recruitment', $2, 'ir2d1 atomic')
                """,
                identity_id, assigned_version,
            )
        assert await conn.fetchval(
            "SELECT authorization_version FROM fazle_identity_subjects WHERE identity_id=$1", identity_id
        ) == assigned_version
        assert await conn.fetchval(
            """
            SELECT authorization_version_at_change
              FROM fazle_identity_role_assignments
             WHERE identity_id=$1 AND role_name='second_role'
            """,
            identity_id,
        ) == assigned_version

        async with conn.transaction():
            revoked_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                UPDATE fazle_identity_role_assignments
                   SET active=false, revoked_at=NOW(), revoked_by='ir2d1 atomic',
                       revocation_reason='atomic revoke', authorization_version_at_change=$1
                 WHERE identity_id=$2 AND role_name='candidate'
                """,
                revoked_version, identity_id,
            )
        assert revoked_version == assigned_version + 1

        async with conn.transaction():
            link_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                UPDATE wbom_identity_links
                   SET verification_state='pending', authorization_version_at_change=$1
                 WHERE identity_id=$2 AND platform_user_id=$3
                """,
                link_version, identity_id, phone,
            )
            await conn.execute(
                "UPDATE fazle_identity_subjects SET verification_state='pending' WHERE identity_id=$1",
                identity_id,
            )
        assert await conn.fetchval(
            "SELECT authorization_version FROM fazle_identity_subjects WHERE identity_id=$1", identity_id
        ) == link_version

        async with conn.transaction():
            hold_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                INSERT INTO fazle_outbound_holds
                    (target_type, identity_id, reason, placed_by)
                VALUES ('identity', $1, 'atomic hold', 'ir2d1 atomic')
                """,
                identity_id,
            )
        async with conn.transaction():
            release_version = await conn.fetchval(
                "SELECT fazle_bump_identity_authorization_version($1)", identity_id
            )
            await conn.execute(
                """
                UPDATE fazle_outbound_holds
                   SET active=false, released_at=NOW(), released_by='ir2d1 atomic',
                       release_reason='atomic release', state_version=state_version+1
                 WHERE target_type='identity' AND identity_id=$1 AND active
                """,
                identity_id,
            )
        assert release_version == hold_version + 1

        # A conversation hold has its own revision and does not stale another
        # conversation through the identity authorization version.
        before = await conn.fetchval(
            "SELECT authorization_version FROM fazle_identity_subjects WHERE identity_id=$1", identity_id
        )
        hold_id = await conn.fetchval(
            """
            INSERT INTO fazle_outbound_holds
                (target_type, conversation_key, reason, placed_by)
            VALUES ('conversation', 'ir2d1-race-conversation', 'race test', 'ir2d1 atomic')
            RETURNING hold_id
            """
        )
        await conn.execute(
            "UPDATE fazle_outbound_holds SET active=false, released_at=NOW(), state_version=state_version+1 WHERE hold_id=$1",
            hold_id,
        )
        assert await conn.fetchval(
            "SELECT authorization_version FROM fazle_identity_subjects WHERE identity_id=$1", identity_id
        ) == before


@pytest.mark.asyncio
async def test_fallback_revalidation_and_zero_transport_after_denial(test_db_pool, monkeypatch):
    async with test_db_pool.acquire() as conn:
        identity_id, phone, version, grants = await _subject(conn, 7)
        await _settings(conn)
        queue_id, _ = await _enqueue(
            test_db_pool, monkeypatch, phone,
            _evidence(identity_id, phone, version, grants), key="ir2d1-fallback",
        )

        transport = AsyncMock()
        primary = await _revalidate(conn, queue_id, channel="bridge1")
        if primary.transport_allowed:
            await transport("bridge1", phone)
        assert primary.decision.allowed
        assert transport.await_count == 1

        # Bridge3 is independently fail-closed when its pairing setting is
        # absent; a primary authorization cannot be reused for fallback.
        fallback = await _revalidate(conn, queue_id, channel="bridge3")
        if fallback.transport_allowed:
            await transport("bridge3", phone)
        assert fallback.decision.reason_code == "bridge_pairing_hold"
        assert transport.await_count == 1

        # A lookup failure is manual review and cannot reach the mock sender.
        with patch(
            "modules.outbound_authorization.load_current_authorization_evidence",
            new=AsyncMock(side_effect=RuntimeError("synthetic lookup failure")),
        ):
            failed = await _revalidate(conn, queue_id)
        if failed.transport_allowed:
            await transport("bridge1", phone)
        assert failed.decision.reason_code == "authorization_lookup_failed"
        assert failed.decision.manual_review_required is True
        assert transport.await_count == 1

        # Idempotency and retry affinity remain queue concerns; revalidation
        # never changes attempts, status, or the idempotency key.
        row = await conn.fetchrow(
            "SELECT status, attempts, idempotency_key FROM fazle_outbound_queue WHERE id=$1", queue_id
        )
        assert dict(row) == {"status": "pending", "attempts": 0, "idempotency_key": "ir2d1-fallback"}

        await conn.execute("UPDATE fazle_outbound_queue SET status='failed', attempts=1, next_retry_at=NOW()+INTERVAL '1 minute' WHERE id=$1", queue_id)
        deferred = await _revalidate(conn, queue_id)
        assert deferred.decision.reason_code == "queue_not_claimable"
        assert await conn.fetchval("SELECT attempts FROM fazle_outbound_queue WHERE id=$1", queue_id) == 1


def _purpose(domain: str) -> str:
    return f"customer-reply:{domain}"


async def _auto_enqueue(test_db_pool, monkeypatch, phone, *, purpose, channel="bridge1",
                        key="cust-key", meta=None):
    from app import database
    from modules import outbound
    monkeypatch.setattr(database, "_pool", test_db_pool)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    return await outbound.enqueue(
        phone, "synthetic reply", source_bridge=channel,
        purpose=purpose, idempotency_key=key, meta=meta,
    )


@pytest.mark.asyncio
async def test_customer_reply_capture_is_scoped_to_customer_reply_purposes():
    """Routing guard: only 'customer-reply:*' purposes trigger an auto-capture.
    Every other purpose must short-circuit to None without touching the pool,
    so admin / protected / system / legacy sends keep their existing contract."""
    from modules.outbound import _capture_customer_authorization_snapshot
    assert await _capture_customer_authorization_snapshot("x", "bridge1", "system_alert") is None
    assert await _capture_customer_authorization_snapshot("x", "bridge1", "protected_admin_alert") is None
    assert await _capture_customer_authorization_snapshot("x", "bridge1", CUSTOMER_AUTO_REPLY) is None
    assert await _capture_customer_authorization_snapshot("x", "bridge1", None) is None


@pytest.mark.asyncio
async def test_recruitment_authorized_customer_reply_captures_snapshot(
    test_db_pool, monkeypatch,
):
    async with test_db_pool.acquire() as conn:
        await conn.execute(MIGRATION_SQL)
        identity_id, phone, version, _grants = await _subject(conn, 1, ("candidate",))
        await _settings(conn)

        qid = await _auto_enqueue(test_db_pool, monkeypatch, phone,
                                  purpose=_purpose("recruitment"), key="cust-recruit")
        assert qid is not None
        row = await conn.fetchrow(
            """
            SELECT authorization_identity_id, authorization_domain, authorization_version,
                   authorization_conversation_key, authorization_role_names, status
              FROM fazle_outbound_queue WHERE id=$1
            """,
            qid,
        )
        assert row["authorization_identity_id"] == identity_id
        assert row["authorization_domain"] == "recruitment"
        assert row["authorization_version"] == version
        assert row["authorization_conversation_key"] == phone
        assert json.loads(row["authorization_role_names"]) == ["candidate"]
        assert row["status"] == "pending"

        res = await _revalidate(conn, qid, conversation=phone, domain="recruitment")
        assert res.decision.allowed and res.decision.reason_code == ALLOWED


@pytest.mark.asyncio
async def test_employee_authorized_customer_reply_captures_snapshot(
    test_db_pool, monkeypatch,
):
    async with test_db_pool.acquire() as conn:
        await conn.execute(MIGRATION_SQL)
        identity_id, phone, version, _grants = await _subject(
            conn, 2, ("candidate",), domain="employee",
        )
        await _settings(conn, extra={"auto_reply.employee": "true"})

        qid = await _auto_enqueue(test_db_pool, monkeypatch, phone,
                                  purpose=_purpose("employee"), key="cust-employee")
        assert qid is not None
        row = await conn.fetchrow(
            "SELECT authorization_identity_id, authorization_domain FROM fazle_outbound_queue WHERE id=$1",
            qid,
        )
        assert row["authorization_identity_id"] == identity_id
        assert row["authorization_domain"] == "employee"

        res = await _revalidate(conn, qid, conversation=phone, domain="employee")
        assert res.decision.allowed and res.decision.reason_code == ALLOWED


@pytest.mark.asyncio
async def test_unlinked_customer_reply_falls_back_to_legacy_and_denied(
    test_db_pool, monkeypatch,
):
    async with test_db_pool.acquire() as conn:
        await conn.execute(MIGRATION_SQL)
        await _settings(conn)
        unlinked_phone = "88017070099"

        qid = await _auto_enqueue(test_db_pool, monkeypatch, unlinked_phone,
                                  purpose=_purpose("recruitment"), key="cust-unlinked")
        assert qid is not None
        row = await conn.fetchrow(
            "SELECT authorization_identity_id FROM fazle_outbound_queue WHERE id=$1", qid
        )
        assert row["authorization_identity_id"] is None

        res = await _revalidate(conn, qid, conversation=unlinked_phone, domain="recruitment")
        assert not res.decision.allowed
        # An unlinked customer must never be eligible: either the identity
        # cannot be resolved (authorization_lookup_failed) or the row is a
        # legacy row (legacy_queue_context). Both are manual-review denials;
        # the exact code depends on whether the phone maps to any identity.
        assert res.decision.reason_code in (LEGACY_QUEUE_CONTEXT, AUTHORIZATION_LOOKUP_FAILED)
        assert res.decision.manual_review_required is True


@pytest.mark.asyncio
async def test_customer_reply_for_wrong_domain_role_is_denied(
    test_db_pool, monkeypatch,
):
    async with test_db_pool.acquire() as conn:
        await conn.execute(MIGRATION_SQL)
        # Identity has a role scoped to the 'employee' domain only.
        identity_id, phone, version, grants = await _subject(
            conn, 4, ("candidate",), domain="employee",
        )
        await _settings(conn, extra={"auto_reply.employee": "true"})

        # Auto-capture targets the 'recruitment' domain: the employee-scoped role
        # does not match, capture refuses, and the row lands as legacy.
        qid = await _auto_enqueue(test_db_pool, monkeypatch, phone,
                                  purpose=_purpose("recruitment"), key="cust-wrong-domain")
        assert qid is not None
        row = await conn.fetchrow(
            "SELECT authorization_identity_id FROM fazle_outbound_queue WHERE id=$1", qid
        )
        assert row["authorization_identity_id"] is None
        denied = await _revalidate(conn, qid, conversation=phone, domain="recruitment")
        assert not denied.decision.allowed
        assert denied.decision.reason_code == LEGACY_QUEUE_CONTEXT

        # Conversely, an employee snapshot is bound to the employee domain:
        # revalidating that same row under 'recruitment' is a context mismatch.
        employee_evidence = _evidence(
            identity_id, phone, version, grants,
            conversation="conversation-employee", domain="employee",
            purpose=_purpose("employee"),
        )
        snapshot = capture_authorization_snapshot(employee_evidence)
        bound_id = await test_db_pool.fetchval(
            """
            INSERT INTO fazle_outbound_queue
                (recipient, body, source_bridge, purpose, idempotency_key, meta_json,
                 authorization_identity_id, authorization_version, authorization_conversation_key,
                 authorization_domain, authorization_scope_type, authorization_scope_key,
                 authorization_role_names, authorization_captured_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb, $14)
               RETURNING id
            """,
            phone, "synthetic reply", "bridge1", _purpose("employee"), "cust-employee-bound",
            "{}", snapshot.identity_id, snapshot.authorization_version,
            snapshot.conversation_key, snapshot.domain, snapshot.scope_type,
            snapshot.scope_key, json.dumps(list(snapshot.role_names)), snapshot.captured_at,
        )
        mismatch = await _revalidate(
            conn, bound_id, conversation="conversation-employee", domain="recruitment",
        )
        assert mismatch.decision.reason_code == CONTEXT_MISMATCH
        assert not mismatch.decision.allowed


@pytest.mark.asyncio
async def test_internal_staff_identity_cannot_enter_customer_flow(
    test_db_pool, monkeypatch,
):
    async with test_db_pool.acquire() as conn:
        await conn.execute(MIGRATION_SQL)
        # A verified internal/staff identity whose only role is non-customer
        # ('internal_staff' in the 'internal' domain) must not be able to
        # capture a customer-reply authorization snapshot.
        identity_id, phone, version, _grants = await _subject(
            conn, 5, ("internal_staff",), domain="internal",
        )
        await _settings(conn)

        qid = await _auto_enqueue(test_db_pool, monkeypatch, phone,
                                  purpose=_purpose("recruitment"), key="cust-internal")
        assert qid is not None
        row = await conn.fetchrow(
            "SELECT authorization_identity_id FROM fazle_outbound_queue WHERE id=$1", qid
        )
        assert row["authorization_identity_id"] is None

        res = await _revalidate(conn, qid, conversation=phone, domain="recruitment")
        assert not res.decision.allowed
        assert res.decision.reason_code == LEGACY_QUEUE_CONTEXT
