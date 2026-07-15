"""
Unit tests for new FPE Payroll/Review Queue business rules.

Uses mocked DB helpers — no real database connection required.
"""
from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from modules.fazle_payroll_engine.validation import (
    validate_for_accounting,
    MESSAGE_RULES,
    get_rule,
)
from modules.fazle_payroll_engine.workers import _eligible_for_accounting_review, _should_skip_payroll_parse
from modules.fazle_payroll_engine.employee import (
    create_employee_manual,
    match_or_create_employee,
    _is_valid_human_name,
)
from modules.fazle_payroll_engine.reconcile import compute_reconciliation
from modules.fazle_payroll_engine.routes import (
    _cleanup_stale_review_queue_rows,
    _mark_stale_employees_inactive,
    _review_queue_candidate_sql,
)
from modules.fazle_payroll_engine.parser import parse_message


# ── _extract_name() line-layout regression (2026-07-10 silent-drop incident) ─

class TestExtractNameLineLayouts:
    """
    2026-07-10: 8 real admin->accountant payment instructions were silently
    dropped because _extract_name() only found a name when it shared a line
    with the payout phone. When "ID:<phone> <Name>" sits on one line and
    "<payout>(method) amount/-" sits on the next, the name was lost. These
    are the real messages that failed, plus the layouts from
    payment_business_rules.md / the Payment Parser Canonical Spec.
    """

    def test_id_and_name_together_payout_on_next_line(self):
        text = "ID:01805668850 Junaid Islam Shami\n01773055387(B) 500/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Junaid Islam Shami"
        assert r.payment.payout_phone == "01773055387"
        assert r.payment.amount == Decimal("500")

    def test_id_and_name_together_trailing_space_before_newline(self):
        text = "ID:01625219710 Oshama Karim \n01625219710(N) 200/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Oshama Karim"

    def test_id_with_space_before_colon(self):
        text = "ID : 01811528446 Washim Akram \n01811528446(N) 2500/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Washim Akram"

    def test_same_id_and_payout_prefix_different_suffix(self):
        text = "ID: 01624874043 Md Alamgir \n01848600028(B) 400/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Md Alamgir"
        assert r.payment.payout_phone == "01848600028"

    def test_repeated_employee_different_payout_numbers(self):
        first = parse_message(
            "ID:01823265742 Ridoyan Khan Rida\n01940125166(B) 4500/-", date(2026, 7, 10)
        )
        second = parse_message(
            "ID:01823265742 Ridoyan Khan Rida\n01831697307(B) 500/-", date(2026, 7, 10)
        )
        assert first.payment.employee_name_raw == "Ridoyan Khan Rida"
        assert second.payment.employee_name_raw == "Ridoyan Khan Rida"
        assert first.payment.payout_phone != second.payment.payout_phone

    # ── Regression: previously-working layouts must remain unaffected ───────

    def test_single_line_unaffected(self):
        text = "ID: 01710730696 Md. Obaidur Rahman 01707273498(B) 4500/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Md. Obaidur Rahman"

    def test_id_only_line_then_name_and_payout_together_unaffected(self):
        text = "ID: 01786178090\nAnis- 01786178090(B) 3000/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Anis"

    def test_three_line_id_name_payout_amount_unaffected(self):
        text = "Id: 01871796723\nSumir 01871796723 (B) \n200/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Sumir"

    def test_dash_separator_no_id_label_unaffected(self):
        text = "Debashish SG - 01891969914 ( N )  = 3000/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Debashish SG"

    def test_cash_method_word_unaffected(self):
        text = "ID: 01710730696 Md. Obaidur Rahman 01710730696(Cash) 1530/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Md. Obaidur Rahman"
        assert r.payment.payout_method.value == "cash"

    def test_no_id_prefix_payout_becomes_lookup_key(self):
        """payment_business_rules.md: without ID:, payout mobile is the lookup key."""
        text = "Manik Mea 01789123456(N) 200/-"
        r = parse_message(text, date(2026, 7, 10))
        assert r.payment.employee_name_raw == "Manik Mea"
        assert r.payment.payout_phone == "01789123456"
        assert r.payment.employee_id_phone == "01789123456"


