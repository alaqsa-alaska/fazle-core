"""
Playwright E2E test suite for the employee-review cleanup pages:
  app/static/wbom-link-review.html
  app/static/wbom-create-link.html

Uses pytest-playwright's sync API (matches tests/e2e/test_dashboard.py —
avoids asyncio event-loop conflicts with pytest-asyncio, see
tests/conftest.py::pytest_collection_modifyitems).

All state-mutating checks (link/create actions, 409/404 handling,
validation) intercept the relevant fetch() calls with page.route() and
synthetic data, so this suite never writes to whichever backend
TEST_APP_URL points at — safe to run against production for read-only
checks without risking real unresolved-employee records. See Phase 1E.1,
knowledge_base/00_governance/management_decisions.md.

Prerequisites
-------------
  playwright install chromium
  export TEST_APP_URL=http://localhost:8200
  export TEST_API_KEY=<your INTERNAL_API_KEY>

Run
---
  TEST_API_KEY=fk_xxx pytest tests/e2e/test_employee_review.py -m e2e -v
"""
from __future__ import annotations

import json
import os
from typing import Generator

import pytest
from playwright.sync_api import Page, Route, expect

APP_URL = os.getenv("TEST_APP_URL", "http://localhost:8200")
API_KEY = os.getenv("TEST_API_KEY", "test-internal-key")

LINK_REVIEW_PATH = "/static/wbom-link-review.html"
CREATE_LINK_PATH = "/static/wbom-create-link.html"
REPORT_PATTERN = "**/reports/unresolved_fpe_employees"
LINK_FPE_PATTERN = "**/api/admin/employees/link-fpe"
CREATE_LINK_PATTERN = "**/api/admin/employees/create-and-link"

pytestmark = pytest.mark.e2e

SYNTHETIC_LINK_REVIEW_REPORT = {
    "rows": [
        {"bucket": "wbom_match_unlinked", "fpe_id": 900001,
         "fpe_name": "Synthetic Test Employee <script>alert(1)</script>",
         "wbom_match_id": 800001, "wbom_match_name": 'Synthetic WBOM "Quoted" & <b>Bold</b>',
         "phone": "01700000001", "name_mismatch": True},
        {"bucket": "wbom_match_unlinked", "fpe_id": 900002,
         "fpe_name": "Unicode Tëst Ünïcödé 测试 পরীক্ষা",
         "wbom_match_id": 800002, "wbom_match_name": "Unicode Match",
         "phone": "01700000002", "name_mismatch": False},
        {"bucket": "wbom_match_unlinked", "fpe_id": 900003, "fpe_name": "Duplicate Link Test",
         "wbom_match_id": 800003, "wbom_match_name": "Dup Match",
         "phone": "01700000003", "name_mismatch": False},
        {"bucket": "wbom_match_unlinked", "fpe_id": 900004, "fpe_name": "Stale Row Test",
         "wbom_match_id": 800004, "wbom_match_name": "Stale Match",
         "phone": "01700000004", "name_mismatch": False},
        {"bucket": "linked_but_mislabeled", "fpe_id": 900005,
         "fpe_name": "Mislabeled <img src=x onerror=alert(1)>",
         "wbom_employee_id": 800005, "resolution_status": "unresolved"},
    ],
    "summary": {"no_wbom_match_count": 0, "wbom_match_unlinked_count": 4,
                "linked_but_mislabeled_count": 1, "total": 5},
}

SYNTHETIC_CREATE_LINK_REPORT = {
    "rows": [
        {"bucket": "no_wbom_match", "fpe_id": 900010, "fpe_name": "Créate Tëst <b>Bold</b>",
         "salary": 12000, "joining_date": None, "created_source": "whatsapp_auto_create",
         "phone": "01700000010", "txn_count": 3},
        {"bucket": "no_wbom_match", "fpe_id": 900011, "fpe_name": "Second Candidate",
         "salary": 10000, "joining_date": None, "created_source": "whatsapp_auto_create",
         "phone": "01700000011", "txn_count": 1},
    ],
    "summary": {"no_wbom_match_count": 2, "wbom_match_unlinked_count": 0,
                "linked_but_mislabeled_count": 0, "total": 2},
}


def _fulfill_json(route: Route, status: int, body: dict) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _login(page: Page, path: str) -> None:
    page.goto(f"{APP_URL}{path}", wait_until="networkidle")
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
# Read-only: page loads, auth, live data contract
# ===========================================================================

