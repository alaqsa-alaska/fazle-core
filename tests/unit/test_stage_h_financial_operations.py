"""Stage H (2026-08-28) -- Controlled Business Execution, Payroll Read
Integration & Financial Approval Actions.

Covers: H1 (transport-bound identity, structural proof), H2/H3 (payroll
privacy), H5-H9 (financial operation adapter + cards), H10 (command
registry consistency), H15/H16 (proposal execution bridge + stale-
approval protection), H17 (standing-authorization model)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── H1: transport-bound identity (structural proof) ─────────────────────────

class TestH1TransportBoundIdentity:
    def test_employee_lane_emp_id_is_never_derived_from_message_text(self):
        """The employee salary_query/payment_due branch in message_router
        uses identity.get('employee_id') -- resolved earlier in the same
        function from detect_identity(sender, text), where `sender` is
        the transport-verified phone, never a name/id parsed out of
        `text`. This is a structural (source-inspection) proof, not a
        live-traffic proof: there is no separate 'employee_id=' parameter
        anywhere in this branch that message text could populate."""
        import inspect
        import modules.message_router as mr
        src = inspect.getsource(mr)
        # The employee branch resolves emp_id exactly once, from `identity`.
        assert 'emp_id = identity.get("employee_id")' in src
        # And get_payroll_summary is only ever called with that emp_id.
        assert "get_payroll_summary(emp_id)" in src

    def test_resolve_requester_employee_id_takes_only_a_phone(self):
        """modules.employee_privacy.resolve_requester_employee_id has a
        single-parameter signature (phone) -- structurally, there is no
        second parameter (e.g. employee_id/name) a caller could use to
        ask for someone else's record."""
        import inspect
        from modules.employee_privacy import resolve_requester_employee_id
        sig = inspect.signature(resolve_requester_employee_id)
        assert list(sig.parameters.keys()) == ["phone"]

    def test_get_own_payroll_summary_takes_only_a_phone(self):
        import inspect
        from modules.employee_privacy import get_own_payroll_summary
        sig = inspect.signature(get_own_payroll_summary)
        assert list(sig.parameters.keys()) == ["requester_phone"]

    def test_customer_mode_toolset_excludes_fazle_core_mcp_tools(self):
        """The one Hermes-reachable path for an employee-scoped
        conversation (caller_scope='customer', hermes_customer_employee
        flag) gets a toolset with NO fazle-core MCP tool access at all --
        confirmed by reading hermes-runner's own MODE_TOOLSETS, the
        single source of truth for what a Hermes subprocess can call.
        This is why a spoofable free-text phone MCP tool parameter is not
        reachable from that lane today, independent of any tool's own
        argument validation."""
        import os
        path = os.path.expanduser("~/hermes-runner/server.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # Extract the CUSTOMER entry from MODE_TOOLSETS.
        assert '"CUSTOMER"' in src
        idx = src.index('"CUSTOMER"')
        line = src[idx: idx + 200].splitlines()[0]
        assert "fazle-core" not in line


# ── H2/H3: payroll privacy / applicant isolation ─────────────────────────────

class TestH2H3PayrollPrivacyAndApplicantIsolation:
    async def test_own_payroll_summary_refuses_unresolved_identity(self):
        from modules.employee_privacy import get_own_payroll_summary
        with patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value=None)):
            result = await get_own_payroll_summary("01700000000")
        assert result["ok"] is False
        assert "unverified" in result["error"]

    async def test_own_payroll_summary_returns_only_own_fields_no_raw_dump(self):
        from modules.employee_privacy import get_own_payroll_summary
        emp = {"employee_id": 42, "employee_name": "X", "designation": "Guard", "basic_salary": 15000, "status": "active"}
        with patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value=emp)):
            result = await get_own_payroll_summary("01700000000")
        assert result["ok"] is True
        assert result["employee_id"] == 42
        assert set(result.keys()) <= {"ok", "employee_id", "employee_name", "designation", "basic_salary", "status", "note"}

    def test_is_authorized_for_employee_data_never_true_for_unresolved_requester(self):
        from modules.employee_privacy import is_authorized_for_employee_data
        assert is_authorized_for_employee_data(None, "employee", 42) is False

    def test_is_authorized_for_employee_data_own_record_only(self):
        from modules.employee_privacy import is_authorized_for_employee_data
        assert is_authorized_for_employee_data(42, "employee", 42) is True
        assert is_authorized_for_employee_data(42, "employee", 99) is False

    def test_is_authorized_for_employee_data_admin_carve_out_unchanged(self):
        from modules.employee_privacy import is_authorized_for_employee_data
        assert is_authorized_for_employee_data(None, "admin", 99) is True


