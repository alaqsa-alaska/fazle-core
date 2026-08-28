"""Stage B (2026-08-28, canonical-history baseline plan) -- actor
attribution, Hermes-relay persistence, device-outbound persistence, and
genuine inbound intent persistence.

Covers:
  B1 -- migration 073's actor_type/actor_id columns, consumed via
        modules.bridge_poller._save_message()'s new optional params.
  B2 -- _deliver_hermes_reply() now also persists a canonical
        wbom_whatsapp_messages row, idempotent on retry.
  B3 -- the Bridge2 self-chat outgoing scanner now persists the
        device-originated Admin message itself (direction='outbound',
        actor_type='human_device'), independent of and never able to
        affect command processing.
  B4 -- every existing writer of wbom_whatsapp_messages passes an
        explicit actor_type at its own call site (source-inspection
        coverage check, matching this repo's own established pattern for
        proving a loop/call-site is actually wired, not just defined).
  B5 -- classify_intent()'s genuine raw output is persisted onto the
        inbound row it was computed for, snapshotted before the
        recruitment-eligibility override that coarsens msg_intent to
        "recruitment" for downstream workflow_triggered tagging (that
        downstream use is completely unchanged).

_save_message() and the self-chat scanner both need a live Postgres
connection to exercise their real INSERT/UPDATE end to end -- this file
mocks modules.bridge_poller.fetch_one/execute directly (this repo's own
established pattern, see tests/unit/test_recruitment_recovery_sweep.py)
rather than requiring a live DB, and uses source-inspection for the parts
of the outgoing scan loop that need a live bridge SQLite session to drive
(same pattern as tests/unit/test_bridge2_selfchat_natural_admin_routing.py
and tests/unit/test_bridge_poller_send_gate_timing_fix.py from earlier
this session).
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

import modules.bridge_poller as bp

pytestmark = pytest.mark.unit


# ── B1/B2/B3 mechanism: _save_message()'s idempotency ──────────────────────

class TestSaveMessageIdempotency:
    async def test_no_source_message_ref_means_null_hash_unchanged_behavior(self):
        """Every pre-existing caller doesn't pass source_message_ref --
        must still insert with message_hash=NULL, byte-identical to this
        function's behavior before the parameter existed."""
        fake_row = {"message_id": 42}
        with patch("modules.bridge_poller.fetch_one", new=AsyncMock(return_value=fake_row)) as mock_fetch:
            result = await bp._save_message("bridge3", "8801700000000", "hi", "outbound", actor_type="automation")
        assert result == 42
        args = mock_fetch.call_args.args
        assert args[-1] is None  # message_hash positional param

    async def test_source_message_ref_produces_deterministic_hash(self):
        """Same source_message_ref -> same message_hash both times, so the
        table's own pre-existing unique index (uq_wbom_messages_hash) can
        actually catch a retry."""
        captured_hashes = []

        async def _capture(*args, **kwargs):
            captured_hashes.append(args[-1])
            return {"message_id": 1}

        with patch("modules.bridge_poller.fetch_one", new=AsyncMock(side_effect=_capture)):
            await bp._save_message(
                "bridge1", "8801880446111", "body", "outbound",
                actor_type="hermes", source_message_ref="hermes_relay_reply:999",
            )
            await bp._save_message(
                "bridge1", "8801880446111", "body", "outbound",
                actor_type="hermes", source_message_ref="hermes_relay_reply:999",
            )
        assert len(captured_hashes) == 2
        assert captured_hashes[0] is not None
        assert captured_hashes[0] == captured_hashes[1]

    async def test_conflict_on_retry_returns_none_no_duplicate(self):
        """Simulates the real ON CONFLICT (message_hash) DO NOTHING
        behavior: the second call with the same ref hits the unique
        index and RETURNING produces no row -- fetch_one returns None,
        and _save_message must surface that as None (no new row), never
        raise, never fabricate a second message_id."""
        with patch("modules.bridge_poller.fetch_one", new=AsyncMock(side_effect=[{"message_id": 7}, None])):
            first = await bp._save_message(
                "bridge1", "8801880446111", "body", "outbound",
                actor_type="hermes", source_message_ref="hermes_relay_reply:1000",
            )
            second = await bp._save_message(
                "bridge1", "8801880446111", "body", "outbound",
                actor_type="hermes", source_message_ref="hermes_relay_reply:1000",
            )
        assert first == 7
        assert second is None

    async def test_sql_includes_on_conflict_do_nothing_and_actor_columns(self):
        with patch("modules.bridge_poller.fetch_one", new=AsyncMock(return_value=None)) as mock_fetch:
            await bp._save_message("bridge2", "x", "y", "outbound", actor_type="human_device")
        sql = mock_fetch.call_args.args[0]
        assert "ON CONFLICT (message_hash) DO NOTHING" in sql
        assert "actor_type" in sql
        assert "actor_id" in sql


