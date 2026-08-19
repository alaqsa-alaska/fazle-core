"""
Stale-session expiry — modules.employee_verification.get_verification_session()

2026-08-19 (HERMES_SYSTEM_MESSAGE_ROOTCAUSE_AGENTIC_BEHAVIOR_AUDIT_2026-08-19.md,
Phase 2 of the same-day follow-up plan): this module had the identical
no-TTL bug modules.employee_conversation.get_conversation_session() was
fixed for earlier the same day (Priority 1) -- a session left open at
STEP_SELFIE/STEP_SLIP/STEP_METHOD (never reaching STEP_DONE/'rejected')
used to be resumed indefinitely regardless of age, so a stale session
could silently absorb a later, unrelated message.

No existing dedicated test file covered this module's own session logic
before this file -- confirmed via repo-wide search; the only prior
reference was a message_router-level mock, not a real test.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestStaleVerificationSessionExpiry:
    async def test_stale_selfie_session_does_not_hijack_unrelated_message(
        self, test_db_pool, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_verification import (
            advance_verification,
            get_verification_session,
            start_advance_verification,
        )

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Start a verification -- leaves the session at STEP_SELFIE waiting
        # for a selfie image.
        await start_advance_verification(phone, "bridge1", emp_id)
        session = await get_verification_session(phone)
        assert session is not None
        assert session["step"] == "pending_selfie"
        session_id = session["session_id"]

        # Backdate it past SESSION_TTL_HOURS (24h).
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE fazle_draft_replies SET created_at = NOW() - INTERVAL '25 hours' WHERE id = $1",
                session_id,
            )

        # An unrelated later message must NOT be absorbed as a (failed)
        # selfie answer -- with the session gone, advance_verification()
        # has no session and no other trigger, so it returns nothing.
        reply, admin_note = await advance_verification(phone, "Ashbo, ektu pore", "bridge2", emp_id)
        assert "সেলফি" not in reply
        assert reply == ""
        assert admin_note is None

        # The stale session itself must be closed, not just ignored.
        async with test_db_pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM fazle_draft_replies WHERE id = $1", session_id
            )
        assert status == "rejected"

    async def test_get_verification_session_returns_none_for_stale_session(
        self, test_db_pool, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_verification import (
            get_verification_session,
            start_advance_verification,
        )

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        await start_advance_verification(phone, "bridge1", emp_id)
        session = await get_verification_session(phone)
        session_id = session["session_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE fazle_draft_replies SET created_at = NOW() - INTERVAL '24 hours 1 minute' WHERE id = $1",
                session_id,
            )

        assert await get_verification_session(phone) is None

    async def test_fresh_session_within_ttl_still_resumes_normally(
        self, test_db_pool, seed_employee
    ):
        """Non-regression: a session well within SESSION_TTL_HOURS must
        continue to resume exactly as before."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_verification import (
            advance_verification,
            start_advance_verification,
        )

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        await start_advance_verification(phone, "bridge1", emp_id)
        reply, admin_note = await advance_verification(phone, "not an image", "bridge1", emp_id)
        assert "সেলফি" in reply
        assert admin_note is None

    async def test_session_just_under_ttl_still_active(
        self, test_db_pool, seed_employee
    ):
        """Boundary check: a session created just under SESSION_TTL_HOURS
        ago must still be treated as active, not expired."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_verification import (
            get_verification_session,
            start_advance_verification,
        )

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        await start_advance_verification(phone, "bridge1", emp_id)
        session = await get_verification_session(phone)
        session_id = session["session_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE fazle_draft_replies SET created_at = NOW() - INTERVAL '23 hours 59 minutes' WHERE id = $1",
                session_id,
            )

        session = await get_verification_session(phone)
        assert session is not None
        assert session["step"] == "pending_selfie"
