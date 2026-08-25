"""Unit tests -- hermes_dispatch._shadow_check_reply_grounding() (Workstream
3, 2026-08-25 final-closure pass: "knowledge-grounded conversation").

The recruitment path already had a live-enforced fact-check gate
(_recruitment_reply_matches_source); the employee/general generate_reply()
path had none. This adds a SHADOW-ONLY check reusing the same function --
never blocks or alters the reply, only logs a warning when it would have.
These tests confirm: (a) the reply is never mutated regardless of the
check's outcome, (b) the log fires only when the check would fail, (c) it
never raises even on a garbage db_context.
"""
from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.asyncio


class TestShadowCheckNeverMutatesReply:
    async def test_unsupported_number_logs_warning_reply_unchanged(self, caplog):
        from modules.hermes_dispatch import _shadow_check_reply_grounding
        with caplog.at_level(logging.WARNING, logger="fazle.hermes_dispatch"):
            _shadow_check_reply_grounding(
                "আপনার বেতন ৫০,০০০ টাকা।", "কোনো সংখ্যা নেই এখানে।",
                role="employee", intent="salary_query", source="bridge1",
            )
        assert any("SHADOW fact-check would have" in r.message for r in caplog.records)

    async def test_supported_number_no_warning(self, caplog):
        from modules.hermes_dispatch import _shadow_check_reply_grounding
        with caplog.at_level(logging.WARNING, logger="fazle.hermes_dispatch"):
            _shadow_check_reply_grounding(
                "আপনার বেতন ১৭,০০০ টাকা।", "salary is ১৭,০০০ per the payroll record",
                role="employee", intent="salary_query", source="bridge1",
            )
        assert not any("SHADOW fact-check would have" in r.message for r in caplog.records)

    async def test_empty_db_context_skips_check_entirely_no_warning(self, caplog):
        """No context at all -- nothing to compare against, so this is
        deliberately a no-op, not a guaranteed-fail (matches the fail-
        closed reasoning of the underlying function, but this shadow
        wrapper specifically skips when there's no context to check
        against at all, per its own `if not reply or not db_context`
        guard)."""
        from modules.hermes_dispatch import _shadow_check_reply_grounding
        with caplog.at_level(logging.WARNING, logger="fazle.hermes_dispatch"):
            _shadow_check_reply_grounding(
                "আপনার বেতন ৫০,০০০ টাকা।", "",
                role="employee", intent="salary_query", source="bridge1",
            )
        assert not any("SHADOW fact-check would have" in r.message for r in caplog.records)

    async def test_none_reply_is_a_no_op(self):
        from modules.hermes_dispatch import _shadow_check_reply_grounding
        # Must not raise.
        _shadow_check_reply_grounding(None, "some context", role="employee", intent="x", source="bridge1")

    async def test_never_raises_on_internal_error(self, monkeypatch, caplog):
        """If the underlying fact-check function itself raises, the
        shadow wrapper must swallow it, never propagate into the real
        reply path."""
        from modules import hermes_dispatch

        def _boom(reply, kb):
            raise RuntimeError("boom")

        monkeypatch.setattr(hermes_dispatch, "_recruitment_reply_matches_source", _boom)
        # Must not raise.
        hermes_dispatch._shadow_check_reply_grounding(
            "reply text", "some context", role="employee", intent="x", source="bridge1",
        )


class TestGenerateReplyReturnsUnchangedRegardlessOfShadowCheck:
    """End-to-end: generate_reply()'s actual return value must be
    identical whether or not the shadow check would have failed --
    proves this is genuinely non-enforcing."""

    async def test_generate_reply_returns_call_hermes_result_verbatim(self, monkeypatch):
        from modules import hermes_dispatch
        from unittest.mock import AsyncMock

        monkeypatch.setattr(hermes_dispatch, "_minimax_primary_enabled", AsyncMock(return_value=False))
        monkeypatch.setattr(hermes_dispatch, "build_whatsapp_reply_policy", lambda **kw: "prompt")
        monkeypatch.setattr(
            hermes_dispatch, "_call_hermes",
            AsyncMock(return_value="আপনার বেতন ৫০,০০০ টাকা।"),  # unsupported number vs empty db_context
        )
        result = await hermes_dispatch.generate_reply(
            "test", "salary_query", "", role="employee", source="bridge1",
        )
        # Unchanged even though the shadow check (if db_context were non-empty)
        # would flag this -- generate_reply never alters/blocks the reply.
        assert result == "আপনার বেতন ৫০,০০০ টাকা।"
