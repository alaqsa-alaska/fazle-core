"""Unit tests -- Unified Admin AI Engine, Phase 3 Step 5 (+ Phase 4
Hermes relay, 2026-08-04).

Covers modules.admin_directives.router: command pattern matching, RBAC
enforcement (allow/deny/fail-closed), the AI-query rate limiter, output
sanitization, and each of the 6 command handlers' dispatch behavior.
message_router.py is not wired to this module yet (Step 6) -- these tests
call process_admin_ai_command()/is_admin_ai_command() directly.
"""
from __future__ import annotations

import asyncio
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
    process_admin_natural_query,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_cooldown():
    router._ai_cooldown.clear()
    router._hermes_cooldown.clear()
    router._investigation_cooldown.clear()
    router._hermes_conversation_sessions.clear()
    yield
    router._ai_cooldown.clear()
    router._hermes_cooldown.clear()
    router._investigation_cooldown.clear()
    router._hermes_conversation_sessions.clear()


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
        "hermes: check bridge health",
        "HERMES what's the DLQ depth right now?",
        "hermes, investigate the stuck messages",
    ])
    def test_positive_matches_all_six_commands(self, text):
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


class _FakeSettings:
    def __init__(
        self, admin_bridge2_number="8801880446111", hermes_runner_secret="test-secret",
        hermes_runner_url="http://127.0.0.1:8093",
        hermes_alert_investigation_enabled=False,
        hermes_alert_investigation_cooldown_seconds=3600,
    ):
        self.admin_bridge2_number = admin_bridge2_number
        self.hermes_runner_secret = hermes_runner_secret
        self.hermes_runner_url = hermes_runner_url
        self.hermes_alert_investigation_enabled = hermes_alert_investigation_enabled
        self.hermes_alert_investigation_cooldown_seconds = hermes_alert_investigation_cooldown_seconds


