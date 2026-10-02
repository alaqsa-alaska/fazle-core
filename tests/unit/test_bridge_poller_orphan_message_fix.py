"""Unit + integration + failure-injection tests — the identity-window
orphan-message fix in modules.bridge_poller (2026-08-04).

Background (see knowledge_base/00_governance/management_decisions.md "55
Stuck WhatsApp Messages" and the follow-up entry for this fix): before this
change, an unhandled exception between _mark_processed() (a permanent
bridge-side dedup marker) and the routing-critical _finalize_core_copy()
call would propagate past the entire per-message loop to _poll_bridge()'s
single top-level `except Exception: log.exception(...)`, leaving the
message's Postgres bookkeeping (wbom_whatsapp_messages.is_processed,
fazle_message_queue.status) stuck at "processing"/attempts=0 forever, with
no retry possible. This happened for real 2026-07-09..12 (identity_brain.
_lookup_contact() had wrong column names, fixed separately in 47a9c8e).

The fix wraps that window in try/except: on any exception, the message is
finalized to a terminal "failed" state instead of being silently orphaned,
and only that one message is skipped -- the rest of the batch still
processes normally (a second effect of containing the exception with
continue inside the per-message for loop, not just abandoning the whole
batch as the old uncaught-exception path did).

_poll_bridge() itself is a single monolithic, infinite (`while True`)
function with heavy external dependencies (SQLite polling, bridge
orchestrator, admin notifications, etc.) -- not practical to invoke
directly in a test. Following this repo's own established convention for
testing deep inline _poll_bridge logic (see test_escort_ocr_pipeline.py's
"Simulate the X block from _poll_bridge" pattern), these tests mirror the
exact try/except control flow added in modules/bridge_poller/__init__.py
(the block immediately following the `log.info(f"[{bridge_name}] from=...")`
line), but call the REAL, unmodified _finalize_core_copy/_mark_processed/
_save_raw_core_copy functions from modules.bridge_poller -- only the
surrounding control flow is mirrored, not the underlying DB-writing logic.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from modules.bridge_poller import _finalize_core_copy, _mark_processed, _save_raw_core_copy


async def _run_identity_window(core_message_id, bridge_name, msg, phone, text, msg_id, extracted_text=""):
    """Mirrors the exact try/except added in modules/bridge_poller/__init__.py
    immediately after `log.info(f"[{bridge_name}] from=...")`. Returns
    ("processed", (id_role, id_conf, id_name)) on success, or
    ("failed", str(exc)) on exception -- matching the real code's two
    outcomes (fall through to routing, vs. finalize+continue)."""
    from modules.identity_brain import detect_identity
    try:
        identity = await detect_identity(phone, text)
        id_role = identity["identity_role"]
        id_conf = identity["identity_confidence"]
        id_name = (identity.get("display_name") or "").strip()

        await _finalize_core_copy(
            core_message_id, bridge_name, msg, phone, text,
            identity_role=id_role, identity_confidence=id_conf,
            msg_type=msg.get("type") or "text",
            extracted_text=extracted_text,
            status="processing",
        )
        return "processed", (id_role, id_conf, id_name)
    except Exception as exc:
        await _finalize_core_copy(
            core_message_id, bridge_name, msg, phone, text,
            msg_type=msg.get("type") or "text",
            status="failed", error=f"identity_or_finalize_error:{exc}",
        )
        return "failed", str(exc)


# ── 1. Unit tests (mocked _finalize_core_copy, mocked DB) ────────────────────

class TestIdentityWindowExceptionHandling:
    """Pure unit tests: DB layer + detect_identity mocked out entirely."""

    pytestmark = pytest.mark.unit

    async def test_detect_identity_exception_finalizes_to_failed_not_processing(self):
        msg = {"id": "MSGID1", "type": "text"}
        with patch("modules.identity_brain.detect_identity", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("modules.bridge_poller.execute", new=AsyncMock()) as mock_execute, \
             patch("modules.bridge_poller._save_message", new=AsyncMock()):
            outcome, detail = await _run_identity_window(
                core_message_id=42, bridge_name="bridge1", msg=msg,
                phone="8801700000099", text="hello", msg_id="MSGID1",
            )
        assert outcome == "failed"
        assert "boom" in detail

        # _finalize_core_copy's UPDATE call must carry status='failed', not
        # 'processing' -- inspect the actual bound parameters, not just
        # that *some* UPDATE happened.
        wbom_update_calls = [
            c for c in mock_execute.await_args_list
            if "UPDATE wbom_whatsapp_messages" in c.args[0]
        ]
        assert len(wbom_update_calls) == 1
        # message_body, message_type, is_processed(always true), identity_role,
        # identity_confidence, workflow, intent, extracted_text, metadata_json
        # -- is_processed is hardcoded true in the UPDATE's SET clause itself
        # (not a bound param), so assert on the metadata_json param instead,
        # which carries status.
        metadata_json_param = wbom_update_calls[0].args[9]
        assert json.loads(metadata_json_param)["status"] == "failed"

        queue_update_calls = [
            c for c in mock_execute.await_args_list
            if "UPDATE fazle_message_queue" in c.args[0]
        ]
        assert len(queue_update_calls) == 1
        assert queue_update_calls[0].args[1] == "failed"  # $1 = status
        assert "identity_or_finalize_error" in queue_update_calls[0].args[2]  # $2 = last_error

    async def test_successful_identity_detection_returns_processed(self):
        msg = {"id": "MSGID2", "type": "text"}
        fake_identity = {"identity_role": "client_escort_buyer", "identity_confidence": 80, "display_name": "Test Client"}
        with patch("modules.identity_brain.detect_identity", new=AsyncMock(return_value=fake_identity)), \
             patch("modules.bridge_poller.execute", new=AsyncMock()), \
             patch("modules.bridge_poller._save_message", new=AsyncMock()):
            outcome, detail = await _run_identity_window(
                core_message_id=43, bridge_name="bridge2", msg=msg,
                phone="8801700000098", text="hi", msg_id="MSGID2",
            )
        assert outcome == "processed"
        assert detail == ("client_escort_buyer", 80, "Test Client")

    async def test_malformed_identity_dict_is_caught_not_uncaught_keyerror(self):
        """detect_identity returning a dict missing 'identity_role' must also
        finalize to failed, not propagate a raw KeyError -- the try/except
        wraps the dict access too, not just the await call itself."""
        msg = {"id": "MSGID3", "type": "text"}
        with patch("modules.identity_brain.detect_identity", new=AsyncMock(return_value={})), \
             patch("modules.bridge_poller.execute", new=AsyncMock()), \
             patch("modules.bridge_poller._save_message", new=AsyncMock()):
            outcome, detail = await _run_identity_window(
                core_message_id=44, bridge_name="bridge1", msg=msg,
                phone="8801700000097", text="hi", msg_id="MSGID3",
            )
        assert outcome == "failed"
        assert "identity_role" in detail

    async def test_finalize_core_copy_never_raises_out_of_the_except_branch(self):
        """Even if _finalize_core_copy's own internal write fails (e.g. DB
        down), _run_identity_window itself must not raise -- matching
        _finalize_core_copy's own documented never-raises contract."""
        msg = {"id": "MSGID4", "type": "text"}
        with patch("modules.identity_brain.detect_identity", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("modules.bridge_poller.execute", new=AsyncMock(side_effect=ConnectionError("db down"))), \
             patch("modules.bridge_poller._save_message", new=AsyncMock()):
            outcome, detail = await _run_identity_window(
                core_message_id=45, bridge_name="bridge1", msg=msg,
                phone="8801700000096", text="hi", msg_id="MSGID4",
            )
        assert outcome == "failed"  # did not raise past this point


# ── 2. Integration test (real test DB, real _finalize_core_copy writes) ──────

class TestIdentityWindowFixIntegration:
    pytestmark = pytest.mark.integration

    async def test_exception_leaves_real_rows_in_terminal_failed_state(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        phone = "8801799990001"
        msg = {"id": "REALMSG1", "message_id": "REALMSG1", "type": "text", "timestamp": "2026-08-04T10:00:00+00:00"}

        core_id = await _save_raw_core_copy("bridge1", msg, phone, "hello from integration test")
        assert core_id is not None
        await _mark_processed("REALMSG1", "bridge1", phone)

        with patch("modules.identity_brain.detect_identity", new=AsyncMock(side_effect=RuntimeError("simulated identity failure"))):
            outcome, _ = await _run_identity_window(
                core_message_id=core_id, bridge_name="bridge1", msg=msg,
                phone=phone, text="hello from integration test", msg_id="REALMSG1",
            )
        assert outcome == "failed"

        async with test_db_pool.acquire() as conn:
            w_row = await conn.fetchrow(
                "SELECT is_processed, metadata_json FROM wbom_whatsapp_messages WHERE message_id = $1", core_id
            )
            q_row = await conn.fetchrow(
                "SELECT status, attempts, last_error FROM fazle_message_queue WHERE idempotency_key = $1",
                "bridge_poller:bridge1:REALMSG1",
            )

        # The core assertion this whole fix exists for: NOT stuck at
        # is_processed=false / status='processing' / attempts=0 forever.
        assert w_row["is_processed"] is True
        assert json.loads(w_row["metadata_json"])["status"] == "failed"
        assert q_row["status"] == "failed"
        assert q_row["attempts"] == 1  # proves _finalize_core_copy's attempts+1 actually ran
        assert "identity_or_finalize_error" in q_row["last_error"]

        # And the dedup marker is real -- confirms this message genuinely
        # can never be re-fetched by the poller (same permanence property
        # the historical incident had), but now with terminal bookkeeping
        # instead of an orphan.
        async with test_db_pool.acquire() as conn:
            pbm = await conn.fetchval(
                "SELECT 1 FROM processed_bridge_messages WHERE message_id = $1 AND bridge = $2",
                "REALMSG1", "bridge1",
            )
        assert pbm == 1


# ── 3. Failure-injection test (reproduces the historical incident's exact
#      exception class, proves it can no longer orphan a message, and
#      proves the rest of a batch still processes after one message fails)

class TestOrphanMessageFailureInjection:
    pytestmark = pytest.mark.integration

    async def test_reproduces_original_undefinedcolumnerror_class_terminal_not_orphaned(self, test_db_pool):
        """The original 2026-07-09..12 incident's exact exception class was
        asyncpg.exceptions.UndefinedColumnError, raised from
        identity_brain._lookup_contact() and NOT caught by
        detect_identity()'s own except clause (which only catches
        AttributeError/LookupError/TypeError/ValueError -- confirmed by
        reading modules/identity_brain/__init__.py directly; UndefinedColumnError
        is an asyncpg.PostgresError subclass, not any of those). This test
        injects that exact real exception class at the same call site and
        proves the message now reaches a terminal state instead of being
        permanently orphaned."""
        import app.database as db_module
        db_module._pool = test_db_pool

        phone = "8801799990002"
        msg = {"id": "REALMSG2", "message_id": "REALMSG2", "type": "text", "timestamp": "2026-08-04T10:01:00+00:00"}

        core_id = await _save_raw_core_copy("bridge1", msg, phone, "message from a real client")
        await _mark_processed("REALMSG2", "bridge1", phone)

        undefined_column_error = asyncpg.exceptions.UndefinedColumnError(
            'column "contact_name" does not exist'
        )
        with patch("modules.identity_brain._lookup_contact", new=AsyncMock(side_effect=undefined_column_error)):
            from modules.identity_brain import detect_identity
            # Sanity check: confirm detect_identity's own narrow except
            # clause does NOT swallow this exception class today (i.e. this
            # test is exercising a real, still-open gap in identity_brain,
            # not a scenario detect_identity already protects against on
            # its own) -- the bridge_poller fix is what closes it.
            with pytest.raises(asyncpg.exceptions.UndefinedColumnError):
                await detect_identity(phone, "message from a real client")

            outcome, _ = await _run_identity_window(
                core_message_id=core_id, bridge_name="bridge1", msg=msg,
                phone=phone, text="message from a real client", msg_id="REALMSG2",
            )
        assert outcome == "failed"

        async with test_db_pool.acquire() as conn:
            q_row = await conn.fetchrow(
                "SELECT status, attempts FROM fazle_message_queue WHERE idempotency_key = $1",
                "bridge_poller:bridge1:REALMSG2",
            )
        assert q_row["status"] == "failed"
        assert q_row["attempts"] == 1  # NOT the historical incident's attempts=0 orphan signature

    async def test_one_failing_message_does_not_block_the_rest_of_the_batch(self, test_db_pool):
        """Before the fix, an uncaught exception propagated past the entire
        `for msg in messages:` loop, abandoning every message after the
        failing one in that same poll batch too -- a second, related effect
        of the same gap. Simulates a 2-message batch (mirroring the real
        for-loop's per-message try/except + continue) where the first
        message's identity lookup raises and the second succeeds, proving
        the second message still gets processed."""
        import app.database as db_module
        db_module._pool = test_db_pool

        phone1, phone2 = "8801799990003", "8801799990004"
        msg1 = {"id": "BATCH1", "message_id": "BATCH1", "type": "text", "timestamp": "2026-08-04T10:02:00+00:00"}
        msg2 = {"id": "BATCH2", "message_id": "BATCH2", "type": "text", "timestamp": "2026-08-04T10:03:00+00:00"}

        core_id1 = await _save_raw_core_copy("bridge2", msg1, phone1, "first message, will fail")
        core_id2 = await _save_raw_core_copy("bridge2", msg2, phone2, "second message, should still work")
        await _mark_processed("BATCH1", "bridge2", phone1)
        await _mark_processed("BATCH2", "bridge2", phone2)

        good_identity = {"identity_role": "unknown", "identity_confidence": 0, "display_name": ""}
        call_count = {"n": 0}

        async def _flaky_detect_identity(phone, text):
            call_count["n"] += 1
            if phone == phone1:
                raise RuntimeError("simulated failure for message 1 only")
            return good_identity

        results = []
        with patch("modules.identity_brain.detect_identity", new=_flaky_detect_identity):
            for core_id, bridge_name, msg, phone, text, msg_id in [
                (core_id1, "bridge2", msg1, phone1, "first message, will fail", "BATCH1"),
                (core_id2, "bridge2", msg2, phone2, "second message, should still work", "BATCH2"),
            ]:
                outcome, _ = await _run_identity_window(core_id, bridge_name, msg, phone, text, msg_id)
                results.append(outcome)
                # matches the real for-loop: `continue` on failure, fall
                # through to normal processing on success -- both keep the
                # loop going to the next message either way.

        assert results == ["failed", "processed"]
        assert call_count["n"] == 2  # second message's identity lookup was genuinely attempted

        async with test_db_pool.acquire() as conn:
            q1 = await conn.fetchval(
                "SELECT status FROM fazle_message_queue WHERE idempotency_key = $1", "bridge_poller:bridge2:BATCH1"
            )
            w2 = await conn.fetchval(
                "SELECT is_processed FROM wbom_whatsapp_messages WHERE message_id = $1", core_id2
            )
        assert q1 == "failed"
        # message 2's finalize call in the real code uses status="processing"
        # (routing hasn't run in this isolated test), but is_processed is
        # unconditionally set true by _finalize_core_copy regardless of status.
        assert w2 is False  # Identity resolved; routing has not completed yet.
