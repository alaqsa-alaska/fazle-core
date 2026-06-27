-- Migration: Add HR Officer users (OfficeAssistant01, OfficeAssistant02)
-- Purpose: Create two HR Officer accounts with 'operator' role for mobile payroll data entry
-- Date: 2026-01-XX
-- Usage: psql -d postgres -f migrations/002_add_hr_officers.sql

-- Insert HR Officer 1: OfficeAssistant01
-- API Key: fk_oHYBRq0koo2EOuoWCQLNvCmDSuNgdz3oUg8jB2-SPQs
-- Hash: 0dc6ea9e147395007f39c2a4b926f0d8ef8ea6293f9a57d7e4ce8b1b6f884eec
INSERT INTO fazle_admins (phone, name, status, api_key_hash, notes)
VALUES (
  '8801700000001',
  'OfficeAssistant01',
  'active',
  '0dc6ea9e147395007f39c2a4b926f0d8ef8ea6293f9a57d7e4ce8b1b6f884eec',
  'HR Officer - Mobile Data Entry - Created by setup script'
)
ON CONFLICT (phone) DO NOTHING;

-- Insert HR Officer 2: OfficeAssistant02
-- API Key: fk_LVvuQciEYTq2JwfQ9lgcuhB29xErPtIf_ipdQpPvAWE
-- Hash: 7f55978c97a0ae223851a9a27d025e60c965e2385abe1ada948c3bd1e1bb880c
INSERT INTO fazle_admins (phone, name, status, api_key_hash, notes)
VALUES (
  '8801700000002',
  'OfficeAssistant02',
  'active',
  '7f55978c97a0ae223851a9a27d025e60c965e2385abe1ada948c3bd1e1bb880c',
  'HR Officer - Mobile Data Entry - Created by setup script'
)
ON CONFLICT (phone) DO NOTHING;

-- Get admin IDs for role assignment
-- Store in variables for use below
-- This is for manual verification; the role inserts work independently

-- Assign 'operator' role to OfficeAssistant01
INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by)
SELECT id, 'operator', 'setup_script'
FROM fazle_admins
WHERE phone = '8801700000001' AND name = 'OfficeAssistant01'
ON CONFLICT (admin_id, role_name) DO NOTHING;

-- Assign 'operator' role to OfficeAssistant02
INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by)
SELECT id, 'operator', 'setup_script'
FROM fazle_admins
WHERE phone = '8801700000002' AND name = 'OfficeAssistant02'
ON CONFLICT (admin_id, role_name) DO NOTHING;

-- Verification query (run manually to confirm):
-- SELECT a.id, a.phone, a.name, a.status, 
--        string_agg(ar.role_name, ',') as roles
-- FROM fazle_admins a
-- LEFT JOIN fazle_admin_roles ar ON ar.admin_id = a.id
-- WHERE a.phone IN ('01700000001', '01700000002')
-- GROUP BY a.id;
