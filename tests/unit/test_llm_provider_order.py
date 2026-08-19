from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _reset_settings(monkeypatch, *, omniroute=False, groq=True, github=True):
    # NOTE: Settings.Config.env_file points at the real repo .env, which on
    # this server has real production keys for groq/omniroute/github. A
    # bare monkeypatch.delenv() does NOT hide those -- pydantic-settings
    # falls through to the .env file for any var absent from os.environ.
    # To genuinely simulate "not configured" we must monkeypatch.setenv()
    # an explicit empty string, which pydantic-settings treats as present
    # (overriding the .env file) and falsy (equivalent to unset for our
    # `if settings.x_api_key:` checks).
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test")
    monkeypatch.setenv("INTERNAL_API_KEY", "test")
    monkeypatch.setenv("PRIMARY_AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_REPLY_DISABLED", "false")
    monkeypatch.setenv("OLLAMA_MODEL", "phi4-mini:latest")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq" if groq else "")
    monkeypatch.setenv("GITHUB_TOKEN", "test-github" if github else "")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-omniroute" if omniroute else "")
    from app.config import get_settings

    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_llm_module_state():
    """Prevent _resolve_local_ollama_model()'s 5-minute cache and the
    per-task attempt trace from leaking between tests in this file."""
    from app import llm

    llm._hermes_model_cache["model"] = None
    llm._hermes_model_cache["checked_at"] = 0.0
    yield
    llm._hermes_model_cache["model"] = None
    llm._hermes_model_cache["checked_at"] = 0.0


def _mock_no_hermes_model(monkeypatch):
    """Standard 'hermes model not provisioned' /api/tags response, so
    _resolve_local_ollama_model() falls back to settings.ollama_model
    without making a real network call."""
    from app import ollama_daemon

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"models": [{"name": "phi4-mini:latest"}]}
    monkeypatch.setattr(ollama_daemon, "get_tags", AsyncMock(return_value=resp))


# ── 2026-08-05 new chain: OmniRoute → Groq → local Ollama/Hermes → Kimi ───
# (Owner decision — supersedes the prior Ollama-first order tested below
# until this rewrite.) Kimi is reached through the SAME app.ollama client,
# using settings.kimi_model_name — not a separate provider module — so it
# is exercised here as a second app.ollama.generate_reply()/
# generate_recruitment_reply() call, distinguished by its `model=` kwarg.


