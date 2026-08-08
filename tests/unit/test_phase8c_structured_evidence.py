from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


async def test_phase8c_legacy_chat_response_keeps_backward_compatible_shape(monkeypatch):
    from app import main
    from modules import admin_chat_lab

    async def fake_flag():
        return False

    async def fake_legacy(**_kwargs):
        return {
            "answer": "legacy-safe",
            "model": "qwen3:8b",
            "rag_used": False,
            "db_tools_used": ["employee_search"],
            "web_fetched": False,
            "citations": ["kb_doc"],
            "top_score": None,
            "memory_context_used": True,
        }

    monkeypatch.setattr(main, "_is_ai_console_chat_enabled", fake_flag)
    monkeypatch.setattr(admin_chat_lab, "orchestrate_chat_message", fake_legacy)

    result = await main.chat_message(_FakeRequest({"q": "legacy request"}))

    assert result["answer"] == "legacy-safe"
    assert result["feature_path"] == "admin_chat_lab"
    assert result["business_used"] == ["employee_search"]
    assert result["knowledge_used"] == ["kb_doc"]
    assert result["memory_used"] == ["ollama_memory"]
    assert result["evidence"]["runtime"] == []
    assert result["evidence"]["business"] == []
    assert result["evidence_summary"]["runtime_count"] == 0
    assert result["evidence_summary"]["warning_count"] == 0


async def test_phase8c_ai_console_response_returns_structured_evidence(monkeypatch):
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
        actor_role_level=100,
    ):
        class _Resp:
            request_id = "live-888"
            mode = "runtime"
            selected_tools = ["RT-001", "BUS-001", "MEM-001"]
            traceability = ["ORCH-005", "RT-001", "BUS-001", "MEM-001"]
            provider_result = {
                "provider": "llm.py",
                "model": "qwen3:8b",
                "response": "structured-evidence-ok",
                "status": "ok",
                "traceability": ["ORCH-005"],
            }
            evidence = {
                "confidence": "Partially Verified",
                "runtime": [
                    {
                        "tool_id": "RT-001",
                        "tool_name": "systemd_status",
                        "status": "ok",
                        "confidence": "Verified",
                        "data": {
                            "unit": "fazle-core.service",
                            "log": "authorization=Bearer super-secret-token",
                        },
                    }
                ],
                "business": [
                    {
                        "tool_id": "BUS-001",
                        "tool_name": "employee_search",
                        "status": "ok",
                        "confidence": "Verified",
                        "data": {"query": message, "employee": "Azim"},
                    }
                ],
                "memory": [
                    {
                        "tool_id": "MEM-001",
                        "tool_name": "owner_instruction",
                        "status": "ok",
                        "confidence": "Verified",
                        "data": {"note": "remember this"},
                    }
                ],
                "knowledge": [
                    {
                        "tool_id": "BUS-008",
                        "tool_name": "knowledge_search",
                        "status": "ok",
                        "confidence": "Inference",
                        "data": {"doc": "ops-runbook"},
                    }
                ],
                "provider": {
                    "selected": {
                        "provider": "ollama",
                        "model": "qwen3:8b",
                        "api_key": "hidden-key",
                    }
                },
                "warnings": ["token=very-secret"],
                "errors": [],
            }

        return _Resp()

    monkeypatch.setattr(main, "_is_ai_console_chat_enabled", fake_flag)
    monkeypatch.setattr(ai_console, "orchestrate_console_request", fake_console_request)

    result = await main.chat_message(_FakeRequest({"q": "show runtime"}))

    assert result["answer"] == "structured-evidence-ok"
    assert result["feature_path"] == "ai_console"
    assert result["provider"]["provider"] == "ollama"
    assert result["provider"]["api_key"] == "[redacted]"
    assert result["evidence"]["runtime"][0]["tool_name"] == "systemd_status"
    assert "[redacted]" in result["evidence"]["runtime"][0]["data"]["log"]
    assert result["evidence"]["warnings"][0] == "token=[redacted]"
    assert result["runtime_used"] == ["systemd_status"]
    assert result["business_used"] == ["employee_search"]
    assert result["memory_used"] == ["owner_instruction"]
    assert result["knowledge_used"] == ["knowledge_search"]
    assert result["evidence_summary"]["runtime_count"] == 1
    assert result["evidence_summary"]["warning_count"] == 1


async def test_phase8c_sanitizer_redacts_and_truncates():
    from app import main

    evidence = {
        "runtime": [
            {
                "tool_id": "RT-001",
                "tool_name": "systemd_status",
                "data": {
                    "password": "super-secret",
                    "nested": {"token": "abc123"},
                    "url": "https://user:pass@example.com/health",
                    "log": "authorization=Bearer abcdefghijklmnopqrstuvwxyz",
                    "oversized": "x" * 1200,
                },
            }
        ],
        "business": [],
        "memory": [],
        "knowledge": [],
        "provider": {"selected": {"secret": "provider-secret"}},
        "warnings": ["api_key=should-hide"],
        "errors": [],
    }

    sanitized = main._sanitize_chat_evidence(evidence)

    runtime_data = sanitized["runtime"][0]["data"]
    assert runtime_data["password"] == "[redacted]"
    assert runtime_data["nested"]["token"] == "[redacted]"
    assert runtime_data["url"] == "https://[redacted]@example.com/health"
    assert runtime_data["log"].endswith("[redacted]")
    assert runtime_data["oversized"].endswith("...[truncated]")
    assert sanitized["provider"]["selected"]["secret"] == "[redacted]"
    assert sanitized["warnings"][0] == "api_key=[redacted]"