# ── Pure validation / eligibility rules ──────────────────────────────────────

class TestReviewQueueEligibility:
    """Incomplete parsed items must not enter the Review Queue."""

    def test_missing_name_still_eligible(self):
        """
        A name-extraction miss must never make a payment-shaped message
        disappear (2026-07-10 silent-drop incident). Mobile-based lookup is
        the authoritative employee identity (payment_business_rules.md),
        so amount + payout_phone + payout_method alone is enough to queue
        the item for review even when no name was resolved.
        """
        pdata = {
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "5000",
        }
        assert _eligible_for_accounting_review(pdata)

    def test_missing_payout_phone_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_method": "bkash",
            "amount": "5000",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_missing_payout_method_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "amount": "5000",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_missing_amount_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_unknown_method_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "unknown",
            "amount": "5000",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_non_positive_amount_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "0",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_complete_item_enters_review_queue(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "5000",
        }
        assert _eligible_for_accounting_review(pdata)


class TestReviewQueueCandidateSql:
    """
    _review_queue_candidate_sql() gates which fpe_unmatched_messages rows are
    visible in /admin/needs-review, /admin/review-summary, and the payroll
    page review UI. It must stay in lockstep with _eligible_for_accounting_review
    (workers.py) — a row that clears one gate but not the other is a row that
    gets silently written but never shown, which is the same class of bug as
    the 2026-07-10 incident, just one layer further down the pipeline.
    """

    def test_does_not_require_employee_name(self):
        sql = _review_queue_candidate_sql("u")
        assert "detected_employee_name" not in sql

    def test_still_requires_amount_phone_and_known_method(self):
        sql = _review_queue_candidate_sql("u")
        assert "u.detected_amount IS NOT NULL" in sql
        assert "u.detected_payout_phone" in sql
        assert "u.detected_payout_method" in sql
        assert "'unknown'" in sql


class TestValidationForAccounting:
    """validate_for_accounting enforces per-type field requirements."""

    def test_payment_missing_each_field(self):
        rule = get_rule("payment")
        assert not validate_for_accounting("payment", {}).valid
        assert not validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
        }).valid
        assert not validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "amount": "5000",
        }).valid

    def test_payment_complete_is_valid(self):
        result = validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "5000",
        })
        assert result.valid

    def test_amount_non_positive_rejected(self):
        result = validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "0",
        })
        assert not result.valid

    def test_escort_payment_empty_entries_rejected(self):
        assert not validate_for_accounting("escort_payment", {"entries": []}).valid

    def test_escort_payment_valid_entries_accepted(self):
        result = validate_for_accounting("escort_payment", {
            "entries": [{"name": "A", "amount": "100"}, {"name": "B", "amount": "200"}]
        })
        assert result.valid


# ── Employee name / creation rules ───────────────────────────────────────────

class TestEmployeeNameValidation:
    """Name helpers reject invalid/placeholder identities."""

    def test_rejects_empty(self):
        assert not _is_valid_human_name(None)
        assert not _is_valid_human_name("")
        assert not _is_valid_human_name("   ")

    def test_rejects_short(self):
        assert not _is_valid_human_name("A")

    def test_rejects_placeholders(self):
        for p in ["unknown", "unnamed", "none", "n/a", "na"]:
            assert not _is_valid_human_name(p), f"should reject {p!r}"

    def test_rejects_pure_numeric(self):
        assert not _is_valid_human_name("01712345678")
        assert not _is_valid_human_name("+8801712345678")

    def test_rejects_no_letters(self):
        assert not _is_valid_human_name("-----")
        assert not _is_valid_human_name("()()()")

    def test_accepts_valid_names(self):
        assert _is_valid_human_name("Jakir Hossain")
        assert _is_valid_human_name("মোঃ আল মোমিন")
        assert _is_valid_human_name("Md. Jakir")


