"""
Regression test — POST /api/fpe/operator/{pending_id}/approve
2026-08-19 (HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md
follow-up, payment-mutation acceptance test): live acceptance testing found
operator_approve() read fpe_unmatched_messages without selecting
promoted_txn_id, then immediately did row.get("promoted_txn_id") -- always
None, so the endpoint returned HTTP 409 ("Pending submission has no linked
pending transaction") for EVERY operator-submitted candidate, unconditionally.

Confirmed via a real production candidate (pending_id=7244, transaction_id=
4326, synthetic sandbox employee 373) before this fix existed: the DB column
was correctly populated by /operator/submit, but the approval route's own
SELECT never fetched it.

Fixing that column alone surfaced a SECOND, adjacent bug in the same
function: the approval audit-log INSERT passed a raw Python dict for the
JSONB after_state column instead of json.dumps()-ing it first (every other
audit insert in this codebase, e.g. accounting.py's own create_transaction(),
already does this correctly). Because the transaction-finalize UPDATE and
the ledger upsert both ran on their own implicit connections before that
crash, a live approval attempt would have left the transaction 'final' and
the ledger incremented with NO audit row and the pending row stuck showing
'operator_pending' forever -- a real, silently inconsistent financial state.

Both are fixed together, plus the four writes (finalize transaction, update
ledger, insert audit row, mark pending row promoted) are now wrapped in one
`async with db_conn() as conn: async with conn.transaction():` block (the
same pattern already used by normalization.py's resolve_to_canonical()) so
they succeed or fail atomically.

This test exercises the real /operator/submit -> /operator/{id}/approve
pipeline end-to-end against the real fetch_one()/execute() DB layer (no
mocking of the SQL itself) and asserts the fixed contract:
  - the endpoint returns 200, not 409
  - the SAME pending transaction is promoted to 'final' (no duplicate)
  - the ledger changes exactly once, reconciling to the approved amount
  - exactly one new approval audit event is written
  - the pending row becomes 'promoted'
  - if the audit insert is forced to fail, ALL earlier financial writes
    in the same call roll back (transaction stays 'pending', ledger
    untouched, pending row stays 'operator_pending')
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def _mock_rbac(monkeypatch):
    """Bypass RBAC exactly like tests/unit/test_phase5_t017_rbac_http.py --
    grant every role check so the test exercises the route's own logic,
    not the RBAC layer (already covered elsewhere)."""
    from modules import rbac

    async def fake_get_admin_by_api_key(_key: str):
        return {"id": 1, "phone": "8801700000099", "status": "active", "name": "test-admin"}

    async def fake_check_permission(*, api_key: str, command: str):
        return {"allowed": True, "admin": {"id": 1, "phone": "8801700000099", "status": "active"}}

    async def fake_record_audit(**_kwargs):
        return None

    monkeypatch.setattr(rbac, "get_admin_by_api_key", fake_get_admin_by_api_key)
    monkeypatch.setattr(rbac, "check_permission", fake_check_permission)
    monkeypatch.setattr(rbac, "record_audit", fake_record_audit)


async def _submit_operator_candidate(test_db_pool, employee_id: int) -> dict:
    """Real POST /api/fpe/operator/submit -- creates a genuine operator_pending
    row + linked pending fpe_cash_transactions row, same as production."""
    import app.database as db_module
    db_module._pool = test_db_pool

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": "test-key"},
    ) as ac:
        resp = await ac.post(
            "/api/fpe/operator/submit",
            json={
                "employee_id": employee_id,
                "amount": "100.00",
                "payout_method": "cash",
                "txn_date": "2026-08-19",
                "txn_category": "salary",
                "notes": "test operator submission",
            },
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
class TestOperatorApprovalFix:
    async def test_approve_returns_200_and_promotes_same_transaction(
        self, test_db_pool, seed_fpe_employee, monkeypatch
    ):
        import app.database as db_module
        from app.database import fetch_one, fetch_all, fetch_val
        db_module._pool = test_db_pool
        _mock_rbac(monkeypatch)

        from app.main import app

        emp_id = seed_fpe_employee["id"]
        submitted = await _submit_operator_candidate(test_db_pool, emp_id)
        pending_id = submitted["pending_id"]
        pending_txn_id = submitted["pending_txn_id"]

        txn_count_before = await fetch_val(
            "SELECT COUNT(*) FROM fpe_cash_transactions WHERE employee_id = $1", emp_id
        )
        audit_count_before = await fetch_val(
            "SELECT COUNT(*) FROM fpe_accounting_audit_logs WHERE entity_id = $1 AND entity_type = 'transaction'",
            pending_txn_id,
        )
        ledger_before = await fetch_all(
            "SELECT accounting_period, total_paid, total_advance, total_earned, txn_count "
            "FROM fpe_employee_ledger WHERE employee_id = $1 ORDER BY accounting_period",
            emp_id,
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Internal-Key": "test-key"},
        ) as ac:
            resp = await ac.post(
                f"/api/fpe/operator/{pending_id}/approve",
                params={
                    "employee_id": emp_id,
                    "reviewer": "test-admin",
                    "payout_method": "cash",
                    "txn_category": "salary",
                },
            )

        # The fixed contract -- was 409 before promoted_txn_id was added
        # to the SELECT.
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved"
        # Same transaction id promoted -- not a new one.
        assert body["txn_id"] == pending_txn_id

        txn = await fetch_one(
            "SELECT id, transaction_status, approval_status, employee_id, amount FROM fpe_cash_transactions WHERE id = $1",
            pending_txn_id,
        )
        assert txn["transaction_status"] == "final"
        assert txn["approval_status"] == "approved"
        assert float(txn["amount"]) == 100.0

        # No duplicate transaction created.
        txn_count_after = await fetch_val(
            "SELECT COUNT(*) FROM fpe_cash_transactions WHERE employee_id = $1", emp_id
        )
        assert txn_count_after == txn_count_before

        # Pending row left operator_pending.
        pending_row = await fetch_one(
            "SELECT review_status FROM fpe_unmatched_messages WHERE id = $1", pending_id
        )
        assert pending_row["review_status"] != "operator_pending"
        assert pending_row["review_status"] == "promoted"

        # Ledger changed exactly once, reconciling to the approved amount
        # under the canonical salary logic (category='salary' -> both
        # total_paid and total_earned move by +amount, total_advance by 0;
        # see accounting.py's _upsert_ledger()).
        ledger_after = await fetch_all(
            "SELECT accounting_period, total_paid, total_advance, total_earned, txn_count "
            "FROM fpe_employee_ledger WHERE employee_id = $1 ORDER BY accounting_period",
            emp_id,
        )
        assert len(ledger_after) == len(ledger_before) + 1, (
            "expected exactly one new ledger row (this candidate's own "
            "accounting_period) with all pre-existing periods untouched"
        )
        before_by_period = {r["accounting_period"]: r for r in ledger_before}
        new_rows = [r for r in ledger_after if r["accounting_period"] not in before_by_period]
        assert len(new_rows) == 1
        new_row = new_rows[0]
        assert float(new_row["total_paid"]) == 100.0
        assert float(new_row["total_earned"]) == 100.0
        assert float(new_row["total_advance"]) == 0.0
        assert new_row["txn_count"] == 1
        # Every pre-existing period must be byte-identical to baseline.
        for period, before_row in before_by_period.items():
            after_row = next(r for r in ledger_after if r["accounting_period"] == period)
            assert after_row == before_row

        # Exactly one new approval audit event for this transaction.
        audit_count_after = await fetch_val(
            "SELECT COUNT(*) FROM fpe_accounting_audit_logs WHERE entity_id = $1 AND entity_type = 'transaction'",
            pending_txn_id,
        )
        assert audit_count_after == audit_count_before + 1
        approve_audit = await fetch_one(
            "SELECT action, performed_by FROM fpe_accounting_audit_logs "
            "WHERE entity_id = $1 AND entity_type = 'transaction' AND action = 'approve'",
            pending_txn_id,
        )
        assert approve_audit is not None
        assert approve_audit["performed_by"] == "test-admin"

    async def test_approve_non_pending_submission_still_409(
        self, test_db_pool, seed_fpe_employee, monkeypatch
    ):
        """Non-regression: the pre-existing state-validation 409
        (review_status != 'operator_pending') must still work exactly as
        before -- this fix must not loosen that check."""
        import app.database as db_module
        from app.database import execute
        db_module._pool = test_db_pool
        _mock_rbac(monkeypatch)

        from app.main import app

        emp_id = seed_fpe_employee["id"]
        submitted = await _submit_operator_candidate(test_db_pool, emp_id)
        pending_id = submitted["pending_id"]

        await execute(
            "UPDATE fpe_unmatched_messages SET review_status = 'rejected' WHERE id = $1",
            pending_id,
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Internal-Key": "test-key"},
        ) as ac:
            resp = await ac.post(
                f"/api/fpe/operator/{pending_id}/approve",
                params={
                    "employee_id": emp_id,
                    "reviewer": "test-admin",
                },
            )

        assert resp.status_code == 409
        assert "not pending" in resp.json()["detail"].lower()

    async def test_forced_audit_insert_failure_rolls_back_all_financial_writes(
        self, test_db_pool, seed_fpe_employee, monkeypatch
    ):
        """Proves the atomicity requirement: if the audit-log insert is
        forced to fail (simulating any crash at that point), the
        transaction-finalize UPDATE and the ledger upsert that ran earlier
        in the SAME call must roll back too -- not persist as an
        inconsistent partial state. Only the audit-insert statement is
        forced to fail; the earlier statements are untouched real writes."""
        import app.database as db_module
        from app.database import execute as real_execute, fetch_all, fetch_one
        db_module._pool = test_db_pool
        _mock_rbac(monkeypatch)

        from app.main import app
        from modules.fazle_payroll_engine import routes as routes_module

        emp_id = seed_fpe_employee["id"]
        submitted = await _submit_operator_candidate(test_db_pool, emp_id)
        pending_id = submitted["pending_id"]
        pending_txn_id = submitted["pending_txn_id"]

        ledger_before = await fetch_all(
            "SELECT accounting_period, total_paid, total_advance, total_earned, txn_count "
            "FROM fpe_employee_ledger WHERE employee_id = $1 ORDER BY accounting_period",
            emp_id,
        )

        async def failing_execute(sql, *args, **kwargs):
            if "INSERT INTO fpe_accounting_audit_logs" in sql:
                raise RuntimeError("forced failure: simulated audit-insert crash for rollback test")
            return await real_execute(sql, *args, **kwargs)

        monkeypatch.setattr(routes_module, "execute", failing_execute)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Internal-Key": "test-key"},
        ) as ac:
            with pytest.raises(RuntimeError, match="forced failure"):
                await ac.post(
                    f"/api/fpe/operator/{pending_id}/approve",
                    params={
                        "employee_id": emp_id,
                        "reviewer": "test-admin",
                        "payout_method": "cash",
                        "txn_category": "salary",
                    },
                )

        # Transaction must still be 'pending' -- the finalize UPDATE
        # (same connection/transaction) must have rolled back.
        txn = await fetch_one(
            "SELECT transaction_status FROM fpe_cash_transactions WHERE id = $1", pending_txn_id
        )
        assert txn["transaction_status"] == "pending"

        # Ledger must be byte-identical to baseline -- the upsert (same
        # connection/transaction) must have rolled back too.
        ledger_after = await fetch_all(
            "SELECT accounting_period, total_paid, total_advance, total_earned, txn_count "
            "FROM fpe_employee_ledger WHERE employee_id = $1 ORDER BY accounting_period",
            emp_id,
        )
        assert ledger_after == ledger_before

        # Pending row must still show operator_pending -- never promoted.
        pending_row = await fetch_one(
            "SELECT review_status FROM fpe_unmatched_messages WHERE id = $1", pending_id
        )
        assert pending_row["review_status"] == "operator_pending"

        # No approval audit row must exist (the insert never committed).
        approve_audit = await fetch_one(
            "SELECT id FROM fpe_accounting_audit_logs "
            "WHERE entity_id = $1 AND entity_type = 'transaction' AND action = 'approve'",
            pending_txn_id,
        )
        assert approve_audit is None
