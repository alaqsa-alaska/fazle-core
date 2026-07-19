from __future__ import annotations

import inspect

import pytest

_FAKE_ROLE_LEVELS = {
    "viewer": 10, "office_assistant": 20, "operator": 30,
    "accountant": 50, "admin": 70, "superadmin": 100,
}


def _patch_role_levels(monkeypatch):
    from modules.ai_console import live_adapters

    async def fake_get_role_level(name):
        return _FAKE_ROLE_LEVELS.get(name, 999)

    monkeypatch.setattr(live_adapters.rbac, "get_role_level", fake_get_role_level)
    monkeypatch.setattr(live_adapters, "_role_level_cache", {})


# ===========================================================================
# RBAC gating
# ===========================================================================

@pytest.mark.asyncio
async def test_accountant_tool_denied_for_viewer_level(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    _patch_role_levels(monkeypatch)

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-003", {"question": "payroll"}, actor_role_level=_FAKE_ROLE_LEVELS["viewer"],
    )

    assert result.status == "permission_denied"
    assert "accountant" in result.data["min_role"]
    assert result.warnings  # a clear warning is surfaced, not silent


@pytest.mark.asyncio
async def test_accountant_tool_allowed_for_accountant_level(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    _patch_role_levels(monkeypatch)

    async def fake_payment_summary(employee_name=None, month=None, year=None):
        return [{"employee_name": "X", "net_salary": 1000}]

    monkeypatch.setattr(
        "modules.ai_console.live_adapters.ai_readonly_tools.get_payment_summary",
        fake_payment_summary,
    )

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-003", {"question": "payroll"}, actor_role_level=_FAKE_ROLE_LEVELS["accountant"],
    )

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_viewer_tool_allowed_for_viewer_level(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    _patch_role_levels(monkeypatch)

    async def fake_employee_list(status="active"):
        return [{"employee_name": "Manik", "employee_mobile": "01711111111"}]

    monkeypatch.setattr(
        "modules.ai_console.live_adapters.ai_readonly_tools.get_employee_list",
        fake_employee_list,
    )

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-001", {"query": "Manik"}, actor_role_level=_FAKE_ROLE_LEVELS["viewer"],
    )

    assert result.status in ("ok", "partial")


@pytest.mark.asyncio
async def test_default_actor_role_level_is_full_access(monkeypatch):
    """Callers that don't pass actor_role_level (e.g. existing internal
    callers) must not be silently locked out — default is full access."""
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    _patch_role_levels(monkeypatch)

    async def fake_daily(question):
        return {"transaction_count": 0, "total_amount": 0, "answer": "ok"}

    monkeypatch.setattr(
        "modules.ai_console.live_adapters.ai_readonly_tools.get_daily_payments", fake_daily,
    )

    result = await ReadonlyBusinessAdapter().fetch("BUS-006", {"question": "today"})

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_unresolvable_role_fails_closed(monkeypatch):
    """If the role-level lookup itself fails (DB unavailable etc.), the
    tool must be denied, not silently allowed."""
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter, _role_level_cache

    async def broken_get_role_level(name):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(
        "modules.ai_console.live_adapters.rbac.get_role_level", broken_get_role_level,
    )
    monkeypatch.setattr("modules.ai_console.live_adapters._role_level_cache", {})

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-003", {"question": "payroll"}, actor_role_level=90,
    )

    assert result.status == "permission_denied"


# ===========================================================================
# New tools — happy paths
# ===========================================================================

@pytest.mark.asyncio
async def test_dispatch_availability_tool(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter
    from modules.ai_console import live_adapters

    _patch_role_levels(monkeypatch)

    async def fake_list_escort_workers(active_only=True, employee_id=None, page=1, page_size=50):
        return {"items": [{"employee_id": 1, "available": True}], "total": 1}

    monkeypatch.setattr(live_adapters.dispatch, "list_escort_workers", fake_list_escort_workers)

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-010", {"limit": 10}, actor_role_level=_FAKE_ROLE_LEVELS["viewer"],
    )

    assert result.status == "ok"
    assert result.data["total"] == 1


@pytest.mark.asyncio
async def test_dispatch_conflict_check_missing_params_is_partial_not_crash(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    _patch_role_levels(monkeypatch)

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-011", {"question": "does this conflict?"}, actor_role_level=_FAKE_ROLE_LEVELS["viewer"],
    )

    assert result.status == "partial"
    assert "insufficient parameters" in result.data["message"]