class TestHermesRelayHandler:
    """Phase 4 (2026-08-04): real Hermes relay, distinct from the AI
    console (TestProcessAdminNaturalQuery below covers that engine)."""

    async def _run(self, text="hermes: check bridge health", phone="8801880446111", source="bridge2"):
        reply = await process_admin_ai_command(text, phone, source_bridge=source)
        await asyncio.sleep(0)  # let any scheduled background task start
        return reply

    async def test_rbac_denied_returns_denial_no_task_created(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_deny_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await self._run()
        assert reply == router._DENIAL_MSG
        mock_deliver.assert_not_awaited()

    async def test_rbac_allowed_but_wrong_channel_denied_and_audited(self):
        # bridge1's own number is also bootstrapped as superadmin, but it
        # is not settings.admin_bridge2_number -- must be denied and
        # separately audited (not silently reusing _authorize's "allowed"
        # verdict), regardless of which channel it arrives on (default
        # source here is still "bridge2", a now-valid channel -- it's the
        # phone-identity check that denies this, unchanged by the 2026-08-12
        # bridge1-channel widening; see test_rbac_allowed_bridge1_channel_
        # now_accepted for the genuinely-widened case).
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()) as mock_audit, \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await self._run(phone="8801958122300")  # bridge1's own number, not admin_bridge2_number
        assert reply == router._DENIAL_MSG
        mock_deliver.assert_not_awaited()
        # Two audit calls: one from _authorize (RBAC allowed), one from the
        # channel-mismatch denial recorded directly by the handler.
        assert mock_audit.await_count == 2
        _, channel_kwargs = mock_audit.await_args_list[-1]
        assert channel_kwargs["allowed"] is False
        assert "Hermes control channel" in channel_kwargs["denied_reason"]

    async def test_rbac_allowed_same_admin_phone_but_wrong_source_bridge_denied(self):
        # S6 (2026-08-08): closes the gap where phone-identity alone was
        # the channel check. Even the genuine bridge2-designated admin
        # phone number must be denied if message_router's own trusted
        # `source` says this message physically arrived via bridge3 or an
        # unrecognized channel -- e.g. a misconfiguration reusing the same
        # number across bridges, or a message replayed/misrouted through
        # the wrong poller. bridge1 is deliberately NOT in this denied set
        # -- see test_rbac_allowed_bridge1_channel_now_accepted below
        # (2026-08-12, bridge1 Hermes Control Channel, Owner-directed).
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()) as mock_audit, \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            for wrong_source in ("bridge3", "", "meta"):
                mock_audit.reset_mock()
                reply = await self._run(phone="8801880446111", source=wrong_source)
                assert reply == router._DENIAL_MSG
                mock_deliver.assert_not_awaited()
                _, channel_kwargs = mock_audit.await_args_list[-1]
                assert channel_kwargs["allowed"] is False
                assert "Hermes control channel" in channel_kwargs["denied_reason"]

    async def test_rbac_allowed_bridge1_channel_now_accepted(self):
        # 2026-08-12 (bridge1 Hermes Control Channel, Owner-directed): the
        # Admin's own phone (admin_bridge2_number), arriving via bridge1
        # instead of bridge2, is now a second valid Hermes control
        # channel -- genuine is_from_me=0 inbound, no self-chat ambiguity.
        # The identity check is untouched (still admin_bridge2_number
        # only); only the channel set widened.
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await self._run(phone="8801880446111", source="bridge1")
        assert reply == ""
        mock_deliver.assert_awaited_once_with("check bridge health", "8801880446111", "bridge1")

    async def test_wrong_phone_and_wrong_source_both_denied(self):
        # Belt-and-suspenders: neither check alone is trusted to save the
        # other -- both wrong simultaneously still denies cleanly.
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await self._run(phone="8801958122300", source="bridge3")
        assert reply == router._DENIAL_MSG
        mock_deliver.assert_not_awaited()

    async def test_not_configured_returns_clear_error(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_settings", return_value=_FakeSettings(hermes_runner_secret="")), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await self._run()
        assert reply == router._HERMES_NOT_CONFIGURED_MSG
        mock_deliver.assert_not_awaited()

    async def test_empty_request_text_rejected(self):
        # Calls the handler directly (not through the regex dispatch layer,
        # which -- like _RE_REMEMBER's equivalent shape -- can't itself
        # produce an empty captured group after the outer .strip()) to
        # cover this defensive check in _handle_hermes_relay directly.
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await router._handle_hermes_relay("", "8801880446111", "bridge2")
        assert "লিখুন" in reply
        mock_deliver.assert_not_awaited()

    async def test_allowed_bridge2_superadmin_gets_no_ack_but_schedules_background_task(self):
        # 2026-08-12 UX fix: no intermediate "sent to Hermes" text -- an
        # empty string return still schedules the real background delivery,
        # it just doesn't produce a visible WhatsApp message for this turn
        # (see message_router's "is not None" check and bridge_poller's
        # "if reply:" check, both of which this relies on).
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await self._run(text="hermes: check bridge health", phone="8801880446111")
        assert reply == ""
        mock_deliver.assert_awaited_once_with("check bridge health", "8801880446111", "bridge2")

    async def test_second_call_within_cooldown_blocked(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            first = await self._run(text="hermes: first", phone="8801880446111")
            second = await self._run(text="hermes: second", phone="8801880446111")
        assert first == ""
        assert "অপেক্ষা" in second
        mock_deliver.assert_awaited_once()

    async def test_allowed_again_after_cooldown_elapses(self):
        with patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            router._hermes_cooldown["8801880446111"] = time.monotonic() - 20.0
            reply = await self._run(text="hermes: after cooldown", phone="8801880446111")
        assert reply == ""
        mock_deliver.assert_awaited_once()


class TestDeliverHermesReply:
    """_deliver_hermes_reply is the background task itself -- verifies it
    always enqueues *some* outbound reply and never raises, regardless of
    how hermes-runner responds."""

    async def test_success_enqueues_reply_text(self):
        mock_resp = type("R", (), {"status_code": 200, "json": lambda self: {"reply": "all healthy"}})()
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("check bridge health", "8801880446111")
        mock_enqueue.assert_awaited_once()
        args, kwargs = mock_enqueue.call_args
        assert args[0] == "8801880446111"
        assert "all healthy" in args[1]
        assert kwargs["source_bridge"] == "bridge2"
        assert kwargs["purpose"] == "hermes_relay_reply"

    async def test_posts_the_whatsapp_relay_readonly_key(self):
        """2026-08-05 Owner decision: the WhatsApp relay must always
        identify itself with its own fixed readonly_key, so it serializes
        against itself, not against Phase 5B job investigations."""
        mock_resp = type("R", (), {"status_code": 200, "json": lambda self: {"reply": "ok"}})()
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("check bridge health", "8801880446111")
        _, post_kwargs = mock_client.post.call_args
        assert post_kwargs["json"]["readonly_key"] == "readonly:whatsapp_relay"

    async def test_does_not_force_read_only_full_authority_phase1(self):
        """Full-authority Phase 1 (2026-08-13, Owner-approved): the live
        Super Admin relay turn must NOT force read_only=true any more --
        it should inherit whatever mode hermes-runner currently has
        persisted (READ by fail-closed default, BUILD/RUN only if the
        Owner explicitly elevated it via the existing `/api/hermes/mode`
        web endpoint). This is the one intentional behavioral change of
        this phase; test_posts_the_whatsapp_relay_readonly_key above was
        updated in the same change to stop asserting the old
        read_only=True value."""
        mock_resp = type("R", (), {"status_code": 200, "json": lambda self: {"reply": "ok"}})()
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("check bridge health", "8801880446111")
        _, post_kwargs = mock_client.post.call_args
        assert post_kwargs["json"]["read_only"] is False

    async def test_hermes_error_status_enqueues_error_text(self):
        mock_resp = type("R", (), {"status_code": 502, "json": lambda self: {"error": "Hermes runner unreachable"}})()
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("check bridge health", "8801880446111")
        args, _ = mock_enqueue.call_args
        assert "Hermes runner unreachable" in args[1]

    async def test_timeout_enqueues_timeout_message_not_raise(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=router.httpx.TimeoutException("timed out"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("check bridge health", "8801880446111")
        args, _ = mock_enqueue.call_args
        assert "সময়মতো" in args[1]

    async def test_enqueue_failure_does_not_raise(self):
        mock_resp = type("R", (), {"status_code": 200, "json": lambda self: {"reply": "ok"}})()
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock(side_effect=RuntimeError("db down"))):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("check bridge health", "8801880446111")  # must not raise

    async def test_bridge1_source_routes_reply_via_bridge1(self):
        # 2026-08-12 (bridge1 Hermes Control Channel): a request that
        # arrived via bridge1 must have its reply delivered back out via
        # bridge1 too, not the old hardcoded "bridge2" literal -- this is
        # the whole point of the new channel (the Admin receives the
        # reply on their own bridge2 number, sent FROM bridge1).
        mock_resp = type("R", (), {"status_code": 200, "json": lambda self: {"reply": "all healthy"}})()
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("check bridge health", "8801880446111", source_bridge="bridge1")
        mock_enqueue.assert_awaited_once()
        args, kwargs = mock_enqueue.call_args
        assert args[0] == "8801880446111"
        assert kwargs["source_bridge"] == "bridge1"
        assert kwargs["purpose"] == "hermes_relay_reply"


class TestHermesConversationContinuity:
    """2026-08-13, Bridge1 Hermes Control Channel follow-up: consecutive
    Admin messages on the same channel must resume the same Hermes
    conversation; different channels (bridge1 vs bridge2 self-chat) and
    idle-stale conversations must not."""

    def _mock_resp(self, reply="ok", session_id="sess-abc", status=200):
        return type("R", (), {
            "status_code": status,
            "json": lambda self: {"reply": reply, "hermes_session_id": session_id},
        })()

    async def test_first_call_stores_returned_session(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=self._mock_resp(session_id="new-session-1"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("hi", "8801880446111", source_bridge="bridge1")
        assert router._get_active_hermes_session("bridge1", "8801880446111") == "new-session-1"

    async def test_second_call_resumes_stored_session(self):
        router._remember_hermes_session("bridge1", "8801880446111", "existing-session")
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=self._mock_resp(session_id="existing-session"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("follow-up question", "8801880446111", source_bridge="bridge1")
        _, post_kwargs = mock_client.post.call_args
        assert post_kwargs["json"]["hermes_session_id"] == "existing-session"

    async def test_bridge1_and_bridge2_are_independent_conversation_threads(self):
        router._remember_hermes_session("bridge1", "8801880446111", "bridge1-session")
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=self._mock_resp(session_id="bridge2-session"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("hi via bridge2", "8801880446111", source_bridge="bridge2")
        _, post_kwargs = mock_client.post.call_args
        assert "hermes_session_id" not in post_kwargs["json"]  # bridge2 had no session of its own yet
        assert router._get_active_hermes_session("bridge1", "8801880446111") == "bridge1-session"  # untouched
        assert router._get_active_hermes_session("bridge2", "8801880446111") == "bridge2-session"

    async def test_idle_stale_session_is_not_resumed(self):
        router._hermes_conversation_sessions[("bridge1", "8801880446111")] = (
            "stale-session", time.monotonic() - router._HERMES_CONVERSATION_IDLE_TTL_SECONDS - 1,
        )
        assert router._get_active_hermes_session("bridge1", "8801880446111") is None
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=self._mock_resp(session_id="fresh-session"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("hi again", "8801880446111", source_bridge="bridge1")
        _, post_kwargs = mock_client.post.call_args
        assert "hermes_session_id" not in post_kwargs["json"]

    async def test_reset_keyword_clears_session_before_call(self):
        router._remember_hermes_session("bridge1", "8801880446111", "old-session")
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=self._mock_resp(session_id="brand-new-session"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("reset", "8801880446111", source_bridge="bridge1")
        _, post_kwargs = mock_client.post.call_args
        assert "hermes_session_id" not in post_kwargs["json"]  # old-session was cleared, not resumed
        assert router._get_active_hermes_session("bridge1", "8801880446111") == "brand-new-session"

    async def test_errored_call_clears_rather_than_stores_stale_session(self):
        router._remember_hermes_session("bridge1", "8801880446111", "existing-session")
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=router.httpx.TimeoutException("timed out"))
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router._deliver_hermes_reply("hi", "8801880446111", source_bridge="bridge1")
        # A timeout means hermes-runner may or may not have actually
        # created/advanced a session -- _call_hermes_readonly returns None
        # for the session id in this case, and _remember_hermes_session
        # correctly treats that as "nothing to remember" rather than
        # caching a guess.
        assert router._get_active_hermes_session("bridge1", "8801880446111") is None


class TestTriggerAdminInvestigation:
    """Phase 5B (2026-08-04, Owner-approved option (b) only): proactive
    alert-triggered investigation. Delivery always targets bridge2;
    fail-closed on every missing-config/empty-context/cooldown case."""

    async def test_disabled_by_default_no_send(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=False)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock()) as mock_call:
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")
        mock_enqueue.assert_not_awaited()
        mock_call.assert_not_awaited()

    async def test_empty_context_skipped_without_calling_hermes(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock()) as mock_call:
            await router.trigger_admin_investigation("dlq_alert", "   ")
        mock_enqueue.assert_not_awaited()
        mock_call.assert_not_awaited()

    async def test_missing_hermes_secret_skipped(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True, hermes_runner_secret="")), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock()) as mock_call:
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")
        mock_enqueue.assert_not_awaited()
        mock_call.assert_not_awaited()

    async def test_missing_bridge2_number_skipped(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True, admin_bridge2_number="")), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock()) as mock_call:
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")
        mock_enqueue.assert_not_awaited()
        mock_call.assert_not_awaited()

    async def test_enabled_and_configured_sends_to_bridge2(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock(return_value=("root cause: DLQ backlog from a bridge outage", None))):
            await router.trigger_admin_investigation("dlq_alert", "actionable=3, pending=1")
        mock_enqueue.assert_awaited_once()
        args, kwargs = mock_enqueue.call_args
        assert args[0] == "8801880446111"  # bridge2, never bridge1/bridge3
        assert "dlq_alert" in args[1]
        assert "root cause: DLQ backlog" in args[1]
        assert kwargs["source_bridge"] == "bridge2"
        assert kwargs["purpose"] == "phase5b_alert_investigation"

    async def test_request_text_includes_job_name_and_context(self):
        captured = {}

        async def _capture(request_text, *, log_label, readonly_key=None, hermes_session_id=None):
            captured["request_text"] = request_text
            captured["readonly_key"] = readonly_key
            captured["hermes_session_id"] = hermes_session_id
            return "ok", None

        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()), \
             patch.object(router, "_call_hermes_readonly", new=_capture):
            await router.trigger_admin_investigation("bridge_watchdog", "stale_bridges=bridge1(650s)")
        assert "bridge_watchdog" in captured["request_text"]
        assert "stale_bridges=bridge1(650s)" in captured["request_text"]
        # 2026-08-05 Owner decision: per-job readonly_key so independent
        # Phase 5B investigations (and the separate WhatsApp relay) don't
        # collide on hermes-runner's shared "new" lock bucket.
        assert captured["readonly_key"] == "readonly:job:bridge_watchdog"
        # 2026-08-13: Phase 5B investigations are deliberately always
        # stateless -- never passes a hermes_session_id, unlike the
        # WhatsApp relay's own conversation continuity.
        assert captured["hermes_session_id"] is None

    async def test_still_forces_read_only_full_authority_phase1(self):
        """Full-authority Phase 1 (2026-08-13, Owner-approved) gave
        _deliver_hermes_reply's live Super Admin turn the ability to
        inherit BUILD/RUN mode -- this function (an unattended, scheduler-
        triggered alert investigation) must NOT have gained that too. It
        does not pass force_read_only at all, so _call_hermes_readonly's
        default (True) must still be what actually reaches hermes-runner.
        This is the regression guard for the safety property documented in
        trigger_admin_investigation's own source comment right above its
        _call_hermes_readonly call site."""
        mock_resp = type("R", (), {"status_code": 200, "json": lambda self: {"reply": "ok"}})()
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.httpx, "AsyncClient") as mock_client_cls, \
             patch.object(router.outbound, "enqueue", new=AsyncMock()):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            await router.trigger_admin_investigation("dlq_alert", "actionable=3, pending=1")
        _, post_kwargs = mock_client.post.call_args
        assert post_kwargs["json"]["read_only"] is True

    async def test_second_call_within_cooldown_skipped(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock(return_value=("ok", None))):
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")
            await router.trigger_admin_investigation("dlq_alert", "actionable=5")
        mock_enqueue.assert_awaited_once()

    async def test_different_job_names_have_independent_cooldowns(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock(return_value=("ok", None))):
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")
            await router.trigger_admin_investigation("bridge_watchdog", "stale_bridges=x")
        assert mock_enqueue.await_count == 2

    async def test_allowed_again_after_cooldown_elapses(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock(return_value=("ok", None))):
            router._investigation_cooldown["dlq_alert"] = time.monotonic() - 4000  # past default 3600s cooldown
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")
        mock_enqueue.assert_awaited_once()

    async def test_hermes_call_exception_still_sends_error_report_not_raise(self):
        """_call_hermes_readonly itself never raises (returns an error
        string) -- confirms trigger_admin_investigation doesn't need its
        own duplicate exception guard around that call, and still delivers
        a report (with the error text) rather than silently doing nothing."""
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock()) as mock_enqueue, \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock(return_value=("⚠️ Hermes-এর সাথে যোগাযোগ করা যায়নি।", None))):
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")
        mock_enqueue.assert_awaited_once()
        args, _ = mock_enqueue.call_args
        assert "যোগাযোগ করা যায়নি" in args[1]

    async def test_enqueue_failure_does_not_raise(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings(hermes_alert_investigation_enabled=True)), \
             patch.object(router.outbound, "enqueue", new=AsyncMock(side_effect=RuntimeError("db down"))), \
             patch.object(router, "_call_hermes_readonly", new=AsyncMock(return_value=("ok", None))):
            await router.trigger_admin_investigation("dlq_alert", "actionable=3")  # must not raise

    async def test_never_uses_bridge1_or_bridge3(self):
        """Structural guard: this function must never reference bridge1 or
        bridge3 as a delivery target, only settings.admin_bridge2_number."""
        source = inspect.getsource(router.trigger_admin_investigation)
        assert "bridge1" not in source
        assert "bridge3" not in source
        assert 'source_bridge="bridge2"' in source


