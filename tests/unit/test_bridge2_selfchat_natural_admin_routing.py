"""Bridge2 self-chat natural-language Admin->Hermes routing fix
(2026-08-27, Owner-directed).

Root cause this closes: modules.bridge_poller's outgoing/self-chat scanner
(the ONLY channel through which the Owner's own is_from_me=1 Bridge2
self-chat messages can ever reach Hermes -- see
_fetch_outgoing_escort_completions()'s docstring) only ever recognized the
explicit "hermes,"/"hermes:" prefix via _RE_HERMES. Any unprefixed natural-
language self-chat message from the Super Admin fell through every check in
the scan and was silently dropped -- confirmed live for 3 real messages on
2026-08-27 (see that session's read-only trace).

Fix: once none of the deterministic operational patterns above it match
(explicit "hermes," prefix, release confirmation, escort completion, trusted-
group order), an unprefixed bridge2 self-chat message -- outside the Trusted
Group -- is now passed to the SAME process_admin_natural_query() the
genuinely-inbound (is_from_me=0) path already uses for unprefixed text on
bridge1/bridge2 (see modules.admin_directives.router.process_admin_natural_
query's own docstring, and tests/unit/test_admin_ai_router.py's
TestProcessAdminNaturalQuery for that function's own, already-covered
identity/channel gate and routing to _handle_hermes_relay()).

This file only proves the NEW call site in bridge_poller -- ordering,
scoping, and non-duplication -- via source inspection, matching this repo's
existing pattern for this exact loop (see
tests/unit/test_bridge_poller_system_notification_guard.py's
test_guard_call_precedes_handle_admin_escort_completion_in_source and
tests/unit/test_trusted_group_escort_orders.py's own module docstring): the
outgoing scan loop needs a live bridge SQLite session to drive end-to-end,
so it is proven wired here rather than re-executed. process_admin_natural_
query()'s own internal identity/channel/RBAC/routing logic is unchanged and
already covered by tests/unit/test_admin_ai_router.py -- not re-tested here.
"""
from __future__ import annotations

import inspect

import pytest

import modules.bridge_poller as bp

pytestmark = pytest.mark.unit


def _outgoing_scan_source() -> str:
    return inspect.getsource(bp)


class TestExplicitHermesPrefixPathUnchanged:
    def test_re_hermes_check_still_present_and_gated_to_bridge2(self):
        source = _outgoing_scan_source()
        assert 'if bridge_name == "bridge2" and _RE_HERMES.match(otext.strip()):' in source

    def test_explicit_prefix_branch_still_calls_process_admin_ai_command(self):
        source = _outgoing_scan_source()
        prefix_idx = source.index('if bridge_name == "bridge2" and _RE_HERMES.match(otext.strip()):')
        call_idx = source.index("ack = await process_admin_ai_command(", prefix_idx)
        # must be the very next relevant statement, not e.g. reused lower down
        assert call_idx - prefix_idx < 400

    def test_process_admin_natural_query_now_imported_alongside_it(self):
        source = _outgoing_scan_source()
        assert "process_admin_natural_query" in source
        assert "from modules.admin_directives.router import" in source


class TestNaturalLanguageFallbackWiring:
    def test_fallback_block_present_and_calls_process_admin_natural_query(self):
        source = _outgoing_scan_source()
        assert 'if bridge_name == "bridge2" and not is_trusted_group_msg:' in source
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        call_idx = source.index("ack = await process_admin_natural_query(", fallback_idx)
        assert call_idx - fallback_idx < 400

    def test_fallback_passes_admin_number_and_bridge_name_not_free_text_identity(self):
        """sender_phone must come from the trusted admin_number local (derived
        from settings.bridge1_number/bridge2_number), never from message
        content -- so non-admin/spoofed text can never reach the relay."""
        source = _outgoing_scan_source()
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        block = source[fallback_idx:fallback_idx + 700]
        assert "sender_phone=admin_number" in block
        assert "source_bridge=bridge_name" in block

    def test_fallback_only_enqueues_when_ack_is_truthy(self):
        """Matches the explicit-prefix branch's own guard exactly -- since
        _handle_hermes_relay() returns "" for its immediate ack (the real
        reply is delivered later, async, via _deliver_hermes_reply()), this
        prevents a second, redundant outbound message and any risk of a
        feedback loop through the outgoing scanner picking its own ack back
        up."""
        source = _outgoing_scan_source()
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        block = source[fallback_idx:fallback_idx + 900]
        assert "if ack:" in block
        assert 'idempotency_key=f"hermes-self-relay-nl:{omsg_id}"' in block

    def test_fallback_never_raises_out_of_the_scan_loop(self):
        source = _outgoing_scan_source()
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        block = source[fallback_idx:fallback_idx + 1400]
        assert "except Exception as _hermes_nl_err:" in block


