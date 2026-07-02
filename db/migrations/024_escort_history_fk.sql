-- Migration 024: Add FK constraints to escort history tables (G-07)
-- Applied: 2026-07-02
-- DEFERRABLE so bulk history imports work without ordering constraints.
-- Uses DO $$ to check if constraint already exists before adding.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_eol_group'
          AND table_name = 'escort_order_lighters'
    ) THEN
        ALTER TABLE escort_order_lighters
            ADD CONSTRAINT fk_eol_group
                FOREIGN KEY (group_id) REFERENCES escort_order_groups(group_id)
                ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_eol_program'
          AND table_name = 'escort_order_lighters'
    ) THEN
        -- First NULL out any orphaned program_ids that don't exist in source table
        UPDATE escort_order_lighters
        SET program_id = NULL
        WHERE program_id IS NOT NULL
          AND program_id NOT IN (SELECT program_id FROM wbom_escort_programs);

        ALTER TABLE escort_order_lighters
            ADD CONSTRAINT fk_eol_program
                FOREIGN KEY (program_id) REFERENCES wbom_escort_programs(program_id)
                ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;
