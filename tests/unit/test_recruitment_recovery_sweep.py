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
        # Owner directive (2026-08-27) safety-gate wiring -- sensible
        # "nothing to see here" defaults; individual gate-behavior tests
        # override these.
        "modules.recruitment_recovery_sweep.fetch_one": AsyncMock(return_value=None),
        "modules.recruitment_recovery_sweep._recovery_safety_gate": AsyncMock(
            return_value=overrides.get("gate_block_reason"),
        ),
        "modules.bridge_poller._save_message": AsyncMock(),
        "modules.recruitment_recovery_sweep._upsert_recovery_state": AsyncMock(),
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

    async def test_should_generate_draft_called_with_sweep_lookback_cutoff(self):
        """2026-08-25 WS2 real-sample bug fix: should_generate_draft()'s own
        default historical_cutoff_hours=2 is tuned for its ORIGINAL caller
        (skip drafting on freshly-imported WA history), not for this
        sweep's much wider _LOOKBACK_HOURS=48 window. Confirmed against a
        real 20-candidate live sample: 19/20 were >2h old and 100% of them
        were skipped as "already_replied" purely from that unrelated 2h
        cutoff, not a genuine prior reply -- silently capping the sweep's
        real effective window to ~15min-2h instead of the intended 48h.
        This locks in the fix: the sweep must pass its own lookback so the
        reused guard's historical cutoff matches its actual candidate
        window."""
        from modules.recruitment_recovery_sweep import _LOOKBACK_HOURS
        patches = _patch_common()
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            await _decide_and_reply(CANDIDATE_ROW, live_send=False)
        patches["shared.draft.should_generate_draft"].assert_awaited_once()
        _, kwargs = patches["shared.draft.should_generate_draft"].call_args
        assert kwargs.get("historical_cutoff_hours") == _LOOKBACK_HOURS == 48


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


# ── Owner directive, 2026-08-27: recovery send safety gate ─────────────────
# Root cause of the observed defect (candidate 8801744835680, 7 sends, one
# asking for their already-known name): _decide_and_reply()'s live-send
# branch never called modules.bridge_poller._save_message() after
# outbound.enqueue(), so wbom_whatsapp_messages -- the table _find_stale_
# unanswered()'s own NOT EXISTS check reads -- never reflected the sweep's
# own prior sends. Fixed below; plus a second, independent semantic/
# resolved-field safety layer checked against the ACTUAL generated reply.

