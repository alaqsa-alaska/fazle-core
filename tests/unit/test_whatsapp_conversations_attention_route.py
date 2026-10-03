"""Stage D8 (2026-08-28) -- GET /api/whatsapp/conversations/attention.

Same pattern as tests/unit/test_whatsapp_messages_query_and_conversation_routes.py:
httpx ASGITransport against the real FastAPI app, app.main.fetch_all
monkeypatched so no live Postgres connection is required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def auth_headers():
    from app.config import get_settings
    return {"X-Internal-Key": get_settings().internal_api_key}


async def _client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _conv_row(**overrides):
    base = {
        "conversation_key": "8801784396009:bridge3",
        "platform": "bridge3",
        "canonical_phone": "8801784396009",
        "sender_number": "8801784396009",
        "direction": "inbound",
        "actor_type": "external_user",
        "intent_detected": "recruitment",
        "workflow_triggered": None,
        "last_message_at": datetime(2026, 8, 28, 3, 0, 0, tzinfo=timezone.utc),
        "message_id": 100,
    }
    base.update(overrides)
    return base


class TestAuth:
    async def test_requires_auth(self):
        async with await _client() as ac:
            resp = await ac.get("/api/whatsapp/conversations/attention")
        assert resp.status_code == 403


class TestMetadataOnlyShape:
    async def test_never_returns_message_body_field(self, auth_headers):
        rows_call = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            rows_call["n"] += 1
            if rows_call["n"] == 1:
                return [_conv_row()]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        body = resp.json()
        assert resp.status_code == 200
        for conv in body["conversations"]:
            assert "message_body" not in conv
            assert "extracted_text" not in conv

    async def test_returns_compact_fields(self, auth_headers):
        rows_call = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            rows_call["n"] += 1
            if rows_call["n"] == 1:
                return [_conv_row()]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        conv = resp.json()["conversations"][0]
        for field in (
            "conversation_key", "domain", "last_intent", "current_state",
            "next_actor", "requires_action", "last_message_at", "last_actor_type",
        ):
            assert field in conv


class TestDomainAndStateResolution:
    async def test_outbound_latest_message_falls_back_to_sender_number(self, auth_headers):
        """Regression for a real live bug found during Stage D10:
        canonical_phone is only ever populated by the inbound writer
        (_save_raw_core_copy), never by the outbound writer
        (_save_message) -- so a conversation whose LATEST message is
        outbound (routine) must still resolve state via sender_number,
        not silently fall back to 'unclear'."""
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(
                    canonical_phone=None, sender_number="8801784396009",
                    direction="outbound", workflow_triggered="recruitment", intent_detected=None,
                )]
            if "wbom_candidates" in sql:
                assert args[0] == ["8801784396009"]
                return [{"phone": "8801784396009", "post_intake_status": "agreed"}]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        conv = resp.json()["conversations"][0]
        assert conv["canonical_phone"] == "8801784396009"
        assert conv["current_state"] == "waiting_for_company"
        assert conv["requires_action"] is True

    async def test_recruitment_conversation_resolves_state_from_candidate_row(self, auth_headers):
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(intent_detected="recruitment", canonical_phone="8801784396009")]
            if "wbom_candidates" in sql:
                return [{"phone": "8801784396009", "post_intake_status": "agreed"}]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        conv = resp.json()["conversations"][0]
        assert conv["domain"] == "recruitment"
        assert conv["current_state"] == "waiting_for_company"
        assert conv["next_actor"] == "company"
        assert conv["requires_action"] is True

    async def test_escort_conversation_resolves_state_from_program_row(self, auth_headers):
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(
                    conversation_key="8801912345678:bridge2", canonical_phone="8801912345678",
                    intent_detected=None, workflow_triggered="escort_duty",
                )]
            if "wbom_escort_programs" in sql:
                return [{"phone": "8801912345678", "status": "draft"}]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        conv = resp.json()["conversations"][0]
        assert conv["domain"] == "escort"
        assert conv["current_state"] == "waiting_for_company"
        assert conv["requires_action"] is True

    async def test_old_conversation_without_matching_business_record_is_unclear(self, auth_headers):
        """A conversation flagged as recruitment-domain but whose phone
        has no wbom_candidates row at all (e.g. pre-dates candidate
        creation, or intent was misdetected) must not fabricate a state."""
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(canonical_phone="8801999999999")]
            return []  # no candidate row found

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        conv = resp.json()["conversations"][0]
        assert conv["current_state"] == "unclear"
        assert conv["requires_action"] is False

    async def test_unrecognized_domain_defaults_to_unclear(self, auth_headers):
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(intent_detected="complaint", workflow_triggered="complaint")]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        conv = resp.json()["conversations"][0]
        assert conv["domain"] is None
        assert conv["current_state"] == "unclear"


class TestStageEAttendanceAndCashDomains:
    async def test_attendance_domain_resolves_via_employee_mobile_join(self, auth_headers):
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(
                    conversation_key="8801812462634:bridge2", canonical_phone="8801812462634",
                    intent_detected=None, workflow_triggered="attendance",
                )]
            if "wbom_attendance" in sql:
                return [{"phone": "8801812462634", "approved_at": None}]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"domain": "attendance"}, headers=auth_headers,
                )
        conv = resp.json()["conversations"][0]
        assert conv["domain"] == "attendance"
        assert conv["current_state"] == "waiting_for_company"
        assert conv["requires_action"] is True

    async def test_cash_domain_resolves_via_transaction_status(self, auth_headers):
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(
                    conversation_key="8801786178090:bridge1", canonical_phone="8801786178090",
                    intent_detected="payment_due", workflow_triggered=None,
                )]
            if "fpe_cash_transactions" in sql:
                return [{"phone": "8801786178090", "transaction_status": "pending"}]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"domain": "cash"}, headers=auth_headers,
                )
        conv = resp.json()["conversations"][0]
        assert conv["domain"] == "cash"
        assert conv["current_state"] == "waiting_for_company"
        assert conv["requires_action"] is True

    async def test_unfiltered_domain_skips_the_expensive_cash_scan_only_when_narrowed(self, auth_headers):
        """domain=recruitment must not trigger a fpe_cash_transactions scan
        -- that table is large (~4.4k rows across 3 unioned phone columns);
        no reason to pay for it on a narrowed, unrelated query."""
        queried_tables = []

        async def _fake_fetch_all(sql, *args):
            for t in ("wbom_candidates", "wbom_escort_programs", "wbom_attendance", "fpe_cash_transactions"):
                if t in sql:
                    queried_tables.append(t)
            if "conversation_key" in sql and "DISTINCT ON" in sql and "wbom_whatsapp_messages" in sql:
                return [_conv_row(intent_detected="recruitment")]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"domain": "recruitment"}, headers=auth_headers,
                )
        assert "fpe_cash_transactions" not in queried_tables
        assert "wbom_attendance" not in queried_tables
        assert "wbom_escort_programs" not in queried_tables
        assert "wbom_candidates" in queried_tables

    async def test_unsupported_domain_string_never_fabricates_state(self, auth_headers):
        """An unrecognized domain= value must not silently match anything
        -- Stage E9's "do not make the endpoint pretend unsupported
        domains are supported"."""
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_conv_row(intent_detected="some_unmapped_domain", workflow_triggered="some_unmapped_domain")]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"domain": "some_unmapped_domain"}, headers=auth_headers,
                )
        conv = resp.json()["conversations"][0]
        assert conv["domain"] is None
        assert conv["current_state"] == "unclear"
        assert conv["requires_action"] is False


