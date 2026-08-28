"""Stage F (2026-08-28) -- modules.business_actions: Business Inbox ->
structured proposed action, built entirely on the existing
modules.hermes_tasks.propose_action() gate (migration 068, extended by
076) -- no second approval framework.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules.business_actions import (
    propose_business_action,
    business_idempotency_key,
    BusinessActionError,
    SUPPORTED_DOMAINS,
    BUSINESS_ACTION_TYPES,
)

pytestmark = pytest.mark.asyncio


def _evidence():
    return [{"type": "message_id", "value": 19779}]


class TestDomainAndTypeValidation:
    @pytest.mark.parametrize("domain", ["payroll", "employee_hr", "client", "operations", "office_internal", "", "RECRUITMENT"])
    async def test_unsupported_or_malformed_domain_is_refused(self, domain):
        with pytest.raises(BusinessActionError):
            await propose_business_action(domain, "8801700000000:bridge3", "business_record_review", "reason", _evidence(), "evt1")

    @pytest.mark.parametrize("domain", sorted(SUPPORTED_DOMAINS))
    async def test_every_supported_domain_is_accepted(self, domain, monkeypatch):
        monkeypatch.setattr("app.database.fetch_one", AsyncMock(return_value=None))
        monkeypatch.setattr("modules.hermes_tasks.propose_action", AsyncMock(return_value=1))
        monkeypatch.setattr("modules.hermes_tasks.get_action", AsyncMock(return_value={"status": "pending"}))
        result = await propose_business_action(domain, "8801700000000:bridge3", "business_record_review", "reason", _evidence(), "evt1")
        assert result["ok"] is True
        assert result["domain"] == domain

    async def test_office_internal_is_explicitly_not_a_supported_domain(self):
        """Stage E3/F4's explicit boundary: Office group is READ/INDEX
        only -- it must never be eligible for a proposed action."""
        assert "office_internal" not in SUPPORTED_DOMAINS
        assert "office" not in SUPPORTED_DOMAINS

    async def test_unsupported_proposed_action_type_is_refused(self):
        with pytest.raises(BusinessActionError):
            await propose_business_action("recruitment", "x:bridge3", "send_message_now", "reason", _evidence(), "evt1")

    async def test_missing_required_fields_refused(self):
        for kwargs in [
            dict(business_reference="", reason="r", evidence_refs=_evidence(), source_event_ref="e"),
            dict(business_reference="x", reason="", evidence_refs=_evidence(), source_event_ref="e"),
            dict(business_reference="x", reason="r", evidence_refs=[], source_event_ref="e"),
            dict(business_reference="x", reason="r", evidence_refs=_evidence(), source_event_ref=""),
        ]:
            with pytest.raises(BusinessActionError):
                await propose_business_action("recruitment", proposed_action_type="business_record_review", **kwargs)


class TestEvidenceIsReferenceOnlyNeverBody:
    async def test_malformed_evidence_entry_refused(self):
        with pytest.raises(BusinessActionError):
            await propose_business_action("recruitment", "x:bridge3", "business_record_review", "r", [{"type": "message_id"}], "e")

    async def test_oversized_evidence_value_refused(self):
        """A reference must be an id/label, not pasted message content."""
        huge = [{"type": "message_body", "value": "x" * 500}]
        with pytest.raises(BusinessActionError):
            await propose_business_action("recruitment", "x:bridge3", "business_record_review", "r", huge, "e")

    async def test_small_reference_values_accepted(self, monkeypatch):
        monkeypatch.setattr("app.database.fetch_one", AsyncMock(return_value=None))
        monkeypatch.setattr("modules.hermes_tasks.propose_action", AsyncMock(return_value=1))
        monkeypatch.setattr("modules.hermes_tasks.get_action", AsyncMock(return_value={"status": "pending"}))
        refs = [{"type": "message_id", "value": 19779}, {"type": "candidate_phone", "value": "8801784396009"}]
        result = await propose_business_action("recruitment", "x:bridge3", "business_record_review", "r", refs, "e")
        assert result["ok"] is True


class TestIdempotency:
    """Stage F7: retry/duplicate delivery/repeated Hermes run/restart/
    duplicated cross-bridge observation must never double-propose the
    same real-world event."""

    def test_idempotency_key_is_stable_and_deterministic(self):
        k1 = business_idempotency_key("recruitment", "8801700000000:bridge3", "business_reply_draft", "msg:100")
        k2 = business_idempotency_key("recruitment", "8801700000000:bridge3", "business_reply_draft", "msg:100")
        assert k1 == k2

    def test_idempotency_key_never_derived_from_message_text(self):
        """Only source/domain/reference/type feed the key -- proven by
        signature, matching Stage F7's explicit "never message text
        alone" rule."""
        import inspect
        sig = inspect.signature(business_idempotency_key)
        assert list(sig.parameters) == ["domain", "business_reference", "proposed_action_type", "source_event_ref"]

    def test_different_source_event_ref_yields_different_key(self):
        k1 = business_idempotency_key("recruitment", "x:bridge3", "business_reply_draft", "msg:1")
        k2 = business_idempotency_key("recruitment", "x:bridge3", "business_reply_draft", "msg:2")
        assert k1 != k2

    async def test_duplicate_source_event_returns_existing_action_not_a_new_one(self, monkeypatch):
        existing_row = {"id": 42}
        monkeypatch.setattr("app.database.fetch_one", AsyncMock(return_value=existing_row))
        propose_mock = AsyncMock(return_value=42)
        monkeypatch.setattr("modules.hermes_tasks.propose_action", propose_mock)
        monkeypatch.setattr("modules.hermes_tasks.get_action", AsyncMock(return_value={"status": "pending"}))

        result = await propose_business_action("recruitment", "x:bridge3", "business_reply_draft", "r", _evidence(), "msg:100")
        assert result["action_id"] == 42
        assert result["duplicate"] is True
        # propose_action() itself is still called (it does its own
        # authoritative dedup check under a real DB) -- this function
        # never bypasses that, only reports the pre-check result.
        propose_mock.assert_awaited_once()
        assert propose_mock.await_args.kwargs["idempotency_key"] == business_idempotency_key(
            "recruitment", "x:bridge3", "business_reply_draft", "msg:100"
        )

    async def test_fresh_event_is_not_flagged_duplicate(self, monkeypatch):
        monkeypatch.setattr("app.database.fetch_one", AsyncMock(return_value=None))
        monkeypatch.setattr("modules.hermes_tasks.propose_action", AsyncMock(return_value=7))
        monkeypatch.setattr("modules.hermes_tasks.get_action", AsyncMock(return_value={"status": "pending"}))
        result = await propose_business_action("recruitment", "x:bridge3", "business_reply_draft", "r", _evidence(), "msg:new")
        assert result["duplicate"] is False
        assert result["action_id"] == 7


