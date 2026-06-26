import pytest


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_chat_exact_payment_question_forces_db_tool_when_db_context_disabled(monkeypatch):
    import app.main as main
    from modules import ai_readonly_tools

    async def fail_llm(*args, **kwargs):
        raise AssertionError("LLM must not answer exact DB questions")

    async def fake_run_tool(tool_name, question):
        assert tool_name == "get_daily_payments"
        return {
            "tool": tool_name,
            "data": {"transaction_count": 1},
            "count": 1,
            "answer": "আজ ১ জনকে পেমেন্ট দেয়া হয়েছে।",
        }

    monkeypatch.setattr(main.ai, "generate_chat_reply", fail_llm)
    monkeypatch.setattr(ai_readonly_tools, "run_tool", fake_run_tool)

    res = await main.chat_message(_FakeRequest({
        "q": "আজ কয়জনকে পেমেন্ট দেয়া হয়েছে?",
        "app_context": False,
        "db_context": False,
        "web_context": False,
    }))

    assert res["model"] == "deterministic-db-tool"
    assert res["db_tools_used"] == ["get_daily_payments"]
    assert "১ জন" in res["answer"]


@pytest.mark.asyncio
async def test_chat_exact_db_tool_failure_does_not_fall_back_to_llm(monkeypatch):
    import app.main as main
    from modules import ai_readonly_tools

    async def fail_llm(*args, **kwargs):
        raise AssertionError("LLM fallback must be blocked for exact DB questions")

    async def broken_run_tool(tool_name, question):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(main.ai, "generate_chat_reply", fail_llm)
    monkeypatch.setattr(ai_readonly_tools, "run_tool", broken_run_tool)

    res = await main.chat_message(_FakeRequest({
        "q": "দেবাষীশ মে ২০২৬ মাসে মোট কত পেমেন্ট পেয়েছে?",
        "app_context": False,
        "db_context": False,
        "web_context": False,
    }))

    assert res["model"] == "deterministic-db-tool"
    assert res["error"] == "exact_db_tool_unavailable"
    assert "ভুল তথ্য না দিয়ে" in res["answer"]
