"""
modules.hermes_tasks — unit tests for the 2026-08-19 Owner-directed
authorization revision: task-scoped BUILD authorization, fused
authorize_action, and the approval-expiry enforcement fix.

Mocking approach matches test_hermes_tasks.py exactly (AsyncMock/MagicMock
mirroring test_phase12_concurrency.py's _make_conn() helper for the
row-locked functions; direct fetch_one/fetch_all/fetch_val/execute mocks
for the rest).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_conn(*, row=None):
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


def _future(hours=1):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _past(hours=1):
    return datetime.now(timezone.utc) - timedelta(hours=hours)


# ── Approval expiry (§3/§15 — real bug found + fixed this session) ─────────

class TestApprovalExpiry:
    async def test_pending_action_past_expiry_treated_as_not_approved(self):
        from modules.hermes_tasks import is_action_approved
        row = {"status": "pending", "action_type": "git_commit", "expires_at": _past(), "task_id": 5}
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=row)), \
             patch("modules.hermes_tasks.execute", new=AsyncMock()) as mock_execute, \
             patch("modules.hermes_tasks._event", new=AsyncMock()) as mock_event:
            result = await is_action_approved(7)
        assert result is False
        mock_execute.assert_awaited_once()
        assert "status='expired'" in mock_execute.await_args.args[0]
        mock_event.assert_awaited_once()

    async def test_approved_action_past_expiry_treated_as_not_approved(self):
        """The historical row stays status='approved' (not overwritten) --
        but is_action_approved() must still say False, and no execution can
        proceed on it."""
        from modules.hermes_tasks import is_action_approved
        row = {"status": "approved", "action_type": "git_commit", "expires_at": _past(), "task_id": 5}
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=row)), \
             patch("modules.hermes_tasks.execute", new=AsyncMock()) as mock_execute:
            result = await is_action_approved(7)
        assert result is False
        mock_execute.assert_not_awaited()  # no write for an already-approved row, just denial

    async def test_approved_action_within_expiry_is_approved(self):
        from modules.hermes_tasks import is_action_approved
        row = {"status": "approved", "action_type": "git_commit", "expires_at": _future(), "task_id": 5}
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=row)):
            assert await is_action_approved(7) is True

    async def test_null_expiry_never_expires(self):
        from modules.hermes_tasks import is_action_approved
        row = {"status": "approved", "action_type": "git_commit", "expires_at": None, "task_id": 5}
        with patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value=row)):
            assert await is_action_approved(7) is True

    async def test_record_execution_result_rejects_expired_approval(self):
        from modules.hermes_tasks import record_execution_result, TaskError
        conn = _make_conn(row={"id": 7, "status": "approved", "task_id": 5, "expires_at": _past()})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError, match="expired"):
                await record_execution_result(7, {"note": "too late"})
        conn.execute.assert_not_awaited()

    async def test_record_execution_result_allows_unexpired_approval(self):
        from modules.hermes_tasks import record_execution_result
        conn = _make_conn(row={"id": 7, "status": "approved", "task_id": 5, "expires_at": _future()})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()), \
             patch("modules.hermes_tasks.update_task_status", new=AsyncMock()):
            result = await record_execution_result(7, {"note": "in time"})
        assert result["status"] == "executed"


# ── authorize_build / find_authorizing_task_for_path ────────────────────────

class TestAuthorizeBuild:
    async def test_grants_build_authorization(self):
        from modules.hermes_tasks import authorize_build
        conn = _make_conn(row={"id": 5, "status": "IN_PROGRESS"})
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()) as mock_event:
            result = await authorize_build(5, ["/home/azim/core"], "8801700000001", ttl_hours=4)
        assert result["ok"] is True
        assert result["repos"] == ["/home/azim/core"]
        conn.execute.assert_awaited_once()
        mock_event.assert_awaited_once()

    async def test_empty_repos_rejected(self):
        from modules.hermes_tasks import authorize_build, TaskError
        with pytest.raises(TaskError):
            await authorize_build(5, [], "8801700000001")

    async def test_unknown_task_rejected(self):
        from modules.hermes_tasks import authorize_build, TaskError
        conn = _make_conn(row=None)
        with patch("modules.hermes_tasks.get_pool", return_value=_mock_pool(conn)):
            with pytest.raises(TaskError):
                await authorize_build(999, ["/home/azim/core"], "8801700000001")


class TestFindAuthorizingTaskForPath:
    async def test_matches_within_authorized_scope(self):
        from modules.hermes_tasks import find_authorizing_task_for_path
        rows = [{"id": 5, "build_scope": {"repos": ["/home/azim/core"]}, "build_expires_at": _future()}]
        with patch("modules.hermes_tasks.fetch_all", new=AsyncMock(return_value=rows)):
            result = await find_authorizing_task_for_path("/home/azim/core/app/main.py")
        assert result == {"task_id": 5, "repos": ["/home/azim/core"]}

    async def test_no_match_outside_any_scope(self):
        from modules.hermes_tasks import find_authorizing_task_for_path
        rows = [{"id": 5, "build_scope": {"repos": ["/home/azim/core"]}, "build_expires_at": _future()}]
        with patch("modules.hermes_tasks.fetch_all", new=AsyncMock(return_value=rows)):
            result = await find_authorizing_task_for_path("/home/azim/fazle-mcp/server.py")
        assert result is None

    async def test_expired_grant_does_not_match(self):
        from modules.hermes_tasks import find_authorizing_task_for_path
        rows = [{"id": 5, "build_scope": {"repos": ["/home/azim/core"]}, "build_expires_at": _past()}]
        with patch("modules.hermes_tasks.fetch_all", new=AsyncMock(return_value=rows)):
            result = await find_authorizing_task_for_path("/home/azim/core/app/main.py")
        assert result is None

    async def test_task_isolation_task_a_scope_does_not_leak_to_task_b_repo(self):
        """Owner §16 regression: Task A's BUILD grant (scoped to fazle-mcp)
        must never authorize an edit in a completely different repo just
        because SOME task somewhere is build_authorized."""
        from modules.hermes_tasks import find_authorizing_task_for_path
        rows = [
            {"id": 1, "build_scope": {"repos": ["/home/azim/fazle-mcp"]}, "build_expires_at": _future()},
        ]
        with patch("modules.hermes_tasks.fetch_all", new=AsyncMock(return_value=rows)):
            result = await find_authorizing_task_for_path("/home/azim/core/modules/rbac/__init__.py")
        assert result is None


# ── authorize_action (fused propose+approve) ────────────────────────────────

class TestAuthorizeAction:
    async def test_creates_already_approved_row(self):
        from modules.hermes_tasks import authorize_action
        with patch("modules.hermes_tasks.fetch_val", new=AsyncMock(return_value=9)), \
             patch("modules.hermes_tasks._event", new=AsyncMock()) as mock_event:
            action_id = await authorize_action(
                5, "git_commit", "commit the routing fix", "8801700000001",
                diff="- old\n+ new", risk="low",
            )
        assert action_id == 9
        mock_event.assert_awaited_once()
        args, kwargs = mock_event.await_args
        assert args[0] == 5 and args[1] == "approved"

    async def test_invalid_action_type_rejected(self):
        from modules.hermes_tasks import authorize_action, TaskError
        with pytest.raises(TaskError):
            await authorize_action(None, "rm_rf_everything", "nope", "8801700000001")

    async def test_status_is_approved_not_pending(self):
        from modules.hermes_tasks import authorize_action
        captured = {}

        async def fake_fetch_val(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return 10

        with patch("modules.hermes_tasks.fetch_val", new=fake_fetch_val), \
             patch("modules.hermes_tasks._event", new=AsyncMock()):
            await authorize_action(None, "deploy", "deploy it", "8801700000001")
        assert "'approved'" in captured["sql"]

    async def test_task_isolation_action_bound_to_specific_task_id(self):
        """Owner §16: an approved action for task A must never satisfy
        is_action_approved() checks intended for task B -- verified here by
        confirming the stored task_id is exactly what was passed, not
        inferred/defaulted."""
        from modules.hermes_tasks import authorize_action

        async def fake_fetch_val(sql, *args):
            # task_id is the first bound param
            assert args[0] == 42
            return 11

        with patch("modules.hermes_tasks.fetch_val", new=fake_fetch_val), \
             patch("modules.hermes_tasks._event", new=AsyncMock()):
            await authorize_action(42, "deploy", "deploy for task 42 only", "8801700000001")
