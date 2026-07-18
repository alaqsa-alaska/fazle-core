"""
Playwright E2E coverage for Dispatch Phase 2A.2 (controlled manual
assignment) on app/static/dispatch.html. Sync Playwright API, matching the
established convention in tests/e2e/test_dispatch.py (Phase 2A.1).

Role-based visibility (read-only vs operator) is exercised via
page.route() mocking of /api/admin/session -- the exact same technique
already used in test_dispatch.py's XSS test -- rather than creating real
admin accounts of different roles in production. Assignment-write outcomes
(success/409/etc.) are exercised via page.route() mocking of the assign
endpoint itself, so this suite never actually writes to production data.
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page

APP_URL = os.getenv("TEST_APP_URL", "http://localhost:8200")
API_KEY = os.getenv("TEST_API_KEY", "test-internal-key")

pytestmark = pytest.mark.e2e

_OPERATOR_SESSION = {
    "user": {"id": 1, "name": "Test Operator", "username": "test_operator", "phone": "01700000000",
              "roles": ["operator"], "normalized_name": "test operator", "normalized_roles": ["operator"],
              "is_restricted_office_assistant": False, "can_manage_admin": False,
              "can_edit_delete_transactions": True, "permissions": {}}
}
_VIEWER_SESSION = {
    "user": {"id": 2, "name": "Test Viewer", "username": "test_viewer", "phone": "01700000001",
              "roles": ["viewer"], "normalized_name": "test viewer", "normalized_roles": ["viewer"],
              "is_restricted_office_assistant": False, "can_manage_admin": False,
              "can_edit_delete_transactions": False, "permissions": {}}
}


def _mock_session(page: Page, session: dict) -> None:
    import json
    page.route("**/api/admin/session", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(session),
    ))


def _login(page: Page) -> None:
    page.goto(f"{APP_URL}/static/dispatch.html", wait_until="networkidle")
    page.evaluate("(k) => localStorage.setItem('fpe_api_key', k)", API_KEY)


class TestAssignmentVisibility:

    def test_viewer_sees_no_assignment_controls(self, page: Page) -> None:
        _login(page)
        _mock_session(page, _VIEWER_SESSION)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        assign_card = page.locator("#assignCard")
        assert "hidden" in (assign_card.get_attribute("class") or "")
        banner = page.locator("#assignBanner")
        assert "hidden" in (banner.get_attribute("class") or "")

    def test_operator_sees_assignment_controls(self, page: Page) -> None:
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        assign_card = page.locator("#assignCard")
        assert "hidden" not in (assign_card.get_attribute("class") or "")

    def test_no_console_errors_with_operator_session(self, page: Page) -> None:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        assert not errors, f"unexpected JS errors: {errors}"


class TestAssignmentWorkflow:

    def test_suggestion_click_fills_field_without_submitting(self, page: Page) -> None:
        """Clicking a suggestion row must only fill the employee-id field --
        never call the assign endpoint itself (no auto-submission)."""
        import json
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        assign_calls = []
        page.route("**/api/dispatch/programs/*/assign", lambda route: (
            assign_calls.append(1), route.fulfill(status=500, body="should not be called")
        )[1])
        page.route("**/api/dispatch/suggestions**", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "items": [{"rank": 1, "employee_id": 143, "employee_name": "Suggested Guy",
                            "employee_mobile": "01712345678", "reasons": ["available"],
                            "idle_days": 5, "active_assignment_count": 0}],
                "proposed_window": {}, "total_eligible": 1,
            }),
        ))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill("#fStartDate", "2026-08-01")
        page.fill("#fEndDate", "2026-08-03")
        page.click("button:has-text('Check Availability')")
        page.wait_for_timeout(1000)
        page.click(".suggestion-row")
        page.wait_for_timeout(300)
        assert page.input_value("#aEmployeeId") == "143"
        assert not assign_calls, "assignment endpoint must never be called by a suggestion click"

    def test_confirmation_required_before_write(self, page: Page) -> None:
        import json
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        assign_calls = []
        page.route("**/api/dispatch/programs/*/assign", lambda route: (
            assign_calls.append(1), route.fulfill(status=200, content_type="application/json",
                                                    body=json.dumps({"program_id": 1, "new_employee_id": 5,
                                                                      "previous_employee_id": None,
                                                                      "employee_name": "X"}))
        )[1])
        page.route("**/api/escort-roster/1", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"program_id": 1, "mother_vessel": "MV Test", "lighter_vessel": "LT",
                              "program_status": "draft", "start_date": "2026-08-01", "start_shift": "D",
                              "end_date": "2026-08-03", "end_shift": "N", "destination": "Narayanganj",
                              "escort_employee_id": None, "escort_name": None}),
        ))
        page.route("**/api/dispatch/check-conflict", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"available": True, "conflicts": []}),
        ))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill("#aProgramId", "1")
        page.fill("#aEmployeeId", "5")
        page.click("text=Load Program")
        page.wait_for_timeout(800)
        # confirmation box must be visible with a distinct confirm button,
        # and the write must NOT have fired just from loading the program
        assert page.locator(".confirm-box").count() > 0
        assert not assign_calls, "loading/previewing a program must never itself submit the write"
        page.click("button:has-text('Confirm Assign')")
        page.wait_for_timeout(500)
        assert len(assign_calls) == 1

    def test_conflict_409_displayed(self, page: Page) -> None:
        import json
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        page.route("**/api/escort-roster/1", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"program_id": 1, "mother_vessel": "MV Test", "lighter_vessel": "LT",
                              "program_status": "draft", "start_date": "2026-08-01", "start_shift": "D",
                              "end_date": "2026-08-03", "end_shift": "N", "destination": "Narayanganj",
                              "escort_employee_id": None, "escort_name": None}),
        ))
        page.route("**/api/dispatch/check-conflict", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"available": True, "conflicts": []}),
        ))
        page.route("**/api/dispatch/programs/*/assign", lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {"detail": "escort employee has an overlapping assignment",
                                          "code": "dispatch_conflict", "conflicts": [{"program_id": 9}]}}),
        ))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill("#aProgramId", "1")
        page.fill("#aEmployeeId", "5")
        page.click("text=Load Program")
        page.wait_for_timeout(800)
        page.click("button:has-text('Confirm Assign')")
        page.wait_for_timeout(500)
        status_text = page.text_content("#assignStatus")
        assert "conflict" in status_text.lower() or "Conflict" in status_text

    def test_stale_assignment_409_triggers_refresh(self, page: Page) -> None:
        import json
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        reload_calls = {"count": 0}

        def program_handler(route):
            reload_calls["count"] += 1
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"program_id": 1, "mother_vessel": "MV Test", "lighter_vessel": "LT",
                                  "program_status": "confirmed", "start_date": "2026-08-01", "start_shift": "D",
                                  "end_date": "2026-08-03", "end_shift": "N", "destination": "Narayanganj",
                                  "escort_employee_id": 7, "escort_name": "Someone Else"}),
            )

        page.route("**/api/escort-roster/1", program_handler)
        page.route("**/api/dispatch/check-conflict", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"available": True, "conflicts": []}),
        ))
        page.route("**/api/dispatch/programs/*/assign", lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {"detail": "stale", "code": "stale_assignment"}}),
        ))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill("#aProgramId", "1")
        page.fill("#aEmployeeId", "5")
        page.click("text=Load Program")
        page.wait_for_timeout(800)
        calls_before_confirm = reload_calls["count"]
        page.click("button:has-text('Confirm')")
        page.wait_for_timeout(800)
        status_text = page.text_content("#assignStatus")
        assert "stale" in status_text.lower()
        assert reload_calls["count"] > calls_before_confirm  # program state was refreshed, no page reload

    def test_unauthorized_403_handled(self, page: Page) -> None:
        import json
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        page.route("**/api/escort-roster/1", lambda route: route.fulfill(status=403, body="forbidden"))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill("#aProgramId", "1")
        page.fill("#aEmployeeId", "5")
        page.click("text=Load Program")
        page.wait_for_timeout(800)
        # 403 on the load path should route to the login screen (handleAuthFailure)
        assert page.is_visible("#loginBox")

    def test_not_found_404_handled(self, page: Page) -> None:
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        page.route("**/api/escort-roster/999999", lambda route: route.fulfill(status=404, body="not found"))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        page.fill("#aProgramId", "999999")
        page.fill("#aEmployeeId", "5")
        page.click("text=Load Program")
        page.wait_for_timeout(800)
        status_text = page.text_content("#assignStatus")
        assert "not found" in status_text.lower()


class TestOverlapsSection:

    def test_overlap_section_renders(self, page: Page) -> None:
        import json
        _login(page)
        _mock_session(page, _VIEWER_SESSION)
        page.route("**/api/dispatch/overlaps**", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "items": [{"employee_id": 1, "employee_name": "Rabbi", "employee_mobile": "01310542862",
                            "programs": [{"program_id": 95, "status": "running", "start_date": "2026-01-29",
                                          "start_shift": "D", "end_date": None, "end_shift": None,
                                          "mother_vessel": "MV Nord Agano", "destination": None}],
                            "conflicting_pairs": [{"program_a": {"program_id": 95}, "program_b": {"program_id": 96},
                                                    "classification": "active-active"}],
                            "conflict_count": 1, "data_quality_warnings": None}],
                "total": 1, "page": 1, "page_size": 20, "pages": 1,
                "note": "Existing overlaps require manual business review. No automatic correction is performed.",
            }),
        ))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        overlaps_html = page.inner_html("#overlapsHost")
        assert "Rabbi" in overlaps_html
        assert "active-active" in overlaps_html
        assert "Resolve Automatically" not in page.content()

    def test_no_resolve_button_anywhere(self, page: Page) -> None:
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        html_lower = page.content().lower()
        assert "resolve automatically" not in html_lower
        assert "auto-resolve" not in html_lower

    def test_bangla_and_english_names_render(self, page: Page) -> None:
        import json
        _login(page)
        _mock_session(page, _VIEWER_SESSION)
        page.route("**/api/dispatch/overlaps**", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "items": [{"employee_id": 1, "employee_name": "রবিন হোসেন", "employee_mobile": "01710000000",
                            "programs": [{"program_id": 1, "status": "confirmed", "start_date": "2026-08-01",
                                          "start_shift": "D", "end_date": None, "end_shift": None,
                                          "mother_vessel": "MV Test", "destination": None}],
                            "conflicting_pairs": [], "conflict_count": 1, "data_quality_warnings": None}],
                "total": 1, "page": 1, "page_size": 20, "pages": 1, "note": "x",
            }),
        ))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        assert "রবিন হোসেন" in page.inner_html("#overlapsHost")

    def test_xss_escaping_in_overlap_employee_name(self, page: Page) -> None:
        import json
        _login(page)
        _mock_session(page, _VIEWER_SESSION)
        page.route("**/api/dispatch/overlaps**", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "items": [{"employee_id": 1, "employee_name": "<img src=x onerror=alert(1)>",
                            "employee_mobile": "01710000000",
                            "programs": [{"program_id": 1, "status": "confirmed", "start_date": "2026-08-01",
                                          "start_shift": "D", "end_date": None, "end_shift": None,
                                          "mother_vessel": "MV Test", "destination": None}],
                            "conflicting_pairs": [], "conflict_count": 1, "data_quality_warnings": None}],
                "total": 1, "page": 1, "page_size": 20, "pages": 1, "note": "x",
            }),
        ))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        overlaps_html = page.inner_html("#overlapsHost")
        assert "<img src=x onerror=" not in overlaps_html
        assert "&lt;img" in overlaps_html


class TestAssignmentMobile:

    def test_mobile_layout_no_overflow_with_assign_card(self, page: Page) -> None:
        page.set_viewport_size({"width": 390, "height": 844})
        _login(page)
        _mock_session(page, _OPERATOR_SESSION)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1200)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 5"
        )
        assert overflow is False