class TestNormalizePhone:
    def test_local_zero_prefixed_to_880(self):
        assert _normalize_phone("01712345678") == "8801712345678"

    def test_plus_prefix_stripped(self):
        assert _normalize_phone("+8801712345678") == "8801712345678"

    def test_spaces_and_dashes_stripped(self):
        assert _normalize_phone("017-1234 5678") == "8801712345678"


class TestProcessAdminNaturalQuery:
    """Owner decision 2026-08-04: admin free text with no 'AI '/structured-
    command prefix routes to the same tool-grounded fazle-core AI console
    as an explicit 'AI <question>' command (NOT the real Hermes -- see the
    function's own docstring, corrected 2026-08-04) -- see the function's
    own docstring for why this differs from the old B25(H4) raw-LLM
    fallback. EXCEPTION (2026-08-12): on the dedicated bridge2/superadmin
    Admin<->Hermes channel, unprefixed text now goes to the real Hermes
    instead -- see TestProcessAdminNaturalQueryHermesChannel below. None of
    these tests below pass source_bridge="bridge2", so the gate never
    matches and behavior here is unchanged from before 2026-08-12."""

    async def test_routes_plain_text_through_handle_ai_query(self):
        with patch.object(router, "_handle_ai_query", new=AsyncMock(return_value="reply text")) as mock_handle:
            reply = await process_admin_natural_query("Hi", "8801700000010")
        assert reply == "reply text"
        mock_handle.assert_awaited_once_with("Hi", "8801700000010")

    async def test_mixed_bangla_english_text_routes_through(self):
        with patch.object(router, "_handle_ai_query", new=AsyncMock(return_value="উত্তর")) as mock_handle:
            reply = await process_admin_natural_query("গতকাল কত টাকা পেমেন্ট করা হয়?", "8801700000011")
        assert reply == "উত্তর"
        mock_handle.assert_awaited_once()

    async def test_empty_text_returns_none_without_calling_handler(self):
        with patch.object(router, "_handle_ai_query", new=AsyncMock()) as mock_handle:
            reply = await process_admin_natural_query("   ", "8801700000012")
        assert reply is None
        mock_handle.assert_not_awaited()

    async def test_exception_in_handler_returns_safe_message_not_raise(self):
        with patch.object(router, "_handle_ai_query", new=AsyncMock(side_effect=RuntimeError("boom"))):
            reply = await process_admin_natural_query("kichu bolo", "8801700000013")
        assert "সমস্যা" in reply

    async def test_bridge2_number_without_bridge2_source_still_uses_ai_console(self):
        # Phone matches the designated Hermes number, but source_bridge
        # doesn't say "bridge2" (e.g. default "", or misrouted) -- the gate
        # requires both, so this must NOT reach Hermes.
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_handle_ai_query", new=AsyncMock(return_value="console reply")) as mock_ai, \
             patch.object(router, "_handle_hermes_relay", new=AsyncMock()) as mock_hermes:
            reply = await process_admin_natural_query("কোথায় আছ?", "8801880446111")
        assert reply == "console reply"
        mock_ai.assert_awaited_once()
        mock_hermes.assert_not_awaited()

    def test_does_not_use_the_old_ungrounded_llm_call(self):
        """Structural guard against reintroducing the B25(H4) failure mode:
        this function must go through _handle_ai_query (RAG+tool-grounded),
        not a bare ai.generate_reply()-style call."""
        source = inspect.getsource(router.process_admin_natural_query)
        assert "_handle_ai_query" in source
        assert "generate_reply" not in source


