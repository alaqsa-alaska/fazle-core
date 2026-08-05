"""Unit tests — modules/social_auto_reply/planner_worker draft-routing gate.

2026-07-15 Phase 4 (Owner-confirmed policy: Selective, not blanket): only
senders matching the existing DRAFT_ALWAYS_ROLES/PHONES/NAMES config get
routed to a fazle_draft_replies row + admin notification instead of a real
send. Everyone else gets enqueue_reply() -> social_reply_queue, same as
pre-2026-06-23 behavior. See
docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.unit


class TestDraftInsteadOfSend:
    async def test_creates_draft_and_notifies_admin(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner

        fake_create_draft = AsyncMock(return_value=42)
        fake_enqueue = AsyncMock(return_value=99)
        monkeypatch.setattr(planner, "create_draft_reply", fake_create_draft)
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)
        monkeypatch.setattr(planner, "_primary_admin_number", lambda: "8801880446111")

        draft_id = await planner._draft_instead_of_send(
            platform="meta_whatsapp",
            target_id="8801711111111",
            reply_text="আসসালামু আলাইকুম, ধন্যবাদ।",
            intent="greeting",
            sender_role="UNKNOWN",
        )

        assert draft_id == 42
        fake_create_draft.assert_awaited_once()
        create_kwargs = fake_create_draft.call_args.kwargs
        assert create_kwargs["sender"] == "8801711111111"
        assert create_kwargs["bridge"] == "meta_whatsapp"
        assert create_kwargs["draft_text"] == "আসসালামু আলাইকুম, ধন্যবাদ।"
        assert create_kwargs["source_module"] == "social_auto_reply.planner_worker"

        fake_enqueue.assert_awaited_once()
        enqueue_args, enqueue_kwargs = fake_enqueue.call_args
        assert enqueue_args[0] == "8801880446111"
        assert "42" in enqueue_args[1]
        assert enqueue_kwargs["purpose"] == "social-draft-pending"
        assert enqueue_kwargs["idempotency_key"] == "social-draft-notify:42"

    async def test_deduped_draft_does_not_notify_admin(self, monkeypatch):
        """create_draft_reply returns None on its own 5-minute collision
        guard -- no draft means no admin notification either."""
        import modules.social_auto_reply.planner_worker as planner

        fake_create_draft = AsyncMock(return_value=None)
        fake_enqueue = AsyncMock()
        monkeypatch.setattr(planner, "create_draft_reply", fake_create_draft)
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)

        draft_id = await planner._draft_instead_of_send(
            platform="meta_whatsapp",
            target_id="8801711111111",
            reply_text="dup reply",
            intent="greeting",
            sender_role="UNKNOWN",
        )

        assert draft_id is None
        fake_enqueue.assert_not_awaited()

    async def test_enqueue_reply_is_wired_for_non_draft_always_senders(self):
        """2026-07-15 Phase 4 policy reversal: unlike the interim blanket-draft
        commit, real auto-send is NOT retired -- it's restored for senders
        that don't match DRAFT_ALWAYS_*, per Owner-confirmed Selective policy."""
        import modules.social_auto_reply.planner_worker as planner
        from modules.social_auto_reply.send_queue import enqueue_reply

        assert planner.enqueue_reply is enqueue_reply


