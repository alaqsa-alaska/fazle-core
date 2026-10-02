from types import SimpleNamespace

import pytest

from modules import business_read_service as service

pytestmark = pytest.mark.integration


def _settings():
    return SimpleNamespace(
        ai_policy_runner_bearer="runner-secret",
        ai_policy_observer_bearer="observer-secret",
    )


@pytest.mark.asyncio
async def test_both_consumers_can_read_every_fixed_dataset(client, monkeypatch):
    monkeypatch.setattr(service, "get_settings", _settings)
    for bearer, expected_consumer in (
        ("runner-secret", "core-hermes"),
        ("observer-secret", "observer-hermes"),
    ):
        for dataset in service._DATASETS:
            response = await client.post(
                "/api/assistant/ops/business-read/query",
                headers={"Authorization": f"Bearer {bearer}"},
                json={"dataset": dataset, "limit": 5000},
            )
            assert response.status_code == 200, (dataset, response.text)
            payload = response.json()
            assert payload["consumer"] == expected_consumer
            assert payload["authority"] == "core"
            assert payload["read_only"] is True


@pytest.mark.asyncio
async def test_business_read_api_denies_unauthorized_and_sql_input(client, monkeypatch):
    monkeypatch.setattr(service, "get_settings", _settings)
    denied = await client.post(
        "/api/assistant/ops/business-read/query",
        headers={"Authorization": "Bearer wrong"},
        json={"dataset": "employee"},
    )
    assert denied.status_code == 403

    sql_input = await client.post(
        "/api/assistant/ops/business-read/query",
        headers={"Authorization": "Bearer runner-secret"},
        json={"dataset": "employee", "sql": "SELECT * FROM fazle_admins"},
    )
    assert sql_input.status_code == 422
