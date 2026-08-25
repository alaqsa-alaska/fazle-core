"""Unit tests -- modules.recruitment_recovery_sweep (Phase 7, 2026-08-25
recovery pass: periodic unanswered Recruitment/Employee conversation
recovery).

Mocks every reused live-pipeline component at the boundary
(identity_brain, owner_contact_registry, bridge_poller, shared.draft,
recruitment_flow, hermes_dispatch, outbound, message_router) -- this
module contains no new routing/reply logic of its own to test in
isolation; what needs proving is that it correctly SEQUENCES the
existing, already-tested components and respects every gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

CANDIDATE_ROW = {
    "sender_number": "8801711111111",
    "message_id": 999,
    "received_at": datetime(2026, 8, 25, tzinfo=timezone.utc),
    "message_body": "সুপারভাইজার পদের বেতন কত?",
    "workflow_triggered": None,
    "platform": "bridge3",
}


def _patch_common(**overrides):
    """Common happy-path patch set for _decide_and_reply -- override
    individual mocks per test."""
    identity = overrides.get(
        "identity", {"identity_role": "candidate", "display_name": ""},
    )
    patches = {
        "modules.identity_brain.detect_identity": AsyncMock(return_value=identity),
        "modules.owner_contact_registry.is_owner_managed_contact": lambda phone: overrides.get("owner_managed", False),
        "modules.bridge_poller._is_draft_always": AsyncMock(return_value=overrides.get("draft_always", False)),
        "shared.draft.should_generate_draft": AsyncMock(return_value=overrides.get("ok_to_consider", True)),
        "modules.intent.classify": lambda text: overrides.get("intent", "unknown"),
        "modules.message_router.get_recent_history": AsyncMock(return_value=["Them: hi", "You: hello"]),
        "modules.recruitment_flow.recruitment_eligibility": AsyncMock(
            return_value=overrides.get("eligibility", {"eligible": True, "autosend": True, "reason": "explicit_recruitment"}),
        ),
        "modules.hermes_dispatch.build_recruitment_context_with_directives": AsyncMock(return_value="kb context"),
        "modules.hermes_dispatch.generate_recruitment_reply": AsyncMock(return_value=overrides.get("reply", "একটি উত্তর")),
        "modules.hermes_dispatch.build_employee_context_with_directives": AsyncMock(return_value="employee ctx"),
        "modules.hermes_dispatch.generate_reply": AsyncMock(return_value=overrides.get("reply", "একটি উত্তর")),
        "modules.outbound.enqueue": AsyncMock(return_value=123),
        "modules.bridge_poller._save_draft": AsyncMock(),
    }
    return patches


async def _run_decide(row=None, **overrides):
    from modules.recruitment_recovery_sweep import _decide_and_reply
    patches = _patch_common(**overrides)
    with _apply_all(patches):
        return await _decide_and_reply(row or CANDIDATE_ROW, live_send=overrides.get("live_send", False))


class _apply_all:
    """Small helper: apply a dict of {target: mock_or_callable} as nested
    patch context managers."""
    def __init__(self, patches: dict):
        self._patches = patches
        self._ctxs = []

    def __enter__(self):
        for target, mock in self._patches.items():
            ctx = patch(target, new=mock)
            self._ctxs.append(ctx)
            ctx.__enter__()
        return self

    def __exit__(self, *a):
        for ctx in reversed(self._ctxs):
            ctx.__exit__(*a)


class TestDecideAndReplyGates:
    async def test_owner_managed_contact_is_skipped(self):
        result = await _run_decide(owner_managed=True)
        assert result["action"] == "skip"
        assert result["reason"] == "owner_managed_contact"

    async def test_operational_role_out_of_scope_is_skipped(self):
        result = await _run_decide(identity={"identity_role": "accountant", "display_name": ""})
        assert result["action"] == "skip"
        assert "role_not_in_sweep_scope" in result["reason"]

    async def test_already_replied_is_skipped(self):
        """Label corrected 2026-08-25 (Workstream 2 live-sample
        verification): should_generate_draft() catches ANY prior
        outbound, not only a human admin's -- see the code's own comment."""
        result = await _run_decide(ok_to_consider=False)
        assert result["action"] == "skip"
        assert result["reason"] == "already_replied"

    async def test_recruitment_ineligible_is_skipped(self):
        result = await _run_decide(eligibility={"eligible": False, "autosend": False, "reason": "no_recruitment_context"})
        assert result["action"] == "skip"
        assert "not_recruitment_eligible" in result["reason"]

    async def test_no_reply_generated_is_skipped(self):
        result = await _run_decide(reply=None)
        assert result["action"] == "skip"
        assert result["reason"] == "no_reply_generated"


class TestDecideAndReplyDryRunVsLiveSend:
    async def test_dry_run_never_calls_outbound_enqueue(self):
        patches = _patch_common()
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=False)
        assert result["action"] == "dry_run_preview"
        patches["modules.outbound.enqueue"].assert_not_awaited()

    async def test_live_send_with_autosend_true_calls_outbound_enqueue(self):
        patches = _patch_common(eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"})
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        assert result["action"] == "sent"
        patches["modules.outbound.enqueue"].assert_awaited_once()
        patches["modules.bridge_poller._save_draft"].assert_not_awaited()

    async def test_live_send_with_draft_always_true_saves_draft_not_send(self):
        """Even with live_send on and the eligibility layer saying
        autosend=True, _is_draft_always()=True (protected/risk contact)
        must still force a draft, never a direct send."""
        patches = _patch_common(
            draft_always=True,
            eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"},
        )
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        assert result["action"] == "drafted"
        patches["modules.outbound.enqueue"].assert_not_awaited()
        patches["modules.bridge_poller._save_draft"].assert_awaited_once()

    async def test_live_send_with_eligibility_autosend_false_saves_draft(self):
        patches = _patch_common(
            eligibility={"eligible": True, "autosend": False, "reason": "active_session_continuation"},
        )
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        assert result["action"] == "drafted"
        patches["modules.outbound.enqueue"].assert_not_awaited()


class TestDecideAndReplyEmployeePath:
    async def test_employee_role_uses_employee_reply_path_not_recruitment(self):
        patches = _patch_common(identity={"identity_role": "employee", "display_name": "Karim"})
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=False)
        assert result["action"] == "dry_run_preview"
        assert result["role"] == "employee"
        patches["modules.hermes_dispatch.generate_reply"].assert_awaited_once()
        patches["modules.hermes_dispatch.generate_recruitment_reply"].assert_not_awaited()
        # employee path never calls recruitment_eligibility at all
        patches["modules.recruitment_flow.recruitment_eligibility"].assert_not_awaited()


class TestDecideAndReplyErrorHandling:
    async def test_exception_in_identity_lookup_returns_error_action_not_raise(self):
        patches = _patch_common()
        patches["modules.identity_brain.detect_identity"] = AsyncMock(side_effect=RuntimeError("db down"))
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=False)
        assert result["action"] == "error"
        assert "db down" in result["error"]

    async def test_empty_text_is_skipped_before_any_lookup(self):
        row = {**CANDIDATE_ROW, "message_body": "   "}
        patches = _patch_common()
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(row, live_send=False)
        assert result["action"] == "skip"
        assert result["reason"] == "empty_phone_or_text"
        patches["modules.identity_brain.detect_identity"].assert_not_awaited()


class TestFindStaleUnanswered:
    async def test_query_shape_and_params(self):
        from modules.recruitment_recovery_sweep import _find_stale_unanswered
        mock_fetch = AsyncMock(return_value=[])
        with patch("modules.recruitment_recovery_sweep.fetch_all", new=mock_fetch):
            await _find_stale_unanswered(20)
        args, _ = mock_fetch.call_args
        query = args[0]
        assert "direction = 'inbound'" in query
        assert "NOT EXISTS" in query
        assert "direction = 'outbound'" in query
        since, until, limit = args[1], args[2], args[3]
        assert since < until
        assert limit == 20

    async def test_returns_rows_as_dicts(self):
        from modules.recruitment_recovery_sweep import _find_stale_unanswered
        with patch("modules.recruitment_recovery_sweep.fetch_all", new=AsyncMock(return_value=[CANDIDATE_ROW])):
            result = await _find_stale_unanswered(20)
        assert result == [dict(CANDIDATE_ROW)]


class TestSchedulerWiring:
    """Confirm the scheduler job wrapper is real and registered -- not
    just present as a standalone module nobody calls."""

    async def test_job_wrapper_delegates_to_run_recovery_sweep(self):
        from modules import scheduler
        with patch(
            "modules.recruitment_recovery_sweep.run_recovery_sweep",
            new=AsyncMock(return_value={"status": "ok", "candidates_checked": 0}),
        ) as mock_run:
            result = await scheduler.job_recruitment_recovery_sweep()
        mock_run.assert_awaited_once()
        assert result["status"] == "ok"

    async def test_job_is_registered_by_name(self):
        """register_job("recruitment_recovery_sweep", ...) must be
        reachable so admin `RUN JOB recruitment_recovery_sweep` and
        trigger_job() work without needing the full start_scheduler()
        AsyncIOScheduler bring-up."""
        import inspect
        from modules import scheduler
        src = inspect.getsource(scheduler)
        assert 'register_job("recruitment_recovery_sweep", job_recruitment_recovery_sweep)' in src
        assert 'id="recruitment_recovery_sweep"' in src


class TestRunRecoverySweep:
    async def test_no_candidates_no_admin_digest_sent(self):
        with (
            patch("modules.preflight_guard.is_feature_active", return_value=False),
            patch("modules.recruitment_recovery_sweep._find_stale_unanswered", new=AsyncMock(return_value=[])),
        ):
            from modules.recruitment_recovery_sweep import run_recovery_sweep
            result = await run_recovery_sweep()
        assert result["status"] == "ok"
        assert result["candidates_checked"] == 0
        assert result["live_send"] is False

    async def test_actionable_results_trigger_one_admin_digest(self):
        mock_enqueue = AsyncMock()
        with (
            patch("modules.preflight_guard.is_feature_active", return_value=False),
            patch("modules.recruitment_recovery_sweep._find_stale_unanswered", new=AsyncMock(return_value=[CANDIDATE_ROW])),
            patch(
                "modules.recruitment_recovery_sweep._decide_and_reply",
                new=AsyncMock(return_value={"phone": "8801711111111", "action": "dry_run_preview"}),
            ),
            patch("modules.scheduler._admin_number", return_value="8801958122300"),
            patch("modules.outbound.enqueue", mock_enqueue),
        ):
            from modules.recruitment_recovery_sweep import run_recovery_sweep
            result = await run_recovery_sweep()
        assert result["candidates_checked"] == 1
        assert result["actions"]["dry_run_preview"] == 1
        mock_enqueue.assert_awaited_once()
        assert mock_enqueue.call_args.kwargs["purpose"] == "recovery-sweep-digest"

    async def test_all_skips_no_admin_digest(self):
        mock_enqueue = AsyncMock()
        with (
            patch("modules.preflight_guard.is_feature_active", return_value=False),
            patch("modules.recruitment_recovery_sweep._find_stale_unanswered", new=AsyncMock(return_value=[CANDIDATE_ROW])),
            patch(
                "modules.recruitment_recovery_sweep._decide_and_reply",
                new=AsyncMock(return_value={"phone": "8801711111111", "action": "skip", "reason": "owner_managed_contact"}),
            ),
            patch("modules.outbound.enqueue", mock_enqueue),
        ):
            from modules.recruitment_recovery_sweep import run_recovery_sweep
            result = await run_recovery_sweep()
        assert result["actions"]["skip"] == 1
        mock_enqueue.assert_not_awaited()
