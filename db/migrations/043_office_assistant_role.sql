-- Migration 043: Formalize office_assistant as a real RBAC role (Owner decision, 2026-07-11)
--
-- Replaces the ad-hoc, name-string-based restriction in
-- modules/admin_transactions/__init__.py (_OFFICE_ASSISTANT_NAMES =
-- {"officeassistant01","officeassistant02"}) with a real role in the
-- fazle_roles hierarchy: viewer(10) < office_assistant(20) < operator(30)
-- < accountant(50) < admin(70) < superadmin(100).
--
-- Permissions (Owner-specified): view attendance list/own records, create
-- attendance draft entries only -- no review, no approve, no edit, no
-- salary/payroll visibility. This sits above viewer (adds draft-submit)
-- but strictly below operator (no approve/reject/edit power).
--
-- Data note (confirmed via query before writing this migration): both known
-- office-assistant accounts already exist as real, active fazle_admins rows
-- (id=18 'officeassistant01', phone 88017996526; id=19 'officeassistant02',
-- phone 88017255693) but currently hold inconsistent, wrong-scoped roles --
-- officeassistant01 has 'operator' (real approve/reject/edit power almost
-- everywhere in the system except the one place the ad-hoc name check
-- happened to intercept it), officeassistant02 has plain 'viewer'. This
-- migration corrects both to the single, properly-scoped 'office_assistant'
-- role, which is a real permission *reduction* for officeassistant01 (loses
-- operator-level edit/approve power it held everywhere else) -- this is the
-- intended fix, not a side effect.
--
-- Safe to apply: purely additive role row + idempotent role reassignment
-- for exactly the 2 known accounts; no other admin account is touched.

INSERT INTO fazle_roles (name, level, description)
VALUES ('office_assistant', 20, 'View attendance list/own records; create attendance draft entries only -- no review/approve/edit/salary')
ON CONFLICT (name) DO NOTHING;

DELETE FROM fazle_admin_roles
WHERE admin_id IN (
    SELECT id FROM fazle_admins WHERE LOWER(name) IN ('officeassistant01', 'officeassistant02')
);

INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by)
SELECT id, 'office_assistant', 'migration_043'
FROM fazle_admins
WHERE LOWER(name) IN ('officeassistant01', 'officeassistant02')
ON CONFLICT (admin_id, role_name) DO NOTHING;
