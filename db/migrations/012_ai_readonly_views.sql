-- ============================================================
-- Fazle Core — Migration 012
-- AI Read-Only Views for fazle_ai_reader role
-- ============================================================
-- Purpose: Allow Ollama/AI to read operational data safely via
--          named views only. No direct table access. No writes.
--
-- Run with:  psql $DATABASE_URL -f db/migrations/012_ai_readonly_views.sql
-- Safety:    Only CREATE VIEW and GRANT SELECT — no data mutations.
-- ============================================================

-- ── 1. ai_read_contacts ──────────────────────────────────────────────────────
-- Contact list with role, name, number. Masks sensitive fields.
CREATE OR REPLACE VIEW ai_read_contacts AS
SELECT
    c.contact_id,
    c.display_name,
    c.whatsapp_number,
    c.company_name,
    c.relation,
    c.is_active,
    c.interaction_count,
    c.interest_level,
    c.created_at::DATE AS joined_date
FROM wbom_contacts c
WHERE c.is_active = TRUE
ORDER BY c.display_name;

-- ── 2. ai_read_employees ─────────────────────────────────────────────────────
-- Employee roster. Excludes NID, bank account, address (privacy).
CREATE OR REPLACE VIEW ai_read_employees AS
SELECT
    e.employee_id,
    e.employee_name,
    e.employee_mobile,
    e.designation,
    e.joining_date,
    e.status,
    e.basic_salary
FROM wbom_employees e
ORDER BY e.employee_name;

-- ── 3. ai_read_recent_messages ───────────────────────────────────────────────
-- Last 100 WhatsApp messages (all bridges). Truncates body at 300 chars.
CREATE OR REPLACE VIEW ai_read_recent_messages AS
SELECT
    m.message_id,
    m.sender_number,
    c.display_name AS sender_name,
    LEFT(m.message_body, 300) AS message_body,
    m.direction,
    m.platform AS source,
    m.received_at
FROM wbom_whatsapp_messages m
LEFT JOIN wbom_contacts c ON c.whatsapp_number = m.sender_number
WHERE m.received_at >= NOW() - INTERVAL '7 days'
ORDER BY m.received_at DESC
LIMIT 100;

-- ── 4. ai_read_escort_programs ───────────────────────────────────────────────
-- Active and recent escort programs with vessel/employee info.
CREATE OR REPLACE VIEW ai_read_escort_programs AS
SELECT
    ep.program_id,
    ep.mother_vessel,
    ep.lighter_vessel,
    ep.destination,
    ep.program_date,
    ep.shift,
    ep.status,
    ep.escort_mobile,
    e.employee_name   AS escort_name,
    ep.assignment_time,
    ep.completion_time
FROM wbom_escort_programs ep
LEFT JOIN wbom_employees e ON e.employee_id = ep.escort_employee_id
WHERE ep.program_date >= CURRENT_DATE - INTERVAL '30 days'
ORDER BY ep.program_date DESC, ep.program_id DESC;

-- ── 5. ai_read_attendance_summary ────────────────────────────────────────────
-- Attendance records for current and last month.
CREATE OR REPLACE VIEW ai_read_attendance_summary AS
SELECT
    a.attendance_id,
    e.employee_name,
    e.employee_mobile,
    e.designation,
    a.attendance_date,
    a.status,
    a.location,
    a.check_in_time,
    a.check_out_time,
    a.remarks
FROM wbom_attendance a
JOIN wbom_employees e ON e.employee_id = a.employee_id
WHERE a.attendance_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'
ORDER BY a.attendance_date DESC, e.employee_name;

-- ── 6. ai_read_payroll_runs ───────────────────────────────────────────────────
-- Payroll run summaries. No individual bank/bkash numbers.
CREATE OR REPLACE VIEW ai_read_payroll_runs AS
SELECT
    r.run_id,
    e.employee_name,
    e.designation,
    r.period_year,
    r.period_month,
    r.status,
    r.total_programs,
    r.program_allowance,
    r.gross_salary,
    r.net_salary,
    r.total_advances,
    r.total_deductions
FROM wbom_payroll_runs r
JOIN wbom_employees e ON e.employee_id = r.employee_id
WHERE r.period_year >= EXTRACT(YEAR FROM CURRENT_DATE)::INT - 1
ORDER BY r.period_year DESC, r.period_month DESC, e.employee_name;

-- ── 7. ai_read_recruitment_leads ─────────────────────────────────────────────
-- Recent recruitment candidates/leads. No NID or salary details.
CREATE OR REPLACE VIEW ai_read_recruitment_leads AS
SELECT
    s.id,
    s.phone,
    s.funnel_stage,
    s.source_bridge AS source,
    s.full_name,
    s.area,
    s.score_bucket,
    s.created_at,
    s.updated_at
FROM fazle_recruitment_sessions s
WHERE s.created_at >= NOW() - INTERVAL '30 days'
ORDER BY s.created_at DESC
LIMIT 50;

-- ── 8. ai_read_module_bridge_status ──────────────────────────────────────────
-- Bridge heartbeats and service health snapshot.
CREATE OR REPLACE VIEW ai_read_module_bridge_status AS
SELECT
    service AS service_name,
    last_seen,
    queue_depth,
    meta_json AS metadata
FROM fazle_service_heartbeats
ORDER BY last_seen DESC;

-- ── 9. ai_read_kb_articles ───────────────────────────────────────────────────
-- Active KB articles searchable by AI (non-internal only).
CREATE OR REPLACE VIEW ai_read_kb_articles AS
SELECT
    k.id,
    k.key,
    k.category,
    k.subcategory,
    LEFT(k.reply_text, 500) AS content_preview,
    k.is_active,
    k.created_at::DATE AS created_date
FROM fazle_knowledge_base k
WHERE k.is_active = TRUE
ORDER BY k.category, k.key;

-- ── Grant SELECT on all ai_read_* views to fazle_ai_reader ───────────────────
DO $$
DECLARE
    v TEXT;
BEGIN
    FOR v IN
        SELECT viewname FROM pg_views
        WHERE schemaname = 'public' AND viewname LIKE 'ai_read_%'
    LOOP
        EXECUTE 'GRANT SELECT ON ' || v || ' TO fazle_ai_reader';
        RAISE NOTICE 'Granted SELECT on % to fazle_ai_reader', v;
    END LOOP;
END $$;
