"""Unit tests -- Unified Admin AI Engine, Phase 3 Step 5.

Covers modules.admin_directives.router: command pattern matching, RBAC
enforcement (allow/deny/fail-closed), the AI-query rate limiter, output
sanitization, and each of the 5 command handlers' dispatch behavior.
message_router.py is not wired to this module yet (Step 6) -- these tests
call process_admin_ai_command()/is_admin_ai_command() directly.
"""
from __future__ import annotations

import inspect
import time
from unittest.mock import AsyncMock, patch

import pytest

import modules.admin_directives.router as router
from modules.admin_directives.router import (
    _normalize_phone,
    _sanitize_ai_reply,
    is_admin_ai_command,
    process_admin_ai_command,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_cooldown():
    router._ai_cooldown.clear()
    yield
    router._ai_cooldown.clear()


def _allow_perm(admin_id: int = 1) -> dict:
    return {
        "allowed": True,
        "admin": {"id": admin_id, "status": "active", "name": "Test Admin"},
        "required_role": "admin",
        "reason": "",
    }


def _deny_perm() -> dict:
    return {
        "allowed": False,
        "admin": {"id": 1, "status": "active"},
        "required_role": "admin",
        "reason": "need admin (level 70) have level 10",
    }


class TestIsAdminAiCommand:
    @pytest.mark.parametrize("text", [
        "AI what is the status of employee X",
        "ai: show me the last payment",
        "AI, explain this error",
        "autoreply off 01712345678",
        "AUTOREPLY ON +8801712345678",
        "autoreply status 01712345678",
        "remember: always confirm advances over 5000tk",
        "REMEMBER call the office first",
        "forget 42",
        "FORGET 7",
        "send 01712345678 | Hello, please confirm your shift",
    ])
    def test_positive_matches_all_five_commands(self, text):
        assert is_admin_ai_command(text) is True

    @pytest.mark.parametrize("text", [
        "",
        "   ",
        "hello there",
        "PAID 12 5000 bkash",
        "APPROVE 5",
        "aiport is closed today",  # must not fuzzy-match "ai" as a substring
        "autoreply maybe 01712345678",  # invalid action word
        "forget abc",  # non-numeric id
        "send 01712345678 no pipe here",  # missing '|' separator
    ])
    def test_negative_for_non_matching_text(self, text):
        assert is_admin_ai_command(text) is False

    def test_bangla_ai_trigger_matches(self):
        assert is_admin_ai_command("এআই আজকের পেমেন্ট কত?") is True
        assert is_admin_ai_command("এআই: গত রাতের এরর কী ছিল") is True

    def test_never_raises_on_garbage_input(self):
        assert is_admin_ai_command(None) is False  # type: ignore[arg-type]
        assert is_admin_ai_command(12345) is False  # type: ignore[arg-type]


class TestRateLimiter:
    async def test_second_call_within_5s_is_blocked(self):
        with patch.object(router, "_authorize", new=AsyncMock(return_value={"id": 1})), \
             patch.object(router, "orchestrate_console_request", new=AsyncMock()) as mock_orch, \
             patch.object(router.rbac, "max_role_level", new=AsyncMock(return_value=70)):
            mock_orch.return_value = type(
                "R", (), {"provider_result": {"response": "first answer"}, "evidence": {}}
            )()
            first = await process_admin_ai_command("AI first question", "8801700000001")
            assert first == "first answer"

            second = await process_admin_ai_command("AI second question", "8801700000001")
            assert "অপেক্ষা" in second
            # Orchestrator must not have been called a second time.
            assert mock_orch.await_count == 1

    async def test_allowed_again_after_cooldown_elapses(self):
        with patch.object(router, "_authorize", new=AsyncMock(return_value={"id": 1})), \
             patch.object(router, "orchestrate_console_request", new=AsyncMock()) as mock_orch, \
             patch.object(router.rbac, "max_role_level", new=AsyncMock(return_value=70)):
            mock_orch.return_value = type(
                "R", (), {"provider_result": {"response": "answer"}, "evidence": {}}
            )()
            router._ai_cooldown["8801700000002"] = time.monotonic() - 6.0  # simulate 6s elapsed
            reply = await process_admin_ai_command("AI another question", "8801700000002")
            assert reply == "answer"
            assert mock_orch.await_count == 1


class TestRbacEnforcement:
    async def test_denial_returns_denial_string_no_handler_called(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_deny_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "save_directive", new=AsyncMock()) as mock_save:
            reply = await process_admin_ai_command("remember: test note", "8801700000003")
        assert reply == router._DENIAL_MSG
        mock_save.assert_not_awaited()

    async def test_check_permission_exception_is_treated_as_denied(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(side_effect=RuntimeError("db down"))), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "save_directive", new=AsyncMock()) as mock_save:
            reply = await process_admin_ai_command("remember: test note", "8801700000004")
        assert reply == router._DENIAL_MSG
        mock_save.assert_not_awaited()