class TestFilters:
    async def test_domain_filter_adds_condition(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured.setdefault("sqls", []).append(sql)
            captured.setdefault("args", []).append(args)
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"domain": "recruitment"}, headers=auth_headers,
                )
        assert "workflow_triggered = $1 OR intent_detected = $1" in captured["sqls"][0]
        assert "recruitment" in captured["args"][0]

    async def test_domain_label_translation_for_escort(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured.setdefault("args", []).append(args)
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"domain": "escort"}, headers=auth_headers,
                )
        assert "escort_duty" in captured["args"][0]

    async def test_requires_action_filter_excludes_non_matching_rows(self, auth_headers):
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [
                    _conv_row(conversation_key="a:bridge3", canonical_phone="8801111111111", intent_detected="recruitment"),
                    _conv_row(conversation_key="b:bridge3", canonical_phone="8801222222222", intent_detected="recruitment", message_id=101),
                ]
            if "wbom_candidates" in sql:
                return [
                    {"phone": "8801111111111", "post_intake_status": "declined"},  # requires_action False
                    {"phone": "8801222222222", "post_intake_status": "agreed"},    # requires_action True
                ]
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"requires_action": "true"}, headers=auth_headers,
                )
        body = resp.json()
        assert body["returned"] == 1
        assert body["conversations"][0]["canonical_phone"] == "8801222222222"

    async def test_invalid_start_time_returns_clean_error(self, auth_headers):
        with patch("app.main.fetch_all", new=lambda *a, **k: []):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/conversations/attention",
                    params={"start_time": "garbage"}, headers=auth_headers,
                )
        assert resp.status_code == 200
        assert "error" in resp.json()


class TestPagination:
    async def test_has_more_true_when_extra_row_present(self, auth_headers):
        rows = [_conv_row(conversation_key=f"{i}:bridge3", canonical_phone=f"88017000000{i:02d}", message_id=i) for i in range(21)]
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return rows
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/conversations/attention", headers=auth_headers)
        body = resp.json()
        assert body["has_more"] is True
        assert body["next_cursor"] is not None

    async def test_invalid_cursor_returns_clean_error_not_500(self, auth_headers):
        async with await _client() as ac:
            resp = await ac.get(
                "/api/whatsapp/conversations/attention",
                params={"cursor": "not-valid-base64!!"}, headers={"X-Internal-Key": "x"},
            )
        # auth fails first for a bad key; use a real key
        from app.config import get_settings
        async with await _client() as ac:
            resp = await ac.get(
                "/api/whatsapp/conversations/attention",
                params={"cursor": "not-valid-base64!!"},
                headers={"X-Internal-Key": get_settings().internal_api_key},
            )
        assert resp.status_code == 200
        assert "error" in resp.json()