class TestSaveMessageWriteBackRootCauseFix:
    async def test_live_send_calls_save_message_with_outbound_direction(self):
        """The actual regression fix: every live recovery send must now
        also record itself into wbom_whatsapp_messages, exactly like
        bridge_poller's own customer-reply and admin-hermes-reply paths
        already do -- this is what lets _find_stale_unanswered()'s
        existing NOT EXISTS check see it on the next scheduler tick."""
        patches = _patch_common(eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"})
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        assert result["action"] == "sent"
        patches["modules.bridge_poller._save_message"].assert_awaited_once()
        args, kwargs = patches["modules.bridge_poller._save_message"].call_args
        assert args[0] == "bridge3"  # source_bridge
        assert args[1] == CANDIDATE_ROW["sender_number"]
        assert args[3] == "outbound"

    async def test_dry_run_never_calls_save_message(self):
        patches = _patch_common()
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            await _decide_and_reply(CANDIDATE_ROW, live_send=False)
        patches["modules.bridge_poller._save_message"].assert_not_awaited()

    async def test_save_message_failure_does_not_break_the_send_decision(self):
        patches = _patch_common(eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"})
        patches["modules.bridge_poller._save_message"] = AsyncMock(side_effect=RuntimeError("db down"))
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        assert result["action"] == "sent"  # the send itself already succeeded; this is best-effort


class TestRecoveryStateWriteBack:
    async def test_live_send_upserts_recovery_state_when_candidate_id_known(self):
        patches = _patch_common(eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"})
        patches["modules.recruitment_recovery_sweep.fetch_one"] = AsyncMock(return_value={"candidate_id": 42})
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        patches["modules.recruitment_recovery_sweep._upsert_recovery_state"].assert_awaited_once()
        assert patches["modules.recruitment_recovery_sweep._upsert_recovery_state"].call_args.args[0] == 42

    async def test_no_candidate_id_skips_recovery_state_upsert(self):
        patches = _patch_common(eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"})
        patches["modules.recruitment_recovery_sweep.fetch_one"] = AsyncMock(return_value=None)
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        patches["modules.recruitment_recovery_sweep._upsert_recovery_state"].assert_not_awaited()


class TestSafetyGateWiring:
    async def test_gate_block_prevents_enqueue_and_reports_blocked_redundant(self):
        patches = _patch_common(
            gate_block_reason="resolved_field_conflict:collect_name",
            eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"},
        )
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        assert result["action"] == "blocked_redundant"
        assert result["reason"] == "resolved_field_conflict:collect_name"
        patches["modules.outbound.enqueue"].assert_not_awaited()
        patches["modules.bridge_poller._save_message"].assert_not_awaited()

    async def test_gate_block_also_applies_in_dry_run_mode(self):
        """A dry-run digest should show what WOULD be blocked, not a
        misleadingly-clean preview."""
        patches = _patch_common(gate_block_reason="semantic_duplicate:answer_salary")
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=False)
        assert result["action"] == "blocked_redundant"
        patches["modules.outbound.enqueue"].assert_not_awaited()

    async def test_gate_not_applied_to_employee_role(self):
        """The gate reasons about wbom_candidates/recruitment session
        state, which employees don't have -- recruitment role only."""
        patches = _patch_common(identity={"identity_role": "employee", "display_name": "Karim"})
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            result = await _decide_and_reply(CANDIDATE_ROW, live_send=False)
        assert result["action"] == "dry_run_preview"
        patches["modules.recruitment_recovery_sweep._recovery_safety_gate"].assert_not_awaited()

    async def test_gate_receives_actual_generated_reply_text(self):
        patches = _patch_common(
            reply="আপনার নাম জানান।",
            eligibility={"eligible": True, "autosend": True, "reason": "explicit_recruitment"},
        )
        with _apply_all(patches):
            from modules.recruitment_recovery_sweep import _decide_and_reply
            await _decide_and_reply(CANDIDATE_ROW, live_send=True)
        patches["modules.recruitment_recovery_sweep._recovery_safety_gate"].assert_awaited_once()
        assert patches["modules.recruitment_recovery_sweep._recovery_safety_gate"].call_args.kwargs["reply"] == "আপনার নাম জানান।"


class TestResolvedFieldConflict:
    """Owner Step 5, scenario 1 -- the exact regression case: candidate
    8801744835680's name was already known (full_name populated) at the
    time the 06:06 UTC reply asked for it again."""

    def test_collect_name_blocked_when_name_already_known(self):
        from modules.recruitment_recovery_sweep import _resolved_field_conflict
        result = _resolved_field_conflict(
            "আপনি কি এই পদে আবেদন করতে আগ্রহী? আগ্রহী হলে আপনার নাম জানান।",
            {"full_name": "কামরুজ্জামান কায়েস"}, None,
        )
        assert result == "collect_name"

    def test_collect_name_not_flagged_when_name_unknown(self):
        from modules.recruitment_recovery_sweep import _resolved_field_conflict
        result = _resolved_field_conflict(
            "আপনার নাম জানান।", {"full_name": None}, None,
        )
        assert result is None

    def test_collect_previous_company_blocked_when_experience_claim_resolved(self):
        from modules.recruitment_recovery_sweep import _resolved_field_conflict
        claim = {"claimed_company": "GSSL", "claimed_duration": "৩ বছর"}
        result = _resolved_field_conflict(
            "আপনি কি আগে কোন কোম্পানিতে কাজ করেছেন?", {"job_preference": "Security Supervisor"}, claim,
        )
        assert result == "collect_previous_company"

    def test_no_conflict_when_reply_is_about_something_else_entirely(self):
        """Scenario 3 -- genuinely new unresolved objective must NOT be
        flagged just because unrelated fields happen to be known."""
        from modules.recruitment_recovery_sweep import _resolved_field_conflict
        result = _resolved_field_conflict(
            "প্রশিক্ষণকালীন ভাতা ১০,০০০ থেকে ১৫,০০০ টাকা।",
            {"full_name": "X", "age": 25, "area": "Dhaka", "job_preference": "Escort"}, None,
        )
        assert result is None

    def test_new_candidate_no_prior_answers_nothing_flagged(self):
        """Scenario 5 -- brand-new candidate, empty session row."""
        from modules.recruitment_recovery_sweep import _resolved_field_conflict
        result = _resolved_field_conflict("আপনার নাম কি?", {}, None)
        assert result is None


class TestNoProgressionSinceLastRecovery:
    """Owner Step 5, scenario 6 -- repeated recovery for the same
    candidate with no new information must be blocked; scheduler time
    elapsing alone is not sufficient authorization."""

    def test_blocked_when_prior_recovery_exists_and_new_text_is_bare_ack(self):
        from modules.recruitment_recovery_sweep import _no_progression_since_last_recovery
        state = {"objective": "collect_name", "sent_at": "2026-08-27T06:06:00+00:00"}
        assert _no_progression_since_last_recovery(state, "ওকে") is True
        assert _no_progression_since_last_recovery(state, "Ok") is True

    def test_allowed_when_new_text_carries_real_information(self):
        from modules.recruitment_recovery_sweep import _no_progression_since_last_recovery
        state = {"objective": "collect_name", "sent_at": "2026-08-27T06:06:00+00:00"}
        assert _no_progression_since_last_recovery(state, "আমার নাম করিম") is False

    def test_allowed_when_no_prior_recovery_state(self):
        from modules.recruitment_recovery_sweep import _no_progression_since_last_recovery
        assert _no_progression_since_last_recovery(None, "ওকে") is False


class TestRecoveryStateBlockFormatParse:
    def test_round_trip(self):
        from modules.recruitment_recovery_sweep import (
            _format_recovery_state_block, _parse_recovery_state_block,
        )
        block = _format_recovery_state_block(objective="collect_name", topic="greeting", sent_at="2026-08-27T06:06:00+00:00")
        parsed = _parse_recovery_state_block(block)
        assert parsed["objective"] == "collect_name"
        assert parsed["topic"] == "greeting"

    def test_preserves_prior_experience_claim_block_when_upserting(self):
        """[RECOVERY_STATE] and [EXPERIENCE_CLAIM] must coexist in the
        same notes field without one clobbering the other."""
        from modules import recruitment_flow as rf
        from modules.recruitment_recovery_sweep import _format_recovery_state_block

        claim_block = rf._format_experience_claim_block(claimed_role="Security Supervisor")
        recovery_block = _format_recovery_state_block(objective="collect_name", sent_at="2026-08-27T06:06:00+00:00")
        combined = f"{claim_block}\n\n{recovery_block}"

        assert rf._parse_experience_claim_block(combined)["claimed_role"] == "Security Supervisor"
        from modules.recruitment_recovery_sweep import _parse_recovery_state_block
        assert _parse_recovery_state_block(combined)["objective"] == "collect_name"

    def test_none_when_absent(self):
        from modules.recruitment_recovery_sweep import _parse_recovery_state_block
        assert _parse_recovery_state_block(None) is None
        assert _parse_recovery_state_block("unrelated note text") is None


class TestSemanticRedundancyCheck:
    """Owner Step 5, scenario 2 -- same objective, different wording,
    caught by the LLM layer rather than exact-string matching."""

    async def test_redundant_high_confidence_returns_topic(self):
        from modules.recruitment_recovery_sweep import _semantic_redundancy_check
        raw = '{"redundant": true, "confidence": 0.9, "resolved_topic": "collect_name"}'
        with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
            result = await _semantic_redundancy_check("8801711111111", "আপনার পুরো নামটা কি একটু বলবেন?", "history")
        assert result == "collect_name"

    async def test_low_confidence_never_blocks(self):
        from modules.recruitment_recovery_sweep import _semantic_redundancy_check
        raw = '{"redundant": true, "confidence": 0.3, "resolved_topic": "collect_name"}'
        with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
            result = await _semantic_redundancy_check("8801711111111", "text", "history")
        assert result is None

    async def test_not_redundant_returns_none(self):
        from modules.recruitment_recovery_sweep import _semantic_redundancy_check
        raw = '{"redundant": false, "confidence": 0.9, "resolved_topic": null}'
        with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
            result = await _semantic_redundancy_check("8801711111111", "text", "history")
        assert result is None

    async def test_fails_open_on_llm_error(self):
        """Best-effort layer must never itself take recovery offline."""
        from modules.recruitment_recovery_sweep import _semantic_redundancy_check
        with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(side_effect=RuntimeError("timeout"))):
            result = await _semantic_redundancy_check("8801711111111", "text", "history")
        assert result is None

    async def test_fails_open_on_unparsable_response(self):
        from modules.recruitment_recovery_sweep import _semantic_redundancy_check
        with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value="not json")):
            result = await _semantic_redundancy_check("8801711111111", "text", "history")
        assert result is None