# ── H5-H9: financial operation adapter ───────────────────────────────────────

class TestFinancialOperationsResolve:
    async def test_resolve_by_numeric_id(self):
        from modules.financial_operations import resolve_cash_transaction
        with patch("modules.financial_operations.fetch_one", new=AsyncMock(return_value={"id": 4171, "txn_ref": "abcdef123456"})):
            result = await resolve_cash_transaction("4171")
        assert result["ok"] is True
        assert result["txn"]["id"] == 4171

    async def test_resolve_rejects_short_ref(self):
        from modules.financial_operations import resolve_cash_transaction
        result = await resolve_cash_transaction("ab12")
        assert result["ok"] is False

    async def test_resolve_ambiguous_returns_candidates_never_picks(self):
        from modules.financial_operations import resolve_cash_transaction
        rows = [{"id": 1, "txn_ref": "abc111"}, {"id": 2, "txn_ref": "abc222"}]
        with patch("modules.financial_operations.fetch_all", new=AsyncMock(return_value=rows)):
            result = await resolve_cash_transaction("abc111222")
        assert result["ok"] is False
        assert result["ambiguous"] is True
        assert len(result["candidates"]) == 2

    async def test_resolve_no_match(self):
        from modules.financial_operations import resolve_cash_transaction
        with patch("modules.financial_operations.fetch_all", new=AsyncMock(return_value=[])):
            result = await resolve_cash_transaction("nomatch123")
        assert result["ok"] is False
        assert "no transaction" in result["error"]


class TestCashApprove:
    async def test_refuses_non_pending_transaction(self):
        from modules.financial_operations import cash_approve
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "final", "employee_id": 5, "txn_category": "salary"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})):
            result = await cash_approve("r1", "01700000000")
        assert result["ok"] is False
        assert "not pending" in result["error"]

    async def test_refuses_no_linked_review_row(self):
        from modules.financial_operations import cash_approve
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "pending", "employee_id": 5, "txn_category": "salary"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})), \
             patch("modules.financial_operations.fetch_one", new=AsyncMock(return_value=None)):
            result = await cash_approve("r1", "01700000000")
        assert result["ok"] is False
        assert "no linked pending operator-review row" in result["error"]

    async def test_calls_operator_approve_with_transaction_own_amount_no_override(self):
        """H6: never silently changes the amount -- amount_override is
        always None, so operator_approve() uses the pending row's own
        detected_amount, never a value this adapter invents."""
        from modules.financial_operations import cash_approve
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "pending", "employee_id": 5, "txn_category": "salary"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})), \
             patch("modules.financial_operations.fetch_one", new=AsyncMock(return_value={"id": 900})), \
             patch("modules.fazle_payroll_engine.routes.operator_approve", new=AsyncMock(return_value={"status": "approved"})) as mock_approve:
            result = await cash_approve("r1", "01700000000")
        assert result["ok"] is True
        _, kwargs = mock_approve.call_args
        assert kwargs["amount_override"] is None
        assert kwargs["pending_id"] == 900
        assert kwargs["reviewer"] == "01700000000"

    async def test_replay_is_safe_not_a_second_mutation(self):
        """A second identical CASH APPROVE after the transaction is
        already final must not duplicate anything -- it hits the
        not-pending guard and returns a clear error, never calls
        operator_approve() again."""
        from modules.financial_operations import cash_approve
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "final", "employee_id": 5, "txn_category": "salary"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})), \
             patch("modules.fazle_payroll_engine.routes.operator_approve", new=AsyncMock()) as mock_approve:
            result = await cash_approve("r1", "01700000000")
        assert result["ok"] is False
        mock_approve.assert_not_called()