class TestPayrollParseGate:
    """Bridge1 review-only messages must not reach the payroll parser."""

    def test_bridge1_attendance_draft_is_blocked(self):
        row = {
            "source": "bridge1",
            "chat_jid": "8801880446111@s.whatsapp.net",
            "sender_phone": "8801958122300",
        }
        blocked, reason = _should_skip_payroll_parse(
            row,
            "Attendance draft\nপ্রেরক: 8801733364777\nAPPROVE / REJECT",
        )
        assert blocked
        assert reason in {"review_only_marker", "non_payroll_or_unauthorized_source"}

    def test_allowlisted_sync_chat_is_not_blocked(self):
        row = {
            "source": "bridge2",
            "chat_jid": "8801844836824@s.whatsapp.net",
            "sender_phone": "8801880446111",
        }
        blocked, reason = _should_skip_payroll_parse(
            row,
            "Jakir 01725494969(N) 2200/-",
        )
        assert not blocked
        assert reason == ""


class TestCreateEmployeeManual:
    """create_employee_manual enforces existing-table-only inserts."""

    @patch("modules.fazle_payroll_engine.employee.fetch_one", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.employee.db_conn")
    async def test_creates_with_expected_columns(self, mock_db_conn, mock_fetch_one):
        # mock_fetch_one is the module-level fetch_one called after the transaction
        mock_fetch_one.return_value = {"id": 1, "full_name": "Test User",
                                       "primary_phone": "01712345678",
                                       "employee_id_phone": "01712345678",
                                       "employee_code": "EMP-00001",
                                       "status": "active", "department": "Staff",
                                       "created_source": "admin_manual_create"}

        class FakeTransaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeConn:
            def __init__(self):
                self.execute_calls = []
                self.fetchval_calls = []

            def transaction(self):
                return FakeTransaction()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql, *a, **kw):
                self.execute_calls.append(sql)

            async def fetchrow(self, sql, *a, **kw):
                # Simulate no existing employee
                return None

            async def fetchval(self, sql, *a, **kw):
                self.fetchval_calls.append(sql)
                return 1

        fake_conn = FakeConn()
        mock_db_conn.return_value = fake_conn

        emp = await create_employee_manual(
            full_name="Test User",
            employee_mobile="01712345678",
            role_or_type="Staff",
            status="active",
        )
        assert emp["id"] == 1
        # INSERT INTO fpe_employees is done via con.fetchval (RETURNING id)
        assert any("INSERT INTO fpe_employees" in sql for sql in fake_conn.fetchval_calls)
        # No ALTER TABLE should ever be issued
        assert not any("ALTER TABLE" in sql for sql in fake_conn.execute_calls)
        assert not any("ALTER TABLE" in sql for sql in fake_conn.fetchval_calls)


# ── Reconciliation parser-total-availability rule ─────────────────────────────

