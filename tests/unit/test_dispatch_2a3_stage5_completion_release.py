"""
Dispatch Phase 2A.3 Stage 5 -- manual completion and release-slip review.

Manual completion reuses modules.escort_lifecycle.close_program() directly
(the same idempotent path [RELEASE CONFIRMED] messages use). Release-slip
review is built over the existing escort_slip_extractions /
escort_release_matches tables and the existing
modules.escort_roster.extractor.find_slip_matches() weighted scorer.
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


async def _insert_program(test_db_pool, **overrides):
    defaults = dict(
        mother_vessel="MV STAGE5 TEST", lighter_vessel="LT STAGE5 TEST", master_mobile="8801944444444",
        destination="Narayanganj", escort_name="Karim Escort", escort_mobile="8801811111111",
        escort_employee_id=None, program_date=date(2026, 6, 1), start_date=date(2026, 6, 1),
        shift="D", status="confirmed",
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, destination, escort_name, escort_mobile,
                   escort_employee_id, program_date, start_date, shift, status)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            defaults["destination"], defaults["escort_name"], defaults["escort_mobile"],
            defaults["escort_employee_id"], defaults["program_date"], defaults["start_date"],
            defaults["shift"], defaults["status"],
        )
    return dict(row)


@pytest_asyncio.fixture
async def escort_employee(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801811111111', 'Karim Escort', 'Escort', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


class TestManualCompletion:

    async def test_completes_confirmed_program(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        result = await manually_complete_program(
            prog["program_id"], end_date=date(2026, 6, 5), end_shift="N",
            release_point="Narayanganj Ghat", reason="manual close-out", expected_status=None,
            performed_by="admin_tester",
        )
        assert result["status"] == "completed"
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, end_date, end_shift FROM wbom_escort_programs WHERE program_id=$1",
                prog["program_id"])
        assert row["status"] == "completed"
        assert row["end_date"] == date(2026, 6, 5)
        assert row["end_shift"] == "N"

    async def test_already_terminal_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program, DispatchAssignmentError
        prog = await _insert_program(
            test_db_pool, escort_employee_id=escort_employee["employee_id"], status="completed")
        with pytest.raises(DispatchAssignmentError) as exc:
            await manually_complete_program(
                prog["program_id"], end_date=date(2026, 6, 5), end_shift="N",
                release_point=None, reason="dup", expected_status=None, performed_by="admin_tester",
            )
        assert exc.value.code == "already_terminal"

    async def test_no_duplicate_completion_on_second_call(self, test_db_pool, escort_employee):
        """Once completed via this path, a second manual-completion attempt
        must be rejected outright, not silently re-close."""
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        await manually_complete_program(
            prog["program_id"], end_date=date(2026, 6, 5), end_shift="N",
            release_point=None, reason="first close", expected_status=None, performed_by="admin_tester",
        )
        with pytest.raises(DispatchAssignmentError) as exc:
            await manually_complete_program(
                prog["program_id"], end_date=date(2026, 6, 6), end_shift="D",
                release_point=None, reason="second close", expected_status=None, performed_by="admin_tester",
            )
        assert exc.value.code == "already_terminal"

    async def test_reason_required(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        with pytest.raises(DispatchAssignmentError) as exc:
            await manually_complete_program(
                prog["program_id"], end_date=date(2026, 6, 5), end_shift="N",
                release_point=None, reason="   ", expected_status=None, performed_by="admin_tester",
            )
        assert exc.value.code == "reason_required"

    async def test_missing_end_shift_rejected_not_fabricated(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        with pytest.raises(DispatchAssignmentError) as exc:
            await manually_complete_program(
                prog["program_id"], end_date=date(2026, 6, 5), end_shift="X",
                release_point=None, reason="close", expected_status=None, performed_by="admin_tester",
            )
        assert exc.value.code == "end_shift_required"

    async def test_end_before_start_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program, DispatchAssignmentError
        prog = await _insert_program(
            test_db_pool, escort_employee_id=escort_employee["employee_id"],
            program_date=date(2026, 6, 10), start_date=date(2026, 6, 10), shift="D")
        with pytest.raises(DispatchAssignmentError) as exc:
            await manually_complete_program(
                prog["program_id"], end_date=date(2026, 6, 5), end_shift="N",
                release_point=None, reason="close", expected_status=None, performed_by="admin_tester",
            )
        assert exc.value.code == "invalid_window"

    async def test_stale_expected_status_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"], status="running")
        with pytest.raises(DispatchAssignmentError) as exc:
            await manually_complete_program(
                prog["program_id"], end_date=date(2026, 6, 5), end_shift="N",
                release_point=None, reason="close", expected_status="confirmed", performed_by="admin_tester",
            )
        assert exc.value.code == "stale_status"

    async def test_completion_syncs_roster_and_audit(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import manually_complete_program
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO escort_roster_entries
                       (program_id, mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
                        start_date, start_shift, roster_status)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'confirmed')""",
                prog["program_id"], prog["mother_vessel"], prog["lighter_vessel"], prog["master_mobile"],
                prog["escort_name"], prog["escort_mobile"], prog["program_date"], prog["shift"],
            )
        await manually_complete_program(
            prog["program_id"], end_date=date(2026, 6, 5), end_shift="N",
            release_point="Narayanganj Ghat", reason="close-out", expected_status=None,
            performed_by="admin_tester",
        )
        async with test_db_pool.acquire() as conn:
            roster = await conn.fetchrow(
                "SELECT roster_status FROM escort_roster_entries WHERE program_id=$1", prog["program_id"])
            audit = await conn.fetchrow(
                "SELECT * FROM escort_roster_audit_logs WHERE program_id=$1 AND action='dispatch_manual_completion'",
                prog["program_id"])
        assert roster["roster_status"] == "completed"
        assert audit is not None
        assert audit["performed_by"] == "admin_tester"


