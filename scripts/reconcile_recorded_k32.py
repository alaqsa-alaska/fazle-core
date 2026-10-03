#!/usr/bin/env python3
"""Recover K3.2 data when migration 086 is recorded but its data was restored.

This is deliberately not a migration runner.  It accepts only the exact 086
scope, preserves the existing schema_migrations row, and is safe to repeat.
Do not run it against production without the owner-approved recovery gate.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_NAME = "086_k32_recruitment_reconciliation.sql"
MIGRATION_PATH = ROOT / "db" / "migrations" / MIGRATION_NAME
_ADVISORY_LOCK_KEY = 86086


class K32RecoveryError(RuntimeError):
    """The exact recorded-but-restored K3.2 state was not proven."""


def _migration_parts() -> tuple[dict[int, tuple[str, bool]], dict[str, str], str]:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    faq_section = sql.split(
        "CREATE TEMP TABLE _k32_recruitment_governance_expected", 1
    )[0]
    faq = {
        int(row_id): (key, retain == "true")
        for row_id, key, retain in re.findall(
            r"\((\d+), '([^']+)', (true|false)\)", faq_section
        )
    }
    governance_section = sql.split(
        "INSERT INTO _k32_recruitment_governance_expected", 1
    )[1].split(";", 1)[0]
    governance = {
        key: row_id
        for row_id, key in re.findall(
            r"\('([^']+)'[^,]*, '([^']+)'\)", governance_section
        )
    }
    begin_at = sql.upper().find("BEGIN;")
    commit_at = sql.upper().rfind("COMMIT;")
    if begin_at < 0 or commit_at <= begin_at:
        raise K32RecoveryError("migration 086 is not transactionally bounded")
    body = sql[begin_at + len("BEGIN;"):commit_at].strip()
    return faq, governance, body


FAQ_EXPECTED, GOVERNANCE_EXPECTED, _K32_SQL_BODY = _migration_parts()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise K32RecoveryError(message)


async def reconcile_recorded_k32(conn: asyncpg.Connection) -> dict[str, Any]:
    """Reapply only migration 086's data changes after a verified rollback.

    The caller must provide an existing asyncpg connection.  The procedure
    takes a transaction-scoped advisory lock, locks the exact expected rows,
    and never inserts/deletes from schema_migrations.
    """
    faq_ids = list(FAQ_EXPECTED)
    governance_ids = list(GOVERNANCE_EXPECTED.values())
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _ADVISORY_LOCK_KEY)
        recorded = await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE name = $1",
            MIGRATION_NAME,
        )
        _require(recorded == 1, "migration 086 must already have exactly one history row")

        faq_rows = await conn.fetch(
            """
            SELECT id, key, category, is_active
            FROM fazle_knowledge_base
            WHERE id = ANY($1::bigint[])
            ORDER BY id
            FOR UPDATE
            """,
            faq_ids,
        )
        _require(len(faq_rows) == len(FAQ_EXPECTED), "expected 66 FAQ identities are not present")
        for row in faq_rows:
            expected_key, _retain = FAQ_EXPECTED[int(row["id"])]
            _require(
                row["category"] == "recruitment" and row["key"] == expected_key,
                f"unexpected FAQ identity for id={row['id']}",
            )

        governance_rows = await conn.fetch(
            """
            SELECT id, fact_key, category, status, deprecated_at, deprecation_reason
            FROM fazle_knowledge_governance
            WHERE id = ANY($1::uuid[])
            ORDER BY id
            FOR UPDATE
            """,
            governance_ids,
        )
        _require(
            len(governance_rows) == len(GOVERNANCE_EXPECTED),
            "expected 9 governance identities are not present",
        )
        expected_by_id = {row_id: key for key, row_id in GOVERNANCE_EXPECTED.items()}
        for row in governance_rows:
            row_id = str(row["id"])
            _require(
                row["category"] == "recruitment"
                and row["fact_key"] == expected_by_id[row_id],
                f"unexpected governance identity for id={row_id}",
            )

        policy_rows = [row for row in faq_rows if not FAQ_EXPECTED[int(row["id"])][1]]
        greeting = next(row for row in faq_rows if int(row["id"]) == 117)
        policy_active = sum(bool(row["is_active"]) for row in policy_rows)
        _require(bool(greeting["is_active"]), "greeting row 117 must remain active")

        active_governance = sum(row["status"] == "active" for row in governance_rows)
        deprecated_governance = sum(
            row["status"] == "deprecated" and row["deprecated_at"] is not None
            for row in governance_rows
        )
        rolled_back = policy_active == 65 and active_governance == 9
        already_reconciled = policy_active == 0 and deprecated_governance == 9
        _require(
            rolled_back or already_reconciled,
            "K3.2 rows are partially or unexpectedly reconciled; stopping",
        )

        if already_reconciled:
            return {
                "status": "already_reconciled",
                "migration_recorded": True,
                "faq_policy_active": policy_active,
                "governance_active": active_governance,
            }

        await conn.execute(_K32_SQL_BODY)

        policy_active_after = await conn.fetchval(
            """
            SELECT count(*)
            FROM fazle_knowledge_base
            WHERE id = ANY($1::bigint[]) AND is_active IS TRUE
            """,
            [row_id for row_id, (_key, retain) in FAQ_EXPECTED.items() if not retain],
        )
        governance_active_after = await conn.fetchval(
            """
            SELECT count(*)
            FROM fazle_knowledge_governance
            WHERE id = ANY($1::uuid[]) AND status = 'active'
            """,
            governance_ids,
        )
        governance_deprecated_after = await conn.fetchval(
            """
            SELECT count(*)
            FROM fazle_knowledge_governance
            WHERE id = ANY($1::uuid[])
              AND status = 'deprecated' AND deprecated_at IS NOT NULL
            """,
            governance_ids,
        )
        _require(policy_active_after == 0, "recovery did not suppress exactly 65 policy FAQs")
        _require(
            governance_active_after == 0 and governance_deprecated_after == 9,
            "recovery did not deprecate exactly 9 governance rows",
        )
        return {
            "status": "reconciled",
            "migration_recorded": True,
            "faq_policy_active": policy_active_after,
            "governance_active": governance_active_after,
            "governance_deprecated": governance_deprecated_after,
        }


async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(database_url)
    try:
        result = await reconcile_recorded_k32(conn)
        print(result)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
