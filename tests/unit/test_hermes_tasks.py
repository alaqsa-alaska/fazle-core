"""
modules.hermes_tasks — unit tests (2026-08-19, persistent Task/Execution
State + diff-level Action Approval).

All DB-dependent tests use AsyncMock/MagicMock to avoid requiring a real
Postgres connection, mirroring tests/unit/test_phase12_concurrency.py's
_make_conn() helper exactly for the row-locked functions (claim_task,
update_task_status, approve_action, reject_action,
record_execution_result); the simpler non-locked functions (create_task,
list_tasks, get_task, propose_action's insert, get_pending_actions,
get_action, is_action_approved) mock fetch_one/fetch_all/fetch_val/execute
directly, matching test_recruitment_conversation_audit.py's style.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_conn(*, row=None):
    """Mock asyncpg connection supporting `async with pool.acquire() as conn:
    async with conn.transaction(): row = await conn.fetchrow(...)`."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _mock_pool(conn):
    pool = MagicMock()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool


# ── create_task / list_tasks / get_task ─────────────────────────────────────

class TestCreateTask:
    async def test_creates_row_and_event(self):
        from modules.hermes_tasks import create_task
        with patch("modules.hermes_tasks.fetch_val", new=AsyncMock(return_value=42)), \
             patch("modules.hermes_tasks.execute", new=AsyncMock()) as mock_execute:
            task_id = await create_task("Fix the thing", created_by_phone="8801700000001")
        assert task_id == 42
        mock_execute.assert_awaited_once()
        assert mock_execute.await_args.args[0].strip().startswith("INSERT INTO hermes_task_events")

    async def test_empty_title_rejected(self):
        from modules.hermes_tasks import create_task, TaskError
        with pytest.raises(TaskError):
            await create_task("   ")

    async def test_invalid_priority_rejected(self):
        from modules.hermes_tasks import create_task, TaskError
        with pytest.raises(TaskError):
            await create_task("Title", priority="urgent-ish")

    async def test_invalid_execution_mode_rejected(self):
        from modules.hermes_tasks import create_task, TaskError
        with pytest.raises(TaskError):
            await create_task("Title", execution_mode="SUDO")


class TestGetTask:
    async def test_returns_none_when_not_found(self):
        from modules.hermes_tasks import get_task
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=None)):
            result = await get_task(999)
        assert result is None

    async def test_includes_events(self):
        from modules.hermes_tasks import get_task
        task_row = {"id": 5, "title": "X", "status": "BACKLOG"}
        events = [{"id": 1, "event": "created"}]
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=task_row)), \
             patch("modules.hermes_tasks.fetch_all", new=AsyncMock(return_value=events)):
            result = await get_task(5)
        assert result["events"] == events


# ── claim_task ───────────────────────────────────────────────────────────────

class TestClaimTask:
    async def test_ready_task_claimable(self):
        from modules.hermes_tasks import claim_task
        conn = _make_conn(row={"id": 5, "status": "READY"})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()) as mock_event:
            result = await claim_task(5, owner="earth")
        assert result == {"ok": True, "task_id": 5, "status": "IN_PROGRESS"}
        conn.execute.assert_awaited_once()
        mock_event.assert_awaited_once_with(5, "claimed", "READY", "IN_PROGRESS", performed_by="earth")

    async def test_not_found_raises(self):
        from modules.hermes_tasks import claim_task, TaskError
        conn = _make_conn(row=None)
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await claim_task(999)

    async def test_already_in_progress_not_claimable(self):
        from modules.hermes_tasks import claim_task, TaskError
        conn = _make_conn(row={"id": 5, "status": "IN_PROGRESS"})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await claim_task(5)
        conn.execute.assert_not_awaited()


# ── update_task_status — state machine ──────────────────────────────────────

class TestUpdateTaskStatus:
    async def test_legal_transition_succeeds(self):
        from modules.hermes_tasks import update_task_status
        conn = _make_conn(row={"id": 5, "status": "IN_PROGRESS"})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()):
            result = await update_task_status(5, "WAITING_APPROVAL")
        assert result["status"] == "WAITING_APPROVAL"

    async def test_illegal_transition_rejected(self):
        """BACKLOG -> DONE is not a legal edge (must pass through
        READY/IN_PROGRESS first)."""
        from modules.hermes_tasks import update_task_status, TaskError
        conn = _make_conn(row={"id": 5, "status": "BACKLOG"})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await update_task_status(5, "DONE")
        conn.execute.assert_not_awaited()

    async def test_terminal_status_never_leaves(self):
        from modules.hermes_tasks import update_task_status, TaskError
        conn = _make_conn(row={"id": 5, "status": "DONE"})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await update_task_status(5, "IN_PROGRESS")

    async def test_unknown_status_rejected(self):
        from modules.hermes_tasks import update_task_status, TaskError
        with pytest.raises(TaskError):
            await update_task_status(5, "NOT_A_REAL_STATUS")

    async def test_failed_allows_retry_to_ready(self):
        from modules.hermes_tasks import update_task_status
        conn = _make_conn(row={"id": 5, "status": "FAILED"})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()):
            result = await update_task_status(5, "READY")
        assert result["status"] == "READY"

    async def test_full_happy_path_is_all_legal_edges(self):
        """Live smoke-test regression (2026-08-19): record_execution_result()
        tried WAITING_APPROVAL -> VERIFYING and it silently failed because
        that edge was missing from _TASK_TRANSITIONS -- unit tests didn't
        catch it because they mocked update_task_status() itself rather
        than exercising the real transition table. This walks the entire
        real happy path against _TASK_TRANSITIONS directly, so a future
        edge omission fails loudly here instead of only in production."""
        from modules.hermes_tasks import _TASK_TRANSITIONS
        path = ["BACKLOG", "READY", "IN_PROGRESS", "WAITING_APPROVAL", "VERIFYING", "DONE"]
        for current, nxt in zip(path, path[1:]):
            assert nxt in _TASK_TRANSITIONS[current], f"{current} -> {nxt} must be legal (full happy path)"


