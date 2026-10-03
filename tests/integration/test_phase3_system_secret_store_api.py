import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_system_secret_write_only_rotate_audit_and_delete(client, test_db_pool, monkeypatch):
    from modules import system_secret_store as store
    monkeypatch.setattr(store, "get_settings", lambda: type("S", (), {
        "system_secret_master_key": "test-master-key-material-32-bytes-long",
        "ai_policy_runner_bearer": "runner-token",
        "ai_policy_observer_bearer": "observer-token",
    })())
    payload = {
        "secret_ref": "omniroute_api_key", "credential_type": "api_key",
        "secret_value": "first-value", "allowed_providers": ["omniroute"],
        "allowed_consumers": ["runner", "observer"],
        "allowed_workloads": ["administrative_reasoning", "conversational_reply"],
        "enabled": True,
    }
    created = await client.post("/api/assistant/ops/system-secrets", json=payload,
                                headers={"X-Assistant-Actor": "admin-42"})
    assert created.status_code == 201, created.text
    assert created.json()["configured"] is True
    assert "secret_value" not in created.json() and "first-value" not in created.text

    listed = await client.get("/api/assistant/ops/system-secrets")
    assert "encrypted_value" not in listed.text and "first-value" not in listed.text

    rotated = await client.put("/api/assistant/ops/system-secrets/omniroute_api_key", json={
        "credential_type": "api_key", "secret_value": "second-value",
        "allowed_providers": ["omniroute"],
        "allowed_consumers": ["runner", "observer"],
        "allowed_workloads": ["administrative_reasoning", "conversational_reply"],
        "enabled": True,
    })
    assert rotated.status_code == 200 and rotated.json()["version"] == 2
    assert "second-value" not in rotated.text

    deleted = await client.delete("/api/assistant/ops/system-secrets/omniroute_api_key")
    assert deleted.status_code == 200
    async with test_db_pool.acquire() as conn:
        actions = [row["action"] for row in await conn.fetch(
            "SELECT action FROM phase3_system_secret_audit ORDER BY id")]
        audit_text = await conn.fetchval("SELECT string_agg(row_to_json(a)::text, '') FROM phase3_system_secret_audit a")
    assert actions == ["create", "rotate", "delete"]
    assert "first-value" not in audit_text and "second-value" not in audit_text

    invalid = await client.post("/api/assistant/ops/system-secrets", json={
        "secret_ref": "bad", "credential_type": "api_key",
        "secret_value": "must-never-echo", "allowed_providers": [],
        "allowed_consumers": ["core"], "allowed_workloads": ["conversational_reply"],
    })
    assert invalid.status_code == 400
    assert "must-never-echo" not in invalid.text


@pytest.mark.asyncio
async def test_observer_policy_and_credential_are_narrowly_scoped(client, monkeypatch):
    from modules import system_secret_store as store
    monkeypatch.setattr(store, "get_settings", lambda: type("S", (), {
        "system_secret_master_key": "test-master-key-material-32-bytes-long",
        "ai_policy_runner_bearer": "runner-token",
        "ai_policy_observer_bearer": "observer-token",
    })())
    secret = await client.post("/api/assistant/ops/system-secrets", json={
        "secret_ref": "observer_openrouter", "credential_type": "bearer_token",
        "secret_value": "service-only-value", "allowed_providers": ["openrouter"],
        "allowed_consumers": ["observer"], "allowed_workloads": ["conversational_reply"],
        "enabled": True,
    })
    assert secret.status_code == 201
    route = await client.post("/api/assistant/ops/ai-routes", json={
        "route_id": "observer-openrouter", "display_name": "Observer OpenRouter", "priority": 1,
        "transport": "direct_provider", "provider": "openrouter", "model": "model-a",
        "base_url": "https://openrouter.ai/api/v1", "credential_type": "bearer_token",
        "credential_ref": "observer_openrouter", "allowed_workloads": ["conversational_reply"],
        "capabilities": ["text_generation"], "modalities": ["text"],
        "privacy_classes": ["approved_external"], "cost_class": "standard",
        "latency_classes": ["interactive"], "context_limit": 32768,
        "structured_output": False, "enabled": True, "environments": ["production"],
        "max_attempts": 1, "retry_backoff_seconds": 0,
        "eligible_consumers": ["observer"],
    })
    assert route.status_code == 201, route.text

    policy = await client.get(
        "/api/assistant/ops/ai-policy/observer/conversational_reply",
        headers={"Authorization": "Bearer observer-token"},
    )
    assert policy.status_code == 200
    assert policy.json()["routes"][0]["route_id"] == "observer-openrouter"
    assert "service-only-value" not in policy.text

    blocked = await client.post(
        "/api/assistant/ops/ai-policy/resolve-secret",
        json={"consumer": "observer", "workload": "conversational_reply", "credential_ref": "observer_openrouter"},
        headers={"Authorization": "Bearer runner-token"},
    )
    assert blocked.status_code == 403 and "service-only-value" not in blocked.text
    resolved = await client.post(
        "/api/assistant/ops/ai-policy/resolve-secret",
        json={"consumer": "observer", "workload": "conversational_reply", "credential_ref": "observer_openrouter"},
        headers={"Authorization": "Bearer observer-token"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["secret_value"] == "service-only-value"
