"""
Regression tests for modules.admin_commands.nl_payments::intent_employee_totals
(2026-08-20 ambiguity-refusal fix). Previously every resolution tier
(phone match, exact ILIKE name match, Bengali-transliteration/trgm fuzzy
fallback) silently collapsed 2+ matching employees to one arbitrary row
via LIMIT 1 / ORDER BY ... LIMIT 1 -- an admin asking for an ambiguous
employee's payment total got a confidently-wrong single total. Every
tier now refuses to guess and asks for a phone/employee ID instead.
"""
import pytest

from modules.admin_commands.nl_payments import intent_employee_totals

pytestmark = pytest.mark.integration


class TestAmbiguousNameRefusesToGuess:
    async def test_ambiguous_name_no_silent_total(self, clean_tables, test_db_pool):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status)
                VALUES
                    ('8801811111111', 'Karim Uddin', 'Security Guard', 'Active'),
                    ('8801822222222', 'Karim Hossain', 'Escort', 'Active')
            """)
        reply = await intent_employee_totals("Karim total", "8801700000001")
        assert "একাধিক কর্মী" in reply
        assert "৳" not in reply  # no silent total was computed

    async def test_ambiguous_phone_no_silent_total(self, clean_tables, test_db_pool):
        """Defense-in-depth: two employee rows storing the SAME real phone
        number under different literal string formats (880-form vs
        01-form -- a real data-quality pattern this codebase already
        works around elsewhere, see PHONE_NORMALIZER_CONVENTION.md) must
        also refuse rather than guess, since the query's own
        `employee_mobile = $1 OR employee_mobile = $2` matches both."""
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status)
                VALUES
                    ('8801811111111', 'Karim Uddin', 'Security Guard', 'Active'),
                    ('01811111111', 'Duplicate Row Karim', 'Escort', 'Active')
            """)
        reply = await intent_employee_totals("01811111111 total", "8801700000001")
        assert "একাধিক কর্মী" in reply


class TestUniqueMatchStillWorks:
    """Happy-path regression -- the fix must not break the unambiguous case."""

    async def test_unique_name_still_resolves(self, clean_tables, test_db_pool):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status)
                VALUES ('8801811111111', 'Karim Uddin', 'Security Guard', 'Active')
            """)
        reply = await intent_employee_totals("Karim Uddin total", "8801700000001")
        assert "একাধিক কর্মী" not in reply
        # No payment rows exist for this employee -- expect the "no records" reply,
        # not an error, proving the resolver found exactly one employee.
        assert "কোনো পেমেন্ট রেকর্ড নেই" in reply or "৳" in reply

    async def test_unique_phone_still_resolves(self, clean_tables, test_db_pool):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status)
                VALUES ('8801811111111', 'Karim Uddin', 'Security Guard', 'Active')
            """)
        reply = await intent_employee_totals("01811111111 total", "8801700000001")
        assert "একাধিক কর্মী" not in reply
        assert "কোনো পেমেন্ট রেকর্ড নেই" in reply or "৳" in reply