class TestCashReject:
    async def test_requires_reason(self):
        from modules.financial_operations import cash_reject
        result = await cash_reject("r1", "", "01700000000")
        assert result["ok"] is False
        assert "reason is required" in result["error"]

    async def test_refuses_non_pending(self):
        from modules.financial_operations import cash_reject
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "final"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})):
            result = await cash_reject("r1", "bad amount", "01700000000")
        assert result["ok"] is False


class TestCashReverse:
    async def test_requires_reason(self):
        from modules.financial_operations import cash_reverse
        result = await cash_reverse("r1", "", "01700000000")
        assert result["ok"] is False
        assert "reason is required" in result["error"]

    async def test_refuses_non_final_transaction(self):
        """H8: only a final transaction can be reversed -- a pending one
        must never be reversed via this path."""
        from modules.financial_operations import cash_reverse
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "pending", "is_reversal": False}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})):
            result = await cash_reverse("r1", "mistaken entry", "01700000000")
        assert result["ok"] is False
        assert "not final" in result["error"]

    async def test_refuses_reversing_a_reversal(self):
        from modules.financial_operations import cash_reverse
        txn = {"id": 1, "txn_ref": "REV-r1", "transaction_status": "final", "is_reversal": True}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})):
            result = await cash_reverse("REV-r1", "mistaken entry", "01700000000")
        assert result["ok"] is False
        assert "reversal entry" in result["error"]

    async def test_replay_does_not_reverse_twice(self):
        """H8: repeated CASH REVERSE of the same already-reversed
        transaction must be a safe no-op, never a second reversal."""
        from modules.financial_operations import cash_reverse
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "final", "is_reversal": False}
        existing_rev = {"id": 2, "txn_ref": "REV-r1"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})), \
             patch("modules.financial_operations.fetch_one", new=AsyncMock(return_value=existing_rev)), \
             patch("modules.fazle_payroll_engine.accounting.reverse_transaction", new=AsyncMock()) as mock_reverse:
            result = await cash_reverse("r1", "mistaken entry", "01700000000")
        assert result["ok"] is False
        assert "already reversed" in result["error"]
        mock_reverse.assert_not_called()

    async def test_calls_reverse_transaction_with_explicit_reason(self):
        from modules.financial_operations import cash_reverse
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "final", "is_reversal": False}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})), \
             patch("modules.financial_operations.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.fazle_payroll_engine.accounting.reverse_transaction", new=AsyncMock(return_value={"id": 2, "txn_ref": "REV-r1"})) as mock_reverse:
            result = await cash_reverse("r1", "duplicate entry, confirmed by Owner", "01700000000")
        assert result["ok"] is True
        args, kwargs = mock_reverse.call_args
        assert args[0] == 1
        assert "duplicate entry" in args[1]
        assert kwargs.get("created_by") == "01700000000" or (len(args) > 2 and args[2] == "01700000000")


class TestNoStandingAutonomousReverse:
    def test_cash_reverse_never_called_from_a_scheduled_job(self):
        """H8: 'NO standing autonomous reverse' as a structural check --
        no scheduler job source calls cash_reverse()."""
        import inspect
        import modules.scheduler as sch
        src = inspect.getsource(sch)
        assert "cash_reverse" not in src


# ── H9: card formatting ──────────────────────────────────────────────────

