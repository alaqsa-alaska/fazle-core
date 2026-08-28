"""Stage D3/D4/D6/D7 (2026-08-28) -- modules.conversation_state."""
from __future__ import annotations

import pytest

from modules.conversation_state import (
    resolve_recruitment_state,
    resolve_escort_state,
    RECRUITMENT_STATE_MAP,
    ESCORT_STATE_MAP,
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


class TestDisclosedGaps:
    def test_operations_candidates_are_reported_not_silently_wired(self):
        assert "attendance" in STRUCTURED_STATE_SOURCE_CANDIDATES
        assert "cash" in STRUCTURED_STATE_SOURCE_CANDIDATES

    def test_unknown_state_never_claims_action_required(self):
        assert UNKNOWN_STATE["requires_action"] is False
        assert UNKNOWN_STATE["next_actor"] == "unknown"
