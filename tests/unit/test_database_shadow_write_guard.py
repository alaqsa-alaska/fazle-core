"""Unit tests — app/database.py shadow-mode write guard (Phase B Stage 4,
Section 2, 2026-07-19).

A hard runtime assertion, not just an architectural convention: while
shadow_mode_scope() is active, any write statement targeting a table
outside the conversation-memory engine's own tables must raise
immediately. Pure-function tests against the regex/contextvar guard
directly -- no DB connection needed, since the guard fires before any
network call is made.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_guard_inactive_by_default():
    import app.database as db
    # Outside any scope, nothing is blocked -- normal app behavior.
    db._check_shadow_write_guard("INSERT INTO wbom_escort_programs (program_id) VALUES (1)")


@pytest.mark.asyncio
async def test_business_table_insert_blocked_inside_scope():
    import app.database as db
    async with db.shadow_mode_scope():
        with pytest.raises(db.ShadowModeWriteViolation):
            db._check_shadow_write_guard("INSERT INTO wbom_escort_programs (program_id) VALUES (1)")


@pytest.mark.asyncio
async def test_business_table_update_blocked_inside_scope():
    import app.database as db
    async with db.shadow_mode_scope():
        with pytest.raises(db.ShadowModeWriteViolation):
            db._check_shadow_write_guard("UPDATE wbom_escort_programs SET status='confirmed' WHERE program_id=1")


@pytest.mark.asyncio
async def test_business_table_delete_blocked_inside_scope():
    import app.database as db
    async with db.shadow_mode_scope():
        with pytest.raises(db.ShadowModeWriteViolation):
            db._check_shadow_write_guard("DELETE FROM escort_roster_entries WHERE program_id=1")


@pytest.mark.asyncio
async def test_select_on_business_table_still_allowed_inside_scope():
    """Existing-draft awareness (Part 7) needs read-only access to
    wbom_escort_programs even in shadow mode -- only writes are blocked."""
    import app.database as db
    async with db.shadow_mode_scope():
        db._check_shadow_write_guard("SELECT * FROM wbom_escort_programs WHERE status='draft'")


@pytest.mark.asyncio
async def test_conversation_memory_own_tables_still_writable_inside_scope():
    import app.database as db
    async with db.shadow_mode_scope():
        db._check_shadow_write_guard("INSERT INTO fazle_conversation_threads (id) VALUES (1)")
        db._check_shadow_write_guard("UPDATE fazle_conversation_threads SET state='partial' WHERE id=1")
        db._check_shadow_write_guard("INSERT INTO processed_conversation_messages (message_id) VALUES ('x')")
        db._check_shadow_write_guard("INSERT INTO fazle_conversation_shadow_comparisons (id) VALUES (1)")


@pytest.mark.asyncio
async def test_guard_deactivates_after_scope_exits():
    import app.database as db
    async with db.shadow_mode_scope():
        pass
    # Outside the scope again -- no longer guarded.
    db._check_shadow_write_guard("INSERT INTO wbom_escort_programs (program_id) VALUES (1)")


@pytest.mark.asyncio
async def test_guard_deactivates_even_after_exception_inside_scope():
    import app.database as db
    with pytest.raises(RuntimeError, match="boom"):
        async with db.shadow_mode_scope():
            raise RuntimeError("boom")
    db._check_shadow_write_guard("INSERT INTO wbom_escort_programs (program_id) VALUES (1)")


@pytest.mark.asyncio
async def test_shadow_hook_swallows_write_guard_violation_safely(monkeypatch):
    """Fail-safe requirement (Stage 4, Section 3): if shadow-mode code
    were ever tricked into attempting a forbidden write, the violation
    must be caught by the existing broad exception handler and never
    propagate to the message_router caller -- same guarantee as any other
    internal shadow-mode failure."""
    from app.config import get_settings
    from modules.conversation_memory import thread_reconstruction as tr
    import app.database as db

    monkeypatch.setenv("CONVERSATION_MEMORY_ENABLED", "true")
    monkeypatch.setenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", "true")
    get_settings.cache_clear()

    async def _attempt_forbidden_write(**kwargs):
        await db.execute("INSERT INTO wbom_escort_programs (program_id) VALUES (999999)")

    monkeypatch.setattr(tr, "_observe", _attempt_forbidden_write)

    try:
        # Must not raise -- the ShadowModeWriteViolation is caught by
        # shadow_observe_escort_client_message()'s own except Exception.
        await tr.shadow_observe_escort_client_message(
            text="irrelevant", sender_phone="01670535255", source="bridge2",
            message_id="GUARD-TEST-1", sender_role="client_escort_buyer",
        )
    finally:
        monkeypatch.delenv("CONVERSATION_MEMORY_ENABLED", raising=False)
        monkeypatch.delenv("WHATSAPP_THREAD_RECONSTRUCTION_ENABLED", raising=False)
        get_settings.cache_clear()