@pytest.mark.asyncio
async def test_dispatch_conflict_check_with_params(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter
    from modules.ai_console import live_adapters

    _patch_role_levels(monkeypatch)

    async def fake_check_conflict(escort_employee_id, start_date, start_shift, end_date, end_shift, exclude_program_id=None):
        return {"available": True, "conflict": None}

    monkeypatch.setattr(live_adapters.dispatch, "check_conflict", fake_check_conflict)

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-011",
        {"employee_id": 5, "start_date": "2026-07-20", "start_shift": "D"},
        actor_role_level=_FAKE_ROLE_LEVELS["viewer"],
    )

    assert result.status == "ok"
    assert result.data["available"] is True


@pytest.mark.asyncio
async def test_missing_end_date_programs_tool(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter
    from modules.ai_console import live_adapters

    _patch_role_levels(monkeypatch)

    async def fake_missing(limit=50):
        return {"items": [{"program_id": 703}], "count": 1, "note": "missing_completion_data"}

    monkeypatch.setattr(live_adapters.dispatch, "get_missing_end_date_programs", fake_missing)

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-013", {}, actor_role_level=_FAKE_ROLE_LEVELS["office_assistant"],
    )

    assert result.status == "ok"
    assert result.data["count"] == 1
    # must never be phrased as a confirmed conflict
    assert "missing_completion_data" in result.data["note"]


@pytest.mark.asyncio
async def test_employee_advances_tool_empty_result_is_honest_not_fabricated(monkeypatch):
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    _patch_role_levels(monkeypatch)

    async def fake_advances(employee_name=None, limit=20):
        return []

    monkeypatch.setattr(
        "modules.ai_console.live_adapters.ai_readonly_tools.get_employee_advances", fake_advances,
    )

    result = await ReadonlyBusinessAdapter().fetch(
        "BUS-014", {"employee_name": "Nobody"}, actor_role_level=_FAKE_ROLE_LEVELS["accountant"],
    )

    assert result.status == "partial"
    assert result.data["rows"] == []


# ===========================================================================
# Orchestrator tool selection
# ===========================================================================

def test_select_business_tools_routes_dispatch_keywords():
    from modules.ai_console.orchestrator import select_business_tools_for_message

    assert "BUS-010" in select_business_tools_for_message("who is available tomorrow?")
    assert "BUS-011" in select_business_tools_for_message("is there a conflict for this booking?")
    assert "BUS-013" in select_business_tools_for_message("which programs have no end date?")
    assert "BUS-014" in select_business_tools_for_message("how much advance does he have?")


def test_select_business_tools_general_business_question_gets_bounded_default():
    from modules.ai_console.orchestrator import select_business_tools_for_message

    tools = select_business_tools_for_message("tell me about the business")
    assert len(tools) < 14  # never blasts all tools for an unspecific question
    assert tools  # but still returns something useful


def test_orchestrator_business_mode_no_longer_always_runs_all_tools():
    from modules.ai_console.orchestrator import classify_message, select_business_tools_for_message

    assert classify_message("payroll question") == "business"
    tools = select_business_tools_for_message("payroll question")
    assert "BUS-003" in tools
    assert "BUS-010" not in tools  # dispatch tools not pulled in for an unrelated question


# ===========================================================================
# Write-safety proof (structural, not just documentation)
# ===========================================================================

_WRITE_KEYWORDS = ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "TRUNCATE", "ALTER TABLE")


def test_new_dispatch_tool_source_is_select_only():
    from modules import dispatch

    source = inspect.getsource(dispatch.get_missing_end_date_programs)
    upper = source.upper()
    for kw in _WRITE_KEYWORDS:
        assert kw not in upper, f"{kw} found in get_missing_end_date_programs source"


def test_new_readonly_tool_source_is_select_only():
    from modules import ai_readonly_tools

    source = inspect.getsource(ai_readonly_tools.get_employee_advances)
    upper = source.upper()
    for kw in _WRITE_KEYWORDS:
        assert kw not in upper, f"{kw} found in get_employee_advances source"


def test_business_adapter_handlers_only_call_readonly_modules():
    """Static proof: none of the new handler methods reference any known
    write-capable function name (assign_program, approve_draft, etc.)."""
    from modules.ai_console.live_adapters import ReadonlyBusinessAdapter

    forbidden = (
        "assign_program", "approve_draft", "create_transaction", "delete_",
        "mark_paid", "complete_program", "override", "execute(",
    )
    for name in (
        "_dispatch_availability", "_dispatch_conflict_check",
        "_dispatch_suggestions", "_missing_end_date_programs", "_employee_advances",
    ):
        source = inspect.getsource(getattr(ReadonlyBusinessAdapter, name))
        for token in forbidden:
            assert token not in source, f"{token} referenced in {name}"
