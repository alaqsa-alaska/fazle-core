from __future__ import annotations

from pathlib import Path

import pytest


def test_open_chat_ui_contains_phase8b_console_sections():
    html = Path("/home/azim/core/app/static/open-chat.html").read_text()

    assert "Fazle AI Operating Console" in html
    assert 'id="left-panel"' in html
    assert 'id="right-panel"' in html
    assert 'id="provider-select"' in html
    assert 'id="model-select"' in html
    assert 'id="attachment-chips"' in html
    assert 'id="response-meta"' in html
    assert 'id="evidence-list"' in html
    assert 'id="trace-list"' in html


def test_open_chat_ui_consumes_phase8a_metadata_fields():
    html = Path("/home/azim/core/app/static/open-chat.html").read_text()

    for token in (
        "feature_path",
        "ai_console_enabled",
        "provider",
        "confidence",
        "traceability",
        "evidence_summary",
        "evidence",
        "warnings",
        "errors",
        "runtime_used",
        "business_used",
        "memory_used",
        "knowledge_used",
    ):
        assert token in html


@pytest.mark.asyncio
async def test_open_chat_route_serves_console_html():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        response = await ac.get("/open-chat")

    assert response.status_code == 200
    text = response.text
    assert "Fazle AI Operating Console" in text
    assert "Owner AI Workspace" in text
