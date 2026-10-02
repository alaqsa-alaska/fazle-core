"""Authenticated, fixed-query Core business read service for Hermes consumers."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.database import db_conn

router = APIRouter(prefix="/api/assistant/ops/business-read", tags=["business_read"])

Dataset = Literal[
    "employee", "attendance", "duty", "payroll", "salary", "advances",
    "payments", "escort_programs", "release_records", "clients_contacts",
    "recruitment", "bills_invoices", "conversation_history", "kb_policies",
    "operational_reports",
]


@dataclass(frozen=True)
class QuerySpec:
    sql: str
    order_by: str
    filters: dict[str, str]
    search: tuple[str, ...] = ()


_DATASETS: dict[str, QuerySpec] = {
    "employee": QuerySpec(
        "SELECT * FROM wbom_employees", "employee_name,employee_id",
        {"employee_id": "employee_id", "status": "status", "phone": "employee_mobile"},
        ("employee_name", "employee_mobile"),
    ),
    "attendance": QuerySpec(
        "SELECT a.attendance_id,a.employee_id,e.employee_name,a.attendance_date,a.status,a.location,"
        "a.check_in_time,a.check_out_time,a.remarks,a.recorded_by FROM wbom_attendance a "
        "JOIN wbom_employees e ON e.employee_id=a.employee_id", "attendance_date DESC,attendance_id DESC",
        {"employee_id": "employee_id", "status": "status", "date_from": "attendance_date__gte", "date_to": "attendance_date__lte"},
        ("employee_name",),
    ),
    "duty": QuerySpec(
        "SELECT p.*,e.employee_name FROM wbom_escort_programs p LEFT JOIN wbom_employees e "
        "ON e.employee_id=p.escort_employee_id", "program_date DESC,program_id DESC",
        {"employee_id": "escort_employee_id", "program_id": "program_id", "status": "status", "date_from": "program_date__gte", "date_to": "program_date__lte"},
        ("employee_name", "mother_vessel", "lighter_vessel", "destination"),
    ),
    "payroll": QuerySpec(
        "SELECT r.*,e.employee_name,e.designation FROM wbom_payroll_runs r JOIN wbom_employees e "
        "ON e.employee_id=r.employee_id", "period_year DESC,period_month DESC,run_id DESC",
        {"employee_id": "employee_id", "status": "status", "year": "period_year", "month": "period_month"},
        ("employee_name",),
    ),
    "salary": QuerySpec(
        "SELECT employee_id,employee_name,employee_mobile,designation,status,basic_salary,joining_date "
        "FROM wbom_employees", "employee_name,employee_id",
        {"employee_id": "employee_id", "status": "status", "phone": "employee_mobile"},
        ("employee_name",),
    ),
    "advances": QuerySpec(
        "SELECT t.*,t.txn_ref AS transaction_ref,t.txn_date AS transaction_date,"
        "t.txn_category AS category,COALESCE(e.full_name,t.employee_name_raw) AS employee_name "
        "FROM fpe_cash_transactions t LEFT JOIN fpe_employees e ON e.id=t.employee_id "
        "WHERE t.deleted_at IS NULL AND t.txn_category='advance'",
        "transaction_date DESC,transaction_ref", {"employee_id": "employee_id", "status": "transaction_status", "date_from": "transaction_date__gte", "date_to": "transaction_date__lte"},
        ("employee_name_raw", "transaction_ref"),
    ),
    "payments": QuerySpec(
        "SELECT t.*,t.txn_ref AS transaction_ref,t.txn_date AS transaction_date,"
        "t.txn_category AS category,COALESCE(e.full_name,t.employee_name_raw) AS employee_name "
        "FROM fpe_cash_transactions t LEFT JOIN fpe_employees e ON e.id=t.employee_id "
        "WHERE t.deleted_at IS NULL", "transaction_date DESC,transaction_ref",
        {"employee_id": "employee_id", "status": "transaction_status", "category": "category", "date_from": "transaction_date__gte", "date_to": "transaction_date__lte"},
        ("employee_name_raw", "transaction_ref"),
    ),
    "escort_programs": QuerySpec(
        "SELECT p.*,e.employee_name FROM wbom_escort_programs p LEFT JOIN wbom_employees e "
        "ON e.employee_id=p.escort_employee_id", "program_date DESC,program_id DESC",
        {"employee_id": "escort_employee_id", "program_id": "program_id", "status": "status", "date_from": "program_date__gte", "date_to": "program_date__lte"},
        ("employee_name", "mother_vessel", "lighter_vessel", "destination"),
    ),
    "release_records": QuerySpec(
        "SELECT id,candidate_id,program_id,kind,effective_date,message_sent_at,fields,"
        "evidence_message_ids,actor,reason,confirmed_at FROM escort_evidence_event "
        "WHERE kind IN ('escort_release','escort_program_complete')", "effective_date DESC,id DESC",
        {"program_id": "program_id", "kind": "kind", "date_from": "effective_date__gte", "date_to": "effective_date__lte"},
        ("actor", "reason"),
    ),
    "clients_contacts": QuerySpec(
        "SELECT * FROM wbom_contacts",
        "display_name,contact_id", {"contact_id": "contact_id", "status": "is_active", "phone": "whatsapp_number"},
        ("display_name", "company_name", "whatsapp_number", "relation"),
    ),
    "recruitment": QuerySpec(
        "SELECT * FROM fazle_recruitment_sessions", "created_at DESC,id DESC",
        {"status": "funnel_stage", "phone": "phone", "date_from": "created_at__gte", "date_to": "created_at__lte"},
        ("full_name", "phone", "area"),
    ),
    "bills_invoices": QuerySpec(
        "SELECT b.*,COALESCE(bp.client_name,c.display_name) AS client_name FROM wbom_billing_records b "
        "LEFT JOIN wbom_client_billing_profiles bp ON bp.contact_id=b.contact_id "
        "LEFT JOIN wbom_contacts c ON c.contact_id=b.contact_id", "bill_date DESC,bill_id DESC",
        {"contact_id": "contact_id", "program_id": "program_id", "status": "payment_status", "date_from": "bill_date__gte", "date_to": "bill_date__lte"},
        ("client_name", "bill_number", "remarks"),
    ),
    "conversation_history": QuerySpec(
        """SELECT
             m.message_id, m.whatsapp_msg_id, m.received_at, m.processed_at,
             m.direction, m.platform, m.platform AS source_bridge,
             m.sender_number, m.original_sender_number,
             COALESCE(m.resolved_phone, m.canonical_phone, m.sender_number) AS sender_identity,
             m.receiver_number, m.receiver_number AS receiver_identity,
             m.conversation_key, m.actor_type, m.actor_id, m.identity_role,
             m.identity_confidence, m.intent_detected, m.intent_detected AS intent,
             m.workflow_triggered, m.workflow_triggered AS domain,
             m.message_type, m.content_type, m.is_processed, m.status,
             m.message_body, m.extracted_text, m.source_message_ref,
             m.canonical_message_key,
             CASE WHEN m.direction = 'outbound' THEN COALESCE(q.status, m.status)
                  ELSE 'received' END AS delivery_status,
             CASE WHEN m.direction = 'outbound' THEN q.attempts END AS delivery_attempts,
             CASE WHEN m.direction = 'outbound'
                  THEN GREATEST(COALESCE(q.attempts, 1) - 1, 0) END AS delivery_retry_count,
             CASE WHEN m.direction = 'outbound' THEN q.last_error END AS delivery_error,
             CASE WHEN m.direction = 'outbound' AND q.status IN ('failed', 'dlq')
                  THEN true ELSE false END AS delivery_failed,
             CASE WHEN m.direction = 'outbound' AND COALESCE(q.attempts, 1) > 1
                  THEN true ELSE false END AS delivery_retried,
             CASE WHEN m.actor_type = 'internal_system'
                    OR COALESCE(m.metadata_json->>'echo', '') IN ('true', '1')
                    OR COALESCE(m.metadata_json->>'internal_echo', '') IN ('true', '1')
                  THEN true ELSE false END AS is_echo,
             COUNT(*) OVER (PARTITION BY m.canonical_message_key) AS duplicate_observation_count,
             CASE WHEN m.canonical_message_key IS NOT NULL
                    AND COUNT(*) OVER (PARTITION BY m.canonical_message_key) > 1
                  THEN true ELSE false END AS is_duplicate_observation,
             LAG(m.message_id) OVER (
               PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
             ) AS previous_message_id,
             LAG(m.direction) OVER (
               PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
             ) AS previous_direction,
             LEAD(m.message_id) OVER (
               PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
             ) AS next_message_id,
             LEAD(m.direction) OVER (
               PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
             ) AS next_direction,
             CASE
               WHEN m.direction = 'inbound'
                AND LEAD(m.direction) OVER (
                  PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
                ) = 'outbound'
               THEN LEAD(m.message_id) OVER (
                  PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
               )
               WHEN m.direction = 'outbound'
                AND LAG(m.direction) OVER (
                  PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
                ) = 'inbound'
               THEN LAG(m.message_id) OVER (
                  PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
               )
             END AS adjacent_pair_message_id,
             CASE
               WHEN m.direction = 'inbound'
                AND LEAD(m.direction) OVER (
                  PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
                ) = 'outbound'
               THEN 'inbound_to_adjacent_outbound'
               WHEN m.direction = 'outbound'
                AND LAG(m.direction) OVER (
                  PARTITION BY m.conversation_key ORDER BY m.received_at, m.message_id
                ) = 'inbound'
               THEN 'outbound_to_adjacent_inbound'
             END AS adjacent_pair_basis
           FROM wbom_whatsapp_messages m
           LEFT JOIN LATERAL (
             SELECT q.status, q.attempts, q.last_error
               FROM fazle_outbound_queue q
              WHERE m.direction = 'outbound'
                AND (
                  m.source_message_ref IN (
                    'hermes_relay_reply:' || q.id::text,
                    'recovery_sweep_reply:' || q.id::text,
                    'hermes_admin_send:' || q.id::text
                  )
                  OR (
                    m.source_message_ref IS NULL
                    AND q.recipient = m.sender_number
                    AND q.source_bridge = m.platform
                    AND q.body = m.message_body
                    AND q.created_at <= m.received_at
                  )
                )
              ORDER BY CASE WHEN m.source_message_ref IS NOT NULL THEN 0 ELSE 1 END,
                       q.created_at DESC
              LIMIT 1
           ) q ON true""",
        "received_at DESC,message_id DESC",
        {
            "phone": "sender_identity", "sender": "sender_identity",
            "receiver": "receiver_identity", "message_id": "message_id",
            "conversation_key": "conversation_key", "direction": "direction",
            "platform": "platform", "source_bridge": "source_bridge",
            "intent": "intent_detected", "domain": "workflow_triggered",
            "delivery_status": "delivery_status",
            "date_from": "received_at__gte", "date_to": "received_at__lte",
        },
        (
            "message_body", "extracted_text", "sender_identity",
            "receiver_identity", "conversation_key", "intent_detected",
            "workflow_triggered", "source_bridge",
        ),
    ),
    "kb_policies": QuerySpec(
        "SELECT * FROM fazle_knowledge_base",
        "category,key,id", {"status": "is_active", "category": "category"},
        ("key", "category", "subcategory", "reply_text"),
    ),
    "operational_reports": QuerySpec(
        "SELECT cache_key AS report_name,payload AS report_data,expires_at FROM fazle_report_cache",
        "report_name", {"report_name": "report_name"}, ("report_name",),
    ),
}


class BusinessReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Dataset
    filters: dict[str, str | int | bool] = Field(default_factory=dict)
    search: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=200, ge=1, le=5000)
    offset: int = Field(default=0, ge=0, le=10_000_000)
    include_total: bool = True


def _service_consumer(authorization: str | None) -> str:
    supplied = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else ""
    settings = get_settings()
    for consumer, expected in (
        ("core-hermes", settings.ai_policy_runner_bearer),
        ("observer-hermes", settings.ai_policy_observer_bearer),
    ):
        if expected and hmac.compare_digest(supplied, expected):
            return consumer
    raise HTTPException(status_code=403, detail="Forbidden")


def _query(req: BusinessReadRequest) -> tuple[str, list[Any], str]:
    spec = _DATASETS[req.dataset]
    clauses: list[str] = []
    args: list[Any] = []
    for name, value in req.filters.items():
        target = spec.filters.get(name)
        if not target:
            raise HTTPException(status_code=400, detail=f"unsupported filter for {req.dataset}")
        column, _, operator = target.partition("__")
        args.append(value)
        op = {"gte": ">=", "lte": "<="}.get(operator, "=")
        clauses.append(f"business_read.{column} {op} ${len(args)}")
    if req.search:
        if not spec.search:
            raise HTTPException(status_code=400, detail=f"search unsupported for {req.dataset}")
        args.append(f"%{req.search}%")
        clauses.append("(" + " OR ".join(
            f"CAST(business_read.{column} AS TEXT) ILIKE ${len(args)}" for column in spec.search
        ) + ")")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    base = f"SELECT * FROM ({spec.sql}) AS business_read{where}"
    count_sql = f"SELECT count(*) FROM ({base}) AS business_read_count"
    args.extend((req.limit, req.offset))
    sql = f"{base} ORDER BY {spec.order_by} LIMIT ${len(args)-1} OFFSET ${len(args)}"
    return sql, args, count_sql


@router.get("/datasets")
async def list_business_datasets(authorization: str | None = Header(default=None)):
    consumer = _service_consumer(authorization)
    return {"consumer": consumer, "datasets": list(_DATASETS)}


@router.post("/query")
async def query_business_data(body: BusinessReadRequest, authorization: str | None = Header(default=None)):
    consumer = _service_consumer(authorization)
    sql, args, count_sql = _query(body)
    query_args = args[:-2]
    async with db_conn() as conn, conn.transaction(readonly=True):
        rows = await conn.fetch(sql, *args)
        total = await conn.fetchval(count_sql, *query_args) if body.include_total else None
    return {
        "dataset": body.dataset, "consumer": consumer, "authority": "core",
        "read_only": True, "offset": body.offset, "limit": body.limit,
        "count": len(rows), "total": total, "rows": [dict(row) for row in rows],
        "human_disclosure": "requires_server_authorization",
    }


class AdminFullReadRequest(BaseModel):
    """Verified-Admin business read with transparent internal chunking."""
    model_config = ConfigDict(extra="forbid")
    dataset: Dataset
    filters: dict[str, str | int | bool | date] = Field(default_factory=dict)
    search: str | None = Field(default=None, max_length=500)


_SECRET_KEYS = frozenset({
    "api_key", "apikey", "password", "passwd", "secret", "token",
    "bearer", "authorization", "session_cookie", "private_key",
    "database_password",
})


def _scrub_admin_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[credential excluded]" if str(key).lower() in _SECRET_KEYS
            else _scrub_admin_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_admin_values(item) for item in value]
    return value


async def _verify_bridge2_admin() -> str:
    """Server-side Admin gate for complete reads; never trusts the caller text."""
    admin_phone = get_settings().admin_bridge2_number
    if not admin_phone:
        raise HTTPException(status_code=503, detail="Bridge2 Admin identity is not configured")
    from modules import rbac
    permission = await rbac.check_permission(phone=admin_phone, command="hermes_relay")
    if not permission.get("allowed"):
        raise HTTPException(status_code=403, detail="Verified Admin authorization required")
    return admin_phone


def _admin_kb_query(body: AdminFullReadRequest) -> tuple[str, list[Any], str]:
    """Build the Admin-only complete KB query.

    Normal business reads keep the historical active-only KB contract. This
    separate fixed query is reached only after _verified_admin() in the
    admin-full-read route, so inactive records become visible to Admin
    without widening public or observer retrieval.
    """
    allowed = {"category", "status"}
    clauses: list[str] = []
    args: list[Any] = []
    for name, value in body.filters.items():
        if name not in allowed:
            raise HTTPException(status_code=400, detail="unsupported filter for kb_policies")
        if name == "status":
            if isinstance(value, bool):
                parsed = value
            elif str(value).lower() in {"true", "1", "active"}:
                parsed = True
            elif str(value).lower() in {"false", "0", "inactive", "historical"}:
                parsed = False
            else:
                raise HTTPException(status_code=400, detail="invalid KB status filter")
            args.append(parsed)
            clauses.append("k.is_active = $" + str(len(args)))
        else:
            args.append(value)
            clauses.append("k.category = $" + str(len(args)))
    if body.search:
        args.append("%" + body.search + "%")
        n = len(args)
        clauses.append(
            "(k.key ILIKE $" + str(n) + " OR k.category ILIKE $" + str(n)
            + " OR array_to_string(k.trigger_keywords, ' ') ILIKE $" + str(n)
            + " OR k.reply_text ILIKE $" + str(n) + ")"
        )
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    base = f"""
        SELECT k.*,
               'database'::text AS source_kind,
               k.id AS source_record_id,
               CASE WHEN k.is_active THEN 'current' ELSE 'historical' END AS lifecycle_status,
               (NOT k.is_active) AS is_historical,
               to_jsonb(k)->>'version' AS source_version,
               COALESCE(to_jsonb(k)->>'effective_date', to_jsonb(k)->>'valid_from') AS effective_date,
               to_jsonb(k)->>'superseded_by' AS superseded_by,
               k.category || ':' || k.key AS conflict_group_key
          FROM fazle_knowledge_base k{where}
    """
    count_sql = f"SELECT count(*) FROM ({base}) AS admin_kb_count"
    args.extend((5000, 0))
    sql = (
        f"{base} ORDER BY k.is_active DESC, k.category, k.key, k.id "
        + "LIMIT $" + str(len(args)-1) + " OFFSET $" + str(len(args))
    )
    return sql, args, count_sql


@router.post("/admin-full-read")
async def query_admin_full_read(body: AdminFullReadRequest, authorization: str | None = Header(default=None)):
    """Return all matching rows to a verified Admin using fixed SQL."""
    consumer = _service_consumer(authorization)
    report_type = "conversation_anomalies" if body.dataset == "conversation_history" else "employee_ledger"
    admin_phone = await _verify_bridge2_admin()
    if body.dataset == "kb_policies":
        sql, args, count_sql = _admin_kb_query(body)
        async with db_conn() as conn, conn.transaction(readonly=True):
            query_total = await conn.fetchval(count_sql, *args[:-2])
            rows = [dict(row) for row in await conn.fetch(sql, *args)]
        return {
            "dataset": body.dataset, "consumer": consumer, "authority": "core",
            "read_only": True, "complete": True, "count": len(rows),
            "total": int(query_total or 0), "rows": _scrub_admin_values(rows),
            "knowledge_scope": "database_all_lifecycle_states",
            "human_disclosure": "verified_admin",
            "reply_to": {"phone": admin_phone, "channel": "bridge2"},
        }
    probe = BusinessReadRequest(
        dataset=body.dataset, filters=body.filters, search=body.search,
        limit=1, offset=0, include_total=True,
    )
    _, probe_args, count_sql = _query(probe)
    async with db_conn() as conn, conn.transaction(readonly=True):
        query_total = await conn.fetchval(count_sql, *probe_args[:-2])
        rows = []
        page_size = 5000
        offset = 0
        while offset < int(query_total or 0):
            page = BusinessReadRequest(
                dataset=body.dataset, filters=body.filters, search=body.search,
                limit=page_size, offset=offset, include_total=False,
            )
            sql, args, _ = _query(page)
            rows.extend(dict(row) for row in await conn.fetch(sql, *args))
            offset += page_size
    if body.dataset == "conversation_history":
        # The fixed SQL paginates newest-first for stable offsets; the
        # complete Admin reconstruction is returned oldest-first so adjacent
        # inbound/outbound links read in natural conversation order.
        rows.reverse()
    return {
        "dataset": body.dataset, "consumer": consumer, "authority": "core",
        "read_only": True, "complete": True, "count": len(rows),
        "total": int(query_total or 0), "rows": _scrub_admin_values(rows),
        "reconstruction_order": "oldest_first" if body.dataset == "conversation_history" else None,
        "human_disclosure": "verified_admin",
        "reply_to": {"phone": admin_phone, "channel": "bridge2"},
    }
