-- Migration 046: fpe_employee_edit_requests (Employee Edit approval queue)
--
-- Backs the payroll.html Employees tab's role-gated edit flow: admin/
-- superadmin edits write directly to fpe_employees/wbom_employees as before;
-- any other role's edit is stored here as a pending request for an admin to
-- approve or reject.
--
-- Deliberately a new, purpose-built table rather than reusing the existing
-- Operator-submission pattern's storage (fpe_unmatched_messages with
-- review_status='operator_pending') -- that table requires a synthetic
-- fpe_wa_messages FK row per entry (it's WhatsApp-message-shaped) and has no
-- room for an arbitrary employee-field diff. Same submit -> pending ->
-- approve/reject spirit, same RBAC dependency functions
-- (_require_operator_or_above / _require_viewer_or_above / _require_admin_role
-- in modules/fazle_payroll_engine/routes.py), different storage shape.
--
-- Named "edit_requests", not "review_queue" -- this app already has an
-- unrelated "Review Queue" tab (parser-confidence triage on WhatsApp
-- messages, fpe_review_audit_logs); reusing that name for a third concept
-- would recreate the exact naming collision that caused real navigation/
-- session bugs earlier in this project.
--
-- changes is a flat {field: new_value} JSON diff covering only the fields
-- that actually changed, spanning both employee-identity systems (FPE:
-- full_name/primary_phone/employee_id_phone; WBOM: designation/basic_salary/
-- joining_date/status) -- the approve handler splits it by field name and
-- applies each half through the same update logic the direct-admin-edit path
-- already uses.

CREATE TABLE IF NOT EXISTS fpe_employee_edit_requests (
    id                  SERIAL PRIMARY KEY,
    employee_id         INTEGER NOT NULL REFERENCES fpe_employees(id),
    wbom_employee_id     INTEGER,
    requested_by         VARCHAR(50) NOT NULL,
    changes              JSONB NOT NULL,
    status               VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
    reject_reason        TEXT,
    submitted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by          VARCHAR(50),
    reviewed_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_fpe_employee_edit_requests_employee_id ON fpe_employee_edit_requests(employee_id);
CREATE INDEX IF NOT EXISTS idx_fpe_employee_edit_requests_status ON fpe_employee_edit_requests(status);
