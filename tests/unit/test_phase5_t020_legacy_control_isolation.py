from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.asyncio


async def _ensure_feature_flags_table(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_feature_flags (
                feature_name TEXT PRIMARY KEY,
                is_active BOOLEAN NOT NULL DEFAULT FALSE,
                kill_switch_active BOOLEAN NOT NULL DEFAULT FALSE,
                updated_by TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


async def test_t020_safeops_killswitch_does_not_block_legacy_approve_flow(test_db_pool):
    import app.database as db_module
    from modules import admin_commands
    from modules.admin_commands import process_admin_command
    from modules.preflight_guard import is_feature_active, kill_switch_feature

    db_module._pool = test_db_pool
    await _ensure_feature_flags_table(test_db_pool)
    await kill_switch_feature("fg5_global_safeops", updated_by="phase5-t020-test")
    admin_commands._dedup_cache.clear()

    async with test_db_pool.acquire() as conn:
        draft = await conn.fetchrow(
            """
            INSERT INTO fazle_draft_replies
                (recipient, reply_text, intent, source, status)
            VALUES ('8801811111111', 'Legacy path draft', 'generic', 'bridge1', 'pending')
            RETURNING id
            """
        )

    assert is_feature_active("fg5_global_safeops", default=True) is False

    with patch(
        "modules.rbac.check_permission",
        new=AsyncMock(
            return_value={
                "allowed": True,
                "required_role": "operator",
                "admin": {
                    "id": 1,
                    "phone": "8801700000001",
                    "name": "ops-user",
                    "status": "active",
                },
            }
        ),
    ):
        with patch("app.bridge.BridgeClient.send", new=AsyncMock(return_value=True)):
            await process_admin_command(
                text=f"APPROVE {draft['id']}",
                admin_phone="8801700999911",
            )

    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM fazle_draft_replies WHERE id=$1",
            draft["id"],
        )
    assert row["status"] == "approved"
    async with test_db_pool.acquire() as conn:
        queue = await conn.fetchrow(
            "SELECT status FROM fazle_outbound_queue WHERE purpose=\x27admin_approve\x27 "
            "AND meta_json->>\x27draft_id\x27=$1 ORDER BY id DESC LIMIT 1",
            str(draft["id"]),
        )
    assert queue["status"] == "pending"


async def test_t020_safeops_killswitch_does_not_block_legacy_reject_flow(test_db_pool):
    import app.database as db_module
    from modules import admin_commands
    from modules.admin_commands import process_admin_command
    from modules.preflight_guard import is_feature_active, kill_switch_feature

    db_module._pool = test_db_pool
    await _ensure_feature_flags_table(test_db_pool)
    await kill_switch_feature("fg5_global_safeops", updated_by="phase5-t020-test")
    admin_commands._dedup_cache.clear()

    assert is_feature_active("fg5_global_safeops", default=True) is False

    async with test_db_pool.acquire() as conn:
        draft = await conn.fetchrow(
            """
            INSERT INTO fazle_draft_replies
                (recipient, reply_text, intent, source, status)
            VALUES ('8801811111111', 'Legacy reject path draft', 'generic', 'bridge1', 'pending')
            RETURNING id
            """
        )

    with patch(
        "modules.rbac.check_permission",
        new=AsyncMock(
            return_value={
                "allowed": True,
                "required_role": "operator",
                "admin": {
                    "id": 1,
                    "phone": "8801700000001",
                    "name": "ops-user",
                    "status": "active",
                },
            }
        ),
    ):
        await process_admin_command(
            text=f"REJECT {draft['id']}",
            admin_phone="8801700999922",
        )

    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM fazle_draft_replies WHERE id=$1",
            draft["id"],
        )
    assert row["status"] == "rejected"
