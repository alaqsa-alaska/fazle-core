"""
Phase 2B -- concurrency tests for modules.escort_roster.db
.create_roster_entry() (Writer 3, the manual "add roster entry" admin
tool). Two real, independently-confirmed races were fixed here:

1. program_id generation (SELECT MAX+1 then INSERT) -- reproduced: 9/10
   concurrent calls hit a live UniqueViolationError on the identical
   computed program_id.
2. _find_duplicate_roster_entry()'s check-then-act had no DB-level
   constraint backing it -- concurrent identical-vessel calls could both
   pass the check before either committed.

Fixed with a single transaction-scoped advisory lock
(pg_advisory_xact_lock) around the whole check+generate+insert sequence,
serializing all concurrent callers of this one (low-frequency, admin-only)
function -- no schema change, no new unique constraint. See
knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md
and management_decisions.md "Phase 2B" for the full reproduction evidence
and decision record.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

pytestmark = pytest.mark.workflow


class TestCreateRosterEntryConcurrency:

    async def test_10_concurrent_distinct_vessels_no_collision(self, test_db_pool):
        """10 concurrent calls with different vessel data (duplicate check
        never fires, start_date=None short-circuits it) must all succeed
        with 10 distinct program_ids -- proves the ID-generation race is
        fixed."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import create_roster_entry

        async def _call(i: int):
            return await asyncio.wait_for(
                create_roster_entry({
                    "mother_vessel": f"MV CONC {i}",
                    "lighter_vessel": f"LT CONC {i}",
                    "start_date": None,
                }, actor=f"c{i}"),
                timeout=10.0,
            )

        results = await asyncio.gather(*[_call(i) for i in range(10)], return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, f"unexpected errors: {errors}"

        pids = [r["program_id"] for r in results]
        assert len(set(pids)) == 10, f"expected 10 distinct program_ids, got {pids}"

    async def test_10_concurrent_identical_vessel_exactly_one_row(self, test_db_pool):
        """10 concurrent calls with IDENTICAL vessel+lighter+date+escort
        must result in exactly 1 row -- 9 correctly blocked with the
        duplicate-roster ValueError, not silently creating duplicates."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import create_roster_entry

        async def _call(i: int):
            try:
                return await asyncio.wait_for(
                    create_roster_entry({
                        "mother_vessel": "MV DUPE CONC",
                        "lighter_vessel": "LT DUPE CONC",
                        "start_date": date(2026, 6, 1),
                        "escort_name": "Same Guard",
                    }, actor=f"d{i}"),
                    timeout=10.0,
                )
            except ValueError as e:
                return {"blocked": str(e)}

        results = await asyncio.gather(*[_call(i) for i in range(10)])
        n_ok = sum(1 for r in results if r.get("id"))
        n_blocked = sum(1 for r in results if "Duplicate roster blocked" in (r.get("blocked") or ""))
        assert n_ok == 1, f"expected exactly 1 success, got {n_ok}"
        assert n_blocked == 9, f"expected 9 blocked, got {n_blocked}"

        async with test_db_pool.acquire() as conn:
            row_count = await conn.fetchval(
                "SELECT COUNT(*) FROM escort_roster_entries WHERE mother_vessel='MV DUPE CONC'"
            )
        assert row_count == 1

    async def test_idempotent_no_deadlock_no_hang(self, test_db_pool):
        """Sequential repeated calls for different vessels must all
        succeed quickly -- the advisory lock must not leak/hold across
        calls (each is its own transaction, lock auto-released on
        commit)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import create_roster_entry

        for i in range(5):
            result = await asyncio.wait_for(
                create_roster_entry({
                    "mother_vessel": f"MV SEQ {i}",
                    "lighter_vessel": f"LT SEQ {i}",
                    "start_date": None,
                }, actor="seq"),
                timeout=5.0,
            )
            assert result["id"]

    async def test_rollback_on_duplicate_leaves_no_partial_row(self, test_db_pool):
        """A blocked (duplicate) attempt must not leave any partial row --
        proves the whole check+insert sequence is genuinely one
        transaction."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import create_roster_entry

        await create_roster_entry({
            "mother_vessel": "MV ROLLBACK TEST",
            "lighter_vessel": "LT ROLLBACK TEST",
            "start_date": date(2026, 6, 15),
        }, actor="first")

        with pytest.raises(ValueError, match="Duplicate roster blocked"):
            await create_roster_entry({
                "mother_vessel": "MV ROLLBACK TEST",
                "lighter_vessel": "LT ROLLBACK TEST",
                "start_date": date(2026, 6, 15),
            }, actor="second")

        async with test_db_pool.acquire() as conn:
            row_count = await conn.fetchval(
                "SELECT COUNT(*) FROM escort_roster_entries WHERE mother_vessel='MV ROLLBACK TEST'"
            )
        assert row_count == 1