class TestProcessAdminNaturalQueryHermesChannel:
    """2026-08-12 (direct Owner instruction, Hermes conversation UX fix):
    on the dedicated bridge2/superadmin Admin<->Hermes channel, unprefixed
    free text is delegated straight to the real Hermes relay instead of
    fazle-core's AI console, so the Admin doesn't have to type "Hermes,"
    on every message. Every other channel/admin is unaffected (see
    TestProcessAdminNaturalQuery above)."""

    async def test_bridge2_superadmin_no_prefix_delegates_to_hermes_relay(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_handle_ai_query", new=AsyncMock()) as mock_ai, \
             patch.object(router, "_handle_hermes_relay", new=AsyncMock(return_value="")) as mock_hermes:
            reply = await process_admin_natural_query(
                "কোথায় আছ?", "8801880446111", source_bridge="bridge2",
            )
        assert reply == ""
        mock_hermes.assert_awaited_once_with("কোথায় আছ?", "8801880446111", "bridge2")
        mock_ai.assert_not_awaited()

    async def test_bridge2_superadmin_tool_question_no_prefix_delegates_to_hermes(self):
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_handle_ai_query", new=AsyncMock()) as mock_ai, \
             patch.object(router, "_handle_hermes_relay", new=AsyncMock(return_value="")) as mock_hermes:
            reply = await process_admin_natural_query(
                "আজ FPE cash transaction কত হয়েছে?", "8801880446111", source_bridge="bridge2",
            )
        assert reply == ""
        mock_hermes.assert_awaited_once()
        mock_ai.assert_not_awaited()

    async def test_hermes_relay_denial_and_audit_still_enforced(self):
        # _handle_hermes_relay does its own full _authorize() call -- a
        # denial there (RBAC/fail-closed) must still surface here, proving
        # authentication isn't bypassed just because the "Hermes," prefix
        # was omitted.
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_deny_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()) as mock_audit, \
             patch.object(router, "_handle_ai_query", new=AsyncMock()) as mock_ai:
            reply = await process_admin_natural_query(
                "কোথায় আছ?", "8801880446111", source_bridge="bridge2",
            )
        assert reply == router._DENIAL_MSG
        mock_ai.assert_not_awaited()
        mock_audit.assert_awaited_once()

    async def test_wrong_phone_on_bridge2_source_still_uses_ai_console(self):
        # source_bridge says "bridge2" but the sender isn't the designated
        # Hermes admin phone -- must not reach Hermes.
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_handle_ai_query", new=AsyncMock(return_value="console reply")) as mock_ai, \
             patch.object(router, "_handle_hermes_relay", new=AsyncMock()) as mock_hermes:
            reply = await process_admin_natural_query(
                "কোথায় আছ?", "8801958122300", source_bridge="bridge2",
            )
        assert reply == "console reply"
        mock_hermes.assert_not_awaited()

    async def test_bridge1_admin_no_prefix_still_uses_ai_console(self):
        # bridge1's OWN number (not the Admin's bridge2 number) is still
        # correctly routed to the AI console, not Hermes -- the 2026-08-12
        # widening is keyed on phone identity (admin_bridge2_number), never
        # on "any admin-recognized phone arriving via bridge1".
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_handle_ai_query", new=AsyncMock(return_value="console reply")) as mock_ai, \
             patch.object(router, "_handle_hermes_relay", new=AsyncMock()) as mock_hermes:
            reply = await process_admin_natural_query(
                "কোথায় আছ?", "8801958122300", source_bridge="bridge1",
            )
        assert reply == "console reply"
        mock_hermes.assert_not_awaited()
        mock_ai.assert_awaited_once()

    async def test_bridge2_admin_phone_via_bridge1_delegates_to_hermes_relay(self):
        # 2026-08-12 (bridge1 Hermes Control Channel, Owner-directed): the
        # Admin's OWN phone (admin_bridge2_number), arriving via bridge1
        # instead of bridge2, is the new dedicated control channel -- no
        # "hermes," prefix needed here either, same as the original bridge2
        # self-chat bypass above.
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router, "_handle_ai_query", new=AsyncMock()) as mock_ai, \
             patch.object(router, "_handle_hermes_relay", new=AsyncMock(return_value="")) as mock_hermes:
            reply = await process_admin_natural_query(
                "কোথায় আছ?", "8801880446111", source_bridge="bridge1",
            )
        assert reply == ""
        mock_hermes.assert_awaited_once_with("কোথায় আছ?", "8801880446111", "bridge1")
        mock_ai.assert_not_awaited()

    async def test_reply_delivery_channel_matches_request_channel_bridge2(self):
        """No new channel-selection mechanism inside process_admin_natural_
        query itself: the implicit path reuses _handle_hermes_relay ->
        _deliver_hermes_reply, whose outbound reply now targets whichever
        channel the request arrived on. This is the bridge2 regression
        guard -- the original, working self-chat path must keep replying
        via bridge2, unchanged. See also
        test_reply_delivery_channel_matches_request_channel_bridge1 (the
        new channel) and TestDeliverHermesReply for _deliver_hermes_reply's
        own direct coverage of this parameter. Split into two test methods
        (not one with two calls) because _handle_hermes_relay's own
        cooldown would otherwise block the second call within the same
        test -- the autouse _reset_cooldown fixture only resets between
        tests."""
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await process_admin_natural_query(
                "কোথায় আছ?", "8801880446111", source_bridge="bridge2",
            )
            await asyncio.sleep(0)
        assert reply == ""
        mock_deliver.assert_awaited_once_with("কোথায় আছ?", "8801880446111", "bridge2")

    async def test_reply_delivery_channel_matches_request_channel_bridge1(self):
        """The new channel: a request arriving via bridge1 must have its
        reply delivered back out via bridge1 too. See the bridge2 sibling
        test above for why this is a separate test method."""
        with patch.object(router, "get_settings", return_value=_FakeSettings()), \
             patch.object(router.rbac, "check_permission", new=AsyncMock(return_value=_allow_perm())), \
             patch.object(router.rbac, "record_audit", new=AsyncMock()), \
             patch.object(router, "_deliver_hermes_reply", new=AsyncMock()) as mock_deliver:
            reply = await process_admin_natural_query(
                "কোথায় আছ?", "8801880446111", source_bridge="bridge1",
            )
            await asyncio.sleep(0)
        assert reply == ""
        mock_deliver.assert_awaited_once_with("কোথায় আছ?", "8801880446111", "bridge1")


