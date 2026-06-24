from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


def _reset_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test")
    monkeypatch.setenv("INTERNAL_API_KEY", "test")
    monkeypatch.setenv("PRIMARY_AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_REPLY_DISABLED", "false")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("GITHUB_TOKEN", "test-github")
    from app.config import get_settings

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generate_reply_uses_ollama_before_external_fallbacks(monkeypatch):
    _reset_settings(monkeypatch)
    from app import github_models, groq_provider, llm, ollama

    monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
    monkeypatch.setattr(ollama, "generate_reply", AsyncMock(return_value="ollama reply"))
    monkeypatch.setattr(groq_provider, "generate_reply", AsyncMock(return_value="groq reply"))
    monkeypatch.setattr(github_models, "generate_reply", AsyncMock(return_value="github reply"))

    reply = await llm.generate_reply("hello", "greeting")

    assert reply == "ollama reply"
    ollama.generate_reply.assert_awaited_once()
    groq_provider.generate_reply.assert_not_called()
    github_models.generate_reply.assert_not_called()


@pytest.mark.asyncio
async def test_generate_reply_falls_back_to_groq_then_github(monkeypatch):
    _reset_settings(monkeypatch)
    from app import github_models, groq_provider, llm, ollama

    monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
    monkeypatch.setattr(ollama, "generate_reply", AsyncMock(side_effect=RuntimeError("ollama down")))
    monkeypatch.setattr(groq_provider, "generate_reply", AsyncMock(return_value="groq reply"))
    monkeypatch.setattr(github_models, "generate_reply", AsyncMock(return_value="github reply"))

    reply = await llm.generate_reply("hello", "greeting")

    assert reply == "groq reply"
    ollama.generate_reply.assert_awaited_once()
    groq_provider.generate_reply.assert_awaited_once()
    github_models.generate_reply.assert_not_called()


@pytest.mark.asyncio
async def test_chat_reply_uses_ollama_before_external_fallbacks(monkeypatch):
    _reset_settings(monkeypatch)
    from app import github_models, groq_provider, llm, ollama

    monkeypatch.setattr(ollama, "generate_chat_reply", AsyncMock(return_value="ollama chat"))
    monkeypatch.setattr(groq_provider, "generate_chat_reply", AsyncMock(return_value="groq chat"))
    monkeypatch.setattr(github_models, "generate_chat_reply", AsyncMock(return_value="github chat"))

    reply = await llm.generate_chat_reply("question", "context", [])

    assert reply == "ollama chat"
    ollama.generate_chat_reply.assert_awaited_once()
    groq_provider.generate_chat_reply.assert_not_called()
    github_models.generate_chat_reply.assert_not_called()


@pytest.mark.asyncio
async def test_recruitment_reply_uses_ollama_before_external_fallbacks(monkeypatch):
    _reset_settings(monkeypatch)
    from app import github_models, groq_provider, llm, ollama

    monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
    monkeypatch.setattr(ollama, "generate_recruitment_reply", AsyncMock(return_value="ollama recruitment"))
    monkeypatch.setattr(groq_provider, "generate_recruitment_reply", AsyncMock(return_value="groq recruitment"))
    monkeypatch.setattr(github_models, "generate_recruitment_reply", AsyncMock(return_value="github recruitment"))

    reply = await llm.generate_recruitment_reply("job?", "kb")

    assert reply == "ollama recruitment"
    ollama.generate_recruitment_reply.assert_awaited_once()
    groq_provider.generate_recruitment_reply.assert_not_called()
    github_models.generate_recruitment_reply.assert_not_called()
