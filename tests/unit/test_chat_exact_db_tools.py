import asyncio
import json
import logging

import pytest

from modules import admin_chat_lab


_EXACT_DB_TOOLS = {
    "get_daily_payments",
    "get_employee_month_payments",
    "get_lighter_assignment",
}


@pytest.fixture(autouse=True)
def _stub_orchestrator_background_persistence(monkeypatch):
    async def _noop_persist_model_decision(*args, **kwargs):
        return None

    async def _noop_save_memory_record(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_chat_lab, "_persist_model_decision", _noop_persist_model_decision)
    monkeypatch.setattr(admin_chat_lab, "_save_memory_record", _noop_save_memory_record)


@pytest.mark.asyncio
async def test_orchestrator_exact_payment_question_uses_deterministic_db_tool(monkeypatch):
    from modules import ai_readonly_tools

    async def fail_llm(*args, **kwargs):
        raise AssertionError("LLM must not answer exact DB questions")

    async def fake_run_tool(tool_name, _question):
        assert tool_name == "get_daily_payments"
        return {
            "tool": tool_name,
            "data": {"transaction_count": 1},
            "count": 1,
            "answer": "আজ ১ জনকে পেমেন্ট দেয়া হয়েছে।",
        }

    monkeypatch.setattr(ai_readonly_tools, "detect_tools_needed", lambda question: ["get_daily_payments"])
    monkeypatch.setattr(ai_readonly_tools, "run_tool", fake_run_tool)

    res = await admin_chat_lab.orchestrate_chat_message(
        question="আজ কয়জনকে পেমেন্ট দেয়া হয়েছে?",
        requested_model=None,
        active_model="qwen3:8b",
        history=[],
        app_context=False,
        db_context=False,
        web_context=False,
        exact_db_answer_tools=_EXACT_DB_TOOLS,
        llm_generate=fail_llm,
        chat_timeout_s=120,
        logger=logging.getLogger("test-chat"),
    )

    assert res["model"] == "deterministic-db-tool"
    assert res["db_tools_used"] == ["get_daily_payments"]
    assert "১ জন" in res["answer"]


@pytest.mark.asyncio
async def test_orchestrator_exact_db_tool_failure_does_not_fall_back_to_llm(monkeypatch):
    from modules import ai_readonly_tools

    async def fail_llm(*args, **kwargs):
        raise AssertionError("LLM fallback must be blocked for exact DB questions")

    async def broken_run_tool(tool_name, question):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(ai_readonly_tools, "detect_tools_needed", lambda question: ["get_employee_month_payments"])
    monkeypatch.setattr(ai_readonly_tools, "run_tool", broken_run_tool)

    res = await admin_chat_lab.orchestrate_chat_message(
        question="দেবাষীশ মে ২০২৬ মাসে মোট কত পেমেন্ট পেয়েছে?",
        requested_model=None,
        active_model="qwen3:8b",
        history=[],
        app_context=False,
        db_context=False,
        web_context=False,
        exact_db_answer_tools=_EXACT_DB_TOOLS,
        llm_generate=fail_llm,
        chat_timeout_s=120,
        logger=logging.getLogger("test-chat"),
    )

    assert res["model"] == "deterministic-db-tool"
    assert res["error"] == "exact_db_tool_unavailable"
    assert "ভুল তথ্য না দিয়ে" in res["answer"]


@pytest.mark.asyncio
async def test_orchestrator_non_deterministic_path_uses_llm_and_returns_trace(monkeypatch):
    from modules import ai_readonly_tools

    async def fake_llm(question, context, history, model=None):
        assert question == "এই সার্ভিস সম্পর্কে সংক্ষেপে বলো"
        assert context == ""
        assert history == [{"role": "user", "content": "আগের কথা"}]
        assert model == "qwen3:8b"
        return "সংক্ষিপ্ত উত্তর"

    monkeypatch.setattr(ai_readonly_tools, "detect_tools_needed", lambda question: [])

    res = await admin_chat_lab.orchestrate_chat_message(
        question="এই সার্ভিস সম্পর্কে সংক্ষেপে বলো",
        requested_model="qwen3:8b",
        active_model="qwen3:8b",
        history=[{"role": "user", "content": "আগের কথা"}],
        app_context=False,
        db_context=False,
        web_context=False,
        exact_db_answer_tools=_EXACT_DB_TOOLS,
        llm_generate=fake_llm,
        chat_timeout_s=120,
        logger=logging.getLogger("test-chat"),
    )

    assert res["answer"] == "সংক্ষিপ্ত উত্তর"
    assert res["model"] == "qwen3:8b"
    assert res["db_tools_used"] == []
    assert res["web_fetched"] is False
    assert res["memory_context_used"] is False
    assert res["trace"]["context_plan"]["load_kb"] is False
    assert res["trace"]["tool_plan"]["business_tools"] == []
    assert res["trace"]["provider_selection"]["selected_model"] == "qwen3:8b"


@pytest.mark.asyncio
async def test_orchestrator_timeout_returns_504_response(monkeypatch):
    from modules import ai_readonly_tools

    async def timeout_llm(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(ai_readonly_tools, "detect_tools_needed", lambda question: [])

    res = await admin_chat_lab.orchestrate_chat_message(
        question="একটু দীর্ঘ বিশ্লেষণ দাও",
        requested_model="qwen3:8b",
        active_model="qwen3:8b",
        history=[],
        app_context=False,
        db_context=False,
        web_context=False,
        exact_db_answer_tools=_EXACT_DB_TOOLS,
        llm_generate=timeout_llm,
        chat_timeout_s=120,
        logger=logging.getLogger("test-chat"),
    )

    body = json.loads(res.body.decode("utf-8"))
    assert res.status_code == 504
    assert body["error"] == "llm_timeout"
    assert body["model"] == "qwen3:8b"
    assert body["timeout_s"] == 120