# ── propose_action / approve_action / reject_action ─────────────────────────

class TestProposeAction:
    async def test_creates_pending_action_and_moves_task_to_waiting_approval(self):
        from modules.hermes_tasks import propose_action
        with patch("modules.hermes_tasks.fetch_val", new=AsyncMock(return_value=7)), \
             patch("modules.hermes_tasks.update_task_status", new=AsyncMock()) as mock_update, \
             patch("modules.hermes_tasks._event", new=AsyncMock()):
            action_id = await propose_action(
                task_id=5, action_type="file_mutation", summary="Fix typo in README",
                files_changed=["README.md"], diff="- old\n+ new", risk="low",
            )
        assert action_id == 7
        mock_update.assert_awaited_once_with(5, "WAITING_APPROVAL", performed_by="earth")

    async def test_invalid_action_type_rejected(self):
        from modules.hermes_tasks import propose_action, TaskError
        with pytest.raises(TaskError):
            await propose_action(None, "delete_prod_db", "nope")

    async def test_invalid_risk_rejected(self):
        from modules.hermes_tasks import propose_action, TaskError
        with pytest.raises(TaskError):
            await propose_action(None, "file_mutation", "summary", risk="extreme")

    async def test_no_task_id_skips_status_update(self):
        from modules.hermes_tasks import propose_action
        with patch("modules.hermes_tasks.fetch_val", new=AsyncMock(return_value=8)), \
             patch("modules.hermes_tasks.update_task_status", new=AsyncMock()) as mock_update:
            action_id = await propose_action(None, "git_commit", "standalone action")
        assert action_id == 8
        mock_update.assert_not_awaited()


class TestApproveRejectAction:
    async def test_approve_pending_action_succeeds(self):
        from modules.hermes_tasks import approve_action
        conn = _make_conn(row={"id": 7, "status": "pending", "task_id": 5})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()):
            result = await approve_action(7, "8801700000001")
        assert result == {"ok": True, "action_id": 7, "status": "approved"}

    async def test_approve_non_pending_action_rejected(self):
        from modules.hermes_tasks import approve_action, TaskError
        conn = _make_conn(row={"id": 7, "status": "executed", "task_id": 5})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await approve_action(7, "8801700000001")
        conn.execute.assert_not_awaited()

    async def test_approve_unknown_action_rejected(self):
        from modules.hermes_tasks import approve_action, TaskError
        conn = _make_conn(row=None)
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await approve_action(999, "8801700000001")

    async def test_reject_pending_action_blocks_task(self):
        from modules.hermes_tasks import reject_action
        conn = _make_conn(row={"id": 7, "status": "pending", "task_id": 5})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()), \
             patch("modules.hermes_tasks.update_task_status", new=AsyncMock()) as mock_update:
            result = await reject_action(7, "8801700000001", "too risky")
        assert result["status"] == "rejected"
        mock_update.assert_awaited_once_with(5, "BLOCKED", performed_by="8801700000001", failure_reason="too risky")


class TestIsActionApproved:
    async def test_approved_action_returns_true(self):
        from modules.hermes_tasks import is_action_approved
        row = {"status": "approved", "action_type": "opencode_dispatch"}
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=row)):
            assert await is_action_approved(7, expected_action_type="opencode_dispatch") is True

    async def test_pending_action_returns_false(self):
        from modules.hermes_tasks import is_action_approved
        row = {"status": "pending", "action_type": "opencode_dispatch"}
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=row)):
            assert await is_action_approved(7) is False

    async def test_unknown_action_returns_false(self):
        from modules.hermes_tasks import is_action_approved
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=None)):
            assert await is_action_approved(999) is False

    async def test_wrong_action_type_returns_false(self):
        """An approved 'file_mutation' action must not authorize an
        opencode_dispatch call -- the id and the action_type must both
        match, not just an approved status somewhere."""
        from modules.hermes_tasks import is_action_approved
        row = {"status": "approved", "action_type": "file_mutation"}
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=row)):
            assert await is_action_approved(7, expected_action_type="opencode_dispatch") is False


class TestRecordExecutionResult:
    async def test_approved_action_records_result_and_advances_task(self):
        from modules.hermes_tasks import record_execution_result
        conn = _make_conn(row={"id": 7, "status": "approved", "task_id": 5})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()), \
             patch("modules.hermes_tasks.update_task_status", new=AsyncMock()) as mock_update:
            result = await record_execution_result(7, {"commit": "abc123"})
        assert result["status"] == "executed"
        mock_update.assert_awaited_once()
        assert mock_update.await_args.args[:2] == (5, "VERIFYING")

    async def test_non_approved_action_rejected(self):
        from modules.hermes_tasks import record_execution_result, TaskError
        conn = _make_conn(row={"id": 7, "status": "pending", "task_id": 5})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await record_execution_result(7, {"commit": "abc123"})
        conn.execute.assert_not_awaited()
