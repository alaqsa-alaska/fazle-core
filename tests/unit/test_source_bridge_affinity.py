"""Unit tests -- source-bridge affinity (2026-08-21, urgent bridge3
recruitment-reply recovery pass, Task 2/9).

Owner's stated concern: "the Super Admin's bridge, current Hermes relay
bridge, or a default bridge" must never override a candidate's own
source_bridge for an automated reply. Live investigation (2026-08-21)
found the actual automated customer-reply/admin-Hermes-reply pipeline in
modules.bridge_poller already does this correctly -- every inbound-to-
outbound enqueue call uses source_bridge=bridge_name (the bridge THIS
message arrived on), never a fixed/admin/default bridge. These tests lock
that in with source-inspection (matching the existing convention in
test_phase_e_grounding_fixes.py for this same large, hard-to-unit-mock
function) so a future edit can't silently reintroduce a hardcoded bridge.

The actual wrong-bridge bug found live (2026-08-21) was NOT in this
automated pipeline -- it was in fazle-mcp's send_whatsapp_message MCP tool
schema, which advertised source_bridge="bridge2" as its own default even
though the underlying module had already dropped its silent default. That
regression lives in fazle-mcp/tests/test_send_whatsapp_tools.py, not here.
"""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


class TestCustomerReplyBridgeAffinity:
    def test_customer_reply_enqueue_uses_the_inbound_bridge_name(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        # The customer-reply enqueue_outbound() call must pass
        # source_bridge=bridge_name (the variable holding whichever bridge
        # THIS message arrived on), not a literal "bridge1"/"bridge2" or
        # an admin-number-derived value.
        i = source.index('purpose=f"customer-reply:{msg_intent')
        block = source[max(0, i - 400):i]
        assert "source_bridge=bridge_name" in block

    def test_admin_hermes_reply_enqueue_also_uses_the_inbound_bridge_name(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        i = source.index('purpose=f"admin-hermes-reply:{msg_intent')
        block = source[max(0, i - 400):i]
        assert "source_bridge=bridge_name" in block

    def test_bridge3_has_no_cross_bridge_fallback(self):
        """bridge3 (REC2) is deliberately excluded from the bridge1<->
        bridge2 fallback swap -- a failed bridge3 send must stay a bridge3
        failure (queued/failed/dlq), never silently retry via a different
        WhatsApp number the candidate never messaged."""
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        i = source.index('fallback = {"bridge1": "bridge2", "bridge2": "bridge1"}')
        line = source[i:source.index("\n", i)]
        assert '"bridge3"' not in line

    def test_recruitment_intent_never_gets_a_fallback_bridge(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        i = source.index('fallback = {"bridge1": "bridge2", "bridge2": "bridge1"}')
        block = source[max(0, i - 600):i]
        assert 'if msg_intent != "recruitment":' in block


class TestCrossBridgeLoopGuardStillIntact:
    """Regression guard: the existing 2026-08-04 loop-guard fix (see
    test_phase_e_grounding_fixes.py) must still prevent an admin reply
    from bouncing to another bridge's own number -- i.e. affinity to the
    inbound bridge must never be achieved by weakening this guard."""

    def test_loop_guard_diverts_to_draft_not_auto_send(self):
        import modules.bridge_poller as bp
        source = inspect.getsource(bp)
        i = source.index("LOOP_GUARD: admin reply recipient")
        block = source[i:i + 400]
        assert "_save_draft" in block
