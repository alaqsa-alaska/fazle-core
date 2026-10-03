"""Stage D3/D4/D6/D7 (2026-08-28) -- modules.conversation_state."""
from __future__ import annotations

import pytest

from datetime import datetime, timezone

from modules.conversation_state import (
    resolve_recruitment_state,
    resolve_escort_state,
    resolve_attendance_state,
    resolve_cash_state,
    RECRUITMENT_STATE_MAP,
    ESCORT_STATE_MAP,
    ATTENDANCE_STATE_MAP,
    CASH_STATE_MAP,
    UNKNOWN_STATE,
    STRUCTURED_STATE_SOURCE_CANDIDATES,
)


class TestRecruitmentStateMapping:
    def test_every_existing_post_intake_status_is_mapped(self):
        """modules.recruitment_flow.POST_INTAKE_STATUSES must stay in sync
        with this map -- if a new status is ever added there without a
        corresponding entry here, resolve_recruitment_state() silently
        falls back to UNKNOWN_STATE rather than erroring, so this test
        pins the exact expected set."""
        from modules.recruitment_flow import POST_INTAKE_STATUSES
        mapped = set(k for k in RECRUITMENT_STATE_MAP if k is not None)
        assert POST_INTAKE_STATUSES == mapped

    def test_declined_is_resolved_terminal(self):
        state = resolve_recruitment_state("declined")
        assert state["state"] == "resolved"
        assert state["next_actor"] == "none"
        assert state["requires_action"] is False

    def test_declined_matches_recruitment_flows_own_terminal_set(self):
        from modules.recruitment_flow import POST_INTAKE_TERMINAL_STATUSES
        for status in POST_INTAKE_TERMINAL_STATUSES:
            assert resolve_recruitment_state(status)["state"] == "resolved"

    @pytest.mark.parametrize("status", ["awaiting_agreement", "not_ready_now"])
    def test_waiting_on_candidate_means_no_company_action_required(self, status):
        state = resolve_recruitment_state(status)
        assert state["state"] == "waiting_for_counterparty"
        assert state["next_actor"] == "applicant"
        assert state["requires_action"] is False

    @pytest.mark.parametrize("status", ["agreed", "joining_ready", "salary_objection", "future_contact"])
    def test_company_action_pending_states(self, status):
        state = resolve_recruitment_state(status)
        assert state["state"] == "waiting_for_company"
        assert state["next_actor"] == "company"
        assert state["requires_action"] is True

    def test_none_status_is_unclear_not_active(self):
        """Pre-scored / pre-migration-072 candidates: NULL must not be
        fabricated as an active/needs-action state."""
        state = resolve_recruitment_state(None)
        assert state["state"] == "unclear"
        assert state["requires_action"] is False

    def test_unmapped_garbage_value_falls_back_to_unknown_not_a_crash(self):
        state = resolve_recruitment_state("some_future_status_not_yet_mapped")
        assert state == UNKNOWN_STATE

    def test_recruitment_flow_semantics_not_renamed_or_mutated(self):
        """This module only reads modules.recruitment_flow's existing
        constants -- it must never redefine them locally."""
        from modules.recruitment_flow import POST_INTAKE_STATUSES
        assert POST_INTAKE_STATUSES == frozenset({
            "awaiting_agreement", "agreed", "joining_ready",
            "salary_objection", "not_ready_now", "declined", "future_contact",
        })


class TestEscortStateMapping:
    def test_draft_requires_company_confirmation(self):
        state = resolve_escort_state("draft")
        assert state["state"] == "waiting_for_company"
        assert state["next_actor"] == "company"
        assert state["requires_action"] is True

    def test_confirmed_is_active_no_reply_pending(self):
        state = resolve_escort_state("confirmed")
        assert state["state"] == "active"
        assert state["requires_action"] is False

    @pytest.mark.parametrize("status", ["completed", "cancelled"])
    def test_terminal_statuses_are_resolved(self, status):
        state = resolve_escort_state(status)
        assert state["state"] == "resolved"
        assert state["requires_action"] is False

    def test_none_status_is_unclear(self):
        assert resolve_escort_state(None)["state"] == "unclear"

    def test_unmapped_value_falls_back_to_unknown(self):
        assert resolve_escort_state("some_new_status") == UNKNOWN_STATE

    def test_mapped_statuses_match_the_real_lifecycle_values_observed_live(self):
        """wbom_escort_programs.status's actual lifecycle values (confirmed
        live via direct query during Stage D investigation: draft,
        confirmed, completed, cancelled) -- NOT the same vocabulary as
        modules.escort.routes._VALID_STATUSES (a different, unrelated
        linking-flow input set), so deliberately not cross-checked against
        that constant."""
        assert {"draft", "confirmed", "completed", "cancelled"} == set(
            k for k in ESCORT_STATE_MAP if k is not None
        )


class TestAttendanceStateMapping:
    """Stage E6: linkage proven deterministic (wbom_employees.employee_mobile
    -> employee_id -> wbom_attendance, the same resolution
    modules.identity_brain._lookup_attendance_identity already uses live).
    State derives from approved_at nullability (modules.attendance's own
    save_attendance(): approved_at = NOW() if approved_by else None),
    never from the DUTY_STATUS_VALUES business fact (Present/Half Day/
    Absent) which is a different concept."""

    def test_approved_record_is_resolved(self):
        state = resolve_attendance_state(datetime(2026, 8, 28, tzinfo=timezone.utc))
        assert state["state"] == "resolved"
        assert state["requires_action"] is False

    def test_unapproved_record_requires_company_action(self):
        state = resolve_attendance_state(None)
        assert state["state"] == "waiting_for_company"
        assert state["next_actor"] == "company"
        assert state["requires_action"] is True

    def test_map_has_exactly_two_states_true_and_false(self):
        assert set(ATTENDANCE_STATE_MAP) == {True, False}


class TestCashStateMapping:
    """Stage E7: linkage via fpe_cash_transactions.{employee_phone,
    employee_id_phone,payout_phone} (same canonical BD phone format,
    normalized the same way). transaction_status is the genuine, dominant,
    already-live state field (live distribution confirmed during
    investigation: final=4390, pending=27, reversed=1)."""

    def test_pending_requires_company_action(self):
        state = resolve_cash_state("pending")
        assert state["state"] == "waiting_for_company"
        assert state["next_actor"] == "company"
        assert state["requires_action"] is True

    @pytest.mark.parametrize("status", ["final", "reversed"])
    def test_final_and_reversed_are_resolved(self, status):
        state = resolve_cash_state(status)
        assert state["state"] == "resolved"
        assert state["requires_action"] is False

    def test_none_is_unclear(self):
        assert resolve_cash_state(None)["state"] == "unclear"

    def test_unmapped_value_falls_back_to_unknown(self):
        assert resolve_cash_state("some_future_status") == UNKNOWN_STATE

    def test_only_uses_states_actually_present_in_schema_not_invented(self):
        """Never invents e.g. 'submitted'/'disputed' from the Stage E7
        example list -- only the 3 real transaction_status values found
        live are mapped."""
        assert set(k for k in CASH_STATE_MAP if k is not None) == {"pending", "final", "reversed"}


class TestDisclosedGaps:
    def test_attendance_and_cash_are_no_longer_reported_as_unwired(self):
        """Stage E6/E7 wired what Stage D7 had only found -- the
        candidates dict must now be empty, not stale."""
        assert STRUCTURED_STATE_SOURCE_CANDIDATES == {}

    def test_unknown_state_never_claims_action_required(self):
        assert UNKNOWN_STATE["requires_action"] is False
        assert UNKNOWN_STATE["next_actor"] == "unknown"
