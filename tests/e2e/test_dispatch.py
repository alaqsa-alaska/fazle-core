"""
Playwright E2E coverage for app/static/dispatch.html and the
/api/dispatch/* read-only endpoints (Dispatch Phase 2A.1). Sync Playwright
API, matching tests/e2e/test_dashboard.py's established convention (avoids
the documented asyncio-event-loop conflict with pytest-asyncio).

Every test in this file is read-only by construction -- none of them
create, edit, or delete any wbom_escort_programs/escort_roster_entries
row. The explicit no-write-button check (test_no_write_controls_present)
is the closest thing to an assignment-mutation regression guard this
suite can offer for a page that, by design, has no write path to guard.

Prerequisites
-------------
  playwright install chromium
  export TEST_APP_URL=http://localhost:8200
  export TEST_API_KEY=<your INTERNAL_API_KEY>

Run
---
  TEST_API_KEY=fk_xxx pytest tests/e2e/test_dispatch.py -m e2e -v
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page

APP_URL = os.getenv("TEST_APP_URL", "http://localhost:8200")
API_KEY = os.getenv("TEST_API_KEY", "test-internal-key")

pytestmark = pytest.mark.e2e


@pytest.fixture
def dispatch_page(page: Page) -> Page:
    page.goto(f"{APP_URL}/static/dispatch.html", wait_until="networkidle")
    page.evaluate("(k) => localStorage.setItem('fpe_api_key', k)", API_KEY)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1500)
    return page


class TestDispatchPageLoad:

    def test_page_returns_200(self, page: Page) -> None:
        resp = page.goto(f"{APP_URL}/static/dispatch.html")
        assert resp.status == 200

    def test_login_flow(self, dispatch_page: Page) -> None:
        assert dispatch_page.is_hidden("#loginBox")
        assert dispatch_page.is_visible("#app")

    def test_wrong_key_rejected(self, page: Page) -> None:
        page.goto(f"{APP_URL}/static/dispatch.html", wait_until="networkidle")
        page.fill("#keyInput", "definitely-wrong-key")
        page.click("text=Continue")
        page.wait_for_timeout(800)
        assert page.is_visible("#loginBox")
        assert page.is_hidden("#app")

    def test_no_console_errors_on_load(self, dispatch_page: Page) -> None:
        errors = []
        dispatch_page.on("pageerror", lambda e: errors.append(str(e)))
        dispatch_page.reload(wait_until="networkidle")
        dispatch_page.wait_for_timeout(1500)
        assert not errors, f"unexpected JS errors: {errors}"

    def test_readonly_notice_visible(self, dispatch_page: Page) -> None:
        banner = dispatch_page.text_content(".readonly-banner")
        assert "Read-only" in banner
        assert "No assignment changes are made" in banner

    def test_no_write_controls_present(self, dispatch_page: Page) -> None:
        """No assign/save/auto-assign button anywhere on the page."""
        html = dispatch_page.content().lower()
        for forbidden in ("auto assign", "auto-assign", "save assignment",
                           "confirm assignment", "reserve worker"):
            assert forbidden not in html


class TestDispatchFilters:

    def test_list_all_workers_renders(self, dispatch_page: Page) -> None:
        dispatch_page.click("button:has-text('List All Escort Workers')")
        dispatch_page.wait_for_timeout(1000)
        progress = dispatch_page.text_content("#progress")
        assert "active escort worker" in progress

    def test_filters_submit_and_results_render(self, dispatch_page: Page) -> None:
        dispatch_page.fill("#fStartDate", "2026-08-01")
        dispatch_page.fill("#fEndDate", "2026-08-03")
        dispatch_page.click("button:has-text('Check Availability')")
        dispatch_page.wait_for_timeout(1500)
        # suggestions should have rendered something (either items or the empty state)
        host = dispatch_page.text_content("#suggestionsHost")
        assert host.strip() != ""

    def test_suggestions_show_rank_and_reasons(self, dispatch_page: Page) -> None:
        dispatch_page.fill("#fStartDate", "2026-08-01")
        dispatch_page.fill("#fEndDate", "2026-08-03")
        dispatch_page.click("button:has-text('Check Availability')")
        dispatch_page.wait_for_timeout(1500)
        host = dispatch_page.locator("#suggestionsHost")
        # either real suggestions with rank badges, or the explicit empty state
        content = host.inner_html()
        assert "rank-badge" in content or "No eligible" in content

    def test_empty_state_for_far_future_impossible_window(self, dispatch_page: Page) -> None:
        dispatch_page.fill("#fEmployeeId", "999999999")
        dispatch_page.click("button:has-text('List All Escort Workers')")
        dispatch_page.wait_for_timeout(1000)
        table_html = dispatch_page.inner_html("#tableHost")
        assert "No escort workers match" in table_html

    def test_error_state_missing_dates(self, dispatch_page: Page) -> None:
        dispatch_page.fill("#fStartDate", "")
        dispatch_page.fill("#fEndDate", "")
        dispatch_page.click("button:has-text('Check Availability')")
        dispatch_page.wait_for_timeout(500)
        status = dispatch_page.text_content("#filterStatus")
        assert "required" in status.lower()


class TestDispatchAPIDirect:

    def test_availability_endpoint(self, dispatch_page: Page) -> None:
        result = dispatch_page.evaluate("""
            async () => {
                try { return {ok: true, r: await FazleAuthFetch.getJson('/api/dispatch/availability?page_size=5', API_KEY)}; }
                catch (e) { return {ok: false, error: e.message}; }
            }
        """)
        assert result["ok"] is True
        assert "items" in result["r"]

    def test_check_conflict_endpoint(self, dispatch_page: Page) -> None:
        result = dispatch_page.evaluate("""
            async () => {
                try {
                    return {ok: true, r: await FazleAuthFetch.postJson('/api/dispatch/check-conflict', API_KEY, {
                        escort_employee_id: 1, start_date: '2026-08-01', start_shift: 'D',
                        end_date: '2026-08-03', end_shift: 'N',
                    })};
                } catch (e) { return {ok: false, error: e.message}; }
            }
        """)
        assert result["ok"] is True
        assert result["r"]["available"] in (True, False, None)

    def test_xss_escaping_in_employee_name(self, dispatch_page: Page) -> None:
        """Route-mock a malicious employee name into the availability
        response and confirm the page renders it as inert text, not markup
        -- matches the established pattern from Phase 1E.1's employee
        review UI tests."""
        dispatch_page.route("**/api/dispatch/availability**", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"items":[{"employee_id":1,"employee_name":"<img src=x onerror=alert(1)>",'
                 '"employee_mobile":"01700000000","status":"Active","designation":"Escort",'
                 '"available":true,"reason":null,"current_conflict":null,'
                 '"last_assignment_date":null,"active_assignment_count":0}],'
                 '"total":1,"page":1,"page_size":50,"pages":1}',
        ))
        dispatch_page.click("text=List All Escort Workers")
        dispatch_page.wait_for_timeout(800)
        table_html = dispatch_page.inner_html("#tableHost")
        assert "<img src=x onerror=" not in table_html
        assert "&lt;img" in table_html


class TestDispatchMobile:

    def test_mobile_no_horizontal_overflow(self, page: Page) -> None:
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{APP_URL}/static/dispatch.html", wait_until="networkidle")
        page.evaluate("(k) => localStorage.setItem('fpe_api_key', k)", API_KEY)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1500)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 5"
        )
        assert overflow is False