class TestMessageRouterAdminDispatchOrder:
    """Source-order regression guard (matches this repo's test_c0d_source_
    bridge_fix.py convention) for modules.message_router's admin branch.
    Structured commands and NL admin queries must still be checked -- and
    win -- before anything else. The draft/payment/attendance keyword
    shortcuts must still win for every sender/channel EXCEPT the Admin on
    a dedicated Hermes control channel (2026-08-13 fix, see the early-exit
    check's own comment) -- for that one case, Hermes must be reached
    first, which is exactly why a second process_admin_natural_query(...)
    call site now exists earlier in the source than the keyword shortcuts,
    intentionally. The GENERIC fallback (used by every other admin/
    channel) is still the LAST such call site, still after the keyword
    shortcuts, still before the static help text."""

    def test_structured_checks_precede_hermes_channel_bypass_precedes_keywords(self):
        import modules.message_router as mr
        source = inspect.getsource(mr)

        admin_branch = source[source.index('if role_str == "admin":'):]
        i_structured = admin_branch.index("is_admin_command(text)")
        i_nl = admin_branch.index("is_nl_admin_query(text)")
        i_ai_prefix = admin_branch.index("is_admin_ai_command(text)")
        i_hermes_channel_bypass = admin_branch.index(
            'source in ("bridge1", "bridge2")'
        )
        i_keywords = admin_branch.index('"draft" in lower')

        assert i_structured < i_nl < i_ai_prefix < i_hermes_channel_bypass < i_keywords, (
            "Admin dispatch order changed -- structured commands and NL "
            "admin queries must still win first; the 2026-08-13 Hermes-"
            "control-channel bypass (Admin phone + bridge1/bridge2) must "
            "run before the draft/payment/attendance keyword shortcuts, "
            "not after -- that ordering is the actual fix for the silent-"
            "draft bug those keywords caused on bridge1 (see the bypass "
            "check's own comment)."
        )

    def test_generic_fallback_still_last_before_static_help(self):
        """The ORIGINAL, generic process_admin_natural_query() call (every
        admin/channel not covered by the 2026-08-13 early bypass) must
        still be the LAST thing checked, after the keyword shortcuts and
        before the static help text -- unchanged from before this fix."""
        import modules.message_router as mr
        source = inspect.getsource(mr)

        admin_branch = source[source.index('if role_str == "admin":'):]
        i_keywords = admin_branch.index('"draft" in lower')
        # rindex, not index: the 2026-08-13 bypass added an EARLIER call
        # site with the identical call signature -- this must find the
        # later (generic-fallback) one specifically.
        i_natural = admin_branch.rindex("process_admin_natural_query(text, sender, source_bridge=source)")
        i_static_help = admin_branch.index("কমান্ড বুঝিনি")

        assert i_keywords < i_natural < i_static_help, (
            "The generic process_admin_natural_query() fallback (for admins/"
            "channels other than the 2026-08-13 Hermes-control-channel "
            "bypass) must still run after the keyword shortcuts and before "
            "the static help text."
        )

    def test_natural_query_import_present(self):
        import modules.message_router as mr
        assert hasattr(mr, "process_admin_natural_query")
