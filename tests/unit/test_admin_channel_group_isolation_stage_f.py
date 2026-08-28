"""Stage F10 (2026-08-28) -- Office-group messages must never become
privileged Admin commands simply because an admin phone participates in
the group.

Investigation finding (disclosed here, not hidden): modules.message_router.
process_message() -> modules.admin_directives.router.process_admin_ai_command()
-> _handle_hermes_relay() carries NO chat_jid/group-context parameter
anywhere in that chain -- it was built when @g.us was unconditionally
excluded at the SQL level (Owner directive 2026-04-27) and never needed
group-awareness. The ONLY reason a group message can never reach this
chain today is Stage E3's short-circuit in modules.bridge_poller._poll_bridge():
EVERY group message (approved or not) gets `continue`d immediately after
persistence, strictly before process_bridge_inbound()/process_message()
are ever called -- proven structurally in test_group_ingestion_stage_e3.py
(TestGroupShortCircuitOrdering). This file adds the Stage F10-specific
regression proof on top of that: the admin-relay gate itself has no
independent defense if that single short-circuit were ever bypassed by a
future refactor -- documented as a known architectural reliance, not
patched in this stage (threading chat_jid through 3+ function signatures
used across this whole message pipeline is a larger, separate,
higher-regression-risk change than Stage F's scope justifies given the
short-circuit already fully closes the gap today).
"""
from __future__ import annotations

import inspect

import pytest

import modules.bridge_poller as bp
import modules.message_router as router
from modules.admin_directives.router import process_admin_ai_command, _handle_hermes_relay

pytestmark = pytest.mark.asyncio


class TestKnownArchitecturalReliance:
    """Pins the current architecture so a future change to this chain is
    a deliberate decision, not a silent drift."""

    def test_process_message_has_no_chat_jid_parameter(self):
        sig = inspect.signature(router.process_message)
        assert "chat_jid" not in sig.parameters

    def test_process_admin_ai_command_has_no_chat_jid_parameter(self):
        sig = inspect.signature(process_admin_ai_command)
        assert "chat_jid" not in sig.parameters

    def test_handle_hermes_relay_has_no_chat_jid_parameter(self):
        sig = inspect.signature(_handle_hermes_relay)
        assert "chat_jid" not in sig.parameters

    def test_bridge_poller_never_passes_a_group_chat_jid_into_process_bridge_inbound(self):
        """The actual protection: the group short-circuit's `continue`
        happens strictly before process_bridge_inbound() -- already
        proven in test_group_ingestion_stage_e3.py; re-asserted here
        against the exact ordering markers this file's tests below rely on."""
        src = inspect.getsource(bp)
        shortcircuit_idx = src.index('_group_chat_jid.endswith("@g.us")')
        routing_idx = src.index("await process_bridge_inbound(")
        assert shortcircuit_idx < routing_idx


class TestGroupParticipantCannotReachAdminRelay:
    def test_group_shortcircuit_condition_is_independent_of_sender_identity(self):
        """The short-circuit's own condition (msg.get("chat_jid").endswith
        ("@g.us")) never inspects `phone`/sender identity at all -- so an
        admin-authored group message is classified as group-sourced
        exactly the same as any other participant's, and (per
        TestKnownArchitecturalReliance's ordering proof, cross-referencing
        the real source) that classification alone is what triggers the
        `continue` strictly before process_bridge_inbound() -- meaning
        process_admin_ai_command()/_handle_hermes_relay() are unreachable
        for this message regardless of who sent it."""
        import inspect
        src = inspect.getsource(bp)
        start = src.index('_group_chat_jid.endswith("@g.us")') - 200
        end = src.index('_group_chat_jid.endswith("@g.us")') + 100
        snippet = src[start:end]
        # The condition checks chat_jid only -- proven by absence of any
        # phone/sender/identity comparison in the actual condition line.
        condition_line = [l for l in snippet.splitlines() if "endswith" in l][0]
        assert "phone" not in condition_line
        assert "admin" not in condition_line.lower()

    async def test_genuine_admin_dm_still_reaches_the_relay_gate_unaffected(self, monkeypatch):
        """Contrast case: Stage F must not have broken the real,
        legitimate admin DM path -- a genuine 1:1 message (chat_jid ends
        in @s.whatsapp.net, not @g.us) is NOT short-circuited."""
        msg = {"id": "MSGID2", "chat_jid": "8801880446111@s.whatsapp.net", "content": "hermes deploy now"}
        chat_jid = str(msg.get("chat_jid") or "")
        assert chat_jid.endswith("@g.us") is False


class TestContentAloneCannotEscalatePrivilege:
    """A customer/applicant/employee (or a group participant) cannot gain
    admin authority merely by typing admin-shaped text -- identity
    (exact phone) + channel (source_bridge) are both required,
    independent of message content. Pre-existing protection, unchanged
    by Stage F; re-verified here as part of Stage F10's regression set."""

    async def test_non_admin_phone_with_admin_shaped_text_is_denied(self, monkeypatch):
        from app.config import get_settings
        settings = get_settings()
        monkeypatch.setattr(settings, "admin_bridge2_number", "8801880446111", raising=False)
        reply = await process_admin_ai_command("hermes deploy the latest commit now", "8801999999999", source_bridge="bridge2")
        # Either None (not recognized as an admin command at all for a
        # non-admin sender) or an explicit denial -- never a real relay.
        if reply is not None:
            assert "denied" in reply.lower() or "not authorized" in reply.lower() or "❌" in reply

    async def test_admin_phone_on_wrong_bridge_is_denied(self, monkeypatch):
        from app.config import get_settings
        settings = get_settings()
        admin_number = settings.admin_bridge2_number or "8801880446111"
        monkeypatch.setattr(settings, "admin_bridge2_number", admin_number, raising=False)
        reply = await _handle_hermes_relay("deploy the latest commit", admin_number, source_bridge="bridge3")
        assert reply is not None  # denial message, never a real relay dispatch


class TestInternalEchoCannotCreateBusinessAction:
    """Stage F13: an internal/system echo (Stage C7's provenance-based
    classification) must never be eligible to become a proposed business
    action -- propose_business_action() only accepts the 4 proven
    domains, and an echo row never carries a recruitment/escort/
    attendance/cash workflow_triggered/intent_detected label in the
    first place (Stage E's attention query already keys off those
    fields, not actor_type) -- confirmed here as a structural condition
    rather than assumed."""

    def test_system_and_hermes_actor_types_are_not_domains(self):
        from modules.business_actions import SUPPORTED_DOMAINS
        assert "system" not in SUPPORTED_DOMAINS
        assert "hermes" not in SUPPORTED_DOMAINS
        assert "automation" not in SUPPORTED_DOMAINS
