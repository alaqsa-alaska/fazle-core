from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date

import pytest

pytestmark = pytest.mark.unit


def _set_escort_env(monkeypatch, clients: str = "8801670535255,8801955555555") -> None:
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", clients)
    monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
    monkeypatch.setenv("BRIDGE1_NUMBER", "8801958122300")
    monkeypatch.setenv("BRIDGE2_NUMBER", "8801880446111")
    from app.config import get_settings
    get_settings.cache_clear()


class TestEscortParserExamples:
    def test_dubai_eco_example(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message, is_minimum_escort_order

        text = """
        MV DUBAI ECO
        A/C Nabil Feed
        Soybean Meal
        8. Haji Salim 01708149090 Local 1100 m.t
        """
        order = parse_escort_message(text)
        lighter = order["lighters"][0]
        assert order["mother_vessel"] == "MV DUBAI ECO"
        assert order["importer"] == "Nabil Feed"
        assert order["cargo_type"] == "Soybean Meal"
        assert lighter["lighter_vessel"] == "Haji Salim"
        assert lighter["master_mobile"] == "01708149090"
        assert lighter["destination"] == "Chattogram"
        assert lighter["capacity"] == "1100 MT"
        assert is_minimum_escort_order(order) is True

    def test_truong_minh_prosperity_example(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message

        text = """
        M.V. TRUONG MINH PROSPERITY
        Account: Suguna
        Corn
        9. Banglar Odhinayok 01827128225 Rupshi 900 MT
        """
        order = parse_escort_message(text)
        lighter = order["lighters"][0]
        assert order["mother_vessel"] == "MV TRUONG MINH PROSPERITY"
        assert order["importer"] == "Suguna"
        assert order["cargo_type"] == "Corn"
        assert lighter["lighter_vessel"] == "Banglar Odhinayok"
        assert lighter["master_mobile"] == "01827128225"
        assert lighter["destination"] == "Narayanganj"
        assert lighter["capacity"] == "900 MT"

    def test_mv_label_on_lighter_vessel(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message

        text = """
        Mother Vessel: MV GOLDEN STAR
        A/C Quality
        19. MV: ANJ-10
        01711273432
        Destination: N.Bari
        Capacity: 1200
        """
        order = parse_escort_message(text)
        lighter = order["lighters"][0]
        assert order["mother_vessel"] == "MV GOLDEN STAR"
        assert lighter["lighter_vessel"] == "ANJ-10"
        assert lighter["master_mobile"] == "01711273432"
        assert lighter["destination"] == "Nagarbari"
        assert lighter["capacity"] == "1200 MT"

    def test_multiple_serial_numbers(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message

        text = """
        MV SPAR LIBRA
        8. Haji Salim-4 01708149090 Local 1100 MT
        9. Banglar Odhinayok 01883158655 Rupshi 900 MT
        """
        order = parse_escort_message(text)
        assert len(order["lighters"]) == 2
        assert order["lighters"][0]["lighter_vessel"] == "Haji Salim-4"
        assert order["lighters"][1]["lighter_vessel"] == "Banglar Odhinayok"

    def test_missing_cargo(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message, is_minimum_escort_order

        text = "MV BRIGHT FALCON\nA/C Alal Feed\n1. ST 1-01321170929 Local"
        order = parse_escort_message(text)
        assert order["cargo_type"] is None
        assert is_minimum_escort_order(order) is True

    def test_missing_destination(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message, is_minimum_escort_order

        text = "MV GM FORTUNE\nA/C Suguna\n01) MV: Jewel-6 Cap: 900 Mt Mob: 01827-128225"
        order = parse_escort_message(text)
        lighter = order["lighters"][0]
        assert lighter["destination"] is None
        assert lighter["capacity"] == "900 MT"
        assert is_minimum_escort_order(order) is True

    def test_missing_importer(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message

        text = "MV MARIMYR A\nSoyabean Meal\n01 City-46 01829830522 Narayanganj 1000 Night"
        order = parse_escort_message(text)
        assert order["importer"] is None
        assert order["cargo_type"] == "Soybean Meal"

    def test_local_normalizes_to_chattogram(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message

        text = "MV TEST\nAtlas 2-01712345678 Local 700mt"
        order = parse_escort_message(text)
        assert order["lighters"][0]["destination"] == "Chattogram"

    def test_rupshi_normalizes_to_narayanganj(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message

        text = "MV TEST\nAtlas 2-01712345678 Rupshi 700mt"
        order = parse_escort_message(text)
        assert order["lighters"][0]["destination"] == "Narayanganj"

    def test_build_admin_message_uses_strict_template(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import build_admin_message, parse_escort_message

        text = """
        MV SPAR LIBRA
        8. Haji Salim-4 01708149090 Local 1100 MT
        9. Banglar Odhinayok 01883158655 Rupshi 900 MT
        """
        order = parse_escort_message(text)
        msg = build_admin_message(order, "8801670535255")
        assert "Automated Reply System" not in msg
        assert msg.count("Notun escort order:") >= 1


class TestEscortSourceOfTruth:
    def test_completed_draft_parser_accepts_test_style_tokens(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_completed_draft

        draft = parse_completed_draft(
            """
            Mother Vessel: MV TEST_SOT_FIX_123_MV
            Lighter Vessel: TEST_SOT_FIX_123_LV
            Master Mobile: 01999123999
            Destination: Rupshi
            Importer: TEST IMPORTER
            Escort Name: Rabbi
            Escort Mobile: 01310542862
            Start Date: 27/06/2026 (D)
            Al-Aqsa Security Service
            """
        )
        assert draft["mother_vessel"] == "MV TEST_SOT_FIX_123_MV"
        assert draft["lighter_vessel"] == "TEST_SOT_FIX_123_LV"
        assert draft["destination"] == "Narayanganj"
        assert draft["importer"] == "TEST IMPORTER"

    @pytest.mark.asyncio
    async def test_client_message_without_minimum_fields_ignored(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import handle_escort_client_message
        import modules.escort as escort_module

        called = {"saved": False}

        async def _fake_save(*args, **kwargs):
            called["saved"] = True
            return [1]

        monkeypatch.setattr(escort_module, "save_escort_programs", _fake_save)
        reply, note = await handle_escort_client_message(
            "MV GOLDEN STAR\nEscort lagbe urgently",
            "8801670535255",
            "bridge2",
            is_historical=True,
        )
        assert reply == ""
        assert note is None
        assert called["saved"] is False

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import parse_escort_message, save_escort_programs
        import modules.escort as escort_module

        state = {"inserted": [], "next_id": 1}

        async def _fake_get_contact_id(_phone):
            return 77

        async def _fake_sync(_program_id):
            return None

        async def _fake_fetch_one(query, *args):
            if "SELECT program_id FROM wbom_escort_programs" in query:
                if state["inserted"]:
                    return {"program_id": state["inserted"][0]["program_id"]}
                return None
            if "RETURNING program_id" in query:
                pid = state["next_id"]
                state["next_id"] += 1
                state["inserted"].append({
                    "program_id": pid,
                    "mother_vessel": args[0],
                    "lighter_vessel": args[1],
                    "master_mobile": args[2],
                    "destination": args[3],
                })
                return {"program_id": pid}
            return None

        monkeypatch.setattr(escort_module, "_get_contact_id", _fake_get_contact_id)
        monkeypatch.setattr(escort_module, "_sync_roster_draft", _fake_sync)
        monkeypatch.setattr(escort_module, "fetch_one", _fake_fetch_one)

        order = parse_escort_message("""
            MV DUBAI ECO
            A/C Nabil Feed
            Soybean Meal
            8. Haji Salim 01708149090 Local 1100 MT
            12/04/2026 Day
        """)
        ids1 = await save_escort_programs(order, "8801670535255", "bridge2")
        ids2 = await save_escort_programs(order, "8801670535255", "bridge2")

        assert ids1 == [1]
        assert ids2 == [1]
        assert len(state["inserted"]) == 1

    @pytest.mark.asyncio
    async def test_draft_updated_by_bridge_confirmation(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import handle_admin_escort_completion
        import modules.escort as escort_module

        captured = {}

        async def _fake_find_pending_program(*args, **kwargs):
            return {
                "program_id": 12,
                "mother_vessel": "MV WRONG DRAFT",
                "lighter_vessel": "TEST_SOT_WRONG_LIGHTER",
                "destination": "Old Dest",
                "remarks": json.dumps({
                    "sender_phone": "8801670535255",
                    "source_bridge": "bridge2",
                    "cargo_type": "Soybean Meal",
                    "importer": "Nabil Feed",
                    "capacity": "1100 MT",
                }),
            }

        async def _fake_resolve(*args, **kwargs):
            return 99

        async def _fake_update_program_confirmed(program_id, escort_name, escort_mobile, **kwargs):
            captured["program_id"] = program_id
            captured["escort_name"] = escort_name
            captured["escort_mobile"] = escort_mobile
            captured["kwargs"] = kwargs
            return True

        async def _fake_sync(_program_id):
            captured["synced"] = _program_id

        monkeypatch.setattr(escort_module, "_find_pending_program", _fake_find_pending_program)
        monkeypatch.setattr(escort_module, "_resolve_escort_employee_id", _fake_resolve)
        monkeypatch.setattr(escort_module, "_update_program_confirmed", _fake_update_program_confirmed)
        monkeypatch.setattr(escort_module, "_sync_roster_after_confirm", _fake_sync)

        confirm_text = """
        Mother Vessel: MV DUBAI ECO
        Lighter Vessel: Haji Salim
        Master Mobile: 01708149090
        Destination: Local
        Escort Name: Ainul
        Escort Mobile: 01883158655
        Start Date: 12/04/2026 (D)
        Al-Aqsa Security Service
        """
        reply, note = await handle_admin_escort_completion(
            confirm_text,
            "8801880446111",
            "bridge2",
            recipient_phone="8801670535255",
        )
        assert note is not None
        assert "Sent to 8801670535255" in reply
        assert captured["program_id"] == 12
        assert captured["escort_name"] == "Ainul"
        assert captured["escort_mobile"] == "01883158655"
        assert captured["kwargs"]["mother_vessel"] == "MV DUBAI ECO"
        assert captured["kwargs"]["lighter_vessel"] == "Haji Salim"
        assert captured["kwargs"]["master_mobile"] == "01708149090"
        assert captured["kwargs"]["destination"] == "Chattogram"
        assert captured["kwargs"]["cargo_type"] == "Soybean Meal"
        assert captured["kwargs"]["importer"] == "Nabil Feed"
        assert captured["kwargs"]["capacity"] == "1100 MT"
        assert captured["synced"] == 12

    @pytest.mark.asyncio
    async def test_update_program_confirmed_overwrites_authoritative_fields(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import _update_program_confirmed
        import modules.escort as escort_module

        captured = {}

        async def _fake_execute(query, *args, **kwargs):
            captured["query"] = query
            captured["args"] = args
            return "UPDATE 1"

        monkeypatch.setattr(escort_module, "execute", _fake_execute)
        async def _fake_columns():
            return {"capacity", "cargo_type", "importer", "start_date"}

        async def _fake_merge(*args, **kwargs):
            return None

        monkeypatch.setattr(escort_module, "_escort_program_columns", _fake_columns)
        monkeypatch.setattr(escort_module, "_merge_program_remarks", _fake_merge)

        ok = await _update_program_confirmed(
            22,
            "Rabbi",
            "8801811111111",
            lighter_vessel="TEST_SOT_CORRECT_LIGHTER",
            shift="D",
            program_date=date(2026, 6, 27),
            escort_employee_id=9,
            mother_vessel="MV TEST SOURCE OF TRUTH",
            destination="Rupshi",
            master_mobile="01999123459",
            cargo_type="Soybean Meal",
            importer="Nabil Feed",
            capacity="1100 MT",
        )

        assert ok is True
        # Phase 2A.3 Decision 4: status is no longer unconditionally reset to
        # 'confirmed' -- it preserves an existing running/completed status
        # (attendance-gated progress must never be silently reverted).
        assert "THEN status ELSE 'confirmed' END" in captured["query"]
        assert "lighter_vessel = COALESCE" in captured["query"]
        assert "destination = COALESCE" in captured["query"]
        assert "cargo_type = COALESCE" in captured["query"]
        assert "importer = COALESCE" in captured["query"]
        assert "capacity = COALESCE" in captured["query"]
        assert "start_date = CASE" in captured["query"]
        assert captured["args"][0] == 22
        assert "MV TEST SOURCE OF TRUTH" in captured["args"]
        assert "TEST_SOT_CORRECT_LIGHTER" in captured["args"]
        assert "Narayanganj" in captured["args"]
        assert "01999123459" in captured["args"]
        assert "Soybean Meal" in captured["args"]
        assert "Nabil Feed" in captured["args"]
        assert "1100 MT" in captured["args"]

    @pytest.mark.asyncio
    async def test_confirmation_from_wrong_sender_ignored(self, monkeypatch):
        _set_escort_env(monkeypatch)
        from modules.escort import handle_admin_escort_completion

        confirm_text = """
        Mother Vessel: MV TEST
        Lighter Vessel: Atlas 2
        Master Mobile: 01712345678
        Escort Name: Rahim
        Escort Mobile: 01811111111
        Start Date: 12/04/2026 (D)
        Al-Aqsa Security Service
        """
        reply, note = await handle_admin_escort_completion(
            confirm_text,
            "8801999999999",
            "bridge2",
            recipient_phone="8801670535255",
        )
        assert reply == ""
        assert note is None

    @pytest.mark.asyncio
    async def test_confirmation_to_wrong_recipient_ignored(self, monkeypatch):
        _set_escort_env(monkeypatch, clients="8801670535255")
        from modules.escort import handle_admin_escort_completion

        confirm_text = """
        Mother Vessel: MV TEST
        Lighter Vessel: Atlas 2
        Master Mobile: 01712345678
        Escort Name: Rahim
        Escort Mobile: 01811111111
        Start Date: 12/04/2026 (D)
        Al-Aqsa Security Service
        """
        reply, note = await handle_admin_escort_completion(
            confirm_text,
            "8801880446111",
            "bridge2",
            recipient_phone="8801555555555",
        )
        assert reply == ""
        assert note is None


class TestUnmatchedConfirmationQueue:
    """Workstream 2 Stage 2: no-match must queue for review, never
    silently auto-create a second program row."""

    @pytest.mark.asyncio
    async def test_no_match_queues_instead_of_creating_program(self, monkeypatch):
        _set_escort_env(monkeypatch)
        import modules.escort as escort_module
        from modules.escort import handle_admin_escort_completion

        async def _fake_find_pending_program(*args, **kwargs):
            return None

        async def _fake_score_candidates(*args, **kwargs):
            return [{"program_id": 41, "score": 3.1, "matched_fields": ["mother_vessel"], "warnings": []}]

        create_calls = []

        async def _fake_create_confirmed_from_admin(*args, **kwargs):
            create_calls.append((args, kwargs))
            return 999  # would prove the old auto-create path fired if ever called

        enqueue_calls = []

        async def _fake_enqueue(**kwargs):
            enqueue_calls.append(kwargs)
            return 77

        reconcile_calls = []
        # Old code imported reconcile_drafts_for_confirmation lazily inside the
        # no-match branch; the branch it lived in no longer exists, so this
        # module-level patch just proves it's genuinely never reached.
        async def _fake_reconcile(**kwargs):
            reconcile_calls.append(kwargs)

        monkeypatch.setattr(escort_module, "_find_pending_program", _fake_find_pending_program)
        monkeypatch.setattr(escort_module, "_score_pending_program_candidates", _fake_score_candidates)
        monkeypatch.setattr(escort_module, "_create_confirmed_from_admin", _fake_create_confirmed_from_admin)
        monkeypatch.setattr(escort_module, "_enqueue_unmatched_confirmation", _fake_enqueue)

        confirm_text = """
        Mother Vessel: MV NEVER SEEN BEFORE
        Lighter Vessel: Unknown Lighter
        Master Mobile: 01799999999
        Escort Name: Karim
        Escort Mobile: 01888888888
        Start Date: 12/04/2026 (D)
        Al-Aqsa Security Service
        """
        # recipient_phone=None matches the real message_router.py call shape
        # for role=="admin" confirmations (modules/message_router/__init__.py
        # ~line 430) -- deliberately not exercising recipient_phone="...",
        # which hits a separate, pre-existing, already-known bug in
        # _is_authoritative_bridge_confirmation()'s phone-format comparison
        # (_escort_client_phone_list() returns unnormalized numbers compared
        # against a normalized recipient; same root cause as the existing
        # "1 pre-existing, isolation-verified, unrelated failure" on
        # test_draft_updated_by_bridge_confirmation above -- out of scope
        # for the draft-confirmation invariant, not fixed here).
        reply, note = await handle_admin_escort_completion(
            confirm_text,
            "8801880446111",
            "bridge2",
            recipient_phone=None,
            message_id="MSG-XYZ",
        )

        assert create_calls == [], "no-match must never call _create_confirmed_from_admin"
        assert reconcile_calls == [], "no-match must never reconcile/delete draft rows"
        assert note is None, "no-match must never produce a client-facing delivery"
        assert "Queued for review" in reply
        assert "#77" in reply
        assert len(enqueue_calls) == 1
        assert enqueue_calls[0]["message_id"] == "MSG-XYZ"
        assert enqueue_calls[0]["admin_phone"] == "8801880446111"
        assert enqueue_calls[0]["client_phone"] is None
        assert enqueue_calls[0]["candidates"][0]["program_id"] == 41
        assert enqueue_calls[0]["best_score"] == 3.1

    @pytest.mark.asyncio
    async def test_enqueue_is_idempotent_on_existing_message_id(self, monkeypatch):
        _set_escort_env(monkeypatch)
        import modules.escort as escort_module

        async def _fake_fetch_val(query, *args, **kwargs):
            assert "SELECT id FROM wbom_unmatched_confirmations" in query, (
                "message_id present -> must pre-check before attempting an insert"
            )
            return 55

        monkeypatch.setattr(escort_module, "fetch_val", _fake_fetch_val)

        queue_id = await escort_module._enqueue_unmatched_confirmation(
            text="raw",
            draft={"mother_vessel": "MV X"},
            source="bridge1",
            admin_phone="8801880446111",
            client_phone="8801670535255",
            message_id="MSG-DUP",
            candidates=[],
            best_score=None,
        )
        assert queue_id == 55

    @pytest.mark.asyncio
    async def test_enqueue_inserts_when_message_id_not_seen_before(self, monkeypatch):
        _set_escort_env(monkeypatch)
        import modules.escort as escort_module

        calls = []

        async def _fake_fetch_val(query, *args, **kwargs):
            calls.append(query)
            if "SELECT id FROM wbom_unmatched_confirmations" in query:
                return None  # not queued before
            assert "INSERT INTO wbom_unmatched_confirmations" in query
            assert "ON CONFLICT (source_bridge, source_message_id)" in query
            return 88

        monkeypatch.setattr(escort_module, "fetch_val", _fake_fetch_val)

        queue_id = await escort_module._enqueue_unmatched_confirmation(
            text="raw text",
            draft={"mother_vessel": "MV X"},
            source="bridge1",
            admin_phone="8801880446111",
            client_phone="8801670535255",
            message_id="MSG-NEW",
            candidates=[{"program_id": 1, "score": 2.0, "matched_fields": [], "warnings": []}],
            best_score=2.0,
        )
        assert queue_id == 88
        assert len(calls) == 2  # pre-check, then insert

    @pytest.mark.asyncio
    async def test_enqueue_without_message_id_skips_precheck(self, monkeypatch):
        _set_escort_env(monkeypatch)
        import modules.escort as escort_module

        calls = []

        async def _fake_fetch_val(query, *args, **kwargs):
            calls.append(query)
            assert "INSERT INTO wbom_unmatched_confirmations" in query
            return 91

        monkeypatch.setattr(escort_module, "fetch_val", _fake_fetch_val)

        queue_id = await escort_module._enqueue_unmatched_confirmation(
            text="raw text",
            draft={"mother_vessel": "MV X"},
            source="meta",
            admin_phone="8801880446111",
            client_phone=None,
            message_id=None,
            candidates=[],
            best_score=None,
        )
        assert queue_id == 91
        assert len(calls) == 1  # no reliable id -> no pre-check possible, straight to insert


class _FakeConn:
    """Minimal asyncpg.Connection stand-in for the Stage 3 review-action
    tests. queue_row/target_row are plain dicts (dict(record) on a dict
    just copies it, matching real asyncpg.Record behavior closely enough
    for these tests). advisory_lock_ok controls every pg_try_advisory_lock
    call uniformly -- good enough since these tests only ever hold one
    lock contention scenario at a time."""

    def __init__(self, *, advisory_lock_ok=True, queue_row=None, target_row=None):
        self.advisory_lock_ok = advisory_lock_ok
        self.queue_row = dict(queue_row) if queue_row else None
        self.target_row = dict(target_row) if target_row else None
        self.executed: list[tuple] = []

    async def fetchval(self, query, *args):
        if "pg_try_advisory_lock" in query:
            return self.advisory_lock_ok
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def fetchrow(self, query, *args):
        if "wbom_unmatched_confirmations" in query:
            return dict(self.queue_row) if self.queue_row else None
        if "wbom_escort_programs" in query:
            return dict(self.target_row) if self.target_row else None
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"


def _fake_db_conn_factory(conn):
    @asynccontextmanager
    async def _fake_db_conn():
        yield conn
    return _fake_db_conn


class TestUnmatchedConfirmationReviewActions:
    """Workstream 2 Stage 3: link-existing / confirm-new / reject / defer."""

    @pytest.mark.asyncio
    async def test_link_existing_happy_path(self, monkeypatch):
        import modules.escort as escort_module

        queue_row = {
            "id": 5, "status": "pending", "resolved_program_id": None,
            "source": "bridge1", "client_phone": None,
            "parsed_confirmation": json.dumps({
                "mother_vessel": "MV TEST", "escort_name": "Karim",
                "escort_mobile": "01888888888",
            }),
        }
        target_row = {"program_id": 41, "status": "draft", "mother_vessel": "MV TEST"}
        conn = _FakeConn(queue_row=queue_row, target_row=target_row)

        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        emp_calls = []
        async def _fake_resolve(mobile, name=None, *, auto_create=False):
            emp_calls.append((mobile, name, auto_create))
            return 77

        update_calls = []
        async def _fake_update(*args, **kwargs):
            update_calls.append((args, kwargs))
            return True

        sync_calls = []
        async def _fake_sync(program_id):
            sync_calls.append(program_id)

        async def _fake_fetch_one(query, *args, **kwargs):
            return {"program_id": 41, "status": "confirmed"}

        audit_calls = []
        async def _fake_log_audit(*args, **kwargs):
            audit_calls.append((args, kwargs))

        monkeypatch.setattr(escort_module, "_resolve_escort_employee_id", _fake_resolve)
        monkeypatch.setattr(escort_module, "_update_program_confirmed", _fake_update)
        monkeypatch.setattr(escort_module, "_sync_roster_after_confirm", _fake_sync)
        monkeypatch.setattr(escort_module, "fetch_one", _fake_fetch_one)
        monkeypatch.setattr("modules.escort_roster.db.log_audit", _fake_log_audit)

        result = await escort_module.link_unmatched_confirmation_to_existing_draft(
            5, 41, performed_by="admin_test", reason="matches after manual review",
        )

        assert result == {
            "queue_id": 5, "resolved_program_id": 41, "employee_id": 77, "status": "linked_existing",
        }
        assert emp_calls == [("01888888888", "Karim", True)]
        assert sync_calls == [41]
        assert len(audit_calls) == 1
        assert audit_calls[0][0][0] == 41  # program_id positional arg to log_audit
        assert audit_calls[0][0][1] == "unmatched_confirmation_linked"
        # queue row UPDATE actually executed on the locked connection
        # (executed also contains the advisory-unlock calls, in order,
        # so search rather than assume position)
        assert any("SET status = 'linked_existing'" in q for q, _ in conn.executed)

    @pytest.mark.asyncio
    async def test_link_existing_blocked_when_already_resolved(self, monkeypatch):
        import modules.escort as escort_module

        queue_row = {
            "id": 6, "status": "linked_existing", "resolved_program_id": 41,
            "source": "bridge1", "client_phone": None, "parsed_confirmation": "{}",
        }
        conn = _FakeConn(queue_row=queue_row)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        with pytest.raises(escort_module.UnmatchedConfirmationConflict):
            await escort_module.link_unmatched_confirmation_to_existing_draft(
                6, 41, performed_by="admin_test", reason="x",
            )

    @pytest.mark.asyncio
    async def test_link_existing_blocked_by_lock_contention(self, monkeypatch):
        import modules.escort as escort_module

        conn = _FakeConn(advisory_lock_ok=False)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        with pytest.raises(escort_module.UnmatchedConfirmationConflict):
            await escort_module.link_unmatched_confirmation_to_existing_draft(
                7, 41, performed_by="admin_test", reason="x",
            )

    @pytest.mark.asyncio
    async def test_link_existing_rejects_invalid_target_status(self, monkeypatch):
        import modules.escort as escort_module

        queue_row = {
            "id": 8, "status": "pending", "resolved_program_id": None,
            "source": "bridge1", "client_phone": None, "parsed_confirmation": "{}",
        }
        target_row = {"program_id": 41, "status": "completed", "mother_vessel": "MV TEST"}
        conn = _FakeConn(queue_row=queue_row, target_row=target_row)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        with pytest.raises(escort_module.UnmatchedConfirmationConflict):
            await escort_module.link_unmatched_confirmation_to_existing_draft(
                8, 41, performed_by="admin_test", reason="x",
            )

    @pytest.mark.asyncio
    async def test_confirm_new_happy_path(self, monkeypatch):
        import modules.escort as escort_module

        queue_row = {
            "id": 9, "status": "pending", "resolved_program_id": None,
            "source": "bridge1", "client_phone": None,
            "parsed_confirmation": json.dumps({
                "mother_vessel": "MV NEW", "lighter_vessel": "New Lighter",
                "escort_name": "Rahim",
            }),
        }
        conn = _FakeConn(queue_row=queue_row)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        async def _fake_find_pending_program(*a, **kw):
            return None  # no confident match now -- confirm-new legitimately allowed

        create_calls = []
        async def _fake_create(draft, source, client_phone=None):
            create_calls.append((draft, source, client_phone))
            return 55

        async def _fake_fetch_one(query, *args, **kwargs):
            return {"program_id": 55, "status": "confirmed"}

        audit_calls = []
        async def _fake_log_audit(*args, **kwargs):
            audit_calls.append((args, kwargs))

        monkeypatch.setattr(escort_module, "_find_pending_program", _fake_find_pending_program)
        monkeypatch.setattr(escort_module, "_create_confirmed_from_admin", _fake_create)
        monkeypatch.setattr(escort_module, "fetch_one", _fake_fetch_one)
        monkeypatch.setattr("modules.escort_roster.db.log_audit", _fake_log_audit)

        result = await escort_module.confirm_unmatched_confirmation_as_new(
            9, performed_by="admin_test", reason="genuinely a second duty segment",
        )

        assert result == {"queue_id": 9, "resolved_program_id": 55, "status": "confirmed_new"}
        assert len(create_calls) == 1
        assert len(audit_calls) == 1

    @pytest.mark.asyncio
    async def test_confirm_new_requires_reason(self, monkeypatch):
        import modules.escort as escort_module
        with pytest.raises(ValueError):
            await escort_module.confirm_unmatched_confirmation_as_new(
                9, performed_by="admin_test", reason="   ",
            )

    @pytest.mark.asyncio
    async def test_confirm_new_blocked_when_confident_match_now_exists(self, monkeypatch):
        """The revalidation requirement: confirm-new must never be used
        merely because the fuzzy score was low at admission time -- if a
        real match has since appeared, link-existing is the correct action."""
        import modules.escort as escort_module

        queue_row = {
            "id": 10, "status": "pending", "resolved_program_id": None,
            "source": "bridge1", "client_phone": None,
            "parsed_confirmation": json.dumps({"mother_vessel": "MV NEW"}),
        }
        conn = _FakeConn(queue_row=queue_row)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        async def _fake_find_pending_program(*a, **kw):
            return {"program_id": 99}  # a confident match now exists

        create_calls = []
        async def _fake_create(*a, **kw):
            create_calls.append((a, kw))
            return 55

        monkeypatch.setattr(escort_module, "_find_pending_program", _fake_find_pending_program)
        monkeypatch.setattr(escort_module, "_create_confirmed_from_admin", _fake_create)

        with pytest.raises(escort_module.UnmatchedConfirmationConflict):
            await escort_module.confirm_unmatched_confirmation_as_new(
                10, performed_by="admin_test", reason="a reason",
            )
        assert create_calls == [], "must never create a program when a confident match now exists"

    @pytest.mark.asyncio
    async def test_reject_requires_reason(self, monkeypatch):
        import modules.escort as escort_module
        with pytest.raises(ValueError):
            await escort_module.reject_unmatched_confirmation(1, performed_by="admin_test", reason="")

    @pytest.mark.asyncio
    async def test_reject_happy_path(self, monkeypatch):
        import modules.escort as escort_module

        queue_row = {"id": 11, "status": "pending"}
        conn = _FakeConn(queue_row=queue_row)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        result = await escort_module.reject_unmatched_confirmation(
            11, performed_by="admin_test", reason="spam / not a real order",
        )
        assert result == {"queue_id": 11, "status": "rejected"}
        assert any("SET status = " in q for q, _ in conn.executed)

    @pytest.mark.asyncio
    async def test_defer_does_not_require_reason(self, monkeypatch):
        import modules.escort as escort_module

        queue_row = {"id": 12, "status": "pending"}
        conn = _FakeConn(queue_row=queue_row)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        result = await escort_module.defer_unmatched_confirmation(12, performed_by="admin_test")
        assert result == {"queue_id": 12, "status": "deferred"}

    @pytest.mark.asyncio
    async def test_defer_blocked_on_already_terminal_item(self, monkeypatch):
        import modules.escort as escort_module

        queue_row = {"id": 13, "status": "rejected"}
        conn = _FakeConn(queue_row=queue_row)
        monkeypatch.setattr(escort_module, "db_conn", _fake_db_conn_factory(conn))

        with pytest.raises(escort_module.UnmatchedConfirmationConflict):
            await escort_module.defer_unmatched_confirmation(13, performed_by="admin_test")
