-- Migration 033: Invoice number counter table (Phase 2B, Commit A)
--
-- Context: docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md Phase 2B Commit A,
-- docs/PHASE2B_READINESS_REPORT.md Re-Audit section. Owner Decision 4
-- (2026-07-09): deterministic yearly sequence, AQS-YYYY-NNNNNN.
--
-- Decision-independent: this table/function does not depend on the
-- still-open service_charge question (docs/PHASE2B_READINESS_REPORT.md
-- Owner Decision 1) and is safe to build and validate now.
--
-- Design: a counter TABLE, not a Postgres SEQUENCE. A raw SEQUENCE's
-- nextval() is non-transactional (the same behavior that caused the
-- Migration 032 dry-run incident, documented in
-- knowledge_base/06_developer_system/database_rules.md) -- a number
-- consumed by a transaction that later rolls back would be permanently
-- lost, leaving a gap in the invoice sequence. This counter table's
-- UPDATE ... RETURNING participates normally in the surrounding
-- transaction: if the caller's transaction rolls back, the increment
-- rolls back with it, and no number is lost. The cost is that concurrent
-- callers serialize on the one counter row for the current year -- at
-- this system's actual volume (342 escort programs total, ~64 Completed)
-- this is not a real throughput concern, and gapless invoice numbering is
-- the more valuable property here.

CREATE TABLE IF NOT EXISTS invoice_number_counters (
    year        INTEGER PRIMARY KEY,
    last_number INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- next_invoice_number(year) -- atomically reserves and returns the next
-- sequential number for that year (starting at 1). Safe to call
-- concurrently: the INSERT ... ON CONFLICT DO UPDATE takes a row-level
-- lock, serializing concurrent callers for the same year rather than
-- risking a duplicate.
CREATE OR REPLACE FUNCTION next_invoice_number(p_year INTEGER)
RETURNS INTEGER AS $$
DECLARE
    v_number INTEGER;
BEGIN
    INSERT INTO invoice_number_counters (year, last_number, updated_at)
    VALUES (p_year, 1, now())
    ON CONFLICT (year) DO UPDATE
        SET last_number = invoice_number_counters.last_number + 1,
            updated_at  = now()
    RETURNING last_number INTO v_number;
    RETURN v_number;
END;
$$ LANGUAGE plpgsql;