# ── B2: Hermes relay reply canonical persistence ────────────────────────────

class TestHermesReplyCanonicalPersistence:
    async def test_persists_with_correct_direction_actor_and_ref(self, monkeypatch):
        import modules.admin_directives.router as router

        async def _fake_call_hermes_readonly(*a, **k):
            return "the answer", "sess-1"

        monkeypatch.setattr(router, "_call_hermes_readonly", _fake_call_hermes_readonly)
        monkeypatch.setattr(router, "_get_active_hermes_session", lambda *a, **k: None)
        monkeypatch.setattr(router, "_remember_hermes_session", lambda *a, **k: None)
        enqueue_mock = AsyncMock(return_value=5750)
        monkeypatch.setattr(router.outbound, "enqueue", enqueue_mock)
        save_message_mock = AsyncMock(return_value=123)
        monkeypatch.setattr("modules.bridge_poller._save_message", save_message_mock)

        await router._deliver_hermes_reply("what's my status", "8801880446111", source_bridge="bridge1")

        save_message_mock.assert_awaited_once()
        args, kwargs = save_message_mock.call_args
        assert args[0] == "bridge1"
        assert args[1] == "8801880446111"
        assert args[3] == "outbound"
        assert kwargs["actor_type"] == "hermes"
        assert kwargs["actor_id"] == "hermes-runner"
        assert kwargs["source_message_ref"] == "hermes_relay_reply:5750"
        assert "🤖 Hermes:" in args[2]

    async def test_retry_with_same_qid_uses_same_source_message_ref(self, monkeypatch):
        """Two deliveries that both resolve to the same outbound queue id
        (a genuine retry of the same row) must produce the same
        source_message_ref -- the actual duplicate-prevention guarantee
        lives in _save_message()'s own message_hash/ON CONFLICT mechanism
        (proven above), this proves the call site feeds it consistently."""
        import modules.admin_directives.router as router

        async def _fake_call_hermes_readonly(*a, **k):
            return "same answer", "sess-1"

        monkeypatch.setattr(router, "_call_hermes_readonly", _fake_call_hermes_readonly)
        monkeypatch.setattr(router, "_get_active_hermes_session", lambda *a, **k: None)
        monkeypatch.setattr(router, "_remember_hermes_session", lambda *a, **k: None)
        monkeypatch.setattr(router.outbound, "enqueue", AsyncMock(return_value=5750))
        save_message_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("modules.bridge_poller._save_message", save_message_mock)

        await router._deliver_hermes_reply("q", "8801880446111", source_bridge="bridge1")
        await router._deliver_hermes_reply("q", "8801880446111", source_bridge="bridge1")

        refs = [c.kwargs["source_message_ref"] for c in save_message_mock.call_args_list]
        assert refs[0] == refs[1] == "hermes_relay_reply:5750"

    async def test_persistence_skipped_when_enqueue_returns_none(self, monkeypatch):
        """qid=None means either enqueue failed or outbound.enqueue()'s
        own idempotency_key dedup absorbed the retry -- nothing new was
        actually queued, so nothing new should be persisted either."""
        import modules.admin_directives.router as router

        monkeypatch.setattr(router, "_call_hermes_readonly", AsyncMock(return_value=("ans", "sess")))
        monkeypatch.setattr(router, "_get_active_hermes_session", lambda *a, **k: None)
        monkeypatch.setattr(router, "_remember_hermes_session", lambda *a, **k: None)
        monkeypatch.setattr(router.outbound, "enqueue", AsyncMock(return_value=None))
        save_message_mock = AsyncMock()
        monkeypatch.setattr("modules.bridge_poller._save_message", save_message_mock)

        await router._deliver_hermes_reply("q", "8801880446111", source_bridge="bridge1")

        save_message_mock.assert_not_called()

    async def test_persistence_failure_does_not_raise_or_block_delivery(self, monkeypatch):
        """Delivery already happened (enqueue succeeded) by the time
        persistence is attempted -- a persistence failure must never
        propagate and must never look like a delivery failure."""
        import modules.admin_directives.router as router

        monkeypatch.setattr(router, "_call_hermes_readonly", AsyncMock(return_value=("ans", "sess")))
        monkeypatch.setattr(router, "_get_active_hermes_session", lambda *a, **k: None)
        monkeypatch.setattr(router, "_remember_hermes_session", lambda *a, **k: None)
        monkeypatch.setattr(router.outbound, "enqueue", AsyncMock(return_value=5751))
        monkeypatch.setattr(
            "modules.bridge_poller._save_message",
            AsyncMock(side_effect=RuntimeError("db down")),
        )

        # Must not raise.
        await router._deliver_hermes_reply("q", "8801880446111", source_bridge="bridge1")

    def test_call_site_wrapped_in_its_own_try_except(self):
        source = inspect.getsource(__import__(
            "modules.admin_directives.router", fromlist=["_deliver_hermes_reply"]
        )._deliver_hermes_reply)
        idx = source.index('from modules.bridge_poller import _save_message')
        block = source[idx:idx + 400]
        assert "except Exception as _persist_exc:" in block


