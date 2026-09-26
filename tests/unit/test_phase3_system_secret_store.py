from types import SimpleNamespace

import pytest

from modules import system_secret_store as store


def _settings(key="k" * 32, runner="runner-token", observer="observer-token"):
    return SimpleNamespace(
        system_secret_master_key=key,
        ai_policy_runner_bearer=runner,
        ai_policy_observer_bearer=observer,
    )


def test_secret_ciphertext_roundtrip_and_tamper_failure(monkeypatch):
    monkeypatch.setattr(store, "get_settings", lambda: _settings())
    encoded = store.encrypt_secret("omniroute_api_key", "api_key", 1, "not-a-real-secret")
    assert "not-a-real-secret" not in encoded
    assert store.decrypt_secret("omniroute_api_key", "api_key", 1, encoded) == "not-a-real-secret"
    with pytest.raises(Exception):
        store.decrypt_secret("groq_api_key", "api_key", 1, encoded)


def test_store_fails_closed_without_master_key(monkeypatch):
    monkeypatch.setattr(store, "get_settings", lambda: _settings(key="short"))
    with pytest.raises(RuntimeError, match="not configured"):
        store.encrypt_secret("omniroute_api_key", "api_key", 1, "value")


def test_secret_scope_cannot_cross_workload_owner():
    with pytest.raises(ValueError, match="not owned"):
        store.SecretWrite(
            secret_ref="runner_key", credential_type="api_key", secret_value="value",
            allowed_providers=["omniroute"],
            allowed_consumers=["runner"], allowed_workloads=["conversational_reply"],
        ).validate_scope()


def test_service_actor_secrets_are_separate(monkeypatch):
    monkeypatch.setattr(store, "get_settings", lambda: _settings())
    store._service_actor("runner", "Bearer runner-token")
    with pytest.raises(Exception):
        store._service_actor("observer", "Bearer runner-token")