class TestReconcileParserTotalAvailability:
    """Delta must be None (not misleading) when parser total is unavailable."""

    @patch("modules.fazle_payroll_engine.reconcile.fetch_one", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.reconcile.fetch_val", new_callable=AsyncMock)
    async def test_delta_none_when_no_parser_data(self, mock_fetch_val, mock_fetch_one):
        mock_fetch_one.return_value = None
        mock_fetch_val.return_value = Decimal("0")

        result = await compute_reconciliation(period="2026-05")
        assert result["delta"] is None
        assert result["parser_total_available"] is False
        assert result["ok"] is None

    @patch("modules.fazle_payroll_engine.reconcile.fetch_one", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.reconcile.fetch_val", new_callable=AsyncMock)
    async def test_delta_calculated_when_parser_data_present(self, mock_fetch_val, mock_fetch_one):
        parser_row = {"parser_sum": Decimal("10000"), "parser_count": 5}
        mock_fetch_one.return_value = parser_row
        # side_effect: ledger_sum, unmatched_sum, pending_review, dlq_count
        mock_fetch_val.side_effect = [Decimal("9500"), Decimal("500"), 0, 0]

        result = await compute_reconciliation(period="2026-05")
        assert result["delta"] is not None
        assert result["parser_total_available"] is True


# ── Review queue cleanup rule ─────────────────────────────────────────────────

class TestReviewQueueCleanup:
    """Hard-deletes only stale unmatched rows, never touches ledger."""

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.fetch_val", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.execute", new_callable=AsyncMock)
    async def test_deletes_only_stale_unmatched(self, mock_execute, mock_fetch_val, mock_fetch_all):
        cutoff = datetime.utcnow() - timedelta(hours=24)
        mock_fetch_all.return_value = [{"id": 101}, {"id": 102}]
        mock_fetch_val.return_value = 1

        result = await _cleanup_stale_review_queue_rows()

        assert result["deleted_reviews"] == 2
        sqls = [c.args[0] for c in mock_execute.call_args_list]
        assert any("DELETE FROM fpe_unmatched_messages" in str(s) for s in sqls)
        assert any("DELETE FROM fpe_review_audit_logs" in str(s) for s in sqls)
        assert not any("fpe_cash_transactions" in str(s) for s in sqls)

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    async def test_noop_when_nothing_stale(self, mock_fetch_all):
        mock_fetch_all.return_value = []

        result = await _cleanup_stale_review_queue_rows()
        assert result["deleted_reviews"] == 0


# ── 90-day inactivity rule ────────────────────────────────────────────────────

class TestMarkStaleEmployeesInactive:
    """Only employees with no payments in 90+ days become inactive."""

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.execute", new_callable=AsyncMock)
    async def test_marks_only_old_employees(self, mock_execute, mock_fetch_all):
        cutoff = date.today() - timedelta(days=90)
        mock_fetch_all.return_value = [
            {"id": 1, "last_payment_date": cutoff - timedelta(days=10)},
            {"id": 2, "last_payment_date": cutoff - timedelta(days=5)},
        ]

        count = await _mark_stale_employees_inactive()
        assert count == 2
        update_sql = mock_execute.call_args[0][0]
        assert "UPDATE fpe_employees SET status = 'inactive'" in update_sql

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.execute", new_callable=AsyncMock)
    async def test_no_inactive_when_all_recently_paid(self, mock_execute, mock_fetch_all):
        cutoff = date.today() - timedelta(days=90)
        mock_fetch_all.return_value = []  # no rows match < cutoff

        count = await _mark_stale_employees_inactive()
        assert count == 0
        assert not mock_execute.called


class TestRequireAdminRole:
    """
    Section 18.3 — promote/dismiss/DLQ-requeue/salary endpoints must require
    admin/superadmin specifically, not just "any active admin account with
    any role" (which is all _require_api_key alone checked for).
    """

    async def test_non_admin_role_rejected(self):
        from fastapi import HTTPException
        from modules.fazle_payroll_engine.routes import _require_admin_role

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": False, "reason": "need admin (level 3) have level 1"}),
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_admin_role("fake-viewer-key")
        assert exc.value.status_code == 403

    async def test_admin_role_accepted(self):
        from modules.fazle_payroll_engine.routes import _require_admin_role

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": True}),
        ):
            result = await _require_admin_role("fake-admin-key")
        assert result == "fake-admin-key"

    async def test_internal_key_accepted(self):
        from modules.fazle_payroll_engine.routes import _require_admin_role
        from app.config import get_settings

        internal_key = get_settings().internal_api_key
        result = await _require_admin_role(internal_key)
        assert result == internal_key