class TestGenerateReplyChainOrder:
    @pytest.mark.asyncio
    async def test_omniroute_first_when_configured(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=True)
        from app import groq_provider, llm, ollama, omniroute_provider

        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(omniroute_provider, "generate_reply", AsyncMock(return_value="omniroute reply"))
        monkeypatch.setattr(groq_provider, "generate_reply", AsyncMock(return_value="groq reply"))
        monkeypatch.setattr(ollama, "generate_reply", AsyncMock(return_value="ollama reply"))

        reply = await llm.generate_reply("hello", "greeting")

        assert reply == "omniroute reply"
        omniroute_provider.generate_reply.assert_awaited_once()
        groq_provider.generate_reply.assert_not_called()
        ollama.generate_reply.assert_not_called()

        attempts = llm.get_last_attempts()
        assert attempts[0]["provider"] == "omniroute"
        assert attempts[0]["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_skips_omniroute_when_not_configured_goes_to_groq(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=False)
        from app import groq_provider, llm, ollama, omniroute_provider

        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(omniroute_provider, "generate_reply", AsyncMock(side_effect=AssertionError("must not be called")))
        monkeypatch.setattr(groq_provider, "generate_reply", AsyncMock(return_value="groq reply"))
        monkeypatch.setattr(ollama, "generate_reply", AsyncMock(return_value="ollama reply"))

        reply = await llm.generate_reply("hello", "greeting")

        assert reply == "groq reply"
        omniroute_provider.generate_reply.assert_not_called()
        groq_provider.generate_reply.assert_awaited_once()
        ollama.generate_reply.assert_not_called()

        attempts = llm.get_last_attempts()
        assert attempts[0]["provider"] == "omniroute"
        assert attempts[0]["outcome"] == "skipped_not_configured"
        assert attempts[1]["provider"] == "groq"
        assert attempts[1]["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_falls_through_omniroute_and_groq_to_local_ollama(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=True)
        from app import groq_provider, llm, ollama, omniroute_provider

        _mock_no_hermes_model(monkeypatch)
        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(omniroute_provider, "generate_reply", AsyncMock(return_value=None))
        monkeypatch.setattr(groq_provider, "generate_reply", AsyncMock(side_effect=RuntimeError("groq down")))
        monkeypatch.setattr(ollama, "generate_reply", AsyncMock(return_value="ollama reply"))

        reply = await llm.generate_reply("hello", "greeting")

        assert reply == "ollama reply"
        omniroute_provider.generate_reply.assert_awaited_once()
        groq_provider.generate_reply.assert_awaited_once()
        ollama.generate_reply.assert_awaited_once()
        # local ollama step must use the resolved local model, not kimi's
        _, kwargs = ollama.generate_reply.call_args
        assert kwargs["model"] == "phi4-mini:latest"

        attempts = llm.get_last_attempts()
        assert [a["provider"] for a in attempts] == ["omniroute", "groq", "ollama"]
        assert attempts[0]["outcome"] == "empty_reply"
        assert attempts[1]["outcome"] == "error"
        assert attempts[2]["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_ollama_internal_fallback_text_is_not_treated_as_success(self, monkeypatch):
        """app.ollama.generate_reply() never returns empty -- on internal
        failure it returns its own FALLBACK_REPLY_TEXT. The chain must
        detect that and continue to Kimi rather than returning it as a
        genuine reply."""
        _reset_settings(monkeypatch, omniroute=False, groq=False)
        from app import llm, ollama

        _mock_no_hermes_model(monkeypatch)
        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())

        async def _fake_generate_reply(*args, **kwargs):
            if kwargs.get("model") == "kimi-k2.6:cloud":
                return "kimi reply"
            return ollama.FALLBACK_REPLY_TEXT

        monkeypatch.setattr(ollama, "generate_reply", AsyncMock(side_effect=_fake_generate_reply))

        reply = await llm.generate_reply("hello", "greeting")

        assert reply == "kimi reply"
        assert ollama.generate_reply.await_count == 2

        attempts = llm.get_last_attempts()
        assert attempts[-2]["provider"] == "ollama"
        assert attempts[-2]["outcome"] == "empty_or_internal_fallback"
        assert attempts[-1]["provider"] == "kimi"
        assert attempts[-1]["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_all_providers_exhausted_returns_fallback_and_records_attempts(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=False, groq=False)
        from app import llm, ollama

        _mock_no_hermes_model(monkeypatch)
        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(ollama, "generate_reply", AsyncMock(return_value=ollama.FALLBACK_REPLY_TEXT))

        reply = await llm.generate_reply("hello", "greeting")

        assert reply == llm._FALLBACK_REPLY
        attempts = llm.get_last_attempts()
        providers = [a["provider"] for a in attempts]
        assert providers == ["omniroute", "groq", "ollama", "kimi"]
        assert attempts[0]["outcome"] == "skipped_not_configured"
        assert attempts[1]["outcome"] == "skipped_not_configured"
        assert attempts[2]["outcome"] == "empty_or_internal_fallback"
        assert attempts[3]["outcome"] == "empty_or_internal_fallback"

    @pytest.mark.asyncio
    async def test_ollama_disabled_skips_local_step_but_still_tries_kimi(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=False, groq=False)
        monkeypatch.setenv("OLLAMA_REPLY_DISABLED", "true")
        from app.config import get_settings
        get_settings.cache_clear()
        from app import llm, ollama

        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(ollama, "generate_reply", AsyncMock(return_value="kimi reply"))

        reply = await llm.generate_reply("hello", "greeting")

        assert reply == "kimi reply"
        # only the kimi-step call happens -- the local-ollama step is skipped
        ollama.generate_reply.assert_awaited_once()
        attempts = llm.get_last_attempts()
        assert attempts[2]["provider"] == "ollama"
        assert attempts[2]["outcome"] == "skipped_disabled"


class TestGenerateRecruitmentReplyChainOrder:
    @pytest.mark.asyncio
    async def test_omniroute_first_when_configured(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=True)
        from app import groq_provider, llm, ollama, omniroute_provider

        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(omniroute_provider, "generate_recruitment_reply", AsyncMock(return_value="omniroute recruitment"))
        monkeypatch.setattr(groq_provider, "generate_recruitment_reply", AsyncMock(return_value="groq recruitment"))
        monkeypatch.setattr(ollama, "generate_recruitment_reply", AsyncMock(return_value="ollama recruitment"))

        reply = await llm.generate_recruitment_reply("job?", "kb")

        assert reply == "omniroute recruitment"
        omniroute_provider.generate_recruitment_reply.assert_awaited_once()
        groq_provider.generate_recruitment_reply.assert_not_called()
        ollama.generate_recruitment_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_groq_then_local_ollama(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=False)
        from app import groq_provider, llm, ollama, omniroute_provider

        _mock_no_hermes_model(monkeypatch)
        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(omniroute_provider, "generate_recruitment_reply", AsyncMock(side_effect=AssertionError("skip")))
        monkeypatch.setattr(groq_provider, "generate_recruitment_reply", AsyncMock(side_effect=RuntimeError("groq down")))
        monkeypatch.setattr(ollama, "generate_recruitment_reply", AsyncMock(return_value="ollama recruitment"))

        reply = await llm.generate_recruitment_reply("job?", "kb")

        assert reply == "ollama recruitment"
        omniroute_provider.generate_recruitment_reply.assert_not_called()
        groq_provider.generate_recruitment_reply.assert_awaited_once()
        ollama.generate_recruitment_reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ollama_internal_fallback_continues_to_kimi(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=False, groq=False)
        from app import llm, ollama

        _mock_no_hermes_model(monkeypatch)
        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())

        async def _fake_generate_recruitment_reply(*args, **kwargs):
            if kwargs.get("model") == "kimi-k2.6:cloud":
                return "kimi recruitment"
            return ollama.RECRUITMENT_FALLBACK_TEXT

        monkeypatch.setattr(ollama, "generate_recruitment_reply", AsyncMock(side_effect=_fake_generate_recruitment_reply))

        reply = await llm.generate_recruitment_reply("job?", "kb")

        assert reply == "kimi recruitment"
        assert ollama.generate_recruitment_reply.await_count == 2

    @pytest.mark.asyncio
    async def test_all_providers_exhausted_returns_office_contact_fallback(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=False, groq=False)
        from app import llm, ollama

        _mock_no_hermes_model(monkeypatch)
        monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
        monkeypatch.setattr(ollama, "generate_recruitment_reply", AsyncMock(return_value=ollama.RECRUITMENT_FALLBACK_TEXT))

        reply = await llm.generate_recruitment_reply("job?", "kb")

        assert reply == "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"
        attempts = llm.get_last_attempts()
        assert [a["provider"] for a in attempts] == ["omniroute", "groq", "ollama", "kimi"]


class TestChatReplyUnaffectedByChainChange:
    """generate_chat_reply() (admin Chat Lab) is explicitly out of scope --
    still Ollama-first, still reaches github_models, no omniroute/kimi."""

    @pytest.mark.asyncio
    async def test_chat_reply_uses_ollama_before_external_fallbacks(self, monkeypatch):
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


class TestResolveLocalOllamaModel:
    @pytest.mark.asyncio
    async def test_prefers_hermes3_3b_when_provisioned(self, monkeypatch):
        """hermes3:3b is the real, smallest official Ollama-library tag for
        the Hermes family (confirmed via ollama.com/library/hermes3,
        2026-08-05) -- must be preferred over a larger hermes3 variant."""
        _reset_settings(monkeypatch)
        from app import llm, ollama_daemon

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "models": [
                {"name": "phi4-mini:latest"},
                {"name": "hermes3:8b"},
                {"name": "hermes3:3b"},
            ]
        }
        monkeypatch.setattr(ollama_daemon, "get_tags", AsyncMock(return_value=resp))

        model = await llm._resolve_local_ollama_model()

        assert model == "hermes3:3b"

    @pytest.mark.asyncio
    async def test_falls_back_to_bare_hermes_alias_when_hermes3_absent(self, monkeypatch):
        """Bare "hermes"/"hermes:latest" are kept as aliases in case a model
        is ever pulled/tagged that way manually instead of the real
        "hermes3" family name."""
        _reset_settings(monkeypatch)
        from app import llm, ollama_daemon

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "phi4-mini:latest"}, {"name": "hermes"}]}
        monkeypatch.setattr(ollama_daemon, "get_tags", AsyncMock(return_value=resp))

        model = await llm._resolve_local_ollama_model()

        assert model == "hermes"

    @pytest.mark.asyncio
    async def test_falls_back_to_configured_model_when_hermes_absent(self, monkeypatch):
        _reset_settings(monkeypatch)
        _mock_no_hermes_model(monkeypatch)
        from app import llm

        model = await llm._resolve_local_ollama_model()

        assert model == "phi4-mini:latest"

    @pytest.mark.asyncio
    async def test_never_raises_on_network_failure(self, monkeypatch):
        _reset_settings(monkeypatch)
        from app import llm, ollama_daemon

        monkeypatch.setattr(ollama_daemon, "get_tags", AsyncMock(side_effect=RuntimeError("connection refused")))

        model = await llm._resolve_local_ollama_model()

        assert model == "phi4-mini:latest"

    @pytest.mark.asyncio
    async def test_result_is_cached_within_ttl(self, monkeypatch):
        _reset_settings(monkeypatch)
        from app import llm, ollama_daemon

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "hermes"}]}
        mock_get_tags = AsyncMock(return_value=resp)
        monkeypatch.setattr(ollama_daemon, "get_tags", mock_get_tags)

        first = await llm._resolve_local_ollama_model()
        second = await llm._resolve_local_ollama_model()

        assert first == second == "hermes"
        mock_get_tags.assert_awaited_once()