class TestRecoverySafetyGateIntegration:
    """End-to-end (mocked-DB) coverage of _recovery_safety_gate combining
    all layers -- the function actually wired into _decide_and_reply."""

    async def test_regression_8801744835680_name_already_known_blocks(self):
        """Owner Step 5's exact reproduction target."""
        from modules.recruitment_recovery_sweep import _recovery_safety_gate

        async def fake_fetch_one(sql, *args):
            if "fazle_recruitment_sessions" in sql:
                return {"full_name": "কামরুজ্জামান কায়েস", "age": 23, "job_preference": "Security Supervisor"}
            if "notes" in sql:
                return {"notes": None}
            return None

        with patch("modules.recruitment_recovery_sweep.fetch_one", new=AsyncMock(side_effect=fake_fetch_one)), \
             patch("modules.recruitment_recovery_sweep._semantic_redundancy_check", new=AsyncMock(return_value=None)):
            result = await _recovery_safety_gate(
                phone="8801744835680", candidate_id=42,
                reply="আপনি কি এই পদে আবেদন করতে আগ্রহী? আগ্রহী হলে আপনার নাম জানান।",
                text="ওকে", history="Them: নাম: মো: কামরুজ্জামান কায়েস",
            )
        assert result == "resolved_field_conflict:collect_name"

    async def test_semantic_duplicate_different_wording_blocks(self):
        """Scenario 2."""
        from modules.recruitment_recovery_sweep import _recovery_safety_gate

        with patch("modules.recruitment_recovery_sweep.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.recruitment_recovery_sweep._semantic_redundancy_check",
                   new=AsyncMock(return_value="answer_salary")):
            result = await _recovery_safety_gate(
                phone="8801711111111", candidate_id=None,
                reply="বেতন সংক্রান্ত তথ্য আগেই জানানো হয়েছে।",
                text="বেতন কত?", history="...",
            )
        assert result == "semantic_duplicate:answer_salary"

    async def test_genuinely_new_objective_allowed(self):
        """Scenario 3."""
        from modules.recruitment_recovery_sweep import _recovery_safety_gate

        async def fake_fetch_one(sql, *args):
            if "fazle_recruitment_sessions" in sql:
                return {"full_name": "X", "job_preference": "Escort"}
            if "notes" in sql:
                return {"notes": None}
            return None

        with patch("modules.recruitment_recovery_sweep.fetch_one", new=AsyncMock(side_effect=fake_fetch_one)), \
             patch("modules.recruitment_recovery_sweep._semantic_redundancy_check", new=AsyncMock(return_value=None)):
            result = await _recovery_safety_gate(
                phone="8801711111111", candidate_id=42,
                reply="প্রশিক্ষণকালীন ভাতা ১০,০০০ থেকে ১৫,০০০ টাকা।",
                text="বেতন কত?", history="...",
            )
        assert result is None

    async def test_new_candidate_no_prior_state_allowed(self):
        """Scenario 5."""
        from modules.recruitment_recovery_sweep import _recovery_safety_gate

        with patch("modules.recruitment_recovery_sweep.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.recruitment_recovery_sweep._semantic_redundancy_check", new=AsyncMock(return_value=None)):
            result = await _recovery_safety_gate(
                phone="8801799999999", candidate_id=None,
                reply="আপনার পুরো নাম কি?", text="চাকরি করতে চাই", history="",
            )
        assert result is None

    async def test_no_progression_blocks_repeat_after_prior_recovery(self):
        """Scenario 6."""
        from modules.recruitment_recovery_sweep import _recovery_safety_gate, _format_recovery_state_block

        state_notes = _format_recovery_state_block(
            objective="collect_name", sent_at="2026-08-27T06:00:00+00:00",
        )

        async def fake_fetch_one(sql, *args):
            if "fazle_recruitment_sessions" in sql:
                return {"job_preference": "Security Supervisor"}
            if "notes" in sql:
                return {"notes": state_notes}
            return None

        with patch("modules.recruitment_recovery_sweep.fetch_one", new=AsyncMock(side_effect=fake_fetch_one)), \
             patch("modules.recruitment_recovery_sweep._semantic_redundancy_check", new=AsyncMock(return_value=None)):
            result = await _recovery_safety_gate(
                phone="8801744835680", candidate_id=42,
                reply="আপনি কি আগ্রহী?", text="ওকে", history="...",
            )
        assert result == "no_progression:collect_name"

    async def test_gate_fails_open_on_internal_error(self):
        from modules.recruitment_recovery_sweep import _recovery_safety_gate
        with patch("modules.recruitment_recovery_sweep.fetch_one", new=AsyncMock(side_effect=RuntimeError("db down"))):
            result = await _recovery_safety_gate(
                phone="8801711111111", candidate_id=42,
                reply="আপনার নাম জানান।", text="hi", history="",
            )
        assert result is None