class TestActionTypeRegistrySync:
    """Regression for a real bug caught live during Stage F route testing:
    migration 076 widened the DB CHECK constraint to include
    'business_reply_draft'/'business_record_review' but the Python-level
    ACTION_TYPES frozenset (modules.hermes_tasks.propose_action()'s own
    validation, checked BEFORE any SQL runs) was not updated in the same
    change -- every business-action proposal failed with a TaskError
    before ever reaching the database. Pins both business action types
    into ACTION_TYPES so this can't silently drift again."""

    def test_both_business_action_types_are_registered(self):
        from modules.hermes_tasks import ACTION_TYPES
        assert BUSINESS_ACTION_TYPES <= ACTION_TYPES

    async def test_propose_business_action_does_not_raise_task_error_for_a_valid_type(self, monkeypatch):
        monkeypatch.setattr("app.database.fetch_one", AsyncMock(return_value=None))
        monkeypatch.setattr("modules.hermes_tasks.fetch_one", AsyncMock(return_value=None))
        monkeypatch.setattr("modules.hermes_tasks.fetch_val", AsyncMock(return_value=1))
        monkeypatch.setattr("modules.hermes_tasks.get_action", AsyncMock(return_value={"status": "pending"}))
        # No mocking of ACTION_TYPES validation itself -- this exercises
        # the REAL frozenset check that caught the live bug.
        result = await propose_business_action("recruitment", "x:bridge3", "business_reply_draft", "r", _evidence(), "e1")
        assert result["ok"] is True


class TestNoSecondApprovalFramework:
    def test_propose_business_action_delegates_to_the_existing_gate(self):
        """Structural proof this module does not implement its own
        status machine / approval table -- the only write to
        hermes_action_approvals happens via modules.hermes_tasks.
        propose_action() (the same function code/infra actions already
        use); this module's own touch of that table is a read-only
        SELECT (the idempotency pre-check), never an INSERT/UPDATE."""
        import inspect
        import modules.business_actions as ba
        src = inspect.getsource(ba.propose_business_action)
        assert "from modules.hermes_tasks import propose_action" in src
        assert "INSERT INTO hermes_action_approvals" not in src
        assert "UPDATE hermes_action_approvals" not in src
        assert "SELECT id FROM hermes_action_approvals" in src  # the idempotency pre-check only

    def test_module_never_creates_or_writes_a_second_approval_table(self):
        import inspect
        import modules.business_actions as ba
        src = inspect.getsource(ba)
        assert "CREATE TABLE" not in src
        assert "INSERT INTO" not in src
        assert "UPDATE " not in src