class TestPageLoad:

    def test_link_review_loads(self, tracked_page: Page) -> None:
        resp = tracked_page.goto(f"{APP_URL}{LINK_REVIEW_PATH}")
        assert resp.status == 200
        expect(tracked_page.locator("#loginBox")).to_be_visible()

    def test_create_link_loads(self, tracked_page: Page) -> None:
        resp = tracked_page.goto(f"{APP_URL}{CREATE_LINK_PATH}")
        assert resp.status == 200
        expect(tracked_page.locator("#loginBox")).to_be_visible()

    def test_wrong_key_rejected(self, tracked_page: Page) -> None:
        tracked_page.goto(f"{APP_URL}{LINK_REVIEW_PATH}", wait_until="networkidle")
        tracked_page.fill("#keyInput", "definitely-wrong-key")
        tracked_page.click("text=Continue")
        tracked_page.wait_for_timeout(800)
        expect(tracked_page.locator("#loginBox")).to_be_visible()
        expect(tracked_page.locator("#app")).to_be_hidden()

    def test_correct_key_loads_live_data(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        expect(tracked_page.locator("#app")).to_be_visible()
        candidates_len = tracked_page.evaluate("CANDIDATES.length")
        assert isinstance(candidates_len, int)
        assert not tracked_page._console_errors  # type: ignore[attr-defined]

    def test_no_static_fixture_referenced(self, tracked_page: Page) -> None:
        _login(tracked_page, CREATE_LINK_PATH)
        is_fixture = tracked_page.evaluate(
            "typeof _DEMO_FIXTURE_CANDIDATES !== 'undefined' && CANDIDATES === _DEMO_FIXTURE_CANDIDATES"
        )
        assert is_fixture is False


# ===========================================================================
# Layout
# ===========================================================================

class TestLayout:

    def test_link_review_no_mobile_overflow(self, tracked_page: Page) -> None:
        tracked_page.set_viewport_size({"width": 390, "height": 844})
        _login(tracked_page, LINK_REVIEW_PATH)
        overflow = tracked_page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 5"
        )
        assert overflow is False, "table must scroll within its own container on mobile, not the whole page"

    def test_create_link_no_mobile_overflow(self, tracked_page: Page) -> None:
        tracked_page.set_viewport_size({"width": 390, "height": 844})
        _login(tracked_page, CREATE_LINK_PATH)
        overflow = tracked_page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 5"
        )
        assert overflow is False


# ===========================================================================
# Link-review page: synthetic-data interaction (mocked network, no real writes)
# ===========================================================================

