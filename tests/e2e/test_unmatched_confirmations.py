"""
Playwright E2E test suite for app/static/unmatched-confirmations.html
(Draft-Confirmation Invariant, Workstream 2 Stage 4).

Same safe pattern as tests/e2e/test_employee_review.py: all state-mutating
checks intercept the relevant fetch() calls with page.route() and
synthetic data, so this suite never writes to whichever backend
TEST_APP_URL points at -- safe to run against production for read-only
checks without ever touching the two real MV Horda queue items.

Prerequisites
-------------
  playwright install chromium
  export TEST_APP_URL=http://localhost:8200
  export TEST_API_KEY=<your INTERNAL_API_KEY>

Run
---
  TEST_API_KEY=fk_xxx pytest tests/e2e/test_unmatched_confirmations.py -m e2e -v
"""
from __future__ import annotations

import json
import os
from typing import Generator

import pytest
from playwright.sync_api import Page, Route, expect

APP_URL = os.getenv("TEST_APP_URL", "http://localhost:8200")
API_KEY = os.getenv("TEST_API_KEY", "test-internal-key")

PAGE_PATH = "/static/unmatched-confirmations.html"
LIST_PATTERN = "**/api/escort/unmatched-confirmations?*"
DETAIL_PATTERN = "**/api/escort/unmatched-confirmations/*"
LINK_PATTERN = "**/api/escort/unmatched-confirmations/*/link-existing"
CONFIRM_NEW_PATTERN = "**/api/escort/unmatched-confirmations/*/confirm-new"
REJECT_PATTERN = "**/api/escort/unmatched-confirmations/*/reject"
DEFER_PATTERN = "**/api/escort/unmatched-confirmations/*/defer"

pytestmark = pytest.mark.e2e

SYNTHETIC_LIST = {
    "items": [
        {
            "id": 501, "status": "pending", "source": "bridge1",
            "admin_phone_masked": "*******0099", "client_phone_masked": None,
            "best_candidate_score": 4.5, "candidate_matches": [{"program_id": 900, "score": 4.5}],
            "created_at": "2026-07-19T10:00:00+00:00",
            "parsed_confirmation": {
                "mother_vessel": "MV XSS <script>alert(1)</script>", "lighter_vessel": "LV Test",
                "master_mobile": "01900000001", "escort_name": 'Escort "Quoted" & <b>Bold</b>',
            },
        },
        {
            "id": 502, "status": "pending", "source": "bridge2",
            "admin_phone_masked": "*******0098", "client_phone_masked": "*******1234",
            "best_candidate_score": None, "candidate_matches": [],
            "created_at": "2026-07-19T09:00:00+00:00",
            "parsed_confirmation": {
                "mother_vessel": "Unicode Tëst 测试 পরীক্ষা", "lighter_vessel": "LV2",
                "master_mobile": "01900000002", "escort_name": "Escort2",
            },
        },
    ],
    "count": 2, "total": 2, "page": 1, "page_size": 100,
}

SYNTHETIC_DETAIL_WITH_CANDIDATE = {
    "id": 501, "status": "pending", "source": "bridge1", "admin_phone_masked": "*******0099",
    "client_phone_masked": None, "raw_confirmation_text": "raw synthetic text",
    "created_at": "2026-07-19T10:00:00+00:00", "reviewed_by": None, "reviewed_at": None, "review_reason": None,
    "parsed_confirmation": {
        "mother_vessel": "MV TEST", "lighter_vessel": "LV Test", "master_mobile": "01900000001",
        "escort_name": "Escort Name", "escort_mobile": "01900000003", "shift": "D", "date_str": "20.07.2026",
    },
    "candidate_matches": [
        {
            "program_id": 900, "score": 4.5, "matched_fields": ["mother_vessel"],
            "warnings": ["confirmation_start_date_missing"],
            "current_live_state": {
                "program_id": 900, "status": "draft", "mother_vessel": "MV TEST",
                "lighter_vessel": "LV Test", "master_mobile": "01900000001",
                "escort_name": None, "escort_mobile": None, "program_date": None,
            },
        },
        {
            "program_id": 901, "score": 2.0, "matched_fields": [], "warnings": [],
            "current_live_state": None,  # gone -- candidate program no longer exists
        },
    ],
}

SYNTHETIC_DETAIL_RESOLVED = {
    "id": 503, "status": "rejected", "source": "bridge1", "admin_phone_masked": "*******0099",
    "client_phone_masked": None, "raw_confirmation_text": "already resolved",
    "created_at": "2026-07-19T08:00:00+00:00", "reviewed_by": "admin_x",
    "reviewed_at": "2026-07-19T08:05:00+00:00", "review_reason": "spam",
    "parsed_confirmation": {"mother_vessel": "MV X", "lighter_vessel": "LV X"},
    "candidate_matches": [],
}


