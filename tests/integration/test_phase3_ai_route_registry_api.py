"""Disposable-DB proof that /settings CRUD drives the real route registry."""
import pytest

pytestmark = pytest.mark.integration


def _payload(route_id="route-a", **overrides):
    value = {
        "route_id": route_id,
        "display_name": "Provider A",
        "priority": 1,
        "transport": "direct_provider",
        "provider": "groq",
        "model": "model-a",
        "base_url": "https://api.groq.com/openai/v1",
        "credential_type": "api_key",
        "credential_ref": "groq_api_key",
        "allowed_workloads": ["conversational_reply"],
        "capabilities": ["text_generation"],
        "modalities": ["text"],
        "privacy_classes": ["approved_external"],
        "cost_class": "standard",
        "latency_classes": ["interactive"],
        "context_limit": 32768,
        "structured_output": False,
        "enabled": False,
        "environments": ["development", "test"],
        "max_attempts": 1,
        "retry_backoff_seconds": 0,
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_route_crud_reorder_audit_and_secret_redaction(client, test_db_pool):
    created = await client.post(
        "/api/assistant/ops/ai-routes", json=_payload(),
        headers={"X-Assistant-Actor": "admin-42"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["credential_configured"] is False
    assert "api_key" not in created.json()

    second = await client.post(
        "/api/assistant/ops/ai-routes",
        json=_payload(
            "local", display_name="Local final", priority=2,
            transport="local_ollama", provider="ollama-local", model="qwen3:8b",
            base_url="http://127.0.0.1:11434", credential_type="local_runtime",
            credential_ref="local_runtime",
        ),
    )
    assert second.status_code == 201, second.text

    updated_payload = _payload(model="edited-model")
    updated = await client.put("/api/assistant/ops/ai-routes/route-a", json=updated_payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["model"] == "edited-model"

    reordered = await client.post(
        "/api/assistant/ops/ai-routes/reorder", json={"route_ids": ["local", "route-a"]},
    )
    assert reordered.status_code == 200, reordered.text
    listed = await client.get("/api/assistant/ops/ai-routes")
    assert [route["route_id"] for route in listed.json()["routes"]] == ["local", "route-a"]

    rejected = await client.post(
        "/api/assistant/ops/ai-routes", json={**_payload("unsafe"), "api_key": "plaintext"},
    )
    assert rejected.status_code == 400
    assert "plaintext" not in rejected.text

    deleted = await client.delete("/api/assistant/ops/ai-routes/route-a")
    assert deleted.status_code == 200
    listed = await client.get("/api/assistant/ops/ai-routes")
    assert [route["route_id"] for route in listed.json()["routes"]] == ["local"]

    async with test_db_pool.acquire() as conn:
        actions = await conn.fetch(
            "SELECT action, actor_ref FROM phase3_ai_route_audit ORDER BY id"
        )
    assert {row["action"] for row in actions} == {"create", "update", "reorder", "delete"}
    assert any(row["actor_ref"] == "admin-42" for row in actions)


@pytest.mark.asyncio
async def test_route_registry_requires_exact_internal_service_key(client):
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as unauthenticated:
        response = await unauthenticated.get("/api/assistant/ops/ai-routes")
    assert response.status_code == 403