class TestSelectiveDraftRoutingGate:
    """_is_draft_always_sender() decides the draft-vs-real-send branch --
    must reuse modules.bridge_poller._is_draft_always(), not reimplement it."""

    async def test_draft_always_role_routes_to_draft(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner
        import modules.identity_brain as identity_brain
        import modules.bridge_poller as bridge_poller

        monkeypatch.setattr(
            identity_brain, "detect_identity",
            AsyncMock(return_value={"role": "accountant", "display_name": "Test Accountant"}),
        )
        # Exercise the real _is_draft_always() -- "accountant" is in the
        # default DRAFT_ALWAYS_ROLES set, so no need to mock it too.
        result = await planner._is_draft_always_sender(
            "8801811111111", {"message_text": "hi"},
        )
        assert result is True

    async def test_unknown_role_routes_to_real_send(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner
        import modules.identity_brain as identity_brain

        monkeypatch.setattr(
            identity_brain, "detect_identity",
            AsyncMock(return_value={"role": "unknown", "display_name": None}),
        )
        result = await planner._is_draft_always_sender(
            "8801799999999", {"message_text": "hi"},
        )
        assert result is False

    async def test_non_phone_target_id_falls_back_to_unknown_and_real_send(self, monkeypatch):
        """Facebook comment/PSID identifiers aren't phone numbers --
        detect_identity() must safely fall back rather than raise, and the
        result must not accidentally match DRAFT_ALWAYS_*."""
        import modules.social_auto_reply.planner_worker as planner

        result = await planner._is_draft_always_sender(
            "969218865858934_1528952915612330", {"message_text": "hi"},
        )
        assert result is False


# ── 2026-08-04 Owner decision: never a silent failure ─────────────────────────
# _safe_polish() rejections and no-safe-reply/risk-flagged messages must
# always reach the Admin with full context instead of being silently
# substituted (polish rejection) or silently marked 'ignored' (no plan).

class TestSafePolishValidationFailureNotifiesAdmin:
    async def test_validation_reason_included_in_draft_notification(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner
        import modules.identity_brain as identity_brain

        fake_create_draft = AsyncMock(return_value=7)
        fake_enqueue = AsyncMock(return_value=99)
        monkeypatch.setattr(planner, "create_draft_reply", fake_create_draft)
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)
        monkeypatch.setattr(planner, "_primary_admin_number", lambda: "8801880446111")
        monkeypatch.setattr(
            identity_brain, "detect_identity",
            AsyncMock(return_value={"display_name": "Test Candidate"}),
        )

        draft_id = await planner._draft_instead_of_send(
            platform="meta_whatsapp",
            target_id="8801711111111",
            reply_text="নিরাপদ rule-based উত্তর",
            intent="job_details",
            sender_role="UNKNOWN",
            ai_polished=True,
            validation_reason="AI-polished reply contained unapproved number(s): 99999",
            events=[{"id": 555, "message_text": "original inbound text"}],
        )

        assert draft_id == 7
        fake_enqueue.assert_awaited_once()
        args, kwargs = fake_enqueue.call_args
        notification = args[1]
        assert "Message ID: 555" in notification
        assert "Sender Name: Test Candidate" in notification
        assert "Sender Mobile: 8801711111111" in notification
        assert "Original Message: original inbound text" in notification
        assert "AI-polished reply contained unapproved number(s): 99999" in notification
        assert kwargs["meta"]["validation_reason"] == "AI-polished reply contained unapproved number(s): 99999"

    async def test_no_validation_reason_keeps_old_notification_shape(self, monkeypatch):
        """Backward-compat: a plain AI-polish-pending-review draft (no
        rejection) must not gain the rich fields -- those are specifically
        for validation failures."""
        import modules.social_auto_reply.planner_worker as planner

        fake_create_draft = AsyncMock(return_value=8)
        fake_enqueue = AsyncMock(return_value=99)
        monkeypatch.setattr(planner, "create_draft_reply", fake_create_draft)
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)
        monkeypatch.setattr(planner, "_primary_admin_number", lambda: "8801880446111")

        await planner._draft_instead_of_send(
            platform="meta_whatsapp",
            target_id="8801711111111",
            reply_text="polished text",
            intent="job_details",
            sender_role="UNKNOWN",
            ai_polished=True,
        )
        notification = fake_enqueue.call_args.args[1]
        assert "Message ID:" not in notification
        assert "🤖 AI-Polished Reply Pending Approval" in notification


class TestNoSafeReplyNotifiesAdminInsteadOfSilentIgnore:
    async def test_notify_admin_flagged_sends_full_context(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner
        import modules.identity_brain as identity_brain

        fake_enqueue = AsyncMock(return_value=1)
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)
        monkeypatch.setattr(planner, "_primary_admin_number", lambda: "8801880446111")
        monkeypatch.setattr(
            identity_brain, "detect_identity",
            AsyncMock(return_value={"display_name": "Jane Doe"}),
        )

        await planner._notify_admin_flagged(
            platform="meta_whatsapp",
            target_id="8801799999999",
            events=[{"id": 42, "message_text": "some ambiguous message"}],
            reason="no_safe_reply",
            sender_role="UNKNOWN",
        )

        fake_enqueue.assert_awaited_once()
        args, kwargs = fake_enqueue.call_args
        assert args[0] == "8801880446111"
        notification = args[1]
        assert "Message ID: 42" in notification
        assert "Sender Name: Jane Doe" in notification
        assert "Sender Mobile: 8801799999999" in notification
        assert "Original Message: some ambiguous message" in notification
        assert "Reason: no_safe_reply" in notification
        assert kwargs["purpose"] == "social-auto-reply-needs-decision"

    async def test_no_admin_number_configured_sends_nothing(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner

        fake_enqueue = AsyncMock()
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)
        monkeypatch.setattr(planner, "_primary_admin_number", lambda: "")

        await planner._notify_admin_flagged(
            platform="meta_whatsapp", target_id="8801799999999",
            events=[{"id": 1, "message_text": "x"}], reason="no_safe_reply", sender_role="UNKNOWN",
        )
        fake_enqueue.assert_not_awaited()

    async def test_process_due_threads_flags_instead_of_silently_ignoring(self, monkeypatch):
        """End-to-end guard on the actual bug: before this fix, plan.auto_send
        == False marked the event 'ignored' with zero admin visibility. Now
        it must call _flag_events + _notify_admin_flagged."""
        import modules.social_auto_reply.planner_worker as planner
        from modules.social_auto_reply.intelligent_generator import ReplyPlan

        no_reply_plan = ReplyPlan("", "manual_review", False, "no_safe_reply", [1], "8801799999999", None)

        monkeypatch.setattr(planner, "pending_threads", AsyncMock(
            return_value=[{"platform": "meta_whatsapp", "target_id": "8801799999999"}]
        ))
        monkeypatch.setattr(planner, "pending_events", AsyncMock(
            return_value=[{"id": 1, "message_text": "unclear message", "classification": "greeting"}]
        ))
        monkeypatch.setattr(planner, "find_by_mobile", AsyncMock(return_value=None))
        monkeypatch.setattr(planner, "_classify_event", lambda e: type("C", (), {"intent": "greeting"})())
        monkeypatch.setattr(planner, "_refresh_classifications", AsyncMock())
        monkeypatch.setattr(planner, "is_recruiting_intent", lambda intent: True)
        monkeypatch.setattr(planner, "plan_reply", AsyncMock(return_value=no_reply_plan))
        monkeypatch.setattr(planner, "execute", AsyncMock())

        fake_flag = AsyncMock()
        fake_notify = AsyncMock()
        monkeypatch.setattr(planner, "_flag_events", fake_flag)
        monkeypatch.setattr(planner, "_notify_admin_flagged", fake_notify)

        result = await planner.process_due_threads(limit=1)

        fake_flag.assert_awaited_once()
        fake_notify.assert_awaited_once()
        assert result["flagged"] == 1
        assert result["skipped"] == 0


# ── 2026-08-04 Owner decision: _safe_polish() Bangla-numeral fix + reason ──────

class TestSafePolishBanglaNumeralFix:
    def test_bangla_numeral_number_outside_whitelist_is_now_caught(self):
        """The bug this fixes: the old ASCII-only regex never matched
        Bangla-script digits at all, so a hallucinated Bangla number
        silently passed unchecked. Now it must be caught exactly like an
        ASCII one."""
        from modules.social_auto_reply.intelligent_generator import _safe_polish

        # ৯৯৯৯৯ = 99999 in Bangla numerals, not in the allowed set.
        is_safe, reason = _safe_polish("অফিসে যোগাযোগ করুন ৯৯৯৯৯ নম্বরে।")
        assert is_safe is False
        assert "99999" in reason

    def test_bangla_numeral_number_inside_whitelist_still_passes(self):
        from modules.social_auto_reply.intelligent_generator import _safe_polish

        # ১০,০০০ = 10,000, which IS in _ALLOWED_POLISH_NUMBERS.
        is_safe, reason = _safe_polish("বেতন ১০,০০০ টাকা থেকে শুরু।")
        assert is_safe is True
        assert reason is None

    def test_ascii_number_still_validated_as_before(self):
        from modules.social_auto_reply.intelligent_generator import _safe_polish

        # Avoid any substring accidentally containing a poison word (e.g.
        # "details" contains "ai") -- this test is specifically about the
        # number check, not the poison-word check (separately covered
        # below).
        is_safe, reason = _safe_polish("Please contact us at 99999 today")
        assert is_safe is False
        assert "99999" in reason

    def test_extra_allowed_merges_with_static_whitelist(self):
        """This is how plan_reply() wires the dynamic office-numbers lookup
        through — a number not in the static set but present in
        extra_allowed must pass."""
        from modules.social_auto_reply.intelligent_generator import _safe_polish

        is_safe, reason = _safe_polish("যোগাযোগ: 01958-122300", extra_allowed={"01958", "122300"})
        assert is_safe is True
        assert reason is None

    def test_corrected_office_numbers_are_in_the_static_allowed_set(self):
        """Regression guard for the exact bug found during investigation:
        the old whitelist (122311/122327) never matched reply_rules.
        OFFICE_NUMBERS (122300) at all -- a reply correctly quoting the
        real number would have been wrongly rejected. Office numbers now
        come from extra_allowed (dynamic), not the static set -- this just
        confirms the static set no longer contains the stale, wrong values."""
        from modules.social_auto_reply.intelligent_generator import _ALLOWED_POLISH_NUMBERS

        assert "122311" not in _ALLOWED_POLISH_NUMBERS
        assert "122327" not in _ALLOWED_POLISH_NUMBERS

    def test_poison_word_reason_is_descriptive(self):
        from modules.social_auto_reply.intelligent_generator import _safe_polish

        is_safe, reason = _safe_polish("This uses the word prompt in it")
        assert is_safe is False
        assert "prompt" in reason.lower()

    def test_too_long_reason_is_descriptive(self):
        from modules.social_auto_reply.intelligent_generator import _safe_polish

        is_safe, reason = _safe_polish("x" * 1401)
        assert is_safe is False
        assert "1401" in reason

    def test_empty_text_reason_is_descriptive(self):
        from modules.social_auto_reply.intelligent_generator import _safe_polish

        is_safe, reason = _safe_polish("")
        assert is_safe is False
        assert reason is not None


class TestPlanReplyValidationFailureForcesReview:
    async def test_safe_polish_failure_sets_needs_review_and_reason(self, monkeypatch):
        """2026-08-04 Owner decision: a validation failure must never be a
        silent substitution -- needs_review must flip to True (was False
        before this fix) and validation_reason must carry the exact
        reason, so the Admin sees why."""
        import modules.social_auto_reply.intelligent_generator as gen

        monkeypatch.setattr(gen, "_polish_with_ai", AsyncMock(return_value="বাজে উত্তর ৯৯৯৯৯"))
        monkeypatch.setattr(gen, "_combined_rule_reply", lambda *a, **k: "নিরাপদ rule-based উত্তর")
        monkeypatch.setattr(gen, "_thread_risk", lambda *a, **k: None)
        monkeypatch.setattr(gen, "recent_thread_history", AsyncMock(return_value=[]))
        monkeypatch.setattr(gen, "thread_state", AsyncMock(return_value=None))
        monkeypatch.setattr(gen, "detect_identity", AsyncMock(return_value={"role": "candidate"}))
        monkeypatch.setattr(gen, "_update_thread_state", AsyncMock())
        monkeypatch.setattr(gen.rules, "get_effective_office_numbers", AsyncMock(return_value="01958-122300"))

        plan = await gen.plan_reply(
            "meta_whatsapp", "8801711111111",
            [{"id": 1, "message_text": "job details?"}],
        )

        assert plan is not None
        assert plan.reply_text == "নিরাপদ rule-based উত্তর"  # safe fallback, never the rejected AI text
        assert plan.needs_review is True
        assert plan.validation_reason is not None
        assert "99999" in plan.validation_reason

    async def test_safe_polish_success_still_respects_review_flag(self, monkeypatch):
        import modules.social_auto_reply.intelligent_generator as gen

        monkeypatch.setattr(gen, "_polish_with_ai", AsyncMock(return_value="বেতন ১০,০০০ টাকা"))
        monkeypatch.setattr(gen, "_combined_rule_reply", lambda *a, **k: "rule reply")
        monkeypatch.setattr(gen, "_thread_risk", lambda *a, **k: None)
        monkeypatch.setattr(gen, "recent_thread_history", AsyncMock(return_value=[]))
        monkeypatch.setattr(gen, "thread_state", AsyncMock(return_value=None))
        monkeypatch.setattr(gen, "detect_identity", AsyncMock(return_value={"role": "candidate"}))
        monkeypatch.setattr(gen, "_update_thread_state", AsyncMock())
        monkeypatch.setattr(gen.rules, "get_effective_office_numbers", AsyncMock(return_value="01958-122300"))
        monkeypatch.setenv("SOCIAL_AI_POLISH_REVIEW_REQUIRED", "false")

        plan = await gen.plan_reply(
            "meta_whatsapp", "8801711111111",
            [{"id": 1, "message_text": "job details?"}],
        )

        assert plan.reply_text == "বেতন ১০,০০০ টাকা"
        assert plan.needs_review is False
        assert plan.validation_reason is None


class TestGetEffectiveOfficeNumbers:
    async def test_falls_back_to_static_default_when_no_directive(self, monkeypatch):
        from modules.social_auto_reply import reply_rules
        from modules import admin_directives

        monkeypatch.setattr(admin_directives, "list_directives", AsyncMock(return_value=[]))
        result = await reply_rules.get_effective_office_numbers()
        assert result == reply_rules.OFFICE_NUMBERS

    async def test_uses_active_directive_when_present(self, monkeypatch):
        """Foundation for the planned Admin UI at /escort-roster: whatever
        an admin (eventually via that UI, today via POST /admin/directives)
        sets as the active office_numbers directive must be what this
        returns."""
        from modules.social_auto_reply import reply_rules
        from modules import admin_directives

        monkeypatch.setattr(
            admin_directives, "list_directives",
            AsyncMock(return_value=[{"directive_text": "01958-999999"}]),
        )
        result = await reply_rules.get_effective_office_numbers()
        assert result == "01958-999999"

    async def test_never_raises_on_lookup_failure(self, monkeypatch):
        from modules.social_auto_reply import reply_rules
        from modules import admin_directives

        monkeypatch.setattr(admin_directives, "list_directives", AsyncMock(side_effect=RuntimeError("db down")))
        result = await reply_rules.get_effective_office_numbers()
        assert result == reply_rules.OFFICE_NUMBERS