def _fulfill_json(route: Route, status: int, body: dict) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _login(page: Page) -> None:
    page.goto(f"{APP_URL}{PAGE_PATH}", wait_until="networkidle")
    page.fill("#keyInput", API_KEY)
    page.click("text=Continue")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(400)


@pytest.fixture
def tracked_page(page: Page) -> Generator[Page, None, None]:
    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page._console_errors = console_errors  # type: ignore[attr-defined]
    yield page


# ===========================================================================
# Read-only: page loads, auth, live real queue (safe -- GET only, no writes)
# ===========================================================================

class TestPageLoad:

    def test_page_loads(self, tracked_page: Page) -> None:
        resp = tracked_page.goto(f"{APP_URL}{PAGE_PATH}")
        assert resp.status == 200
        expect(tracked_page.locator("#loginBox")).to_be_visible()

    def test_wrong_key_rejected(self, tracked_page: Page) -> None:
        tracked_page.goto(f"{APP_URL}{PAGE_PATH}", wait_until="networkidle")
        tracked_page.fill("#keyInput", "definitely-wrong-key")
        tracked_page.click("text=Continue")
        tracked_page.wait_for_timeout(800)
        expect(tracked_page.locator("#app")).to_be_hidden()

    def test_correct_key_loads_real_queue(self, tracked_page: Page) -> None:
        """Real GET against production -- the 2 real MV Horda items may
        appear here, but this test only reads, never writes."""
        _login(tracked_page)
        expect(tracked_page.locator("#app")).to_be_visible()
        assert not tracked_page._console_errors  # type: ignore[attr-defined]

    def test_default_tab_is_pending_deferred(self, tracked_page: Page) -> None:
        _login(tracked_page)
        active = tracked_page.locator(".tab.active")
        expect(active).to_have_text("Pending + Deferred")


class TestLayout:

    def test_no_mobile_overflow(self, tracked_page: Page) -> None:
        tracked_page.set_viewport_size({"width": 390, "height": 844})
        _login(tracked_page)
        overflow = tracked_page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 5"
        )
        assert overflow is False, "table must scroll within its own container on mobile, not the whole page"


# ===========================================================================
# Synthetic-data interaction (mocked network, no real writes)
# ===========================================================================