async def _insert_extraction(test_db_pool, **overrides):
    defaults = dict(
        source_file="/tmp/slip1.jpg", document_type="printed_template_slip",
        mother_vessel="MV STAGE5 TEST", lighter_vessel="LT STAGE5 TEST", master_mobile="8801944444444",
        escort_name="Karim Escort", escort_mobile="8801811111111",
        start_date="01-06-2026", completion_date="05-06-2026", release_place="Narayanganj Ghat",
        confidence=0.85, raw_text="raw ocr text",
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO escort_slip_extractions
                   (source_file, document_type, mother_vessel, lighter_vessel, master_mobile,
                    escort_name, escort_mobile, start_date, completion_date, release_place,
                    confidence, raw_text)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
               RETURNING *""",
            defaults["source_file"], defaults["document_type"], defaults["mother_vessel"],
            defaults["lighter_vessel"], defaults["master_mobile"], defaults["escort_name"],
            defaults["escort_mobile"], defaults["start_date"], defaults["completion_date"],
            defaults["release_place"], defaults["confidence"], defaults["raw_text"],
        )
    return dict(row)


class TestReleaseSlipReview:

    async def test_pending_queue_lists_unreviewed_extraction(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_pending_release_slips
        extraction = await _insert_extraction(test_db_pool)
        result = await list_pending_release_slips()
        ids = [it["id"] for it in result["items"]]
        assert extraction["id"] in ids

    async def test_candidates_returns_weighted_matches(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import get_release_slip_candidates
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        extraction = await _insert_extraction(test_db_pool)
        result = await get_release_slip_candidates(extraction["id"], top_n=5)
        candidate_ids = [c["program_id"] for c in result["candidates"]]
        assert prog["program_id"] in candidate_ids

    async def test_candidates_extraction_not_found(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import get_release_slip_candidates, DispatchAssignmentError
        with pytest.raises(DispatchAssignmentError) as exc:
            await get_release_slip_candidates(999999, top_n=5)
        assert exc.value.code == "extraction_not_found"

    async def test_approve_completes_program_and_writes_match_row(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import approve_release_slip
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        extraction = await _insert_extraction(test_db_pool)
        result = await approve_release_slip(
            extraction["id"], prog["program_id"], end_shift="N", performed_by="admin_tester")
        assert result["status"] == "completed"
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, end_date FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
            match = await conn.fetchrow(
                "SELECT * FROM escort_release_matches WHERE extraction_id=$1", extraction["id"])
        assert row["status"] == "completed"
        assert row["end_date"] == date(2026, 6, 5)
        assert match is not None
        assert match["admin_action"] == "keep"
        assert match["program_id"] == prog["program_id"]

    async def test_approve_already_reviewed_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import approve_release_slip, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        extraction = await _insert_extraction(test_db_pool)
        await approve_release_slip(extraction["id"], prog["program_id"], end_shift="N", performed_by="admin_tester")
        prog2 = await _insert_program(
            test_db_pool, escort_employee_id=escort_employee["employee_id"], status="confirmed")
        with pytest.raises(DispatchAssignmentError) as exc:
            await approve_release_slip(
                extraction["id"], prog2["program_id"], end_shift="N", performed_by="admin_tester")
        assert exc.value.code == "already_reviewed"

    async def test_approve_program_already_terminal_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import approve_release_slip, DispatchAssignmentError
        prog = await _insert_program(
            test_db_pool, escort_employee_id=escort_employee["employee_id"], status="completed")
        extraction = await _insert_extraction(test_db_pool)
        with pytest.raises(DispatchAssignmentError) as exc:
            await approve_release_slip(
                extraction["id"], prog["program_id"], end_shift="N", performed_by="admin_tester")
        assert exc.value.code == "already_terminal"

    async def test_approve_missing_end_shift_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import approve_release_slip, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        extraction = await _insert_extraction(test_db_pool)
        with pytest.raises(DispatchAssignmentError) as exc:
            await approve_release_slip(
                extraction["id"], prog["program_id"], end_shift="X", performed_by="admin_tester")
        assert exc.value.code == "end_shift_required"

    async def test_approve_invalid_completion_date_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import approve_release_slip, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        extraction = await _insert_extraction(test_db_pool, completion_date=None)
        with pytest.raises(DispatchAssignmentError) as exc:
            await approve_release_slip(
                extraction["id"], prog["program_id"], end_shift="N", performed_by="admin_tester")
        assert exc.value.code == "invalid_completion_date"

    async def test_approve_parses_ddmmyyyy_completion_date(self, test_db_pool, escort_employee):
        """Regression guard: modules.escort_slip_extractor stores
        completion_date as DD-MM-YYYY text, not ISO -- this must parse
        that format correctly, not reject every real extraction."""
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import approve_release_slip
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        extraction = await _insert_extraction(test_db_pool, completion_date="25-12-2026")
        result = await approve_release_slip(
            extraction["id"], prog["program_id"], end_shift="D", performed_by="admin_tester")
        assert result["end_date"] == "2026-12-25"

    async def test_approve_extraction_not_found(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import approve_release_slip, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee_id=escort_employee["employee_id"])
        with pytest.raises(DispatchAssignmentError) as exc:
            await approve_release_slip(999999, prog["program_id"], end_shift="N", performed_by="admin_tester")
        assert exc.value.code == "extraction_not_found"


class TestIncompleteDraftsListing:

    async def test_lists_ttl_eligible_draft(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_incomplete_drafts
        prog = await _insert_program(
            test_db_pool, status="draft", escort_name="", escort_mobile="", escort_employee_id=None)
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO escort_roster_entries
                       (program_id, mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
                        start_date, start_shift, roster_status)
                   VALUES ($1,$2,$3,$4,'','', $5,$6,'draft')""",
                prog["program_id"], prog["mother_vessel"], prog["lighter_vessel"], prog["master_mobile"],
                prog["program_date"], prog["shift"],
            )
        result = await list_incomplete_drafts()
        ids = [it["program_id"] for it in result["items"]]
        assert prog["program_id"] in ids

    async def test_excludes_drafts_with_escort_identity(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_incomplete_drafts
        prog = await _insert_program(
            test_db_pool, status="draft", escort_name="Someone", escort_mobile="", escort_employee_id=None)
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO escort_roster_entries
                       (program_id, mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
                        start_date, start_shift, roster_status)
                   VALUES ($1,$2,$3,$4,'Someone','', $5,$6,'draft')""",
                prog["program_id"], prog["mother_vessel"], prog["lighter_vessel"], prog["master_mobile"],
                prog["program_date"], prog["shift"],
            )
        result = await list_incomplete_drafts()
        ids = [it["program_id"] for it in result["items"]]
        assert prog["program_id"] not in ids
