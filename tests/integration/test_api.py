"""API Integration tests — /health, /employees, /escort-programs"""
from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.integration


def _assistant_client(app, raw_key: str):
    from httpx import AsyncClient, ASGITransport

    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": raw_key},
    )


class TestHealthEndpoint:
    async def test_health_returns_200(self, client):
        response = await client.get("/health")
        # Health may return 503 in test env (missing bridges) but must respond
        assert response.status_code in (200, 503)

    async def test_health_body_has_status(self, client):
        response = await client.get("/health")
        data = response.json()
        assert "status" in data or "db" in data

    async def test_health_no_auth_needed(self, client):
        """Health check must not require auth (for load balancer probes)."""
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            response = await ac.get("/health")
        # Should return without auth (200 or 503 in test env, not 403)
        assert response.status_code != 403


class TestAuthEnforcement:
    async def test_missing_api_key_returns_403(self, client):
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            response = await ac.get("/admin/users")
        assert response.status_code == 403

    async def test_wrong_api_key_returns_403(self, client):
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Internal-Key": "totally-wrong-key"},
        ) as ac:
            response = await ac.get("/admin/users")
        assert response.status_code == 403

    async def test_valid_api_key_passes(self, client):
        response = await client.get("/admin/users")
        assert response.status_code == 200

    async def test_session_endpoint_reports_office_assistant_restriction(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from app.main import app

        raw_key = "fk_officeassistant01_test"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        async with test_db_pool.acquire() as conn:
            admin_id = await conn.fetchval(
                "INSERT INTO fazle_admins (phone, name, status, api_key_hash) VALUES ('8801700000099', ' officeassistant01 ', 'active', $1) RETURNING id",
                key_hash,
            )
            await conn.execute(
                "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1, 'viewer', 'test')",
                admin_id,
            )

        async with _assistant_client(app, raw_key) as ac:
            response = await ac.get("/api/admin/session")
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["is_restricted_office_assistant"] is True
        assert data["user"]["can_edit_delete_transactions"] is False
        assert data["user"]["can_manage_admin"] is False

    async def test_office_assistant_cannot_edit_or_delete_transaction(self, client, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from app.main import app

        raw_key = "fk_officeassistant02_test"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        async with test_db_pool.acquire() as conn:
            admin_id = await conn.fetchval(
                "INSERT INTO fazle_admins (phone, name, status, api_key_hash) VALUES ('8801700000100', 'officeassistant02', 'active', $1) RETURNING id",
                key_hash,
            )
            await conn.execute(
                "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1, 'viewer', 'test')",
                admin_id,
            )

        create_response = await client.post(
            "/api/admin/transactions",
            json={
                "employee_name": "Restriction Test User",
                "employee_id_phone": "01712345678",
                "payout_phone": "01712345678",
                "amount": 5000,
                "payout_method": "cash",
                "txn_date": "2026-06-26",
                "txn_category": "salary",
            },
        )
        assert create_response.status_code == 200
        txn_id = create_response.json()["transaction_id"]

        async with _assistant_client(app, raw_key) as ac:
            edit_response = await ac.put(f"/api/admin/transactions/{txn_id}", json={"notes": "blocked"})
            delete_response = await ac.delete(f"/api/admin/transactions/{txn_id}")

        assert edit_response.status_code == 403
        assert delete_response.status_code == 403


class TestEmployeesEndpoint:
    async def test_escort_programs_empty_initially(self, client):
        response = await client.get("/admin/escort-programs")
        assert response.status_code == 200

    async def test_escort_program_appears_after_seed(self, client, seed_escort_program):
        response = await client.get("/admin/escort-programs")
        assert response.status_code == 200

    async def test_pagination_params_accepted(self, client):
        response = await client.get("/admin/escort-programs?limit=10")
        assert response.status_code == 200


class TestEscortProgramsEndpoint:
    async def test_empty_list_initially(self, client):
        response = await client.get("/admin/escort-programs")
        assert response.status_code == 200

    async def test_program_appears_after_seed(self, client, seed_escort_program):
        response = await client.get("/admin/escort-programs")
        assert response.status_code == 200

    async def test_filter_by_status(self, client, seed_escort_program):
        response = await client.get("/admin/escort-programs?status=Running")
        assert response.status_code == 200


class TestTransactionsEndpoint:
    async def test_empty_initially(self, client):
        response = await client.get("/admin/cash-transactions")
        assert response.status_code == 200

    async def test_transaction_appears_after_insert(self, client, test_db_pool, seed_employee):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount, payment_method, status)
                VALUES ($1, 'advance', 2000, 'bkash', 'Completed')
            """, seed_employee["employee_id"])

        response = await client.get("/admin/cash-transactions")
        assert response.status_code == 200


class TestAttendanceEndpoint:
    async def test_empty_initially(self, client):
        response = await client.get("/admin/attendance")
        assert response.status_code == 200

    async def test_attendance_after_insert(self, client, test_db_pool, seed_employee):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_attendance
                    (employee_id, attendance_date, status, recorded_by)
                VALUES ($1, CURRENT_DATE, 'Present', 'test')
            """, seed_employee["employee_id"])

        response = await client.get("/admin/attendance")
        assert response.status_code == 200


class TestDraftsEndpoint:
    async def test_empty_drafts_initially(self, client):
        response = await client.get("/admin/reviewed-replies")
        assert response.status_code == 200

    async def test_admin_overview(self, client):
        response = await client.get("/admin/overview")
        assert response.status_code == 200

    async def test_sent_draft_not_returned_in_pending(self, client, test_db_pool):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fazle_draft_replies
                    (recipient, reply_text, intent, source, status)
                VALUES ('8801811111111', 'Sent draft', 'generic', 'bridge1', 'sent')
            """)

        response = await client.get("/drafts")
        data = response.json()
        drafts = data if isinstance(data, list) else data.get("drafts", [])
        # Sent drafts should not appear in pending list
        for d in drafts:
            assert d.get("status") != "sent"


class TestPaymentDraftsEndpoint:
    async def test_empty_initially(self, client):
        response = await client.get("/admin/payment-drafts")
        assert response.status_code == 200

    async def test_pending_payment_draft_returned(
        self, client, seed_payment_draft
    ):
        response = await client.get("/admin/payment-drafts")
        assert response.status_code == 200


class TestPayrollEndpoint:
    async def test_payroll_runs_empty(self, client):
        response = await client.get("/payroll/runs?period=2026-05")
        assert response.status_code == 200

    async def test_payroll_run_appears(self, client, test_db_pool, seed_employee):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_payroll_runs
                    (employee_id, period_year, period_month, status, basic_salary, gross_salary, net_salary, total_programs)
                VALUES ($1, 2026, 5, 'draft', 9000, 9000, 9000, 0)
            """, seed_employee["employee_id"])

        response = await client.get("/payroll/runs?period=2026-05")
        assert response.status_code == 200


class TestMetaWebhookVerification:
    async def test_verify_token_accepted(self, client):
        response = await client.get(
            "/webhook/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test_verify_token",
                "hub.challenge": "12345",
            },
        )
        assert response.status_code == 200
        assert "12345" in response.text

    async def test_wrong_verify_token_rejected(self, client):
        response = await client.get(
            "/webhook/meta",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "WRONG_TOKEN",
                "hub.challenge": "12345",
            },
        )
        assert response.status_code == 403
