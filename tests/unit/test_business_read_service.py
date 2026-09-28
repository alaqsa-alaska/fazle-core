from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from modules import business_read_service as service


class _Tx:
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return False


class _Conn:
    def __init__(self): self.calls = []
    def transaction(self, **kwargs):
        assert kwargs == {"readonly": True}
        return _Tx()
    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        assert sql.lstrip().upper().startswith("SELECT")
        return [{"ok": True}]
    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        assert sql.lstrip().upper().startswith("SELECT")
        return 1


def _settings():
    return SimpleNamespace(ai_policy_runner_bearer="runner-secret", ai_policy_observer_bearer="observer-secret")


def test_inventory_covers_every_phase2_dataset():
    assert set(service._DATASETS) == {
        "employee", "attendance", "duty", "payroll", "salary", "advances", "payments",
        "escort_programs", "release_records", "clients_contacts", "recruitment", "bills_invoices",
        "conversation_history", "kb_policies", "operational_reports",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("token,consumer", [("runner-secret", "core-hermes"), ("observer-secret", "observer-hermes")])
async def test_both_authorized_consumers_receive_core_owned_read_only_data(monkeypatch, token, consumer):
    conn = _Conn()
    @asynccontextmanager
    async def fake_conn(): yield conn
    monkeypatch.setattr(service, "get_settings", _settings)
    monkeypatch.setattr(service, "db_conn", fake_conn)
    result = await service.query_business_data(
        service.BusinessReadRequest(dataset="conversation_history", search="invoice", limit=5000),
        authorization=f"Bearer {token}",
    )
    assert result["consumer"] == consumer
    assert result["authority"] == "core" and result["read_only"] is True
    assert result["human_disclosure"] == "requires_server_authorization"
    assert result["limit"] == 5000 and result["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, "", "Bearer wrong", "runner-secret"])
async def test_unauthorized_reads_fail_closed(monkeypatch, authorization):
    monkeypatch.setattr(service, "get_settings", _settings)
    with pytest.raises(HTTPException) as exc:
        await service.list_business_datasets(authorization)
    assert exc.value.status_code == 403


def test_only_allowlisted_filters_can_shape_fixed_queries():
    with pytest.raises(HTTPException) as exc:
        service._query(service.BusinessReadRequest(dataset="employee", filters={"sql": "DROP TABLE x"}))
    assert exc.value.status_code == 400


def test_every_dataset_compiles_to_select_only_fixed_sql():
    for dataset in service._DATASETS:
        sql, args, count_sql = service._query(service.BusinessReadRequest(dataset=dataset))
        assert sql.startswith("SELECT * FROM (")
        assert count_sql.startswith("SELECT count(*) FROM (SELECT * FROM (")
        assert args == [200, 0]
        assert not any(word in sql.upper() for word in (" INSERT ", " UPDATE ", " DELETE ", " ALTER ", " DROP "))


def test_broad_admin_pagination_is_not_tiny_but_remains_bounded():
    req = service.BusinessReadRequest(dataset="employee", limit=5000, offset=25000)
    sql, args, count_sql = service._query(req)
    assert "LIMIT $1 OFFSET $2" in sql
    assert args == [5000, 25000]
    assert count_sql.startswith("SELECT count(*)")


def test_conversation_history_query_exposes_reviewed_audit_fields():
    sql, args, count_sql = service._query(
        service.BusinessReadRequest(
            dataset="conversation_history",
            filters={
                "source_bridge": "bridge3",
                "sender": "8801",
                "receiver": "8802",
                "message_id": 42,
                "conversation_key": "8801:bridge3",
                "intent": "salary_query",
                "domain": "employee",
                "delivery_status": "failed",
            },
            search="question",
        )
    )
    for field in (
        "source_bridge", "sender_identity", "receiver_identity",
        "conversation_key", "intent", "domain", "delivery_status",
        "delivery_attempts", "delivery_retry_count", "delivery_error",
        "delivery_failed", "delivery_retried", "is_echo",
        "duplicate_observation_count", "is_duplicate_observation",
        "adjacent_pair_message_id", "adjacent_pair_basis",
    ):
        assert field in sql
    assert "fazle_outbound_queue" in sql
    assert "LAG(" in sql and "LEAD(" in sql
    assert "ORDER BY received_at DESC,message_id DESC" in sql
    assert args[-2:] == [200, 0]
    assert count_sql.startswith("SELECT count(*)")
    assert not any(word in sql.upper() for word in (" INSERT ", " UPDATE ", " DELETE ", " DROP "))


def test_conversation_history_admin_read_is_complete_and_internally_chunked():
    source = service._DATASETS["conversation_history"]
    assert source.filters.keys() >= {
        "source_bridge", "sender", "receiver", "message_id",
        "conversation_key", "intent", "domain", "delivery_status",
    }
    assert source.order_by == "received_at DESC,message_id DESC"



def test_admin_kb_query_includes_all_lifecycle_metadata_and_fixed_filters():
    body = service.AdminFullReadRequest(
        dataset="kb_policies",
        filters={"status": "historical", "category": "employee_policy"},
        search="leave",
    )
    sql, args, count_sql = service._admin_kb_query(body)
    assert "FROM fazle_knowledge_base k" in sql
    assert "source_kind" in sql
    assert "source_record_id" in sql
    assert "lifecycle_status" in sql
    assert "is_historical" in sql
    assert "source_version" in sql
    assert "effective_date" in sql
    assert "superseded_by" in sql
    assert "conflict_group_key" in sql
    assert "k.is_active = $1" in sql
    assert "k.category = $2" in sql
    assert "ILIKE $3" in sql
    assert args[-2:] == [5000, 0]
    assert count_sql.startswith("SELECT count(*)")
    assert not any(word in sql.upper() for word in (" INSERT ", " UPDATE ", " DELETE ", " DROP "))


def test_admin_kb_query_rejects_unapproved_filters():
    with pytest.raises(HTTPException) as exc:
        service._admin_kb_query(
            service.AdminFullReadRequest(dataset="kb_policies", filters={"is_archived": True})
        )
    assert exc.value.status_code == 400
