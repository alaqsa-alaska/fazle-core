"""
Playwright E2E tests for AI Operating Console file attachments and
evidence/verification-label cards (Stages D + F of the Local AI Operating
Console V1 rollout).

Uses page.route() to intercept /chat/upload and /chat/message so these
tests are deterministic and never depend on the real extraction/LLM
pipeline -- only the frontend attachment state machine, evidence
rendering, and answer-status labeling are under test.

Prerequisites
-------------
  playwright install chromium
  export TEST_APP_URL=http://localhost:8200
  export TEST_API_KEY=<your INTERNAL_API_KEY>

Run
---
  TEST_API_KEY=fk_xxx pytest tests/e2e/test_open_chat_attachments.py -m e2e -v
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest
from playwright.sync_api import Page, expect

APP_URL = os.getenv("TEST_APP_URL", "http://localhost:8200")
API_KEY = os.getenv("TEST_API_KEY", "test-internal-key")
LS_KEY = "fazle_api_key"
OPEN_CHAT = "/open-chat"

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    browser_type_launch_args.update({
        "headless": True,
        "args": ["--no-sandbox", "--disable-dev-shm-usage"],
    })
    return browser_type_launch_args


def _open_chat_authed(page: Page) -> None:
    page.goto(f"{APP_URL}{OPEN_CHAT}")
    page.evaluate("([k, v]) => localStorage.setItem(k, v)", [LS_KEY, API_KEY])
    page.reload()
    page.wait_for_selector("#attach-file-btn", state="attached", timeout=12_000)


@pytest.fixture
def chat_page(page: Page) -> Generator[Page, None, None]:
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(f"[exception] {e}"))
    page.on("console", lambda m: js_errors.append(f"[console.error] {m.text}") if m.type == "error" else None)
    page._js_errors = js_errors  # type: ignore[attr-defined]
    _open_chat_authed(page)
    yield page


@pytest.fixture
def mobile_chat_page(page: Page) -> Generator[Page, None, None]:
    page.set_viewport_size({"width": 390, "height": 844})
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(f"[exception] {e}"))
    page.on("console", lambda m: js_errors.append(f"[console.error] {m.text}") if m.type == "error" else None)
    page._js_errors = js_errors  # type: ignore[attr-defined]
    _open_chat_authed(page)
    yield page


def _route_upload_success(page: Page, filename="notes.txt", extracted_text="hello file contents", warning=None, file_type="txt"):
    def handler(route):
        route.fulfill(json={
            "attachment_id": "chatupload_test_1",
            "filename": filename,
            "type": file_type,
            "size_bytes": len(extracted_text),
            "extracted_text": extracted_text,
            "text_length": len(extracted_text),
            "truncated": False,
            "warning": warning,
            "temporary": True,
        })
    page.route("**/chat/upload", handler)


def _route_chat_message(page: Page, calls: list, *, answer_status="General AI answer",
                         business_verified=False, business_used=None, confidence="Unknown"):
    def handler(route, request):
        calls.append(request.post_data_json)
        route.fulfill(json={
            "answer": "the file says hello",
            "model": "phi4-mini:latest",
            "rag_used": False,
            "db_tools_used": business_used or [],
            "web_fetched": False,
            "citations": [],
            "top_score": None,
            "memory_context_used": False,
            "confidence": confidence,
            "feature_path": "ai_console",
            "ai_console_enabled": True,
            "answer_status": answer_status,
            "business_verified": business_verified,
            "attachments_used": [a.get("filename") for a in (request.post_data_json.get("attachments") or [])],
            "attachments_truncated": False,
            "runtime_used": [], "business_used": business_used or [], "memory_used": [], "knowledge_used": [],
            "evidence": {
                "runtime": [], "memory": [], "knowledge": [],
                "business": [
                    {
                        "tool_name": "dispatch_availability", "status": "ok", "confidence": confidence,
                        "data": {"total": 1, "items": [{"employee_id": 5, "available": True}]},
                    }
                ] if business_used else [],
                "provider": {}, "warnings": [], "errors": [],
            },
            "traceability": ["ORCH-001"],
        })
    page.route("**/chat/message", handler)


def _attach_file(page: Page, name: str, content: bytes, mime: str):
    with page.expect_file_chooser() as fc_info:
        page.click("#attach-file-btn")
    chooser = fc_info.value
    tmp = Path(f"/tmp/{name}")
    tmp.write_bytes(content)
    chooser.set_files(str(tmp))
    tmp.unlink(missing_ok=True)


# ===========================================================================
# File upload / attachment cards
# ===========================================================================

class TestAttachmentUpload:
    def test_plus_button_opens_file_picker(self, chat_page: Page):
        with chat_page.expect_file_chooser() as fc_info:
            chat_page.click("#attach-file-btn")
        assert fc_info.value is not None

    def test_attachment_card_renders_after_upload(self, chat_page: Page):
        _route_upload_success(chat_page, filename="report.txt", extracted_text="quarterly numbers")
        _attach_file(chat_page, "report.txt", b"quarterly numbers", "text/plain")
        chat_page.wait_for_selector(".attachment-card", timeout=5_000)
        card_text = chat_page.locator(".attachment-card").inner_text()
        assert "report.txt" in card_text
        assert "Extracted" in card_text

    def test_remove_attachment_works(self, chat_page: Page):
        _route_upload_success(chat_page)
        _attach_file(chat_page, "notes.txt", b"hello", "text/plain")
        chat_page.wait_for_selector(".attachment-card", timeout=5_000)
        chat_page.click(".attachment-card-remove")
        expect(chat_page.locator(".attachment-card")).to_have_count(0)

    def test_text_preview_is_escaped_not_raw_html(self, chat_page: Page):
        payload = "<img src=x onerror=window.__xss=1>"
        _route_upload_success(chat_page, filename="evil.txt", extracted_text=payload)
        _attach_file(chat_page, "evil.txt", payload.encode(), "text/plain")
        chat_page.wait_for_selector(".attachment-card", timeout=5_000)
        chat_page.click(".attachment-card-toggle")
        chat_page.wait_for_timeout(200)
        assert chat_page.evaluate("window.__xss") is None
        preview_html = chat_page.locator(".attachment-card-preview").inner_html()
        assert "<img" not in preview_html

    def test_upload_warning_shown_for_scanned_pdf(self, chat_page: Page):
        _route_upload_success(
            chat_page, filename="scanned.pdf", extracted_text="", file_type="pdf",
            warning="No extractable text found — this may be a scanned/image-only PDF.",
        )
        _attach_file(chat_page, "scanned.pdf", b"%PDF-1.4", "application/pdf")
        chat_page.wait_for_selector(".attachment-card", timeout=5_000)
        assert "scanned" in chat_page.locator(".attachment-card").inner_text().lower()

    def test_attachment_limit_disables_button_at_three(self, chat_page: Page):
        _route_upload_success(chat_page, filename="f.txt", extracted_text="x")
        for _ in range(3):
            _attach_file(chat_page, "f.txt", b"x", "text/plain")
            chat_page.wait_for_timeout(150)
        expect(chat_page.locator("#attach-file-btn")).to_be_disabled()

    def test_voice_and_file_controls_coexist(self, chat_page: Page):
        expect(chat_page.locator("#attach-file-btn")).to_be_visible()
        expect(chat_page.locator("#voice-btn")).to_be_visible()
        expect(chat_page.locator("#attach-file-btn")).to_be_enabled()


# ===========================================================================
# Chat integration
# ===========================================================================

class TestAttachmentChatIntegration:
    def test_attachment_included_in_chat_message_request(self, chat_page: Page):
        message_calls: list = []
        _route_upload_success(chat_page, filename="data.csv", extracted_text="a,b\n1,2", file_type="csv")
        _route_chat_message(chat_page, message_calls)

        _attach_file(chat_page, "data.csv", b"a,b\n1,2", "text/csv")
        chat_page.wait_for_selector(".attachment-card", timeout=5_000)

        chat_page.fill("#q-input", "summarize this")
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(400)

        assert len(message_calls) == 1
        atts = message_calls[0]["attachments"]
        assert atts[0]["filename"] == "data.csv"
        assert atts[0]["extracted_text"] == "a,b\n1,2"

    def test_attachments_cleared_after_successful_send(self, chat_page: Page):
        message_calls: list = []
        _route_upload_success(chat_page)
        _route_chat_message(chat_page, message_calls)

        _attach_file(chat_page, "notes.txt", b"hello", "text/plain")
        chat_page.wait_for_selector(".attachment-card", timeout=5_000)
        chat_page.fill("#q-input", "what's in this?")
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(400)

        expect(chat_page.locator(".attachment-card")).to_have_count(0)


# ===========================================================================
# Evidence / answer-status labels (Stage F)
# ===========================================================================

class TestEvidenceAndLabels:
    def test_general_answer_labeled_general(self, chat_page: Page):
        message_calls: list = []
        _route_chat_message(chat_page, message_calls, answer_status="General AI answer", business_verified=False)
        chat_page.fill("#q-input", "hello there")
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(400)
        bubble = chat_page.locator(".msg.bot").last.inner_text()
        assert "General AI answer" in bubble

    def test_verified_business_answer_labeled_verified(self, chat_page: Page):
        message_calls: list = []
        _route_chat_message(
            chat_page, message_calls, answer_status="Verified business answer",
            business_verified=True, business_used=["dispatch_availability"], confidence="Verified",
        )
        chat_page.fill("#q-input", "who is available tomorrow?")
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(400)
        bubble = chat_page.locator(".msg.bot").last.inner_text()
        assert "Verified business answer" in bubble

    def test_response_meta_shows_answer_status_row(self, chat_page: Page):
        message_calls: list = []
        _route_chat_message(chat_page, message_calls, answer_status="Partially verified answer")
        chat_page.fill("#q-input", "some question")
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(400)
        meta_text = chat_page.locator("#response-meta").inner_text()
        assert "answer status" in meta_text.lower()  # CSS text-transform:uppercase on labels
        assert "Partially verified answer" in meta_text

    def test_evidence_cards_expand_and_collapse(self, chat_page: Page):
        message_calls: list = []
        _route_chat_message(
            chat_page, message_calls, answer_status="Verified business answer",
            business_verified=True, business_used=["dispatch_availability"], confidence="Verified",
        )
        chat_page.fill("#q-input", "who is available?")
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(400)

        chat_page.click('button[data-tab="evidence"]')
        expect(chat_page.locator("#tab-evidence")).to_be_visible()
        summary = chat_page.locator("#evidence-list details summary").first
        expect(summary).to_be_visible()
        summary.click()
        expect(chat_page.locator("#evidence-list details").first).to_have_attribute("open", "")

    def test_no_console_errors_on_full_attachment_and_business_flow(self, chat_page: Page):
        message_calls: list = []
        _route_upload_success(chat_page)
        _route_chat_message(
            chat_page, message_calls, answer_status="Verified business answer",
            business_verified=True, business_used=["dispatch_availability"], confidence="Verified",
        )
        baseline = len(chat_page._js_errors)  # type: ignore[attr-defined]

        _attach_file(chat_page, "notes.txt", b"hello", "text/plain")
        chat_page.wait_for_selector(".attachment-card", timeout=5_000)
        chat_page.fill("#q-input", "who is available?")
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(400)
        chat_page.click('button[data-tab="evidence"]')
        chat_page.wait_for_timeout(200)

        new_errors = chat_page._js_errors[baseline:]  # type: ignore[attr-defined]
        assert new_errors == []


# ===========================================================================
# Mobile
# ===========================================================================

class TestMobileAttachments:
    def test_attachment_card_visible_at_390px_no_overflow(self, mobile_chat_page: Page):
        _route_upload_success(mobile_chat_page)
        _attach_file(mobile_chat_page, "notes.txt", b"hello", "text/plain")
        mobile_chat_page.wait_for_selector(".attachment-card", timeout=5_000)
        overflow = mobile_chat_page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert overflow is False
