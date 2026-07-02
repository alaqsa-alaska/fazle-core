from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


async def test_chat_message_feature_flag_off_uses_legacy_path(monkeypatch):
    from app import main
    from modules import admin_chat_lab

    observed: dict = {}

    async def fake_flag():
        return False

    async def fake_legacy(**kwargs):
        observed.update(kwargs)
        return {
            "answer": "legacy-ok",
            "model": kwargs["active_model"],
            "rag_used": False,
            "db_tools_used": [],
            "web_fetched": False,
            "citations": [],
            "top_score": None,
            "memory_context_used": False,
        }

    monkeypatch.setattr(main, "_is_ai_console_chat_enabled", fake_flag)
    monkeypatch.setattr(admin_chat_lab, "orchestrate_chat_message", fake_legacy)

    result = await main.chat_message(_FakeRequest({"q": "test question"}))

    assert result["answer"] == "legacy-ok"
    assert result["feature_path"] == "admin_chat_lab"
    assert result["ai_console_enabled"] is False
    assert result["evidence"] == {
        "runtime": [],
        "business": [],
        "memory": [],
        "knowledge": [],
        "provider": {},
        "warnings": [],
        "errors": [],
    }
    assert observed["question"] == "test question"


async def test_chat_message_feature_flag_on_uses_ai_console_path(monkeypatch):
    from app import main
    from modules import ai_console

    async def fake_flag():
        return True

    async def fake_console_request(
        message,
        *,
        requested_model=None,
        history=None,
        app_context=True,
        db_context=True,
        web_context=True,
        include_provider_response=False,
        registry=None,
    ):
        class _Resp:
            request_id = "live-123"
            mode = "runtime"
            selected_tools = ["RT-001", "BUS-001"]
            traceability = ["ORCH-005", "RT-001", "BUS-001"]
            provider_result = {
                "provider": "llm.py",
                "model": "qwen3:8b",
                "response": "ai-console-ok",
                "status": "ok",
                "traceability": ["ORCH-005"],
            }
            evidence = {
                "confidence": "Verified",
                "runtime": [{"tool_name": "systemd", "data": {"status": "ok"}}],
                "business": [{"tool_name": "employee_search", "data": {"employee": "Azim"}}],
                "memory": [{"tool_name": "owner_instruction", "data": {"note": "remember"}}],
                "knowledge": [{"tool_name": "knowledge_search", "data": {"doc": "ops"}}],
                "provider": {"selected": {"provider": "llm.py", "model": "qwen3:8b"}},
            }

        return _Resp()

    monkeypatch.setattr(main, "_is_ai_console_chat_enabled", fake_flag)
    monkeypatch.setattr(ai_console, "orchestrate_console_request", fake_console_request)

    result = await main.chat_message(_FakeRequest({"q": "docker health", "history": []}))

    assert result["answer"] == "ai-console-ok"
    assert result["feature_path"] == "ai_console"
    assert result["ai_console_enabled"] is True
    assert result["confidence"] == "Verified"
    assert result["provider"]["provider"] == "llm.py"
    assert result["runtime_used"] == ["systemd"]
    assert result["business_used"] == ["employee_search"]
    assert result["memory_used"] == ["owner_instruction"]
    assert result["knowledge_used"] == ["knowledge_search"]
    assert result["evidence"]["runtime"][0]["tool_name"] == "systemd"
    assert result["evidence"]["provider"]["selected"]["provider"] == "llm.py"


async def test_chat_models_returns_provider_registry_metadata(monkeypatch):
    from app import main

    async def fake_registry_models(self):
        return {
            "primary_provider": "ollama",
            "providers": {"ollama": {"status": "ok"}},
            "models": [
                {
                    "provider": "ollama",
                    "model_name": "qwen3:8b",
                    "availability": True,
                    "purpose_label": "primary_local_operator",
                    "recommended_use": "Balanced local default",
                    "health_status": "ok",
                    "active": True,
                },
                {
                    "provider": "groq",
                    "model_name": "llama-3.1-8b-instant",
                    "availability": True,
                    "purpose_label": "fast_cloud_fallback",
                    "recommended_use": "Fast cloud fallback",
                    "health_status": "ok",
                    "active": False,
                },
            ],
            "default_model": "qwen3:8b",
        }

    async def fake_flag():
        return False

    monkeypatch.setattr("modules.ai_console.provider_registry.ProviderRegistry.list_models", fake_registry_models)
    monkeypatch.setattr(main, "_is_ai_console_chat_enabled", fake_flag)

    result = await main.chat_models()

    assert result["default"] == "qwen3:8b"
    assert result["primary_provider"] == "ollama"
    assert result["models"][0]["id"] == "qwen3:8b"
    assert result["models"][0]["provider"] == "ollama"
    assert "selectable" in result["models"][0]
    assert result["models"][1]["provider"] == "groq"


async def test_ai_console_route_preserves_context_flags(monkeypatch):
    from app import main
    from modules import ai_console

    observed: dict = {}

    async def fake_flag():
        return True

    async def fake_console_request(
        message,
        *,
        requested_model=None,
        history=None,
        app_context=True,
        db_context=True,
        web_context=True,
        include_provider_response=False,
        registry=None,
    ):
        observed.update(
            {
                "message": message,
                "app_context": app_context,
                "db_context": db_context,
                "web_context": web_context,
                "history": history,
            }
        )

        class _Resp:
            request_id = "live-456"
            mode = "general"
            selected_tools = []
            traceability = ["ORCH-005"]
            provider_result = {"provider": "llm.py", "model": "qwen3:8b", "response": "ok", "status": "ok", "traceability": ["ORCH-005"]}
            evidence = {"confidence": "Unknown", "runtime": [], "business": [], "memory": [], "knowledge": [], "provider": {"selected": {"provider": "llm.py"}}}

        return _Resp()

    monkeypatch.setattr(main, "_is_ai_console_chat_enabled", fake_flag)
    monkeypatch.setattr(ai_console, "orchestrate_console_request", fake_console_request)

    await main.chat_message(
        _FakeRequest(
            {
                "q": "explain this",
                "history": [{"role": "user", "content": "hello"}],
                "app_context": False,
                "db_context": True,
                "web_context": False,
            }
        )
    )

    assert observed["message"] == "explain this"
    assert observed["app_context"] is False
    assert observed["db_context"] is True
    assert observed["web_context"] is False
    assert observed["history"] == [{"role": "user", "content": "hello"}]