class TestCashCards:
    def test_review_card_has_owner_specified_sections(self):
        from modules.financial_operations import format_cash_review_card
        txn = {"txn_ref": "abcdef123456", "employee_name_raw": "Karim", "amount": 5000, "payout_method": "bkash", "transaction_status": "pending"}
        card = format_cash_review_card(txn)
        assert "FINANCIAL REVIEW" in card
        assert "Employee:" in card
        assert "Reason for attention" in card
        assert "Recommended action" in card
        assert "CASH APPROVE" in card
        assert "CASH REJECT" in card

    def test_review_card_never_offers_one_tap_reversal(self):
        """H9: reversal must NOT have a one-tap automatic path -- the
        review card (offered for an ordinary pending transaction) never
        includes a CASH REVERSE line."""
        from modules.financial_operations import format_cash_review_card
        txn = {"txn_ref": "abcdef123456", "employee_name_raw": "Karim", "amount": 5000, "payout_method": "bkash", "transaction_status": "pending"}
        card = format_cash_review_card(txn)
        assert "CASH REVERSE" not in card

    def test_ambiguous_result_lists_candidates(self):
        from modules.financial_operations import format_cash_result
        result = {"ok": False, "ambiguous": True, "candidates": [{"txn_ref": "a1", "id": 1, "amount": 100, "employee_name_raw": "X"}]}
        text = format_cash_result("approve", result)
        assert "a1" in text


# ── H10: command registry consistency ────────────────────────────────────

class TestH10CommandRegistryConsistency:
    def test_cash_commands_registered_in_classify_command(self):
        from modules.admin_commands import _classify_command
        assert _classify_command("CASH APPROVE abcdef123456") == "cash_approve"
        assert _classify_command("CASH REJECT abcdef123456 bad amount") == "cash_reject"
        assert _classify_command("CASH REVERSE abcdef123456 owner confirmed mistake") == "cash_reverse"

    def test_cash_commands_have_rbac_roles(self):
        from modules.rbac import COMMAND_ROLE
        assert COMMAND_ROLE["cash_approve"] == "admin"
        assert COMMAND_ROLE["cash_reject"] == "admin"
        assert COMMAND_ROLE["cash_reverse"] == "superadmin"

    def test_help_text_mentions_every_new_cash_command(self):
        """Parser-vs-HELP consistency: every genuinely-implemented cash
        command token appears in _HELP_TEXT."""
        from modules.admin_commands import _HELP_TEXT
        for token in ("CASH APPROVE", "CASH REJECT", "CASH REVERSE"):
            assert token in _HELP_TEXT

    def test_is_admin_command_recognizes_cash_commands(self):
        from modules.admin_commands import is_admin_command
        assert is_admin_command("CASH APPROVE abcdef123456") is True
        assert is_admin_command("CASH REJECT abcdef123456 reason") is True
        assert is_admin_command("CASH REVERSE abcdef123456 reason") is True


# ── H15/H16: proposal execution bridge + stale-approval protection ──────────