class TestLinkReviewInteraction:

    @pytest.fixture(autouse=True)
    def _mock_report(self, tracked_page: Page):
        tracked_page.route(REPORT_PATTERN, lambda r: _fulfill_json(r, 200, SYNTHETIC_LINK_REVIEW_REPORT))
        yield

    def test_xss_names_escaped(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        row_html = tracked_page.inner_html("#row-0")
        assert "<script>" not in row_html
        assert "&lt;script&gt;" in row_html

    def test_unicode_names_render(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        row_text = tracked_page.inner_text("#row-1")
        assert "Ünïcödé" in row_text and "测试" in row_text and "পরীক্ষা" in row_text

    def test_mislabeled_section_xss_escaped(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        html = tracked_page.inner_html("#mislabeledSection")
        assert "<img src=x" not in html
        assert "&lt;img" in html
        assert "unexpected after Phase 1C" in html

    def test_cancel_causes_no_write(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        write_count = {"n": 0}

        def _count_and_fulfill(route: Route):
            write_count["n"] += 1
            route.fulfill(status=200, body="{}")

        tracked_page.route(LINK_FPE_PATTERN, _count_and_fulfill)
        tracked_page.once("dialog", lambda d: d.dismiss())
        tracked_page.click("#link-2")
        tracked_page.wait_for_timeout(400)
        assert write_count["n"] == 0

    def test_successful_link_marks_row_done(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        tracked_page.route(LINK_FPE_PATTERN, lambda r: _fulfill_json(r, 200, {"message": "linked"}))
        tracked_page.once("dialog", lambda d: d.accept())
        tracked_page.click("#link-2")
        tracked_page.wait_for_timeout(500)
        classes = tracked_page.get_attribute("#row-2", "class") or ""
        assert "done" in classes
        assert "Linked" in (tracked_page.text_content("#status-2") or "")

    def test_409_stale_row_handled_not_overwritten(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        tracked_page.route(
            LINK_FPE_PATTERN,
            lambda r: _fulfill_json(r, 409, {"detail": "already linked"}),
        )
        tracked_page.once("dialog", lambda d: d.accept())
        tracked_page.click("#link-3")
        tracked_page.wait_for_timeout(500)
        status = tracked_page.text_content("#status-3") or ""
        classes = tracked_page.get_attribute("#row-3", "class") or ""
        assert "already linked" in status.lower()
        assert "done" in classes  # removed from active review, not shown as a hard error

    def test_refresh_button_present_and_reloads(self, tracked_page: Page) -> None:
        _login(tracked_page, LINK_REVIEW_PATH)
        btn = tracked_page.locator("#progress button")
        expect(btn).to_be_visible()
        before = tracked_page.evaluate("CANDIDATES.length")
        btn.click()
        tracked_page.wait_for_timeout(500)
        after = tracked_page.evaluate("CANDIDATES.length")
        assert before == after == 4


# ===========================================================================
# Create-link page: synthetic-data interaction (mocked network, no real writes)
# ===========================================================================

class TestCreateLinkInteraction:

    @pytest.fixture(autouse=True)
    def _mock_report(self, tracked_page: Page):
        tracked_page.route(REPORT_PATTERN, lambda r: _fulfill_json(r, 200, SYNTHETIC_CREATE_LINK_REPORT))
        yield

    def test_attribute_breakout_is_escaped(self, tracked_page: Page) -> None:
        payload = 'Evil" onmouseover="window.__xss_fired=true" x="'
        report = json.loads(json.dumps(SYNTHETIC_CREATE_LINK_REPORT))
        report["rows"][0]["fpe_name"] = payload
        tracked_page.route(REPORT_PATTERN, lambda r: _fulfill_json(r, 200, report))
        _login(tracked_page, CREATE_LINK_PATH)
        value = tracked_page.eval_on_selector("#fName", "el => el.value")
        fired = tracked_page.evaluate("window.__xss_fired")
        input_count = tracked_page.eval_on_selector_all("#cardHost input", "els => els.length")
        assert value == payload
        assert fired is None
        assert input_count == 3

    def test_empty_designation_blocked(self, tracked_page: Page) -> None:
        _login(tracked_page, CREATE_LINK_PATH)
        write_count = {"n": 0}

        def _count(route: Route):
            write_count["n"] += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"message": "created"}))

        tracked_page.route(CREATE_LINK_PATTERN, _count)
        tracked_page.click("#btnCreate")
        tracked_page.wait_for_timeout(300)
        assert write_count["n"] == 0
        assert "required" in (tracked_page.text_content("#cardStatus") or "").lower()

    def test_unknown_designation_rejected(self, tracked_page: Page) -> None:
        _login(tracked_page, CREATE_LINK_PATH)
        write_count = {"n": 0}
        tracked_page.route(CREATE_LINK_PATTERN, lambda r: (write_count.__setitem__("n", write_count["n"] + 1),
                                                            _fulfill_json(r, 200, {"message": "created"}))[1])
        tracked_page.fill("#fDesig", "Unknown")
        tracked_page.click("#btnCreate")
        tracked_page.wait_for_timeout(300)
        assert write_count["n"] == 0
        assert "unknown" in (tracked_page.text_content("#cardStatus") or "").lower()

    def test_valid_designation_confirms_and_writes(self, tracked_page: Page) -> None:
        _login(tracked_page, CREATE_LINK_PATH)
        tracked_page.route(CREATE_LINK_PATTERN, lambda r: _fulfill_json(r, 200, {"message": "created"}))
        seen_dialogs: list[str] = []
        tracked_page.once("dialog", lambda d: (seen_dialogs.append(d.message), d.accept())[1])
        tracked_page.fill("#fDesig", "Guard")
        tracked_page.click("#btnCreate")
        tracked_page.wait_for_timeout(500)
        assert any("Create a WBOM record" in m for m in seen_dialogs)
        assert "created" in (tracked_page.text_content("#cardStatus") or "").lower()

    def test_refresh_button_present(self, tracked_page: Page) -> None:
        _login(tracked_page, CREATE_LINK_PATH)
        expect(tracked_page.locator("#progress button")).to_be_visible()
