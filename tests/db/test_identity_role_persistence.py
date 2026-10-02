"""IR-2C persistence qualification against the isolated PostgreSQL schema."""

from pathlib import Path

import asyncpg
import pytest


MIGRATION_SQL = (
    Path(__file__).parents[2] / "db" / "migrations" / "087_identity_role_persistence.sql"
).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_ir2c_identity_role_and_hold_persistence(test_db_pool):
    async with test_db_pool.acquire() as conn:
        # The migration runner already applied this file during schema setup.
        # Executing it again proves the SQL itself is re-runnable as required.
        await conn.execute(MIGRATION_SQL)

        subject_one = await conn.fetchval(
            """
            INSERT INTO fazle_identity_subjects
                (identity_kind, display_name, verification_state)
            VALUES ('person', 'Same Synthetic Name', 'verified')
            RETURNING identity_id
            """
        )
        subject_two = await conn.fetchval(
            """
            INSERT INTO fazle_identity_subjects
                (identity_kind, display_name, verification_state)
            VALUES ('person', 'Same Synthetic Name', 'verified')
            RETURNING identity_id
            """
        )
        organization = await conn.fetchval(
            """
            INSERT INTO fazle_identity_subjects
                (identity_kind, display_name)
            VALUES ('organization', 'Synthetic Organization')
            RETURNING identity_id
            """
        )
        assert len({subject_one, subject_two, organization}) == 3

        version = await conn.fetchval(
            "SELECT fazle_bump_identity_authorization_version($1)", subject_one
        )
        assert version == 2
        await conn.execute(
            """
            INSERT INTO wbom_identity_links
                (identity_key, platform, platform_user_id, identity_id,
                 identifier_type, verification_state, evidence_source,
                 authorization_version_at_change)
            VALUES ($1, 'whatsapp', 'synthetic-jid-1', $2,
                    'whatsapp_jid', 'verified', 'synthetic-test', $3)
            """,
            f"subject:{subject_one}", subject_one, version,
        )
        await conn.execute(
            """
            INSERT INTO wbom_identity_links
                (identity_key, platform, platform_user_id, identity_id,
                 identifier_type, verification_state, evidence_source,
                 authorization_version_at_change)
            VALUES ($1, 'meta', 'synthetic-meta-1', $2,
                    'meta_id', 'verified', 'synthetic-test', $3)
            """,
            f"subject:{subject_one}", subject_one, version,
        )
        assert await conn.fetchval(
            "SELECT count(*) FROM wbom_identity_links WHERE identity_id=$1 AND active",
            subject_one,
        ) == 2

        # Correction/revocation keeps the old identifier row queryable while
        # allowing one replacement active row for the same platform ID.
        version = await conn.fetchval(
            "SELECT fazle_bump_identity_authorization_version($1)", subject_one
        )
        await conn.execute(
            """
            UPDATE wbom_identity_links
               SET active=false, revoked_at=NOW(),
                   revocation_reason='synthetic correction',
                   authorization_version_at_change=$1, updated_at=NOW()
             WHERE identity_id=$2 AND platform='whatsapp'
               AND platform_user_id='synthetic-jid-1'
            """,
            version, subject_one,
        )
        await conn.execute(
            """
            INSERT INTO wbom_identity_links
                (identity_key, platform, platform_user_id, identity_id,
                 identifier_type, verification_state, evidence_source,
                 authorization_version_at_change)
            VALUES ($1, 'whatsapp', 'synthetic-jid-1', $2,
                    'verified_mobile', 'verified', 'synthetic-correction', $3)
            """,
            f"subject:{subject_one}", subject_one, version,
        )
        assert await conn.fetchval(
            """
            SELECT count(*) FROM wbom_identity_links
             WHERE platform='whatsapp' AND platform_user_id='synthetic-jid-1'
            """
        ) == 2
        assert await conn.fetchval(
            """
            SELECT count(*) FROM wbom_identity_links
             WHERE platform='whatsapp' AND platform_user_id='synthetic-jid-1' AND active
            """
        ) == 1

        # Multiple legitimate roles coexist across domains/scopes.
        role_one = await conn.fetchval(
            """
            INSERT INTO fazle_identity_role_assignments
                (identity_id, role_name, business_domain, authorization_version_at_change,
                 assignment_reason)
            VALUES ($1, 'client', 'escort', $2, 'synthetic assignment')
            RETURNING assignment_id
            """,
            subject_one, version,
        )
        role_two = await conn.fetchval(
            """
            INSERT INTO fazle_identity_role_assignments
                (identity_id, role_name, business_domain, scope_type, scope_key,
                 authorization_version_at_change, assignment_reason)
            VALUES ($1, 'operator', 'escort', 'organization', 'org-a', $2,
                    'synthetic assignment')
            RETURNING assignment_id
            """,
            subject_one, version,
        )
        role_three = await conn.fetchval(
            """
            INSERT INTO fazle_identity_role_assignments
                (identity_id, role_name, business_domain, scope_type, scope_key,
                 authorization_version_at_change, assignment_reason)
            VALUES ($1, 'operator', 'recruitment', 'organization', 'org-a', $2,
                    'synthetic assignment')
            RETURNING assignment_id
            """,
            subject_one, version,
        )
        assert len({role_one, role_two, role_three}) == 3
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO fazle_identity_role_assignments
                        (identity_id, role_name, business_domain,
                         authorization_version_at_change)
                    VALUES ($1, 'client', 'escort', $2)
                    """,
                    subject_one, version,
                )

        version = await conn.fetchval(
            "SELECT fazle_bump_identity_authorization_version($1)", subject_one
        )
        await conn.execute(
            """
            UPDATE fazle_identity_role_assignments
               SET active=false, revoked_at=NOW(), revoked_by='synthetic-owner',
                   revocation_reason='synthetic revoke',
                   authorization_version_at_change=$1, updated_at=NOW()
             WHERE assignment_id=$2
            """,
            version, role_one,
        )
        assert await conn.fetchval(
            "SELECT active FROM fazle_identity_role_assignments WHERE assignment_id=$1",
            role_two,
        ) is True
        assert await conn.fetchval(
            "SELECT active FROM fazle_identity_role_assignments WHERE assignment_id=$1",
            role_three,
        ) is True
        assert await conn.fetchval(
            "SELECT count(*) FROM fazle_identity_role_assignments WHERE identity_id=$1",
            subject_one,
        ) == 3

        # Identity and conversation holds are isolated and durable. The
        # identity hold is followed by a role change and must remain active.
        identity_hold_version = await conn.fetchval(
            "SELECT fazle_bump_identity_authorization_version($1)", subject_one
        )
        identity_hold = await conn.fetchval(
            """
            INSERT INTO fazle_outbound_holds
                (target_type, identity_id, reason, placed_by)
            VALUES ('identity', $1, 'synthetic review', 'synthetic-owner')
            RETURNING hold_id
            """,
            subject_one,
        )
        assert identity_hold_version == 5
        conversation_hold = await conn.fetchval(
            """
            INSERT INTO fazle_outbound_holds
                (target_type, conversation_key, reason, placed_by)
            VALUES ('conversation', 'synthetic-conversation-1',
                    'synthetic review', 'synthetic-owner')
            RETURNING hold_id
            """
        )
        assert await conn.fetchval(
            "SELECT count(*) FROM fazle_outbound_holds WHERE identity_id=$1 AND active",
            subject_two,
        ) == 0

        role_version = await conn.fetchval(
            "SELECT fazle_bump_identity_authorization_version($1)", subject_one
        )
        await conn.execute(
            """
            INSERT INTO fazle_identity_role_assignments
                (identity_id, role_name, business_domain, scope_type, scope_key,
                 authorization_version_at_change, assignment_reason)
            VALUES ($1, 'case_manager', 'escort', 'project', 'project-a', $2,
                    'synthetic assignment')
            """,
            subject_one, role_version,
        )
        assert await conn.fetchval(
            "SELECT active FROM fazle_outbound_holds WHERE hold_id=$1", identity_hold
        ) is True

        conversation_version_before_release = await conn.fetchval(
            "SELECT authorization_version FROM fazle_identity_subjects WHERE identity_id=$1",
            subject_one,
        )
        await conn.execute(
            """
            UPDATE fazle_outbound_holds
               SET active=false, released_at=NOW(), released_by='synthetic-owner',
                   release_reason='synthetic release', state_version=state_version+1,
                   updated_at=NOW()
             WHERE hold_id=$1
            """,
            conversation_hold,
        )
        assert await conn.fetchval(
            "SELECT authorization_version FROM fazle_identity_subjects WHERE identity_id=$1",
            subject_one,
        ) == conversation_version_before_release

        await conn.execute(
            """
            INSERT INTO fazle_outbound_queue
                (recipient, body, source_bridge, purpose, idempotency_key)
            VALUES ('synthetic-recipient', 'synthetic body', 'bridge1',
                    'synthetic-test', 'ir2c-synthetic-queue')
            """
        )
        release_version = await conn.fetchval(
            "SELECT fazle_bump_identity_authorization_version($1)", subject_one
        )
        await conn.execute(
            """
            UPDATE fazle_outbound_holds
               SET active=false, released_at=NOW(), released_by='synthetic-owner',
                   release_reason='synthetic release', state_version=state_version+1,
                   updated_at=NOW()
             WHERE hold_id=$1
            """,
            identity_hold,
        )
        assert release_version == 7
        assert await conn.fetchval(
            "SELECT count(*) FROM fazle_outbound_holds WHERE hold_id=$1", identity_hold
        ) == 1
        assert await conn.fetchval(
            "SELECT state_version FROM fazle_outbound_holds WHERE hold_id=$1", identity_hold
        ) == 2
        assert await conn.fetchval(
            "SELECT status FROM fazle_outbound_queue WHERE idempotency_key=$1",
            "ir2c-synthetic-queue",
        ) == "pending"

        # Legacy role and phone-block state remain separate and untouched.
        await conn.execute(
            """
            INSERT INTO fazle_contact_roles (phone, name, role, source)
            VALUES ('synthetic-legacy', 'Synthetic Legacy', 'client', 'ir2c-test')
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fazle_runtime_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            INSERT INTO fazle_runtime_settings (key, value)
            VALUES ('phone_block.synthetic-legacy', 'true')
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
            """
        )
        assert await conn.fetchval(
            "SELECT role FROM fazle_contact_roles WHERE phone=$1",
            "synthetic-legacy",
        ) == "client"
        assert await conn.fetchval(
            "SELECT value FROM fazle_runtime_settings WHERE key=$1",
            "phone_block.synthetic-legacy",
        ) == "true"