class TestHermesAdminSendWriteBack:
    """Owner Step 1 checklist item 'hermes_admin_send interaction' --
    /admin/send-whatsapp (app/main.py) had the SAME missing write-back as
    the recovery sweep itself; without it, an admin's direct send was
    invisible to both _find_stale_unanswered() and get_recent_history(),
    letting the sweep re-contact someone an admin had just personally
    messaged (observed live: 4 of the 60 historical recovery_sweep_reply
    sends collided with a prior hermes_admin_send). Fixed alongside the
    sweep's own fix since Owner's regression scenario 4 depends on it."""

    async def test_send_whatsapp_message_calls_save_message_on_success(self):
        from app import main as app_main
        with patch("modules.outbound.enqueue", new=AsyncMock(return_value=555)), \
             patch("modules.rbac.record_audit", new=AsyncMock()), \
             patch.object(app_main, "_rbac_actor_from_key", new=AsyncMock(return_value={"phone": "8801880446111"})), \
             patch.object(app_main, "_save_message", new=AsyncMock()) as mock_save:
            await app_main.send_whatsapp_message(
                {"recipient": "8801711111111", "body": "hello", "source_bridge": "bridge2"},
                key="test-key",
            )
        mock_save.assert_awaited_once_with("bridge2", "8801711111111", "hello", "outbound")

    async def test_send_whatsapp_message_skips_save_message_when_deduped(self):
        from app import main as app_main
        with patch("modules.outbound.enqueue", new=AsyncMock(return_value=None)), \
             patch("modules.rbac.record_audit", new=AsyncMock()), \
             patch.object(app_main, "_rbac_actor_from_key", new=AsyncMock(return_value={"phone": "8801880446111"})), \
             patch.object(app_main, "_save_message", new=AsyncMock()) as mock_save:
            await app_main.send_whatsapp_message(
                {"recipient": "8801711111111", "body": "hello", "source_bridge": "bridge2", "idempotency_key": "k1"},
                key="test-key",
            )
        mock_save.assert_not_awaited()
