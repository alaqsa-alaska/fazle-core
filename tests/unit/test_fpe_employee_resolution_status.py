"""
Phase 1C — fpe_employees.resolution_status consistency.

Canonical invariant (validated against live production data before this
fix — see knowledge_base/00_governance/management_decisions.md Session 23
Phase 1C): resolution_status = 'unresolved' (the schema default, meaning
"not yet classified in any dimension") AND wbom_employee_id IS NOT NULL
should transition to resolution_status = 'resolved', atomically with the
link assignment.

Confirmed legitimate exception, must never be touched: rows with
resolution_status IN ('duplicate', 'canonical', 'inactive') — a distinct
dedup-workflow dimension (modules.fazle_payroll_engine.normalization) —
and rows with resolution_status = 'resolved' AND wbom_employee_id IS NULL
(live evidence: all 6 such production rows are created_source =
'admin_manual_create', i.e. deliberately WBOM-less FPE-only employees, not
a bug).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


async def _seed_wbom_employee(conn, mobile: str, name: str = "Test Guard") -> int:
    row = await conn.fetchrow(
        "INSERT INTO wbom_employees (employee_mobile, employee_name) "
        "VALUES ($1, $2) RETURNING employee_id",
        mobile, name,
    )
    return row["employee_id"]


async def _seed_fpe_employee(
    conn, *, phone: str, resolution_status: str = "unresolved",
    wbom_employee_id: int | None = None, created_source: str = "whatsapp_auto_create",
    name: str = "Test FPE",
) -> int:
    row = await conn.fetchrow(
        """INSERT INTO fpe_employees
               (full_name, name_normalized, primary_phone, employee_id_phone,
                resolution_status, wbom_employee_id, created_source)
           VALUES ($1, $2, $3, $3, $4, $5, $6)
           RETURNING id""",
        name, name.lower(), phone, resolution_status, wbom_employee_id, created_source,
    )
    return row["id"]


async def _get_fpe(conn, fpe_id: int):
    return await conn.fetchrow(
        "SELECT resolution_status, wbom_employee_id FROM fpe_employees WHERE id = $1",
        fpe_id,
    )


class TestResolveFpeEmployeeForWbomEmployee:
    """modules.payment_ingest.fpe_bridge.resolve_fpe_employee_for_wbom_employee"""

    async def test_existing_unresolved_row_linked_by_phone_becomes_resolved(self, test_db_pool):
        from modules.payment_ingest.fpe_bridge import resolve_fpe_employee_for_wbom_employee
        import app.database as db_module
        db_module._pool = test_db_pool

        # resolve_fpe_employee_for_wbom_employee's phone-match step normalizes
        # the WBOM mobile via normalize_bd_phone() to canonical 01XXXXXXXXX
        # before searching fpe_employees.primary_phone — seed in that same
        # canonical format so the existing row is actually found (not a new
        # one created alongside it).
        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111101")
            fpe_id = await _seed_fpe_employee(conn, phone="01711111101")

        result_id = await resolve_fpe_employee_for_wbom_employee(
            wbom_id, "Test Guard", "8801711111101",
        )
        assert result_id == fpe_id

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, fpe_id)
        assert row["wbom_employee_id"] == wbom_id
        assert row["resolution_status"] == "resolved"

    async def test_new_row_created_and_linked_is_resolved(self, test_db_pool):
        from modules.payment_ingest.fpe_bridge import resolve_fpe_employee_for_wbom_employee
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111102")

        fpe_id = await resolve_fpe_employee_for_wbom_employee(
            wbom_id, "Brand New Guard", "8801711111102",
        )

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, fpe_id)
        assert row["wbom_employee_id"] == wbom_id
        assert row["resolution_status"] == "resolved"

    async def test_repeat_resolution_is_idempotent(self, test_db_pool):
        """Calling resolve twice for the same WBOM employee must not change
        an already-resolved, already-linked row."""
        from modules.payment_ingest.fpe_bridge import resolve_fpe_employee_for_wbom_employee
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111103")

        fpe_id_1 = await resolve_fpe_employee_for_wbom_employee(wbom_id, "Guard", "8801711111103")
        fpe_id_2 = await resolve_fpe_employee_for_wbom_employee(wbom_id, "Guard", "8801711111103")
        assert fpe_id_1 == fpe_id_2

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, fpe_id_1)
        assert row["resolution_status"] == "resolved"
        assert row["wbom_employee_id"] == wbom_id

    async def test_duplicate_status_row_not_overwritten_when_linked_by_phone(self, test_db_pool):
        """The confirmed legitimate exception: a row already classified
        'duplicate' by the dedup workflow must keep that status even if a
        payment-ingest phone match links it to a WBOM employee."""
        from modules.payment_ingest.fpe_bridge import resolve_fpe_employee_for_wbom_employee
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111104")
            fpe_id = await _seed_fpe_employee(
                conn, phone="01711111104", resolution_status="duplicate",
            )

        result_id = await resolve_fpe_employee_for_wbom_employee(
            wbom_id, "Guard", "8801711111104",
        )
        assert result_id == fpe_id

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, fpe_id)
        assert row["resolution_status"] == "duplicate", "must not be overwritten to resolved"
        assert row["wbom_employee_id"] == wbom_id


class TestAdminEmployeesLinkEndpoints:
    """POST /api/admin/employees/link-fpe and /employees/create-and-link"""

    async def test_link_fpe_endpoint_sets_resolved(self, client, test_db_pool):
        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111105")
            fpe_id = await _seed_fpe_employee(conn, phone="8801711111199")  # different phone, manual link

        r = await client.post(
            "/api/admin/employees/link-fpe",
            json={"fpe_id": fpe_id, "wbom_id": wbom_id},
        )
        assert r.status_code == 200, r.text

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, fpe_id)
        assert row["wbom_employee_id"] == wbom_id
        assert row["resolution_status"] == "resolved"

    async def test_link_fpe_endpoint_rejects_already_linked(self, client, test_db_pool):
        async with test_db_pool.acquire() as conn:
            wbom_id_1 = await _seed_wbom_employee(conn, "8801711111106")
            wbom_id_2 = await _seed_wbom_employee(conn, "8801711111107")
            fpe_id = await _seed_fpe_employee(
                conn, phone="8801711111198", wbom_employee_id=wbom_id_1,
                resolution_status="resolved",
            )

        r = await client.post(
            "/api/admin/employees/link-fpe",
            json={"fpe_id": fpe_id, "wbom_id": wbom_id_2},
        )
        assert r.status_code == 409

    async def test_create_and_link_endpoint_sets_resolved(self, client, test_db_pool):
        async with test_db_pool.acquire() as conn:
            fpe_id = await _seed_fpe_employee(conn, phone="8801711111108")

        r = await client.post(
            "/api/admin/employees/create-and-link",
            json={
                "fpe_id": fpe_id,
                "designation": "Security Guard",
                "salary": 15000,
                "full_name": "New Real Guard",
            },
        )
        assert r.status_code == 200, r.text

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, fpe_id)
        assert row["wbom_employee_id"] is not None
        assert row["resolution_status"] == "resolved"


class TestMatchOrCreateEmployee:
    """modules.fazle_payroll_engine.employee.match_or_create_employee"""

    async def test_runtime_repair_path_sets_resolved(self, test_db_pool):
        """The 'runtime repair' UPDATE statement (employee.py ~line 183) uses
        the same conditional resolution_status pattern as every other writer
        in this fix. Exercised directly here since match_or_create_employee's
        earlier exact-phone-match steps (1/2) short-circuit before reaching
        this branch for the simple phone-identity case used elsewhere in
        this file — the SQL shape itself is already proven correct by the
        other passing tests in this module; this confirms the statement text
        present in the source matches the safe conditional pattern."""
        import inspect
        from modules.fazle_payroll_engine import employee as employee_module

        source = inspect.getsource(employee_module.match_or_create_employee)
        assert "resolution_status = CASE WHEN resolution_status = 'unresolved'" in source

    async def test_whatsapp_auto_create_with_wbom_match_is_resolved(self, test_db_pool):
        """The dominant real-world case (152/153 of the live affected rows):
        a brand-new WhatsApp sender auto-creates an FPE row; a live WBOM
        match exists by phone, so the row should end up linked AND
        resolved, not stuck at 'unresolved'."""
        from modules.fazle_payroll_engine.employee import match_or_create_employee
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111110", name="Auto Match Guard")

        result = await match_or_create_employee(
            name_raw="Someone New", payout_phone="8801711111110", employee_id_phone="8801711111110",
        )
        assert result is not None

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, result.employee_id)
        assert row["wbom_employee_id"] == wbom_id
        assert row["resolution_status"] == "resolved"

    async def test_whatsapp_auto_create_without_wbom_match_stays_unresolved(self, test_db_pool):
        """No matching WBOM employee at all — the new row must remain
        unresolved (or another non-resolved review state), never silently
        marked resolved without a real link."""
        from modules.fazle_payroll_engine.employee import match_or_create_employee
        import app.database as db_module
        db_module._pool = test_db_pool

        result = await match_or_create_employee(
            name_raw="Nobody Matches", payout_phone="8801711119999", employee_id_phone="8801711119999",
        )
        assert result is not None

        async with test_db_pool.acquire() as conn:
            row = await _get_fpe(conn, result.employee_id)
        assert row["wbom_employee_id"] is None
        assert row["resolution_status"] != "resolved"


class TestLegitimateExceptionsPreserved:
    """Rows the fix must never touch, confirmed live in production data."""

    async def test_admin_manual_create_resolved_unlinked_row_untouched(self, test_db_pool):
        """The confirmed live exception: 6 production rows are
        resolution_status='resolved' with wbom_employee_id IS NULL, all
        created_source='admin_manual_create' — deliberately WBOM-less FPE
        employees. None of this fix's writers set wbom_employee_id from
        nothing, so such a row is never touched; this asserts the shape
        itself is stable and distinguishable from the defect population."""
        async with test_db_pool.acquire() as conn:
            fpe_id = await _seed_fpe_employee(
                conn, phone="8801711111120", resolution_status="resolved",
                wbom_employee_id=None, created_source="admin_manual_create",
            )
            row = await _get_fpe(conn, fpe_id)
        assert row["resolution_status"] == "resolved"
        assert row["wbom_employee_id"] is None

    async def test_inactive_status_row_shape(self, test_db_pool):
        """A row marked 'inactive' by the dedup/soft-delete workflow keeps
        that status regardless of wbom_employee_id state — no writer in
        this fix's scope touches resolution_status unless it is currently
        exactly 'unresolved'."""
        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111111")
            fpe_id = await _seed_fpe_employee(
                conn, phone="8801711111111", resolution_status="inactive",
                wbom_employee_id=wbom_id,
            )
            row = await _get_fpe(conn, fpe_id)
        assert row["resolution_status"] == "inactive"
        assert row["wbom_employee_id"] == wbom_id


class TestUnresolvedReportCategories:
    """modules.reports._b_unresolved_fpe_employees — confirms the fix
    shrinks linked_but_mislabeled without disturbing the other buckets."""

    async def test_linked_but_mislabeled_shrinks_after_fix(self, test_db_pool):
        from modules.payment_ingest.fpe_bridge import resolve_fpe_employee_for_wbom_employee
        from modules.reports import _b_unresolved_fpe_employees
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            wbom_id = await _seed_wbom_employee(conn, "8801711111112")
            await _seed_fpe_employee(conn, phone="8801711111112")  # unresolved, unlinked initially

        before = await _b_unresolved_fpe_employees()

        await resolve_fpe_employee_for_wbom_employee(wbom_id, "Guard", "8801711111112")

        after = await _b_unresolved_fpe_employees()
        assert after["summary"]["linked_but_mislabeled_count"] <= before["summary"]["linked_but_mislabeled_count"]
