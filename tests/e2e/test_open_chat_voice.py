"""
Playwright E2E tests for the AI Operating Console voice/mobile UX
(Stage B + Stage C of the Local AI Operating Console V1 rollout).

Uses Chromium's built-in fake media device (--use-fake-device-for-media-stream
--use-fake-ui-for-media-stream) so getUserMedia/MediaRecorder are the real
browser APIs backed by a synthetic silent audio device — no real hardware or
OS permission prompt needed, and no risk of a fragile JS-level override of
navigator.mediaDevices (verified unreliable: direct property assignment does
not consistently shadow the native implementation in headless Chromium).
page.route() intercepts /chat/voice and /chat/message so no real
transcription or LLM call happens — only the frontend state machine and DOM
wiring are under test.

Prerequisites
-------------
  playwright install chromium
  export TEST_APP_URL=http://localhost:8200
  export TEST_API_KEY=<your INTERNAL_API_KEY>

Run
---
  TEST_API_KEY=fk_xxx pytest tests/e2e/test_open_chat_voice.py -m e2e -v
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

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    browser_type_launch_args.update({
        "headless": True,
        "args": [
            "--no-sandbox", "--disable-dev-shm-usage",
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        ],
    })
    return browser_type_launch_args


def _deny_mic_permission(page: Page) -> None:
    """Force getUserMedia to reject, simulating a user denying mic access —
    the fake-device flags otherwise always grant access, so this test needs
    its own override, scoped narrowly to just the rejection path."""
    page.add_init_script(
        "navigator.mediaDevices.getUserMedia = () => "
        "Promise.reject(Object.assign(new Error('Permission denied'), {name: 'NotAllowedError'}));"
    )


def _open_chat_authed(page: Page, viewport: dict | None = None) -> None:
    page.goto(f"{APP_URL}{OPEN_CHAT}")
    page.evaluate("([k, v]) => localStorage.setItem(k, v)", [LS_KEY, API_KEY])
    page.reload()
    page.wait_for_selector("#voice-btn", state="attached", timeout=12_000)


@pytest.fixture
def chat_page(page: Page) -> Generator[Page, None, None]:
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(f"[exception] {e}"))
    page.on("console", lambda m: js_errors.append(f"[console.error] {m.text}") if m.type == "error" else None)
    page._js_errors = js_errors  # type: ignore[attr-defined]

    _open_chat_authed(page)
    yield page


@pytest.fixture
def denied_mic_chat_page(page: Page) -> Generator[Page, None, None]:
    _deny_mic_permission(page)
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


def _route_voice_success(page: Page, transcript: str, language: str = "bn", confident: bool = True, warning=None):
    def handler(route):
        route.fulfill(json={
            "transcript": transcript,
            "language": language,
            "confidence": None,
            "confident": confident,
            "duration_ms": 1200,
            "editable": True,
            "warning": warning,
        })
    page.route("**/chat/voice", handler)


def _route_chat_message_capture(page: Page, calls: list):
    def handler(route, request):
        calls.append(request.post_data_json)
        route.fulfill(json={
            "answer": "ok",
            "model": "phi4-mini:latest",
            "rag_used": False,
            "db_tools_used": [],
            "web_fetched": False,
            "citations": [],
            "top_score": None,
            "memory_context_used": False,
            "confidence": "Verified",
            "feature_path": "admin_chat_lab",
            "ai_console_enabled": False,
            "runtime_used": [], "business_used": [], "memory_used": [], "knowledge_used": [],
        })
    page.route("**/chat/message", handler)


# ===========================================================================
# Mobile layout
# ===========================================================================

class TestMobileLayout:
    def test_no_horizontal_overflow_at_390px(self, mobile_chat_page: Page):
        overflow = mobile_chat_page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert overflow is False

    def test_composer_and_controls_visible(self, mobile_chat_page: Page):
        expect(mobile_chat_page.locator("#q-input")).to_be_visible()
        expect(mobile_chat_page.locator("#send-btn")).to_be_visible()
        expect(mobile_chat_page.locator("#voice-btn")).to_be_visible()

    def test_touch_targets_meet_minimum_size(self, mobile_chat_page: Page):
        box = mobile_chat_page.locator("#send-btn").bounding_box()
        assert box is not None
        assert box["width"] >= 40 and box["height"] >= 40

    def test_long_bangla_text_wraps_without_overflow(self, chat_page: Page):
        long_text = "আমি " * 80
        chat_page.fill("#q-input", long_text)
        chat_page.evaluate("document.getElementById('q-input').dispatchEvent(new Event('input'))")
        overflow = chat_page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert overflow is False


# ===========================================================================
# Voice: buttons, permissions, recording state
# ===========================================================================

class TestVoiceUI:
    def test_microphone_button_exists_with_label(self, chat_page: Page):
        btn = chat_page.locator("#voice-btn")
        expect(btn).to_be_visible()
        assert btn.get_attribute("aria-label")

    def test_plus_attachment_buttons_are_disabled(self, chat_page: Page):
        expect(chat_page.locator("#attach-file-btn")).to_be_disabled()
        expect(chat_page.locator("#attach-image-btn")).to_be_disabled()

    def test_permission_denied_shows_clear_message(self, denied_mic_chat_page: Page):
        denied_mic_chat_page.click("#voice-btn")
        denied_mic_chat_page.wait_for_timeout(300)
        status_text = denied_mic_chat_page.locator("#voice-status-text").inner_text()
        assert "denied" in status_text.lower() or "permission" in status_text.lower()

    def test_recording_shows_indicator_and_stop_cancel(self, chat_page: Page):
        chat_page.route("**/chat/voice", lambda route: route.fulfill(json={
            "transcript": "test", "language": "en", "confidence": None,
            "confident": True, "duration_ms": 500, "editable": True, "warning": None,
        }))
        chat_page.click("#voice-btn")
        chat_page.wait_for_timeout(200)
        expect(chat_page.locator("#voice-panel")).to_be_visible()
        expect(chat_page.locator("#voice-cancel-btn")).to_be_visible()
        assert "record" in chat_page.locator("#voice-btn").inner_text().lower() or \
               "stop" in chat_page.locator("#voice-btn").inner_text().lower()

    def test_cancel_recording_discards_without_upload(self, chat_page: Page):
        voice_called = {"count": 0}

        def handler(route):
            voice_called["count"] += 1
            route.fulfill(json={"transcript": "should-not-appear", "language": "en",
                                 "confidence": None, "confident": True, "duration_ms": 500,
                                 "editable": True, "warning": None})
        chat_page.route("**/chat/voice", handler)

        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-cancel-btn")

        expect(chat_page.locator("#voice-panel")).to_be_hidden(timeout=5_000)
        assert voice_called["count"] == 0


# ===========================================================================
# Transcript: editable, no auto-send, manual send
# ===========================================================================

class TestTranscriptWorkflow:
    def test_transcript_fills_editable_box_and_does_not_autosend(self, chat_page: Page):
        message_calls: list = []
        _route_voice_success(chat_page, "কাল সকালে আসবো")
        _route_chat_message_capture(chat_page, message_calls)

        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-btn")  # stop -> triggers fake upload+transcribe
        chat_page.wait_for_selector("#voice-transcript-box:not([hidden])", timeout=5_000)

        box = chat_page.locator("#voice-transcript-box")
        expect(box).to_have_value("কাল সকালে আসবো")
        assert message_calls == []  # never auto-sent

    def test_transcript_remains_editable_before_use(self, chat_page: Page):
        _route_voice_success(chat_page, "original text")
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-transcript-box:not([hidden])", timeout=5_000)

        chat_page.fill("#voice-transcript-box", "corrected text")
        expect(chat_page.locator("#voice-transcript-box")).to_have_value("corrected text")

    def test_use_transcript_then_manual_send_reaches_chat_message(self, chat_page: Page):
        message_calls: list = []
        _route_voice_success(chat_page, "hello world")
        _route_chat_message_capture(chat_page, message_calls)

        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-transcript-box:not([hidden])", timeout=5_000)

        chat_page.get_by_role("button", name="Use Transcript").click()
        expect(chat_page.locator("#q-input")).to_have_value("hello world")
        assert message_calls == []  # still not sent until explicit Send

        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(300)
        assert len(message_calls) == 1
        assert message_calls[0]["q"] == "hello world"

    def test_record_again_replaces_stale_transcript(self, chat_page: Page):
        _route_voice_success(chat_page, "first recording")
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-transcript-box:not([hidden])", timeout=5_000)
        expect(chat_page.locator("#voice-transcript-box")).to_have_value("first recording")

        _route_voice_success(chat_page, "second recording")
        chat_page.get_by_role("button", name="Record Again").click()
        chat_page.wait_for_timeout(200)
        chat_page.click("#voice-btn")  # stop the re-triggered recording
        chat_page.wait_for_selector("#voice-transcript-box:not([hidden])", timeout=5_000)
        expect(chat_page.locator("#voice-transcript-box")).to_have_value("second recording")

    def test_low_confidence_shows_warning_not_fabricated_text(self, chat_page: Page):
        _route_voice_success(chat_page, "", confident=False, warning="ভয়েস বার্তাটি ভালোভাবে বোঝা যায়নি।")
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-btn")
        chat_page.wait_for_timeout(500)
        status_text = chat_page.locator("#voice-status-text").inner_text()
        assert "বোঝা যায়নি" in status_text or "বার্তা" in status_text


# ===========================================================================
# Safety / hygiene
# ===========================================================================

class TestSafetyAndHygiene:
    def test_no_console_errors_on_full_voice_flow(self, chat_page: Page):
        """Voice recording/upload/transcription must not introduce new JS
        errors. Baselined against errors already present right after page
        load (e.g. environment-specific auth/key setup noise unrelated to
        voice) so this test targets only the feature under test."""
        baseline_error_count = len(chat_page._js_errors)  # type: ignore[attr-defined]

        _route_voice_success(chat_page, "clean run")
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-transcript-box:not([hidden])", timeout=5_000)
        chat_page.get_by_role("button", name="Use Transcript").click()
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(300)

        new_errors = chat_page._js_errors[baseline_error_count:]  # type: ignore[attr-defined]
        assert new_errors == []

    def test_transcript_html_is_escaped_when_sent(self, chat_page: Page):
        message_calls: list = []
        payload = "<script>window.__xss_ran = true;</script>"
        _route_voice_success(chat_page, payload)
        _route_chat_message_capture(chat_page, message_calls)

        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-cancel-btn:visible", timeout=3_000)
        chat_page.click("#voice-btn")
        chat_page.wait_for_selector("#voice-transcript-box:not([hidden])", timeout=5_000)
        chat_page.get_by_role("button", name="Use Transcript").click()
        chat_page.click("#send-btn")
        chat_page.wait_for_timeout(300)

        assert chat_page.evaluate("window.__xss_ran") is None
        bubble_html = chat_page.locator(".msg.user .msg-bubble").last.inner_html()
        assert "<script>" not in bubble_html

    def test_plus_button_click_triggers_no_network_call(self, chat_page: Page):
        calls: list = []
        chat_page.route("**/*", lambda route: (calls.append(route.request.url), route.continue_())[-1])
        chat_page.click("#attach-file-btn", force=True)
        chat_page.wait_for_timeout(200)
        upload_calls = [c for c in calls if "/upload" in c or "/kb/" in c]
        assert upload_calls == []