# ── B3: self-chat device-outbound persistence ───────────────────────────────

class TestSelfChatDevicePersistenceWiring:
    """Structural proof (this loop needs a live bridge SQLite session to
    drive end-to-end -- same established pattern as this session's earlier
    tests for this exact loop)."""

    def test_persist_call_present_with_correct_attribution(self):
        source = inspect.getsource(bp)
        idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        block = source[idx:idx + 2600]
        assert 'await _save_message(' in block
        assert '"outbound"' in block
        assert 'actor_type="human_device"' in block
        assert 'workflow="admin_instruction"' in block
        assert 'source_message_ref=omsg_id' in block

    def test_persist_call_precedes_command_processing(self):
        """Persistence and command processing must be independently
        idempotent -- persistence happening first (and being wrapped in
        its own try/except) means a persistence failure can never block
        or alter the routing/execution below it."""
        source = inspect.getsource(bp)
        idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        block = source[idx:idx + 3000]
        persist_idx = block.index("await _save_message(")
        route_idx = block.index("routing to Hermes natural-query")
        assert persist_idx < route_idx

    def test_persist_call_has_its_own_exception_handling(self):
        source = inspect.getsource(bp)
        idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        block = source[idx:idx + 2600]
        assert "except Exception as _persist_exc:" in block

    def test_existing_command_dedup_untouched(self):
        """The pre-existing _is_outgoing_processed/_mark_outgoing_processed
        dedup (which is what actually guarantees an Admin instruction is
        never executed twice, independent of anything this stage added)
        still runs, unchanged, before this whole outgoing-scan section."""
        source = inspect.getsource(bp)
        assert "_is_outgoing_processed(omsg_id, bridge_name)" in source
        assert "_mark_outgoing_processed(omsg_id, bridge_name)" in source
        dedup_idx = source.index("_is_outgoing_processed(omsg_id, bridge_name)")
        persist_idx = source.index('if bridge_name == "bridge2" and not is_trusted_group_msg:')
        assert dedup_idx < persist_idx


# ── B4: attribution coverage across every existing writer ──────────────────