class TestFazlePayrollEngineTierGates:
    """
    Full RBAC audit of fazle_payroll_engine/routes.py: read-only listing ->
    viewer, operator submissions -> operator, payment-review annotations ->
    accountant, approve/edit/delete/salary -> admin. Each gate reuses the
    existing rbac.check_permission level hierarchy (viewer=10 < operator=30
    < accountant=50 < admin=70 <= superadmin=100), not a hand-rolled role list.
    """

    async def test_viewer_gate_rejects_below_viewer(self):
        from fastapi import HTTPException
        from modules.fazle_payroll_engine.routes import _require_viewer_or_above

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": False}),
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_viewer_or_above("fake-key")
        assert exc.value.status_code == 403

    async def test_viewer_gate_accepts_viewer(self):
        from modules.fazle_payroll_engine.routes import _require_viewer_or_above

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": True}),
        ):
            result = await _require_viewer_or_above("fake-viewer-key")
        assert result == "fake-viewer-key"

    async def test_operator_gate_rejects_viewer_only(self):
        from fastapi import HTTPException
        from modules.fazle_payroll_engine.routes import _require_operator_or_above

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": False}),
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_operator_or_above("fake-viewer-key")
        assert exc.value.status_code == 403

    async def test_operator_gate_accepts_operator(self):
        from modules.fazle_payroll_engine.routes import _require_operator_or_above

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": True}),
        ):
            result = await _require_operator_or_above("fake-operator-key")
        assert result == "fake-operator-key"

    async def test_accountant_gate_rejects_operator_only(self):
        from fastapi import HTTPException
        from modules.fazle_payroll_engine.routes import _require_accountant_or_above

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": False}),
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_accountant_or_above("fake-operator-key")
        assert exc.value.status_code == 403

    async def test_accountant_gate_accepts_accountant(self):
        from modules.fazle_payroll_engine.routes import _require_accountant_or_above

        with patch(
            "modules.rbac.check_permission",
            new=AsyncMock(return_value={"allowed": True}),
        ):
            result = await _require_accountant_or_above("fake-accountant-key")
        assert result == "fake-accountant-key"

    async def test_all_three_gates_accept_internal_key(self):
        from app.config import get_settings
        from modules.fazle_payroll_engine.routes import (
            _require_viewer_or_above,
            _require_operator_or_above,
            _require_accountant_or_above,
        )

        internal_key = get_settings().internal_api_key
        for gate in (_require_viewer_or_above, _require_operator_or_above, _require_accountant_or_above):
            assert await gate(internal_key) == internal_key

    async def test_only_ingest_remains_on_weak_gate(self):
        """
        /ingest is a system/webhook endpoint (bridge infrastructure posting
        messages), not a human RBAC action -- it's the sole deliberate
        exception left on _require_api_key (any active admin account, no
        role-level check) after the full endpoint audit.
        """
        import inspect
        from modules.fazle_payroll_engine import routes as fpe_routes

        source = inspect.getsource(fpe_routes)
        # Every dependencies=[Depends(_require_api_key)] left in the file
        # must be immediately followed by the /ingest route registration.
        import re
        for m in re.finditer(r'dependencies=\[Depends\(_require_api_key\)\]', source):
            line_start = source.rfind("\n", 0, m.start()) + 1
            line_end = source.find("\n", m.end())
            line = source[line_start:line_end]
            assert '"/ingest"' in line, f"unexpected weak gate: {line}"


class TestWbomCrossLookupStatusCase:
    """
    2026-07-15 fix: the WBOM cross-lookup in match_or_create_employee() compared
    `status != 'inactive'` (lowercase literal) against wbom_employees.status,
    which is stored Title-case ('Active'/'Inactive'). Postgres text comparison
    is case-sensitive, so the filter was a permanent no-op -- Inactive WBOM
    employees were never excluded. Fixed to `LOWER(status) != 'inactive'`.
    """

    async def test_inactive_wbom_employee_excluded_from_cross_lookup(
        self, test_db_pool, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_employees SET status='Inactive' WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
            fpe_row = await conn.fetchrow(
                """
                INSERT INTO fpe_employees
                    (employee_code, full_name, name_normalized, primary_phone,
                     status)
                VALUES ('EMP-CROSSLOOKUP', $1, $2, $3, 'active')
                RETURNING id
                """,
                seed_employee["employee_name"],
                seed_employee["employee_name"].lower().strip(),
                seed_employee["employee_mobile"],
            )

        result = await match_or_create_employee(
            name_raw=None,
            payout_phone=None,
            employee_id_phone=seed_employee["employee_mobile"],
        )

        # Must resolve to the existing FPE employee (correct identity), but
        # NOT via the wbom_phone_match path -- that path must treat this
        # Inactive WBOM employee as excluded, same as an active-status one
        # that simply has no WBOM counterpart at all.
        assert result.employee_id == fpe_row["id"]
        assert result.match_type != "wbom_phone_match"
