import json

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


@pytest.mark.asyncio
async def test_chat_non_deterministic_path_uses_llm_and_returns_trace(monkeypatch):
    import app.main as main
    from modules import ai_readonly_tools

    async def fake_llm(question, context, history, model=None):
        assert question == "এই সার্ভিস সম্পর্কে সংক্ষেপে বলো"
        assert context == ""
        assert history == [{"role": "user", "content": "আগের কথা"}]
        assert model == "qwen3:8b"
        return "সংক্ষিপ্ত উত্তর"

    monkeypatch.setattr(ai_readonly_tools, "detect_tools_needed", lambda question: [])
    monkeypatch.setattr(main.ai, "generate_chat_reply", fake_llm)

    res = await main.chat_message(_FakeRequest({
        "q": "এই সার্ভিস সম্পর্কে সংক্ষেপে বলো",
        "model": "qwen3:8b",
        "history": [{"role": "user", "content": "আগের কথা"}],
        "app_context": False,
        "db_context": False,
        "web_context": False,
    }))

    assert res["answer"] == "সংক্ষিপ্ত উত্তর"
    assert res["model"] == "qwen3:8b"
    assert res["db_tools_used"] == []
    assert res["web_fetched"] is False
    assert res["memory_context_used"] is False
    assert res["trace"]["context_plan"]["load_kb"] is False
    assert res["trace"]["tool_plan"]["business_tools"] == []
    assert res["trace"]["provider_selection"]["selected_model"] == "qwen3:8b"


@pytest.mark.asyncio
async def test_chat_timeout_returns_504_response(monkeypatch):
    import app.main as main
    from modules import ai_readonly_tools

    async def timeout_llm(*args, **kwargs):
        raise main.asyncio.TimeoutError()

    monkeypatch.setattr(ai_readonly_tools, "detect_tools_needed", lambda question: [])
    monkeypatch.setattr(main.ai, "generate_chat_reply", timeout_llm)

    res = await main.chat_message(_FakeRequest({
        "q": "একটু দীর্ঘ বিশ্লেষণ দাও",
        "app_context": False,
        "db_context": False,
        "web_context": False,
    }))

    body = json.loads(res.body.decode("utf-8"))
    assert res.status_code == 504
    assert body["error"] == "llm_timeout"
    assert body["model"] == "qwen3:8b"
    assert body["timeout_s"] == main._CHAT_TIMEOUT_S
