"""
Dispatch Phase 2A.3 Stage 1 -- schema foundation tests. Covers the two
migrations (049: escort_changed status, 050: replacement linkage) directly
against the database, independent of any application code that will use
them in later stages.
"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.workflow


async def _insert_program(test_db_pool, **overrides):
    defaults = dict(
        mother_vessel="MV SCHEMA TEST", lighter_vessel="LT SCHEMA TEST", master_mobile="8801933333333",
        program_date=date(2026, 6, 1), shift="D", status="confirmed",
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status)
               VALUES ($1,$2,$3,$4,$5,$6)
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            defaults["program_date"], defaults["shift"], defaults["status"],
        )
    return dict(row)


class TestStatusCheckConstraint:

    async def test_all_old_statuses_accepted(self, test_db_pool):
        for status in ("draft", "confirmed", "running", "completed", "cancelled", "expired"):
            row = await _insert_program(test_db_pool, status=status)
            assert row["status"] == status

    async def test_escort_changed_accepted(self, test_db_pool):
        row = await _insert_program(test_db_pool, status="escort_changed")
        assert row["status"] == "escort_changed"

    async def test_unknown_status_rejected(self, test_db_pool):
        import asyncpg
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert_program(test_db_pool, status="not_a_real_status")


class TestReplacementLinkage:

    async def test_valid_replacement_link(self, test_db_pool):
        earlier = await _insert_program(test_db_pool, status="escort_changed")
        async with test_db_pool.acquire() as conn:
            later = await conn.fetchrow(
                """INSERT INTO wbom_escort_programs
                      (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status, replaces_program_id)
                   VALUES ('MV SCHEMA TEST','LT SCHEMA TEST','8801933333333',$1,'D','running',$2)
                   RETURNING *""",
                date(2026, 6, 5), earlier["program_id"],
            )
        assert later["replaces_program_id"] == earlier["program_id"]

    async def test_invalid_fk_rejected(self, test_db_pool):
        import asyncpg
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            async with test_db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO wbom_escort_programs
                          (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status, replaces_program_id)
                       VALUES ('MV X','LT X','8801900000000',$1,'D','running',999999)""",
                    date(2026, 6, 5),
                )

    async def test_self_reference_blocked_by_constraint(self, test_db_pool):
        import asyncpg
        row = await _insert_program(test_db_pool, status="running")
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            async with test_db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE wbom_escort_programs SET replaces_program_id = $1 WHERE program_id = $1",
                    row["program_id"],
                )

    async def test_duplicate_active_replacement_blocked(self, test_db_pool):
        """At most one non-terminal replacement per predecessor -- the
        partial unique index must reject a second 'running' row claiming
        to replace the same earlier program."""
        import asyncpg
        earlier = await _insert_program(test_db_pool, status="escort_changed")
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO wbom_escort_programs
                      (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status, replaces_program_id)
                   VALUES ('MV SCHEMA TEST','LT SCHEMA TEST','8801933333333',$1,'D','running',$2)""",
                date(2026, 6, 5), earlier["program_id"],
            )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            async with test_db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO wbom_escort_programs
                          (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status, replaces_program_id)
                       VALUES ('MV SCHEMA TEST','LT SCHEMA TEST','8801933333333',$1,'D','confirmed',$2)""",
                    date(2026, 6, 6), earlier["program_id"],
                )

    async def test_terminal_historical_replacements_coexist(self, test_db_pool):
        """Two TERMINAL rows (e.g. both later cancelled/completed) may both
        point at the same predecessor -- the partial index only restricts
        non-terminal rows."""
        earlier = await _insert_program(test_db_pool, status="escort_changed")
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO wbom_escort_programs
                      (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status, replaces_program_id)
                   VALUES ('MV SCHEMA TEST','LT SCHEMA TEST','8801933333333',$1,'D','cancelled',$2)""",
                date(2026, 6, 5), earlier["program_id"],
            )
            # second terminal row for the same predecessor -- must succeed
            await conn.execute(
                """INSERT INTO wbom_escort_programs
                      (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status, replaces_program_id)
                   VALUES ('MV SCHEMA TEST','LT SCHEMA TEST','8801933333333',$1,'D','completed',$2)""",
                date(2026, 6, 6), earlier["program_id"],
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_escort_programs WHERE replaces_program_id = $1", earlier["program_id"])
        assert count == 2
