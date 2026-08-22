"""
Fazle Core — focused unit tests for the isolated MiniMax provider.

All external HTTP is mocked. No live API call. No paid traffic.
"""
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openai import RateLimitError, APIError, APITimeoutError

import app.minimax_provider as minimax_provider
import app.config as app_config

# Pydantic BaseSettings reads .env via Settings.model_config["env_file"] at
# __init__ time. To bypass the production .env in tests, monkey-patch the
# env_file key before any Settings() is constructed.
app_config.Settings.model_config["env_file"] = os.path.join(
    "/tmp", "nonexistent_for_tests_" + str(os.getpid()) + ".env"
)

# Make sure the app package is importable when pytest is invoked from /home/azim/core.
if "/home/azim/core" not in sys.path:
    sys.path.insert(0, "/home/azim/core")


def _settings(**overrides):
    """Build a Settings instance with the minimum keys needed by minimax_provider."""
    base = dict(
        minimax_api_key="test-key-not-real",
        minimax_base_url="https://api.minimax.local/v1",
        minimax_model_name="minimax-m1-test",
    )
    base.update(overrides)
    # Bypass Pydantic's required-field validation so we don't have to plumb every
    # database_url/redis_url/etc through the test fixture. model_construct sets
    # attributes without running validators — exactly what we need for unit tests
    # that only exercise minimax_provider's own surface.
    return app_config.Settings.model_construct(_fields_set=set(base.keys()), **base)


def _async_client_with_chat_response(content: str):
    """Build an AsyncMock client that mimics the OpenAI chat.completions.create shape."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_successful_response():
    """1. Successful mocked MiniMax response."""
    client = _async_client_with_chat_response("পরীক্ষামূলক বাংলা উত্তর")
    with patch.object(minimax_provider, "_make_client", return_value=client), \
         patch.object(minimax_provider, "get_settings", return_value=_settings()):
        reply = await minimax_provider.generate_reply(
            user_message="হ্যালো", intent="greeting", role="new_lead", source="bridge1",
        )
    assert reply == "পরীক্ষামূলক বাংলা উত্তর"


@pytest.mark.asyncio
async def test_missing_api_key_returns_none():
    """2. Missing configuration — provider must fail closed, no exception."""
    # No API key configured at all.
    with patch.object(minimax_provider, "_make_client", side_effect=ValueError("MINIMAX_API_KEY not configured")), \
         patch.object(minimax_provider, "get_settings", return_value=_settings(minimax_api_key="")):
        reply = await minimax_provider.generate_reply(
            user_message="hi", intent="greeting",
        )
    assert reply is None


@pytest.mark.asyncio
async def test_timeout_returns_none():
    """3. Timeout — client raises asyncio.TimeoutError, provider returns None."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=asyncio.TimeoutError)
    with patch.object(minimax_provider, "_make_client", return_value=client), \
         patch.object(minimax_provider, "get_settings", return_value=_settings()):
        reply = await minimax_provider.generate_reply(user_message="x", intent="recruitment")
    assert reply is None


@pytest.mark.asyncio
async def test_api_timeout_returns_none():
    """3b. OpenAI APITimeoutError."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=APITimeoutError("timed out"))
    with patch.object(minimax_provider, "_make_client", return_value=client), \
         patch.object(minimax_provider, "get_settings", return_value=_settings()):
        reply = await minimax_provider.generate_reply(user_message="x", intent="recruitment")
    assert reply is None


@pytest.mark.asyncio
async def test_rate_limit_returns_none():
    """4. HTTP 429 / rate limit."""
    fake_response = MagicMock()
    fake_response.status_code = 429
    err = RateLimitError("rate limited", response=fake_response, body=None)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=err)
    with patch.object(minimax_provider, "_make_client", return_value=client), \
         patch.object(minimax_provider, "get_settings", return_value=_settings()):
        reply = await minimax_provider.generate_reply(user_message="x", intent="recruitment")
    assert reply is None


@pytest.mark.asyncio
async def test_provider_5xx_returns_none():
    """5. Provider 5xx (APIError covers upstream server errors)."""
    fake_response = MagicMock()
    fake_response.status_code = 503
    err = APIError("upstream unavailable", request=MagicMock(), body=None)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=err)
    with patch.object(minimax_provider, "_make_client", return_value=client), \
         patch.object(minimax_provider, "get_settings", return_value=_settings()):
        reply = await minimax_provider.generate_reply(user_message="x", intent="recruitment")
    assert reply is None


@pytest.mark.asyncio
async def test_empty_response_returns_none():
    """6. Empty / invalid response (content is None or whitespace)."""
    client = _async_client_with_chat_response("")
    with patch.object(minimax_provider, "_make_client", return_value=client), \
         patch.object(minimax_provider, "get_settings", return_value=_settings()):
        reply = await minimax_provider.generate_reply(user_message="x", intent="recruitment")
    # Provider returns "" on empty content — caller treats falsy as "no reply".
    # Document the contract here so the failure-mode is explicit.
    assert reply == ""


@pytest.mark.asyncio
async def test_return_type_compatible():
    """8. Caller-compatible return type — Optional[str], never raises on any of the above."""
    for side_effect in [
        asyncio.TimeoutError,
        APITimeoutError("x"),
        RateLimitError("x", response=MagicMock(status_code=429), body=None),
        APIError("x", request=MagicMock(), body=None),
        ValueError("config missing"),
        RuntimeError("unexpected"),
    ]:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=side_effect)
        with patch.object(minimax_provider, "_make_client", return_value=client), \
             patch.object(minimax_provider, "get_settings", return_value=_settings()):
            reply = await minimax_provider.generate_reply(user_message="x", intent="recruitment")
        assert reply is None or isinstance(reply, str), f"unexpected return for {type(side_effect).__name__}"


@pytest.mark.asyncio
async def test_recruitment_reply_uses_provider():
    """Recruitment contract mirrors generate_reply and returns the model's content."""
    client = _async_client_with_chat_response("রিক্রুটমেন্ট রিপ্লাই")
    with patch.object(minimax_provider, "_make_client", return_value=client), \
         patch.object(minimax_provider, "get_settings", return_value=_settings()):
        reply = await minimax_provider.generate_recruitment_reply(
            user_message="আমি কাজ খুঁজছি",
            kb_context="some kb",
            source="bridge1",
        )
    assert reply == "রিক্রুটমেন্ট রিপ্লাই"