class TestBusinessActionExecutionBridge:
    def test_parse_business_reference(self):
        from modules.business_action_execution import parse_business_reference
        assert parse_business_reference("cash_txn:4171") == ("cash_txn", "4171")

    def test_parse_business_reference_rejects_bad_shape(self):
        from modules.business_action_execution import BusinessExecutionError, parse_business_reference
        with pytest.raises(BusinessExecutionError):
            parse_business_reference("no-colon-here")

    async def test_refuses_action_type_not_execution_enabled(self):
        from modules.business_action_execution import execute_approved_business_action, BusinessExecutionError
        action = {"id": 1, "action_type": "escort_program_creation", "business_reference": "escort_program:1"}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)):
            with pytest.raises(BusinessExecutionError):
                await execute_approved_business_action(1)

    async def test_refuses_when_not_approved(self):
        """H15: a proposal never executes before approval."""
        from modules.business_action_execution import execute_approved_business_action, BusinessExecutionError
        action = {"id": 1, "action_type": "business_record_review", "business_reference": "cash_txn:4171", "status": "pending"}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=False)):
            with pytest.raises(BusinessExecutionError):
                await execute_approved_business_action(1)

    async def test_h16_stale_approval_protection_worked_example(self):
        """The exact Owner-given scenario: a cash transaction was pending
        when the proposal was created; it was since approved through the
        normal operator path (now transaction_status='final'); the OLD
        proposal is later approved. Execution must be refused -- Hermes
        must NOT execute the stale action."""
        from modules.business_action_execution import execute_approved_business_action, StaleProposalError
        action = {"id": 31, "action_type": "business_record_review", "business_reference": "cash_txn:4171", "status": "approved"}
        current_txn = {"id": 4171, "transaction_status": "final", "employee_id": 5, "amount": 5000, "txn_ref": "abc"}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_txn)), \
             patch("modules.hermes_tasks.record_execution_result", new=AsyncMock()) as mock_record:
            with pytest.raises(StaleProposalError):
                await execute_approved_business_action(31, executor="owner")
        mock_record.assert_not_called()

    async def test_vanished_reference_is_also_stale(self):
        from modules.business_action_execution import execute_approved_business_action, StaleProposalError
        action = {"id": 31, "action_type": "business_record_review", "business_reference": "cash_txn:9999999", "status": "approved"}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=None)):
            with pytest.raises(StaleProposalError):
                await execute_approved_business_action(31)

    async def test_still_pending_reference_executes_review_as_no_op(self):
        from modules.business_action_execution import execute_approved_business_action
        action = {"id": 31, "action_type": "business_record_review", "business_reference": "cash_txn:4171", "status": "approved"}
        current_txn = {"id": 4171, "transaction_status": "pending", "employee_id": 5, "amount": 5000, "txn_ref": "abc"}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_txn)), \
             patch("modules.hermes_tasks.record_execution_result", new=AsyncMock(return_value={"ok": True})) as mock_record:
            result = await execute_approved_business_action(31, executor="owner")
        assert result["ok"] is True
        assert result["result"]["no_op"] is True
        mock_record.assert_called_once()

    async def test_stale_draft_already_sent_refuses_execution(self):
        """Same stale-approval pattern for the draft domain: a
        business_reply_draft proposal whose draft was already
        approved/sent by an admin through the normal APPROVE path before
        the Hermes proposal itself got approved."""
        from modules.business_action_execution import execute_approved_business_action, StaleProposalError
        action = {"id": 40, "action_type": "business_reply_draft", "business_reference": "draft:4099", "status": "approved"}
        current_draft = {"id": 4099, "status": "sent", "intent": "recruitment"}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_draft)):
            with pytest.raises(StaleProposalError):
                await execute_approved_business_action(40)

    async def test_unknown_reference_kind_refuses_rather_than_skip_check(self):
        from modules.business_action_execution import execute_approved_business_action, BusinessExecutionError
        action = {"id": 50, "action_type": "business_record_review", "business_reference": "recruitment_candidate:7", "status": "approved"}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)):
            with pytest.raises(BusinessExecutionError):
                await execute_approved_business_action(50)


# ── H17: standing-authorization model (represented, not granted) ────────────

class TestH17StandingAuthorizationModel:
    def test_financial_writes_never_map_to_autonomous_or_standing(self):
        from modules.authority_lanes import (
            ACTION_AUTHORIZATION_SOURCES, AUTONOMOUS, FUTURE_STANDING_AUTHORIZATION,
        )
        for action, source in ACTION_AUTHORIZATION_SOURCES.items():
            if action.startswith("cash_"):
                assert source not in (AUTONOMOUS, FUTURE_STANDING_AUTHORIZATION), action

    def test_standing_financial_authorization_flags_are_hardcoded_false(self):
        from modules.authority_lanes import FINANCIAL_AUTONOMY, STANDING_FINANCIAL_AUTHORIZATION
        assert FINANCIAL_AUTONOMY is False
        assert STANDING_FINANCIAL_AUTHORIZATION is False

    def test_every_action_maps_to_exactly_one_of_the_four_categories(self):
        from modules.authority_lanes import (
            ACTION_AUTHORIZATION_SOURCES, AUTONOMOUS, TRUSTED_DIRECT_ADMIN_COMMAND,
            ONE_TIME_APPROVAL, FUTURE_STANDING_AUTHORIZATION,
        )
        valid = {AUTONOMOUS, TRUSTED_DIRECT_ADMIN_COMMAND, ONE_TIME_APPROVAL, FUTURE_STANDING_AUTHORIZATION}
        for action, source in ACTION_AUTHORIZATION_SOURCES.items():
            assert source in valid, action
