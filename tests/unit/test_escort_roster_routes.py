from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
pytestmark = pytest.mark.unit


class TestEscortRosterSearchQueries:
    @pytest.mark.asyncio
    async def test_roster_list_search_uses_qualified_columns(self, monkeypatch):
        from modules.escort_roster import db as roster_db

        calls = {"fetch_val": [], "fetch_all": []}

        async def _fake_fetch_val(query, *args, **kwargs):
            calls["fetch_val"].append((query, args))
            return 1

        async def _fake_fetch_all(query, *args, **kwargs):
            calls["fetch_all"].append((query, args))
            return [{
                "program_id": 7,
                "mother_vessel": "MV SEARCH TARGET",
                "lighter_vessel": "LIGHTER SEARCH ONE",
                "master_mobile": "01999120000",
                "escort_name": "Search Guard",
                "escort_mobile": "8801812345678",
                "destination": "Chattogram",
                "start_date": date(2026, 6, 27),
                "created_at": date(2026, 6, 27),
                "salary": Decimal("0"),
                "conveyance": Decimal("0"),
                "total": Decimal("0"),
                "program_status": "confirmed",
                "roster_status": "confirmed",
                "is_historical": False,
                "remarks": None,
                "capacity": None,
                "whatsapp_message_id": None,
            }]

        monkeypatch.setattr(roster_db, "fetch_val", _fake_fetch_val)
        monkeypatch.setattr(roster_db, "fetch_all", _fake_fetch_all)

        result = await roster_db.get_roster_list(
            search="SEARCH TARGET",
            page=1,
            page_size=20,
            start_from="2026-06-01",
            start_to="2026-06-30",
        )

        count_query = calls["fetch_val"][0][0]
        list_query = calls["fetch_all"][0][0]
        for query in (count_query, list_query):
            assert "e.mother_vessel ILIKE $1" in query
            assert "e.lighter_vessel ILIKE $1" in query
            assert "e.escort_name ILIKE $1" in query
            assert "e.master_mobile ILIKE $1" in query
            assert "e.escort_mobile ILIKE $1" in query
            assert "e.destination ILIKE $1" in query
            assert "COALESCE(e.start_date, p.program_date) >= $2" in query
            assert "COALESCE(e.start_date, p.program_date) <= $3" in query

        assert result["total"] == 1
        assert result["items"][0]["program_id"] == 7

    @pytest.mark.asyncio
    async def test_roster_export_with_search_works_via_shared_list_query(self, monkeypatch):
        from modules.escort_roster import routes as roster_routes

        captured = {}

        async def _fake_get_roster_list(**kwargs):
            captured.update(kwargs)
            return {
                "items": [{
                    "program_id": 7,
                    "mother_vessel": "MV SEARCH TARGET",
                    "lighter_vessel": "LIGHTER SEARCH ONE",
                    "master_mobile": "01999120000",
                    "escort_name": "Search Guard",
                    "escort_mobile": "8801812345678",
                    "destination": "Chattogram",
                    "start_date": "2026-06-27",
                    "start_shift": "D",
                    "end_date": None,
                    "end_shift": None,
                    "total_shifts": None,
                    "total_days": None,
                    "salary": None,
                    "conveyance": None,
                    "total": None,
                    "release_point": None,
                    "roster_status": "confirmed",
                }],
                "total": 1,
                "page": 1,
                "page_size": 10000,
                "pages": 1,
            }

        monkeypatch.setattr(roster_routes, "get_roster_list", _fake_get_roster_list)
        response = await roster_routes.api_roster_export(search="SEARCH TARGET")

        assert captured["search"] == "SEARCH TARGET"
        assert response.media_type == "text/csv"
        assert "escort_roster.csv" in response.headers["Content-Disposition"]