# ── 2026-08-05 confirmed dead provider path: models.github.ai/inference ───
# now returns 410 Gone (endpoint retired). check_health()'s own health-check
# ping was the sole source of 117 repeated 410s in production logs (it had
# no gate at all, unlike groq's check_health() which correctly skips when
# unconfigured) -- github_models.check_health() now short-circuits instead
# of making a doomed live network call. GitHub Models is also no longer part
# of the generate_reply/generate_recruitment_reply chain at all (see
# app/llm.py's module docstring) -- classify_intent_llm/generate_chat_reply
# are a separate, unaffected scope and still reference it.

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

    @pytest.mark.asyncio
    async def test_llm_check_health_reports_omniroute_configured_flag(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=True)
        from app import llm

        result = await llm.check_health()

        assert result["omniroute_configured"] is True
        assert "omniroute" in result

    @pytest.mark.asyncio
    async def test_llm_check_health_omniroute_disabled_when_unconfigured(self, monkeypatch):
        _reset_settings(monkeypatch, omniroute=False)
        from app import llm

        result = await llm.check_health()

        assert result["omniroute_configured"] is False
        assert result["omniroute"]["status"] == "disabled"


# ── 2026-08-19 HERMES_SYSTEM_MESSAGE_ROOTCAUSE_AGENTIC_BEHAVIOR_AUDIT --
# Incident A: check_health() used to await omniroute/github_models/groq/
# ollama sequentially with no per-check timeout. omniroute's own internal
# health-check timeout (10s) already exceeds ai_console_monitor.sh's 5s
# curl --max-time on /chat/models, so a slow/hung OmniRoute alone blew
# the monitor's budget every time. Fixed: all 4 run concurrently
# (asyncio.gather), each individually capped at CHECK_TIMEOUT_S (3.0s,
# well under the monitor's 5s window).

