-- Migration 040: Attendance entry-source + approval audit trail (Owner decision, 2026-07-11)
--
-- Office Payroll Hardening: office payroll must read duty days only from
-- manual frontend attendance or admin-approved message-parser attendance --
-- never from the WhatsApp self-report flow, escort-release backfill, or
-- any inferred source. wbom_attendance is shared by both escort and office
-- attendance today with nothing distinguishing them; this migration adds
-- that distinction so compute_run's future duty-day query can filter
-- deterministically instead of guessing from recorded_by text.
--
-- entry_source (governed vocabulary, NOT NULL):
--   manual_office    -- entered directly via the new frontend Attendance tab
--   message_parser   -- supervisor-submitted message, admin-approved (may
--                       count toward office payroll once approved)
--   whatsapp_escort   -- the existing guard self-report ("হাজির আছি") flow
--                       (modules/attendance.py's handle_attendance_message)
--                       -- never counts toward office payroll regardless of
--                       approval, per Owner decision
--   escort_release    -- modules/escort_lifecycle's retroactive backfill on
--                       program release -- escort-only, automatic
--   system            -- reserved for any future automated/administrative
--                       write path, not currently used
--   legacy            -- default for any pre-existing row with no recorded
--                       source (none exist today -- wbom_attendance has 0
--                       rows in production, confirmed before writing this)
--
-- approval_channel records HOW a row was finalized (nullable -- only set
-- once an approval action actually happens):
--   manual_direct        -- created directly via the frontend form (no
--                           draft/review stage)
--   whatsapp_message      -- admin's WhatsApp APPROVE <id> command
--   frontend_review_queue -- the existing Review Queue dashboard's Approve
--                           button
--   system                -- reserved, not currently used
--
-- source_draft_id links back to fazle_draft_replies for parser-originated
-- rows, so the full draft -> approval -> attendance chain stays auditable.
--
-- Safe to apply: wbom_attendance has 0 rows in production (confirmed by
-- audit immediately before writing this migration), so entry_source's
-- NOT NULL DEFAULT 'legacy' has nothing real to backfill.

ALTER TABLE wbom_attendance ADD COLUMN IF NOT EXISTS entry_source VARCHAR(20) NOT NULL DEFAULT 'legacy';
ALTER TABLE wbom_attendance ADD COLUMN IF NOT EXISTS approval_channel VARCHAR(24);
ALTER TABLE wbom_attendance ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE wbom_attendance ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE wbom_attendance ADD COLUMN IF NOT EXISTS source_draft_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'wbom_attendance_entry_source_check') THEN
        ALTER TABLE wbom_attendance
            ADD CONSTRAINT wbom_attendance_entry_source_check
            CHECK (entry_source IN ('manual_office','message_parser','whatsapp_escort','escort_release','system','legacy'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'wbom_attendance_approval_channel_check') THEN
        ALTER TABLE wbom_attendance
            ADD CONSTRAINT wbom_attendance_approval_channel_check
            CHECK (approval_channel IS NULL OR approval_channel IN ('manual_direct','whatsapp_message','frontend_review_queue','system'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'wbom_attendance_source_draft_id_fkey') THEN
        ALTER TABLE wbom_attendance
            ADD CONSTRAINT wbom_attendance_source_draft_id_fkey
            FOREIGN KEY (source_draft_id) REFERENCES fazle_draft_replies(id);
    END IF;
END $$;

-- Confirmed pre-existing (not created here, just documented): UNIQUE
-- (employee_id, attendance_date) already exists in production as
-- wbom_attendance_employee_id_attendance_date_key, which is exactly the
-- duplicate-prevention rule needed (one attendance result per employee per
-- date) -- save_attendance()'s existing ON CONFLICT clause already relies
-- on it. No new constraint added; this migration does not touch it.
