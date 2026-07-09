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


async def test_t019_safeops_killswitch_requires_auth(client):
    from modules.preflight_guard import is_feature_active

    # Ensure endpoint hits auth logic (not missing-table path).
    # The shared `client` fixture carries a default internal key header.
    # Override with an explicit invalid key for this test.
    feature_name = "fg5_global_safeops_auth_test"
    response = await client.post(
        f"/api/assistant/ops/kill-switch/{feature_name}",
        headers={"X-Internal-Key": "invalid-test-key"},
    )
    assert response.status_code == 403
    assert is_feature_active(feature_name, default=False) is False


async def test_t019_safeops_activate_then_killswitch_disables_feature(client, test_db_pool):
    from app.config import get_settings
    from modules.preflight_guard import is_feature_active

    await _ensure_feature_flags_table(test_db_pool)

    feature_name = "fg5_global_safeops"
    auth_headers = {"X-Internal-Key": get_settings().internal_api_key}

    activate = await client.post(
        f"/api/assistant/ops/activate/{feature_name}",
        headers=auth_headers,
        json={"updated_by": "phase5-t019-test"},
    )
    assert activate.status_code == 200
    activate_payload = activate.json()
    assert activate_payload.get("ok") is True
    assert activate_payload.get("action") == "activate"
    assert activate_payload.get("flag", {}).get("feature_name") == feature_name
    assert activate_payload.get("flag", {}).get("is_active") is True
    assert activate_payload.get("flag", {}).get("kill_switch_active") is False
    assert activate_payload.get("flag", {}).get("effective_enabled") is True
    assert is_feature_active(feature_name, default=False) is True

    kill = await client.post(
        f"/api/assistant/ops/kill-switch/{feature_name}",
        headers=auth_headers,
        json={"updated_by": "phase5-t019-test"},
    )
    assert kill.status_code == 200
    kill_payload = kill.json()
    assert kill_payload.get("ok") is True
    assert kill_payload.get("action") == "kill-switch"
    assert kill_payload.get("flag", {}).get("feature_name") == feature_name
    assert kill_payload.get("flag", {}).get("is_active") is False
    assert kill_payload.get("flag", {}).get("kill_switch_active") is True
    assert kill_payload.get("flag", {}).get("effective_enabled") is False
    assert is_feature_active(feature_name, default=True) is False

    flags = await client.get("/api/assistant/ops/flags", headers=auth_headers)
    assert flags.status_code == 200
    flags_payload = flags.json()
    assert flags_payload.get("ok") is True
    target = next(
        item for item in flags_payload.get("flags", []) if item.get("feature_name") == feature_name
    )
    assert target.get("kill_switch_active") is True
    assert target.get("effective_enabled") is False