class TestSanitizeAiReply:
    def test_prefixes_dangerous_looking_reply_with_warning(self):
        result = _sanitize_ai_reply("PAID 12 5000 bkash")
        assert result.startswith("⚠️ ")
        assert "PAID 12 5000 bkash" in result

    def test_normal_reply_not_prefixed(self):
        result = _sanitize_ai_reply("The employee's last payment was on 2026-07-20.")
        assert not result.startswith("⚠️")

    def test_truncates_at_max_reply_chars_with_spillover_note(self):
        long_text = "x" * 5000
        result = _sanitize_ai_reply(long_text)
        assert len(result) <= router.MAX_REPLY_CHARS
        assert result.endswith(router._SPILLOVER_NOTE)

    def test_short_reply_untouched(self):
        assert _sanitize_ai_reply("short answer") == "short answer"


class TestAutoreplyHandler:
    async def test_off_calls_set_runtime_setting_and_save_directive(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "set_runtime_setting", new=AsyncMock()) as mock_set, \
             patch.object(router, "save_directive", new=AsyncMock(return_value=99)) as mock_save:
            reply = await process_admin_ai_command("autoreply off 01712345678", "8801700000005")

        mock_set.assert_awaited_once_with("phone_block.8801712345678", "true")
        mock_save.assert_awaited_once()
        _, kwargs = mock_save.call_args
        assert kwargs["category"] == "autoreply_policy"
        assert kwargs["subject_key"] == "8801712345678"
        assert "বন্ধ" in reply

    async def test_on_calls_set_runtime_setting_false(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "set_runtime_setting", new=AsyncMock()) as mock_set, \
             patch.object(router, "save_directive", new=AsyncMock(return_value=100)):
            reply = await process_admin_ai_command("autoreply on 01712345678", "8801700000005")
        mock_set.assert_awaited_once_with("phone_block.8801712345678", "false")
        assert "চালু" in reply

    async def test_status_reads_live_gate_not_directive_log(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_runtime_setting", new=AsyncMock(return_value="true")) as mock_get:
            reply = await process_admin_ai_command("autoreply status 01712345678", "8801700000005")
        mock_get.assert_awaited_once_with("phone_block.8801712345678", "false")
        assert "বন্ধ" in reply


class TestRememberHandler:
    async def test_calls_save_directive_with_correct_args(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "save_directive", new=AsyncMock(return_value=55)) as mock_save:
            reply = await process_admin_ai_command(
                "remember: always confirm advances over 5000tk", "8801700000006"
            )
        mock_save.assert_awaited_once()
        args, kwargs = mock_save.call_args
        assert args[0] == "always confirm advances over 5000tk"
        assert kwargs["created_by_phone"] == "8801700000006"
        assert kwargs["created_by_channel"] == "whatsapp"
        assert kwargs["category"] == "general"
        assert "ID: 55" in reply


class TestForgetHandler:
    async def test_revoked_true_returns_success_reply(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "revoke_directive", new=AsyncMock(return_value=True)) as mock_revoke:
            reply = await process_admin_ai_command("forget 42", "8801700000007")
        mock_revoke.assert_awaited_once_with(42, revoked_by_phone="8801700000007")
        assert reply == "✅ মুছে ফেলা হয়েছে।"

    async def test_revoked_false_returns_not_found_reply(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "revoke_directive", new=AsyncMock(return_value=False)):
            reply = await process_admin_ai_command("forget 999", "8801700000007")
        assert reply == "⚠️ পাওয়া যায়নি।"


class TestSendHandler:
    async def test_creates_draft_only_never_calls_send_strict(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "create_draft_reply", new=AsyncMock(return_value=123)) as mock_draft:
            reply = await process_admin_ai_command(
                "send 01712345678 | Please confirm your shift tomorrow", "8801700000008"
            )
        mock_draft.assert_awaited_once()
        _, kwargs = mock_draft.call_args
        assert kwargs["sender"] == "8801712345678"
        assert kwargs["draft_text"] == "Please confirm your shift tomorrow"
        assert kwargs["role"] == "admin_ai_composed"
        assert "ID: 123" in reply
        assert "ড্রাফট" in reply

        # Structural proof: this module never references a direct-send call.
        source = inspect.getsource(router)
        assert "send_strict" not in source
        assert "bridge.send(" not in source

    async def test_duplicate_draft_returns_pending_message_not_id(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "create_draft_reply", new=AsyncMock(return_value=None)):
            reply = await process_admin_ai_command(
                "send 01712345678 | duplicate message", "8801700000008"
            )
        assert "পেন্ডিং" in reply


class TestNormalizePhone:
    def test_local_zero_prefixed_to_880(self):
        assert _normalize_phone("01712345678") == "8801712345678"

    def test_plus_prefix_stripped(self):
        assert _normalize_phone("+8801712345678") == "8801712345678"

    def test_spaces_and_dashes_stripped(self):
        assert _normalize_phone("017-1234 5678") == "8801712345678"