class TestEscortRosterSyncStatus:
    @pytest.mark.asyncio
    async def test_sync_program_to_roster_maps_confirmed_status_cleanly(self, monkeypatch):
        from modules.escort_roster import db as roster_db

        program_row = {
            "program_id": 12,
            "mother_vessel": "MV TEST SOURCE OF TRUTH",
            "lighter_vessel": "TEST_SOT_CORRECT_LIGHTER",
            "master_mobile": "01999123459",
            "destination": "Narayanganj",
            "escort_name": "Rabbi",
            "escort_mobile": "8801811111111",
            "escort_employee_id": 1,
            "program_date": date(2026, 6, 27),
            "start_date": date(2026, 6, 27),
            "shift": "D",
            "end_date": None,
            "end_shift": None,
            "status": "confirmed",
            "_escort_name": "Rabbi",
            "release_point": None,
            "release_location": None,
            "total_payment": None,
        }
        existing_row = {"id": 99, "calc_version": 2}
        final_row = {"program_id": 12, "roster_status": "confirmed", "program_status": "confirmed"}

        fetch_one_calls = {"count": 0}
        execute_calls = []

        async def _fake_fetch_one(query, *args, **kwargs):
            fetch_one_calls["count"] += 1
            if fetch_one_calls["count"] == 1:
                return program_row
            if fetch_one_calls["count"] == 2:
                return existing_row
            return final_row

        async def _fake_execute(query, *args, **kwargs):
            execute_calls.append((query, args))
            return "UPDATE 1"

        async def _fake_log_audit(**kwargs):
            return None

        monkeypatch.setattr(roster_db, "fetch_one", _fake_fetch_one)
        monkeypatch.setattr(roster_db, "execute", _fake_execute)
        monkeypatch.setattr(roster_db, "log_audit", _fake_log_audit)

        row = await roster_db.sync_program_to_roster(12, actor="test")

        update_query, update_args = execute_calls[0]
        assert "roster_status       = $18" in update_query
        assert update_args[17] == "confirmed"
        assert row["roster_status"] == "confirmed"


class TestEscortClientRoutes:
    def test_escort_clients_route_is_registered_before_program_id_route(self):
        from modules.escort_roster import routes as roster_routes

        paths = [route.path for route in roster_routes.router.routes]

        assert "/api/escort-roster/escort-clients" in paths
        assert "/api/escort-roster/{program_id}" in paths
        assert paths.index("/api/escort-roster/escort-clients") < paths.index("/api/escort-roster/{program_id}")


class TestEscortRosterRecalculate:
    @pytest.mark.asyncio
    async def test_recalculate_entry_falls_back_to_manual_roster_row(self, monkeypatch):
        from modules.escort_roster import db as roster_db

        manual_row = {
            "program_id": 910,
            "mother_vessel": "TEST_BROWSER_MV_20260627",
            "lighter_vessel": "TEST_BROWSER_LV_20260627",
            "destination": "Chattogram",
            "start_date": date(2026, 6, 27),
            "start_shift": "D",
            "end_date": None,
            "end_shift": "N",
            "conveyance": Decimal("500"),
        }

        fetch_calls = {"roster": 0}
        execute_calls = []
        audit_calls = []

        async def _fake_sync_program_to_roster(program_id, actor="system"):
            raise ValueError(f"Program {program_id} not found")

        async def _fake_fetch_one(query, *args, **kwargs):
            if "escort_roster_entries" not in query:
                return None
            fetch_calls["roster"] += 1
            return manual_row

        async def _fake_execute(query, *args, **kwargs):
            execute_calls.append((query, args))
            return "UPDATE 1"

        async def _fake_log_audit(**kwargs):
            audit_calls.append(kwargs)
            return None

        monkeypatch.setattr(roster_db, "sync_program_to_roster", _fake_sync_program_to_roster)
        monkeypatch.setattr(roster_db, "fetch_one", _fake_fetch_one)
        monkeypatch.setattr(roster_db, "execute", _fake_execute)
        monkeypatch.setattr(roster_db, "log_audit", _fake_log_audit)

        row = await roster_db.recalculate_entry(910, actor="test")

        assert fetch_calls["roster"] >= 2
        assert execute_calls[0][0].startswith("UPDATE escort_roster_entries SET updated_at = NOW()")
        assert audit_calls[0]["source"] == "manual_recalculate"
        assert audit_calls[0]["new_data"] == {"note": "manual_roster_noop"}
        assert row["program_id"] == 910
