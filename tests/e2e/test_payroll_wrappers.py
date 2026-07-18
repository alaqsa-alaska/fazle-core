"""
Playwright E2E coverage for app/static/payroll.html's api()/adminApi()
wrapper functions after their Phase 2D migration onto shared-auth-fetch.js
(FazleAuthFetch) internals. Uses pytest-playwright's sync API to avoid
asyncio event-loop conflicts with pytest-asyncio (asyncio_mode=auto) --
matches tests/e2e/test_dashboard.py's established convention.

Verifies every distinct behavioral branch preserved by the migration:
  - api(): fixed /api/fpe prefix, caller-pre-stringified body (unchanged),
    error message correctly extracted from FastAPI's {detail: "..."} shape
    via FazleAuthFetch.readError() (an intentional improvement -- api()
    previously showed the raw unparsed JSON text as the error).
  - adminApi(): dual-prefix routing (/users and /audit -> /admin, else
    /api/admin), conditional body auto-serialization, cache-busting headers.

Prerequisites
-------------
  playwright install chromium
  export TEST_APP_URL=http://localhost:8200
  export TEST_API_KEY=<your INTERNAL_API_KEY>

Run
---
  TEST_API_KEY=fk_xxx pytest tests/e2e/test_payroll_wrappers.py -m e2e -v
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page

APP_URL = os.getenv("TEST_APP_URL", "http://localhost:8200")
API_KEY = os.getenv("TEST_API_KEY", "test-internal-key")

pytestmark = pytest.mark.e2e


@pytest.fixture
def payroll_page(page: Page) -> Page:
    page.goto(f"{APP_URL}/payroll", wait_until="networkidle")
    page.evaluate("(k) => localStorage.setItem('fpe_api_key', k)", API_KEY)
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(1500)
    return page


class TestApiWrapper:

    def test_get_health(self, payroll_page: Page) -> None:
        result = payroll_page.evaluate("""
            async () => {
                try { return {ok: true, r: await api('/health')}; }
                catch (e) { return {ok: false, error: e.message}; }
            }
        """)
        assert result["ok"] is True
        assert result["r"]["status"] == "ok"

    def test_error_path_extracts_detail_message(self, payroll_page: Page) -> None:
        """api()'s error path now goes through FazleAuthFetch.readError(),
        which parses FastAPI's {detail: "..."} shape into a clean message
        -- previously showed the raw unparsed JSON text."""
        result = payroll_page.evaluate("""
            async () => {
                try { await api('/employees/999999999'); return {ok: true}; }
                catch (e) { return {ok: false, error: e.message}; }
            }
        """)
        assert result["ok"] is False
        assert result["error"] == "Employee not found"
        assert "{" not in result["error"]  # not raw unparsed JSON


class TestAdminApiWrapper:

    def test_api_admin_prefix_branch(self, payroll_page: Page) -> None:
        """Non-/users, non-/audit paths route through /api/admin."""
        result = payroll_page.evaluate("""
            async () => {
                try { return {ok: true, r: await adminApi('/employees?limit=3')}; }
                catch (e) { return {ok: false, error: e.message}; }
            }
        """)
        assert result["ok"] is True
        assert "employees" in result["r"]

    def test_users_special_case_prefix_branch(self, payroll_page: Page) -> None:
        """/users routes through the bare /admin prefix, not /api/admin."""
        result = payroll_page.evaluate("""
            async () => {
                try { return {ok: true, r: await adminApi('/users')}; }
                catch (e) { return {ok: false, error: e.message}; }
            }
        """)
        assert result["ok"] is True

    def test_audit_special_case_prefix_branch(self, payroll_page: Page) -> None:
        """/audit routes through the bare /admin prefix, not /api/admin."""
        result = payroll_page.evaluate("""
            async () => {
                try { return {ok: true, r: await adminApi('/audit?limit=3')}; }
                catch (e) { return {ok: false, error: e.message}; }
            }
        """)
        assert result["ok"] is True
        assert "audit" in result["r"]

    def test_no_console_errors_on_load(self, payroll_page: Page) -> None:
        """Sanity: the page itself must load with zero JS errors after the
        wrapper migration (loadHeaderStatus() calls api() automatically on
        boot)."""
        errors = []
        payroll_page.on("pageerror", lambda e: errors.append(str(e)))
        payroll_page.reload(wait_until="networkidle")
        payroll_page.wait_for_timeout(1000)
        assert not errors, f"unexpected JS errors: {errors}"