class TestB4ActorAttributionCoverage:
    """Every existing call site of both _save_message functions must pass
    an explicit actor_type -- never inferred, never left to guess."""

    def test_bridge_poller_outbound_call_sites_pass_actor_type(self):
        source = inspect.getsource(bp)
        count = source.count('_save_message(bridge_name, phone, reply, "outbound",')
        assert count == 2, "expected exactly the 2 known bridge_poller outbound call sites"
        # Both occurrences' immediate surrounding text must carry actor_type.
        pos = 0
        found = 0
        while True:
            idx = source.find('_save_message(bridge_name, phone, reply, "outbound",', pos)
            if idx == -1:
                break
            block = source[idx:idx + 300]
            assert 'actor_type="automation"' in block
            found += 1
            pos = idx + 1
        assert found == 2

    def test_recovery_sweep_reconciliation_passes_actor_type(self):
        import modules.recruitment_recovery_sweep as sweep
        source = inspect.getsource(sweep)
        idx = source.index("await _save_message(")
        block = source[idx:idx + 200]
        assert 'actor_type="automation"' in block

    def test_app_main_save_message_call_sites_all_pass_actor_type(self):
        import app.main as main_mod
        source = inspect.getsource(main_mod)
        # Every call to _save_message( in app/main.py (excluding the
        # function's own definition) must carry actor_type= somewhere in
        # its own call expression.
        import re
        for m in re.finditer(r"await _save_message\(", source):
            start = m.start()
            # Find the matching closing paren for this call (simple depth
            # count -- call bodies here never contain nested unbalanced
            # parens across this scan window in practice).
            depth = 0
            i = start + len("await _save_message")
            end = None
            for j in range(i, min(i + 800, len(source))):
                if source[j] == "(":
                    depth += 1
                elif source[j] == ")":
                    depth -= 1
                    if depth == 0:
                        end = j
                        break
            assert end is not None, "could not bound a _save_message( call"
            call_text = source[start:end]
            assert "actor_type=" in call_text, f"missing actor_type in call starting at offset {start}"


# ── B5: genuine inbound intent persistence ──────────────────────────────────

class TestB5InboundIntentPersistence:
    def test_raw_intent_snapshotted_before_recruitment_override(self):
        source = inspect.getsource(bp)
        classify_idx = source.index("msg_intent = classify_intent(text)")
        snapshot_idx = source.index("_raw_inbound_intent = msg_intent")
        override_idx = source.index('msg_intent = "recruitment"')
        assert classify_idx < snapshot_idx < override_idx

    def test_workflow_triggered_semantics_unchanged_downstream(self):
        """msg_intent itself (and therefore every existing
        workflow_triggered-tagging call downstream) must still be
        coarsened to "recruitment" exactly as before -- this stage only
        adds a read-only side observation, never changes routing/tagging
        behavior."""
        source = inspect.getsource(bp)
        assert 'if recruit_decision["eligible"]:\n        msg_intent = "recruitment"' in source

    def test_intent_update_is_conditional_and_non_destructive(self):
        source = inspect.getsource(bp)
        idx = source.index("UPDATE wbom_whatsapp_messages SET intent_detected")
        block = source[idx:idx + 200]
        assert "AND intent_detected IS NULL" in block

    def test_core_message_id_threaded_from_poller_to_processor(self):
        source = inspect.getsource(bp)
        assert "core_message_id=core_message_id" in source
        assert "core_message_id: Optional[int] = None" in source

    async def test_process_bridge_inbound_default_core_message_id_is_none(self):
        """The webhook call site in app/main.py doesn't pass
        core_message_id -- must default to None and simply skip the
        intent-persistence step, not raise or require the parameter."""
        import inspect as _inspect
        sig = _inspect.signature(bp.process_bridge_inbound)
        assert sig.parameters["core_message_id"].default is None


# ── B.5-2: cross-bridge Hermes-reply echo attribution ───────────────────────

