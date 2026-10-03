"""IR-2E isolated admin control-plane qualification."""
from pathlib import Path

import asyncpg
import pytest

MIGRATION = (Path(__file__).parents[2] / "db" / "migrations" / "089_identity_role_admin_revocation_actor.sql").read_text()


@pytest.mark.asyncio
async def test_identity_role_admin_transactions_and_legacy_safety(test_db_pool, client):
    async with test_db_pool.acquire() as conn:
        await conn.execute(MIGRATION)
        await conn.execute(MIGRATION)
        one = await conn.fetchval("INSERT INTO fazle_identity_subjects (identity_kind, display_name) VALUES ('person','Equal Name') RETURNING identity_id")
        two = await conn.fetchval("INSERT INTO fazle_identity_subjects (identity_kind, display_name) VALUES ('person','Equal Name') RETURNING identity_id")
        org = await conn.fetchval("INSERT INTO fazle_identity_subjects (identity_kind, display_name) VALUES ('organization','Equal Name') RETURNING identity_id")
        assert len({one, two, org}) == 3

    response = await client.post(f"/admin/identity-role/{one}/roles", json={
        "role_name": "client", "business_domain": "escort", "reason": "synthetic owner review"
    })
    assert response.status_code == 200
    role_one = response.json()["assignment_id"]
    first_version = response.json()["authorization_version"]

    response = await client.post(f"/admin/identity-role/{one}/roles", json={
        "role_name": "client", "business_domain": "escort", "reason": "duplicate"
    })
    assert response.status_code == 409

    response = await client.post(f"/admin/identity-role/{one}/roles", json={
        "role_name": "operator", "business_domain": "recruitment", "scope_type": "organization",
        "scope_key": "org-synthetic", "reason": "second legitimate role"
    })
    assert response.status_code == 200
    role_two = response.json()["assignment_id"]
    assert response.json()["authorization_version"] > first_version

    response = await client.post(f"/admin/identity-role/{one}/holds", json={
        "target_type": "identity", "reason": "manual identity review"
    })
    assert response.status_code == 200
    hold_id = response.json()["hold_id"]
    hold_version = response.json()["authorization_version"]

    response = await client.post(f"/admin/identity-role/{one}/roles/{role_one}/revoke", json={
        "reason": "role no longer current"
    })
    assert response.status_code == 200

    detail = (await client.get(f"/admin/identity-role/{one}")).json()
    active = {r["assignment_id"] for r in detail["roles"] if r["active"]}
    assert active == {role_two}
    assert any(h["hold_id"] == hold_id and h["active"] for h in detail["holds"])
    assert detail["subject"]["authorization_version"] > hold_version

    response = await client.post(f"/admin/identity-role/{one}/holds/{hold_id}/release", json={
        "reason": "owner reviewed; release is not dispatch"
    })
    assert response.status_code == 200
    assert response.json()["dispatch"] == "none"

    async with test_db_pool.acquire() as conn:
        hold = await conn.fetchrow("SELECT active, reason, released_by, release_reason FROM fazle_outbound_holds WHERE hold_id=$1", hold_id)
        assert hold["active"] is False
        assert hold["release_reason"] == "owner reviewed; release is not dispatch"
        assert await conn.fetchval("SELECT count(*) FROM fazle_identity_role_assignments WHERE identity_id=$1", one) == 2
        assert await conn.fetchval("SELECT count(*) FROM fazle_admin_audit WHERE args LIKE $1", f'%"identity_id":{one}%') >= 4

    # A non-privileged viewer can use no mutation even if the frontend is edited.
    from modules import rbac
    viewer_key = "synthetic-viewer-key"
    async with test_db_pool.acquire() as conn:
        viewer = await conn.fetchval("INSERT INTO fazle_admins (phone,name,status,api_key_hash) VALUES ('8801999999999','Viewer','active',$1) RETURNING id", rbac.hash_api_key(viewer_key))
        await conn.execute("INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1,'viewer','test')", viewer)
    denied = await client.post(f"/admin/identity-role/{one}/roles", headers={"X-Internal-Key": viewer_key}, json={
        "role_name": "blocked", "business_domain": "escort", "reason": "must be denied"
    })
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_identifier_revocation_actor_and_audit_failure_contract(test_db_pool, client, monkeypatch):
    async with test_db_pool.acquire() as conn:
        identity_id = await conn.fetchval("INSERT INTO fazle_identity_subjects (identity_kind, display_name) VALUES ('person','Identifier Subject') RETURNING identity_id")
    linked = await client.post(f"/admin/identity-role/{identity_id}/identifiers", json={
        "action": "link", "platform": "whatsapp", "platform_user_id": "synthetic-jid",
        "identifier_type": "whatsapp_jid", "evidence_source": "synthetic evidence", "reason": "explicit link"
    })
    assert linked.status_code == 200
    link_id = linked.json()["identity_link_id"]
    revoked = await client.post(f"/admin/identity-role/{identity_id}/identifiers", json={
        "action": "revoke", "identity_link_id": link_id, "reason": "synthetic correction"
    })
    assert revoked.status_code == 200
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT active, revoked_by_admin_id, revocation_reason FROM wbom_identity_links WHERE identity_link_id=$1", link_id)
        assert row["active"] is False
        assert row["revocation_reason"] == "synthetic correction"

    # An audit failure must prevent the mutation from committing.
    from modules import identity_role_admin
    async def fail_audit(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(identity_role_admin.rbac, "record_audit_conn", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        await client.post(f"/admin/identity-role/{identity_id}/roles", json={
            "role_name": "rollback-role", "business_domain": "escort", "reason": "should roll back"
        })
    async with test_db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM fazle_identity_role_assignments WHERE identity_id=$1 AND role_name='rollback-role'", identity_id) == 0


@pytest.mark.asyncio
async def test_identity_subject_creation_is_explicit_and_unverified_by_default(test_db_pool, client):
    response = await client.post("/admin/identity-role", json={
        "display_name": "Synthetic Owner Canary",
        "reason": "offline canary qualification",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["verification_state"] == "unverified"
    identity_id = body["identity_id"]

    rejected = await client.post("/admin/identity-role", json={
        "display_name": "Must Not Start Verified",
        "verification_state": "verified",
        "reason": "must be rejected",
    })
    assert rejected.status_code == 400

    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT verification_state, lifecycle_state FROM fazle_identity_subjects WHERE identity_id=$1",
            identity_id,
        )
        assert dict(row) == {"verification_state": "unverified", "lifecycle_state": "active"}
        assert await conn.fetchval(
            "SELECT count(*) FROM fazle_admin_audit WHERE command='identity_create' AND args LIKE $1",
            f'%"identity_id":{identity_id}%',
        ) == 1