class TestQueueInteraction:

    @pytest.fixture(autouse=True)
    def _mock_list(self, tracked_page: Page):
        tracked_page.route(LIST_PATTERN, lambda r: _fulfill_json(r, 200, SYNTHETIC_LIST))
        yield

    def test_xss_mother_vessel_escaped(self, tracked_page: Page) -> None:
        _login(tracked_page)
        html = tracked_page.inner_html("#tableHost")
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_unicode_renders(self, tracked_page: Page) -> None:
        _login(tracked_page)
        text = tracked_page.inner_text("#tableHost")
        assert "Tëst" in text and "测试" in text and "পরীক্ষা" in text

    def test_filter_tabs_switch(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("text=Rejected")
        expect(tracked_page.locator(".tab.active")).to_have_text("Rejected")

    def test_status_codes_correspond_to_client_message(self, tracked_page: Page) -> None:
        """400/403/404/409/500 each render a distinguishable message --
        exercised via the detail+action flow below, this test only checks
        the mapping function exists and is reachable from the page."""
        _login(tracked_page)
        msg = tracked_page.evaluate("describeError(409, 'already resolved')")
        assert "Conflict" in msg
        msg404 = tracked_page.evaluate("describeError(404, 'x')")
        assert "no longer found" in msg404.lower()
        msg400 = tracked_page.evaluate("describeError(400, 'reason required')")
        assert "Missing/invalid" in msg400


class TestDetailAndActions:

    @pytest.fixture(autouse=True)
    def _mock_network(self, tracked_page: Page):
        tracked_page.route(LIST_PATTERN, lambda r: _fulfill_json(r, 200, SYNTHETIC_LIST))
        tracked_page.route(
            "**/api/escort/unmatched-confirmations/501",
            lambda r: _fulfill_json(r, 200, SYNTHETIC_DETAIL_WITH_CANDIDATE),
        )
        yield

    def test_detail_opens_with_candidates(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("tr.clickable >> nth=0")
        tracked_page.wait_for_timeout(300)
        expect(tracked_page.locator("#detail")).to_be_visible()
        expect(tracked_page.locator("#detail")).to_contain_text("Program #900")

    def test_gone_candidate_shown_distinctly(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("tr.clickable >> nth=0")
        tracked_page.wait_for_timeout(300)
        expect(tracked_page.locator("#detail")).to_contain_text("no longer exists")

    def test_confirm_new_requires_ack_checkbox(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("tr.clickable >> nth=0")
        tracked_page.wait_for_timeout(300)
        write_count = {"n": 0}
        tracked_page.route(CONFIRM_NEW_PATTERN, lambda r: (write_count.__setitem__("n", write_count["n"] + 1), r.fulfill(status=200, body="{}")))
        tracked_page.fill("#newReason", "a reason")
        # ack checkbox left unchecked -- alert() fires client-side (the JS
        # never reaches confirm()/fetch at all), a single handler dismisses it.
        tracked_page.once("dialog", lambda d: d.dismiss())
        tracked_page.click("button:has-text('Confirm as Separate New Duty')")
        tracked_page.wait_for_timeout(300)
        assert write_count["n"] == 0

    def test_link_existing_requires_candidate_selection(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("tr.clickable >> nth=0")
        tracked_page.wait_for_timeout(300)
        write_count = {"n": 0}
        tracked_page.route(LINK_PATTERN, lambda r: (write_count.__setitem__("n", write_count["n"] + 1), r.fulfill(status=200, body="{}")))
        tracked_page.fill("#linkReason", "a reason")
        tracked_page.on("dialog", lambda d: d.accept())
        tracked_page.click("button:has-text('Link to Existing Draft')")
        tracked_page.wait_for_timeout(300)
        assert write_count["n"] == 0, "must not submit without selecting a candidate"

    def test_successful_link_existing(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("tr.clickable >> nth=0")
        tracked_page.wait_for_timeout(300)
        tracked_page.click("#cand-0")
        tracked_page.fill("#linkReason", "matches after review")
        tracked_page.route(LINK_PATTERN, lambda r: _fulfill_json(r, 200, {"queue_id": 501, "resolved_program_id": 900, "status": "linked_existing"}))
        tracked_page.route("**/api/escort/unmatched-confirmations/501", lambda r: _fulfill_json(r, 200, {**SYNTHETIC_DETAIL_WITH_CANDIDATE, "status": "linked_existing"}))
        tracked_page.on("dialog", lambda d: d.accept())
        tracked_page.click("button:has-text('Link to Existing Draft')")
        tracked_page.wait_for_timeout(400)
        expect(tracked_page.locator("#actionStatus")).to_contain_text("succeeded")

    def test_409_conflict_shown_not_silently_retried(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("tr.clickable >> nth=0")
        tracked_page.wait_for_timeout(300)
        tracked_page.fill("#rejectReason", "duplicate")
        tracked_page.route(REJECT_PATTERN, lambda r: _fulfill_json(r, 409, {"detail": "queue item 501 is already rejected"}))
        tracked_page.on("dialog", lambda d: d.accept())
        tracked_page.click("button:has-text('Reject')")
        tracked_page.wait_for_timeout(400)
        status = tracked_page.text_content("#actionStatus") or ""
        assert "conflict" in status.lower()

    def test_defer_allows_empty_reason(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.click("tr.clickable >> nth=0")
        tracked_page.wait_for_timeout(300)
        tracked_page.route(DEFER_PATTERN, lambda r: _fulfill_json(r, 200, {"queue_id": 501, "status": "deferred"}))
        tracked_page.route("**/api/escort/unmatched-confirmations/501", lambda r: _fulfill_json(r, 200, {**SYNTHETIC_DETAIL_WITH_CANDIDATE, "status": "deferred"}))
        tracked_page.click("button:has-text('Defer')")
        tracked_page.wait_for_timeout(400)
        expect(tracked_page.locator("#actionStatus")).to_contain_text("succeeded")


class TestResolvedItemHasNoActions:

    @pytest.fixture(autouse=True)
    def _mock_network(self, tracked_page: Page):
        tracked_page.route(LIST_PATTERN, lambda r: _fulfill_json(r, 200, SYNTHETIC_LIST))
        tracked_page.route(
            "**/api/escort/unmatched-confirmations/503",
            lambda r: _fulfill_json(r, 200, SYNTHETIC_DETAIL_RESOLVED),
        )
        yield

    def test_no_action_buttons_on_resolved_item(self, tracked_page: Page) -> None:
        _login(tracked_page)
        tracked_page.evaluate("openDetail(503)")
        tracked_page.wait_for_timeout(300)
        expect(tracked_page.locator("text=Link to Existing Draft")).to_have_count(0)
        expect(tracked_page.locator("#detail")).to_contain_text("already resolved")