class TestCrossBridgeEchoAttribution:
    """Live-confirmed in production (2026-08-28): message_ids
    19294/19297/19300/19305/19308 on bridge2 were verbatim echoes of
    Hermes replies 19293/19296/19299/19304/19307 sent via bridge1 seconds
    earlier, all persisted as actor_type='external_user' before this fix.
    _is_hermes_cross_bridge_echo() reuses modules.message_router's own
    already-trusted echo-guard signature verbatim, not a second rule."""

    def test_reuses_same_signature_as_message_router_guard(self):
        """Structural proof this isn't a second, independently-drifting
        rule -- both check the identical three conditions."""
        import modules.message_router as router
        router_source = inspect.getsource(router)
        guard_idx = router_source.index('sender == (settings.bridge1_number or "")')
        guard_block = router_source[guard_idx - 100:guard_idx + 200]
        assert 'source == "bridge2"' in guard_block
        assert 'text.lstrip().startswith("🤖 Hermes:")' in guard_block

        bp_source = inspect.getsource(bp._is_hermes_cross_bridge_echo)
        assert 'source == "bridge2"' in bp_source
        assert 'sender == (_settings.bridge1_number or "")' in bp_source
        assert 'text.lstrip().startswith("🤖 Hermes:")' in bp_source

    def test_genuine_external_inbound_is_not_flagged_as_echo(self, monkeypatch):
        monkeypatch.setattr(bp._settings, "bridge1_number", "8801958122300")
        assert bp._is_hermes_cross_bridge_echo("bridge2", "8801700000000", "আমি চাকরি চাই") is False
        # Same sender/channel as bridge1, but not the Hermes reply prefix --
        # still not an echo (the signature requires all three conditions).
        assert bp._is_hermes_cross_bridge_echo("bridge2", "8801958122300", "hello there") is False
        # Same content prefix, but arrived on bridge1 (not bridge2) or from
        # a different sender -- still not an echo.
        assert bp._is_hermes_cross_bridge_echo("bridge1", "8801958122300", "🤖 Hermes:\nhi") is False
        assert bp._is_hermes_cross_bridge_echo("bridge2", "8801700000000", "🤖 Hermes:\nhi") is False

    def test_known_echo_signature_is_recognized(self, monkeypatch):
        monkeypatch.setattr(bp._settings, "bridge1_number", "8801958122300")
        assert bp._is_hermes_cross_bridge_echo(
            "bridge2", "8801958122300", "🤖 Hermes:\nএই যে Boss — এখানেই আছি",
        ) is True

    async def test_persisted_actor_type_reflects_echo_classification(self, monkeypatch):
        """_save_raw_core_copy() must use 'hermes'/'hermes-runner' for a
        recognized echo, and never suppress/delete the row or change its
        direction -- only actor_type/actor_id."""
        monkeypatch.setattr(bp._settings, "bridge1_number", "8801958122300")
        captured = {}

        async def _fake_fetch_one(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return {"message_id": 1}

        monkeypatch.setattr(bp, "fetch_one", _fake_fetch_one)
        monkeypatch.setattr(bp, "execute", AsyncMock())

        msg = {"id": "MSGID1", "content": "🤖 Hermes:\nBoss, real answer here", "timestamp": None}
        await bp._save_raw_core_copy("bridge2", msg, "8801958122300", "ignored")

        assert "'inbound'" in captured["sql"]  # direction literal, unchanged
        assert captured["args"][-2] == "hermes"
        assert captured["args"][-1] == "hermes-runner"

    async def test_genuine_inbound_still_gets_external_user(self, monkeypatch):
        monkeypatch.setattr(bp._settings, "bridge1_number", "8801958122300")

        async def _fake_fetch_one(sql, *args):
            return {"message_id": 2}

        monkeypatch.setattr(bp, "fetch_one", _fake_fetch_one)
        monkeypatch.setattr(bp, "execute", AsyncMock())

        msg = {"id": "MSGID2", "content": "আমি চাকরি খুঁজছি", "timestamp": None}
        result_args = []

        async def _capture(sql, *args):
            result_args.append(args)
            return {"message_id": 2}

        monkeypatch.setattr(bp, "fetch_one", _capture)
        await bp._save_raw_core_copy("bridge3", msg, "8801700000000", "ignored")

        assert result_args[0][-2] == "external_user"
        assert result_args[0][-1] is None

    def test_message_router_echo_guard_itself_untouched(self):
        """Routing behavior (what the guard actually does once it fires)
        must remain completely unchanged by this persistence-layer fix --
        only proven not to have been edited alongside it."""
        import modules.message_router as router
        source = inspect.getsource(router)
        assert '_hk_fire("admin", "hermes_reply_echo", "no_action", "hermes-cross-bridge-echo-guard")' in source
        assert 'return "", None' in source