class TestOrderingAgainstDeterministicOperationalChecks:
    """The fallback must be the LAST thing checked in the outgoing scan --
    every deterministic, higher-priority pattern gets first refusal, and
    each of those branches `continue`s before the fallback could ever be
    reached for that message."""

    def test_fallback_is_positioned_after_release_confirmation_check(self):
        source = _outgoing_scan_source()
        release_idx = source.index("if is_release_confirmation(otext):")
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        assert release_idx < fallback_idx

    def test_fallback_is_positioned_after_escort_completion_check(self):
        source = _outgoing_scan_source()
        escort_idx = source.index("if is_completed_escort_draft(otext):")
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        assert escort_idx < fallback_idx

    def test_fallback_is_positioned_after_trusted_group_order_check(self):
        source = _outgoing_scan_source()
        order_idx = source.index("if is_trusted_group_msg and is_order_complete(otext):")
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        assert order_idx < fallback_idx

    def test_fallback_is_the_last_branch_in_the_outgoing_for_loop(self):
        """No other outgoing-scan branch is defined below the fallback --
        proves nothing added later in the loop could still be shadowed by
        it, and that the fallback really is the final catch-all."""
        source = _outgoing_scan_source()
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        loop_end_idx = source.index("if new_out_cursor > out_cursor:", fallback_idx)
        block = source[fallback_idx:loop_end_idx]
        # only this branch's own `if`s/continue should appear before the loop ends
        assert block.count("                    if ") == 1

    def test_every_deterministic_branch_continues_before_reaching_fallback(self):
        """Each of the 4 branches above the fallback (prefix, release,
        escort completion, group order) ends its own `continue` before the
        fallback's own `if` statement -- i.e. they are true early-exits, not
        fallthroughs that could double-invoke Hermes for the same message."""
        source = _outgoing_scan_source()
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        preceding = source[:fallback_idx]
        prefix_idx = preceding.index('if bridge_name == "bridge2" and _RE_HERMES.match(otext.strip()):')
        release_idx = preceding.index("if is_release_confirmation(otext):")
        escort_idx = preceding.index("if is_completed_escort_draft(otext):")
        order_idx = preceding.index("if is_trusted_group_msg and is_order_complete(otext):")
        for start, nxt in (
            (prefix_idx, release_idx),
            (release_idx, escort_idx),
            (escort_idx, order_idx),
            (order_idx, fallback_idx),
        ):
            segment = preceding[start:nxt]
            assert "continue" in segment, (
                f"branch starting at offset {start} must reach a `continue` "
                "before the next branch begins"
            )


class TestSafetyScoping:
    def test_fallback_excludes_trusted_group_messages(self):
        source = _outgoing_scan_source()
        assert "not is_trusted_group_msg" in source

    def test_fallback_scoped_to_bridge2_only(self):
        source = _outgoing_scan_source()
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        assert source[fallback_idx:].startswith(
            'if bridge_name == "bridge2" and not is_trusted_group_msg:'
        )

    def test_no_new_authorization_logic_only_reuses_existing_function(self):
        """No new RBAC/identity/channel-check code was introduced in the
        fallback block itself -- it delegates entirely to process_admin_
        natural_query(), which already carries its own, separately-tested
        identity + channel gate (tests/unit/test_admin_ai_router.py)."""
        source = _outgoing_scan_source()
        fallback_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        block = source[fallback_idx:fallback_idx + 900]
        for forbidden in ("admin_bridge2_number", "check_permission", "record_audit"):
            assert forbidden not in block