class TestCheckHealthConcurrencyAndTimeout:
    @pytest.mark.asyncio
    async def test_one_hanging_provider_does_not_block_the_others(self, monkeypatch):
        """The real production failure mode: omniroute hangs past the
        monitor's timeout while every other provider is healthy. The
        whole check_health() call must still complete quickly, and every
        OTHER provider's real status must still come through -- not get
        swallowed because one provider was slow."""
        import asyncio as _asyncio
        import time as _time

        _reset_settings(monkeypatch, omniroute=True)
        from app import llm

        async def _hangs_forever():
            await _asyncio.sleep(3600)
            return {"status": "ok"}  # pragma: no cover -- never reached

        monkeypatch.setattr(llm, "_check_omniroute", lambda: _hangs_forever())

        t0 = _time.monotonic()
        result = await llm.check_health()
        elapsed = _time.monotonic() - t0

        assert elapsed < llm.CHECK_TIMEOUT_S + 1.0, (
            f"check_health() took {elapsed:.1f}s -- a hanging provider must not "
            f"block the whole function past CHECK_TIMEOUT_S"
        )
        assert result["omniroute"]["status"] == "timeout"
        # ollama/groq/github_models still report their own real status --
        # not swallowed by omniroute's hang.
        assert result["github_models"]["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_slow_provider_reports_timeout_status_not_error(self, monkeypatch):
        import asyncio as _asyncio

        _reset_settings(monkeypatch)
        from app import llm

        async def _slow():
            await _asyncio.sleep(llm.CHECK_TIMEOUT_S + 5)
            return {"status": "ok"}  # pragma: no cover

        monkeypatch.setattr(llm, "_check_ollama", lambda: _slow())

        result = await llm.check_health()
        assert result["ollama"]["status"] == "timeout"
        assert str(llm.CHECK_TIMEOUT_S) in result["ollama"]["error"]

    @pytest.mark.asyncio
    async def test_all_four_checks_actually_run_concurrently(self, monkeypatch):
        """Total latency must be close to the SLOWEST single check, not
        the SUM of all four -- proves gather(), not sequential awaits."""
        import asyncio as _asyncio
        import time as _time

        _reset_settings(monkeypatch, omniroute=True)
        from app import llm

        PER_CHECK_DELAY = 0.3

        async def _delayed(status):
            await _asyncio.sleep(PER_CHECK_DELAY)
            return {"status": status}

        monkeypatch.setattr(llm, "_check_omniroute", lambda: _delayed("ok"))
        monkeypatch.setattr(llm, "_check_github_models", lambda: _delayed("ok"))
        monkeypatch.setattr(llm, "_check_ollama", lambda: _delayed("ok"))

        t0 = _time.monotonic()
        await llm.check_health()
        elapsed = _time.monotonic() - t0

        # Sequential would be >= 3 * PER_CHECK_DELAY; concurrent should be
        # close to 1 * PER_CHECK_DELAY plus overhead.
        assert elapsed < PER_CHECK_DELAY * 2, (
            f"check_health() took {elapsed:.2f}s for 3 checks each delayed "
            f"{PER_CHECK_DELAY}s -- looks sequential, not concurrent"
        )

    @pytest.mark.asyncio
    async def test_return_shape_unchanged(self, monkeypatch):
        """Regression guard: callers (modules.ai_console.provider_registry,
        app/main.py's /health/deep) only read these exact top-level keys."""
        _reset_settings(monkeypatch)
        from app import llm

        result = await llm.check_health()
        for key in (
            "primary_provider", "ollama_reply_disabled", "groq_configured",
            "omniroute_configured", "omniroute", "github_models", "groq", "ollama",
        ):
            assert key in result
