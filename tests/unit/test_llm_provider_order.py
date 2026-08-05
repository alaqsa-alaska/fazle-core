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


# ── 2026-08-05 confirmed dead provider path: models.github.ai/inference ───
# now returns 410 Gone (endpoint retired). check_health()'s own health-check
# ping was the sole source of 117 repeated 410s in production logs (it had
# no gate at all, unlike groq's check_health() which correctly skips when
# unconfigured) -- github_models.check_health() now short-circuits instead
# of making a doomed live network call. The reply-generation fallback chain
# tested above (generate_reply/generate_chat_reply/generate_recruitment_reply)
# is a completely separate code path and is untouched by this fix.

class TestGithubModelsHealthCheckDeadEndpoint:
    @pytest.mark.asyncio
    async def test_check_health_short_circuits_without_network_call(self, monkeypatch):
        from app import github_models

        async def _would_fail_if_called(*args, **kwargs):
            raise AssertionError("check_health must not make a live network call to the confirmed-dead endpoint")

        monkeypatch.setattr(github_models, "_make_client", _would_fail_if_called)

        result = await github_models.check_health()

        assert result["status"] == "disabled"
        assert "410" in result["reason"] or "retired" in result["reason"]

    @pytest.mark.asyncio
    async def test_check_health_return_shape_unchanged_for_callers(self, monkeypatch):
        """Regression guard: callers (app.llm.check_health, and transitively
        modules.ai_console.provider_registry) only ever read .get("status")
        etc. -- the dict shape/keys must stay compatible even though the
        live-ping path is now skipped."""
        from app import github_models

        result = await github_models.check_health()
        assert isinstance(result, dict)
        assert "status" in result
        assert "model" in result
        assert "endpoint" in result


class TestLlmCheckHealthComposesGithubModelsStatus:
    @pytest.mark.asyncio
    async def test_llm_check_health_reports_disabled_not_error(self, monkeypatch):
        """app.llm.check_health() (the function modules.ai_console.
        provider_registry calls on every /chat/models request) must now
        surface github_models' "disabled" status cleanly, not swallow an
        exception into a generic error dict -- confirms the fix is actually
        visible end-to-end through the real caller, not just at the
        github_models module boundary."""
        _reset_settings(monkeypatch)
        from app import llm

        result = await llm.check_health()

        assert result["github_models"]["status"] == "disabled"
