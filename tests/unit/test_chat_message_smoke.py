import pytest


pytestmark = pytest.mark.asyncio


async def test_chat_message_route_smoke_delegates_to_orchestrator(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import app
    from modules import admin_chat_lab

    observed: dict = {}

    async def fake_orchestrate_chat_message(**kwargs):
        observed.update(kwargs)
        return {
            "answer": "smoke-ok",
            "model": kwargs["active_model"],
            "rag_used": False,
            "db_tools_used": [],
            "web_fetched": False,
            "citations": [],
            "top_score": None,
            "memory_context_used": False,
        }

    monkeypatch.setattr(
        admin_chat_lab,
        "orchestrate_chat_message",
        fake_orchestrate_chat_message,
    )

    async def fake_flag():
        return False

    monkeypatch.setattr("app.main._is_ai_console_chat_enabled", fake_flag)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": get_settings().internal_api_key},
    ) as ac:
        response = await ac.post(
            "/chat/message",
            json={
                "q": "smoke test question",
                "model": "qwen3:8b",
                "history": [{"role": "user", "content": "hello"}],
                "app_context": False,
                "db_context": False,
                "web_context": False,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "smoke-ok"
    assert data["model"] == "qwen3:8b"

    assert observed["question"] == "smoke test question"
    assert observed["requested_model"] == "qwen3:8b"
    assert observed["active_model"] == "qwen3:8b"
    assert observed["history"] == [{"role": "user", "content": "hello"}]
    assert observed["app_context"] is False
    assert observed["db_context"] is False
    assert observed["web_context"] is False


async def test_chat_message_no_longer_special_cases_voice_placeholder_text(monkeypatch):
    """Stage A regression guard: /chat/voice now produces real transcripts, so
    /chat/message must treat a literal "[voice: ...]" string as ordinary text
    and delegate to the orchestrator like any other question — it must not
    intercept it with the old canned Bangla "not transcribed yet" reply."""
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import app
    from modules import admin_chat_lab

    observed: dict = {}

    async def fake_orchestrate_chat_message(**kwargs):
        observed.update(kwargs)
        return {
            "answer": "delegated-ok",
            "model": kwargs["active_model"],
            "rag_used": False,
            "db_tools_used": [],
            "web_fetched": False,
            "citations": [],
            "top_score": None,
            "memory_context_used": False,
        }

    monkeypatch.setattr(
        admin_chat_lab,
        "orchestrate_chat_message",
        fake_orchestrate_chat_message,
    )

    async def fake_flag():
        return False

    monkeypatch.setattr("app.main._is_ai_console_chat_enabled", fake_flag)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Internal-Key": get_settings().internal_api_key},
    ) as ac:
        response = await ac.post(
            "/chat/message",
            json={"q": "[voice: voice-123.webm]", "app_context": False, "db_context": False, "web_context": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "delegated-ok"
    assert observed["question"] == "[voice: voice-123.webm]"
