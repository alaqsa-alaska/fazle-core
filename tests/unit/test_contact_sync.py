"""
modules/contact_sync — regression tests, added 2026-07-22 alongside the
deployment-readiness audit's two required fixes:

1. A failed/empty SQLite read must never be treated the same as a genuine
   "every contact was deleted" snapshot (previously: 3 consecutive read
   failures for one bridge would hard-delete every contact tracked there).
2. A hard-delete triggered by one bridge's absence count must not remove a
   contact that's still verifiably present via a different bridge.

Also covers the previously-untested core soft-delete/reactivation/
idempotency behavior this module has had since it was written, and
documents (without fixing — out of this pass's scope) the pre-existing
"longest name wins" rename limitation found during the same audit.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.database import execute, fetch_val, fetch_one


@pytest.fixture(autouse=True)
async def _ensure_wbom_contacts_columns():
    """Pre-existing, unrelated test-environment gap (not introduced by this
    change, not fixed here): migration 029's `CREATE TABLE IF NOT EXISTS
    public.wbom_contacts (...)` is the only migration that creates this
    table and defines these columns -- yet on a fresh database built from
    db/migrations/*.sql in order, several of its later-listed columns
    (personality_hint onward) are missing from the live table (confirmed by
    direct query; root cause not investigated, out of this task's scope,
    unrelated to modules/contact_sync itself). Worked around the same way
    the wbom_candidates/003b gap was handled in test_route_b_handoff.py --
    this file's tests genuinely need these columns to exist."""
    for stmt in (
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS personality_hint varchar(200) DEFAULT ''",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS interaction_count integer DEFAULT 0",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS interest_level varchar(20) DEFAULT 'unknown'",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS last_seen timestamptz DEFAULT now()",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS keep_reason text",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS triage_at timestamptz DEFAULT now()",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS keep_tier text",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS marked_for_delete_at timestamptz",
        "ALTER TABLE wbom_contacts ADD COLUMN IF NOT EXISTS is_protected boolean DEFAULT false",
    ):
        await execute(stmt)


def _phone() -> str:
    return "88018" + uuid.uuid4().hex[:8]


async def _cleanup(phone: str) -> None:
    await execute("DELETE FROM wbom_contacts WHERE whatsapp_number = $1", phone)
    await execute("DELETE FROM fazle_contact_presence WHERE phone = $1", phone)
    await execute("DELETE FROM fazle_contact_hard_delete_log WHERE phone = $1", phone)
    await execute("DELETE FROM fazle_draft_replies WHERE recipient = $1", phone)


async def _seed_contact(phone: str, name: str, *, is_protected: bool = False) -> None:
    await execute(
        """INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active, is_protected, last_seen, updated_at)
           VALUES ($1, $2, 'whatsapp', true, $3, NOW(), NOW())
           ON CONFLICT (whatsapp_number, platform) DO UPDATE
               SET display_name = $2, is_active = true, is_protected = $3, marked_for_delete_at = NULL""",
        phone, name, is_protected,
    )


async def _seed_presence(phone: str, bridge: str, consecutive_absences: int) -> None:
    await execute(
        """INSERT INTO fazle_contact_presence (phone, bridge, consecutive_absences, last_present_at)
           VALUES ($1, $2, $3, NOW())
           ON CONFLICT (phone, bridge) DO UPDATE SET consecutive_absences = $3""",
        phone, bridge, consecutive_absences,
    )


# ── Fix 1: empty/failed-snapshot guard ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_failure_skips_absence_processing_and_alerts(monkeypatch):
    """A ContactReadError (simulating a locked file, offline phone, etc.)
    must never reach _handle_missing_contacts() at all."""
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await _seed_contact(phone, "Rahim Uddin")
        await _seed_presence(phone, "bridge1", 2)  # one more miss would hard-delete

        def _raise(*_a, **_kw):
            raise cs.ContactReadError("database is locked")

        monkeypatch.setattr(cs, "_read_bridge_contacts", _raise)
        alert_mock = AsyncMock()
        monkeypatch.setattr(cs, "_alert_sync_failure", alert_mock)

        result = await cs.sync_bridge({"bridge": "bridge1", "whatsapp_db": "irrelevant", "number": "8801000000000"})

        assert result["skipped_absence_processing"] is True
        assert result["soft_deleted"] == 0
        assert result["hard_deleted"] == 0
        alert_mock.assert_awaited_once()

        # The contact must be completely untouched -- still active, absence count unchanged.
        row = await fetch_one("SELECT is_active FROM wbom_contacts WHERE whatsapp_number=$1", phone)
        assert row["is_active"] is True
        count = await fetch_val(
            "SELECT consecutive_absences FROM fazle_contact_presence WHERE phone=$1 AND bridge='bridge1'", phone,
        )
        assert count == 2  # unchanged, not bumped to 3 and hard-deleted
    finally:
        await _cleanup(phone)


@pytest.mark.asyncio
async def test_empty_read_after_prior_history_skips_absence_processing(monkeypatch):
    """A successful read that returns zero contacts, despite this bridge
    having previously tracked some, is treated as suspicious (not a real
    mass-deletion event) -- this is the exact scenario from the audit."""
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await _seed_contact(phone, "Karim Sarkar")
        await _seed_presence(phone, "bridge2", 2)

        monkeypatch.setattr(cs, "_read_bridge_contacts", lambda *_a, **_kw: [])
        alert_mock = AsyncMock()
        monkeypatch.setattr(cs, "_alert_sync_failure", alert_mock)

        result = await cs.sync_bridge({"bridge": "bridge2", "whatsapp_db": "irrelevant", "number": "8801000000000"})

        assert result["skipped_absence_processing"] is True
        alert_mock.assert_awaited_once()
        count = await fetch_val(
            "SELECT consecutive_absences FROM fazle_contact_presence WHERE phone=$1 AND bridge='bridge2'", phone,
        )
        assert count == 2  # not bumped
    finally:
        await _cleanup(phone)


@pytest.mark.asyncio
async def test_empty_read_with_no_prior_history_proceeds_normally(monkeypatch):
    """A bridge with NO tracking history at all (e.g. brand new) returning
    zero contacts is not suspicious -- nothing to protect, should proceed
    (not trigger the guard, since there's no prior state to contradict)."""
    import modules.contact_sync as cs

    bridge = f"bridge-test-{uuid.uuid4().hex[:8]}"  # guaranteed no prior history
    monkeypatch.setattr(cs, "_read_bridge_contacts", lambda *_a, **_kw: [])
    alert_mock = AsyncMock()
    monkeypatch.setattr(cs, "_alert_sync_failure", alert_mock)

    result = await cs.sync_bridge({"bridge": bridge, "whatsapp_db": "irrelevant", "number": "8801000000000"})

    assert result.get("skipped_absence_processing") is not True
    alert_mock.assert_not_called()


# ── Fix 2: cross-bridge hard-delete protection ──────────────────────────────────

@pytest.mark.asyncio
async def test_hard_delete_skipped_when_active_on_other_bridge(monkeypatch):
    """Contact absent 3x on bridge2, but bridge1 still confirms it present
    (consecutive_absences=0) -- must NOT be hard-deleted."""
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await _seed_contact(phone, "Nasrin Akter")
        await _seed_presence(phone, "bridge1", 0)   # still present there
        await _seed_presence(phone, "bridge2", cs.HARD_DELETE_AFTER_ABSENCES - 1)  # one more miss hits threshold

        result = await cs._handle_missing_contacts("bridge2", current_phones=set())

        assert result["hard_deleted"] == 0
        row = await fetch_one("SELECT whatsapp_number FROM wbom_contacts WHERE whatsapp_number=$1", phone)
        assert row is not None  # still exists
        log_row = await fetch_one("SELECT id FROM fazle_contact_hard_delete_log WHERE phone=$1", phone)
        assert log_row is None  # never logged as hard-deleted
    finally:
        await _cleanup(phone)


@pytest.mark.asyncio
async def test_hard_delete_proceeds_when_absent_on_all_bridges():
    """Contact absent on every tracked bridge, reaching the threshold on
    all of them -- hard-delete should proceed as before."""
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await _seed_contact(phone, "Jamal Hossain")
        await _seed_presence(phone, "bridge1", cs.HARD_DELETE_AFTER_ABSENCES - 1)
        await _seed_presence(phone, "bridge2", cs.HARD_DELETE_AFTER_ABSENCES - 1)

        result = await cs._handle_missing_contacts("bridge2", current_phones=set())

        assert result["hard_deleted"] == 1
        row = await fetch_one("SELECT whatsapp_number FROM wbom_contacts WHERE whatsapp_number=$1", phone)
        assert row is None
        log_row = await fetch_one("SELECT id FROM fazle_contact_hard_delete_log WHERE phone=$1", phone)
        assert log_row is not None
    finally:
        await _cleanup(phone)


@pytest.mark.asyncio
async def test_is_contact_active_on_other_bridge_fails_safe(monkeypatch):
    """If the cross-bridge check itself errors, it must fail toward
    blocking the delete (return True), never toward allowing it."""
    import modules.contact_sync as cs

    async def _raise(*_a, **_kw):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(cs, "fetch_one", _raise)
    result = await cs._is_contact_active_on_other_bridge("8801700000000", "bridge1")
    assert result is True


# ── Core regression: soft-delete, reactivation, protection, idempotency ─────────

@pytest.mark.asyncio
async def test_soft_delete_on_first_miss_unchanged():
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await _seed_contact(phone, "Farida Yasmin")
        await _seed_presence(phone, "bridge1", 0)

        result = await cs._handle_missing_contacts("bridge1", current_phones=set())

        assert result["soft_deleted"] == 1
        assert result["hard_deleted"] == 0
        row = await fetch_one("SELECT is_active, marked_for_delete_at FROM wbom_contacts WHERE whatsapp_number=$1", phone)
        assert row["is_active"] is False
        assert row["marked_for_delete_at"] is not None
    finally:
        await _cleanup(phone)


@pytest.mark.asyncio
async def test_reappearing_contact_reactivated_rollback():
    """A soft-deleted contact seen again in a later sync is fully
    reactivated -- proves the soft-delete step is genuinely reversible."""
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await execute(
            """INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active, marked_for_delete_at, last_seen, updated_at)
               VALUES ($1, 'Old Name', 'whatsapp', false, NOW(), NOW(), NOW())""",
            phone,
        )
        await _seed_presence(phone, "bridge1", 1)

        await cs._mark_present(phone, "bridge1")

        row = await fetch_one(
            "SELECT is_active, marked_for_delete_at FROM wbom_contacts WHERE whatsapp_number=$1", phone,
        )
        assert row["is_active"] is True
        assert row["marked_for_delete_at"] is None
        count = await fetch_val(
            "SELECT consecutive_absences FROM fazle_contact_presence WHERE phone=$1 AND bridge='bridge1'", phone,
        )
        assert count == 0
    finally:
        await _cleanup(phone)


@pytest.mark.asyncio
async def test_protected_contact_never_touched():
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await _seed_contact(phone, "Protected Vendor", is_protected=True)
        await _seed_presence(phone, "bridge1", cs.HARD_DELETE_AFTER_ABSENCES - 1)

        result = await cs._handle_missing_contacts("bridge1", current_phones=set())

        assert result["soft_deleted"] == 0
        assert result["hard_deleted"] == 0
        row = await fetch_one("SELECT is_active FROM wbom_contacts WHERE whatsapp_number=$1", phone)
        assert row["is_active"] is True
    finally:
        await _cleanup(phone)


@pytest.mark.asyncio
async def test_sync_idempotent_no_duplicate_presence_rows(monkeypatch):
    """Running the same successful sync twice in a row with identical data
    must not create duplicate presence rows or spuriously bump absences."""
    import modules.contact_sync as cs

    phone = _phone()
    bridge_cfg = {"bridge": "bridge1", "whatsapp_db": "irrelevant", "number": "8801000000000"}
    monkeypatch.setattr(
        cs, "_read_bridge_contacts",
        lambda *_a, **_kw: [{"phone": phone, "display_name": "Sync Test", "source_bridge": "8801000000000"}],
    )
    try:
        await cs.sync_bridge(bridge_cfg)
        await cs.sync_bridge(bridge_cfg)

        rows = await fetch_val(
            "SELECT COUNT(*) FROM fazle_contact_presence WHERE phone=$1 AND bridge='bridge1'", phone,
        )
        assert rows == 1
        count = await fetch_val(
            "SELECT consecutive_absences FROM fazle_contact_presence WHERE phone=$1 AND bridge='bridge1'", phone,
        )
        assert count == 0
    finally:
        await _cleanup(phone)


# ── _best_name() priority (regression) ──────────────────────────────────────────

def test_best_name_priority_order():
    import modules.contact_sync as cs

    assert cs._best_name("Full Name", "First", "Push", "Biz") == "Full Name"
    assert cs._best_name("", "First", "Push", "Biz") == "First"
    assert cs._best_name("", "", "Push", "Biz") == "Push"
    assert cs._best_name("", "", "", "Biz") == "Biz"
    assert cs._best_name("", "", "", "") == ""


# ── Known, still-open gap (documented, NOT fixed this pass) ─────────────────────

@pytest.mark.asyncio
async def test_KNOWN_GAP_rename_to_shorter_name_does_not_apply():
    """2026-07-22 audit finding, out of scope for this pass: _upsert_contacts()
    only overwrites display_name when the new name is LONGER than the
    existing one. A same-source rename to something shorter is silently
    ignored. This test documents the current (undesirable) behavior so a
    future fix is measured against a known baseline, not discovered again
    from scratch -- it is not an assertion that this is correct."""
    import modules.contact_sync as cs

    phone = _phone()
    try:
        await cs._upsert_contacts([{"phone": phone, "display_name": "A Very Long Original Name", "source_bridge": "bridge1"}])
        await cs._upsert_contacts([{"phone": phone, "display_name": "Short", "source_bridge": "bridge1"}])

        row = await fetch_one("SELECT display_name FROM wbom_contacts WHERE whatsapp_number=$1", phone)
        assert row["display_name"] == "A Very Long Original Name"  # KNOWN GAP: should be "Short"
    finally:
        await _cleanup(phone)
