Source: knowledge_base/README.md
SHA256: ee6013e9ff107b066bba6183e3a8ece6741e89eda29689f3816966a5f2d60e6d
Version: ee6013e9ff10

# Fazle AI Platform Knowledge Base

## Purpose
This is the role-centric, source-of-truth knowledge base for the Fazle AI Platform and Al-Aqsa Security & Logistics Services Ltd.

It is designed for:
- AI answer generation;
- RAG and hybrid search;
- Identity Brain;
- WhatsApp, Messenger, and Facebook automation;
- attendance, payroll, escort, payment, recruitment, admin, and frontend/backend workflows.

## Source Of Truth Order
1. Management conflict decisions dated 2026-06-19.
2. New role-centric knowledge folders listed below.
3. Existing old folders retained only for traceability.
4. Original converted `.txt` source files.

## Required Role-Centric Architecture
- `01_employee_knowledge` - public, candidate, employee, escort-safe knowledge.
- `02_admin_knowledge` - admin, HR, operations, supervisor, accountant, management knowledge.
- `03_ai_identity` - role mapping, permission matrix, response rules.
- `04_business_rules` - decision-engine rules.
- `05_workflows` - operational workflows and state transitions.
- `06_developer_system` - RAG, parser, OCR, DB, event, automation, security, visibility.
- `07_archived` - legacy/source rewrite notes and archived mapping.

## Required Reports
- `knowledge_inventory.md`
- `duplicate_report.md`
- `missing_report.md`
- `ai_access_matrix.md`
- `conflict_resolution_record.md`

## Runtime Rule
AI must answer by role and visibility first, then by topic. Business rules control decisions. Workflows control actions. Developer/system files control implementation.

## Frontend/Backend Rule
Any frontend role, permission, employee, candidate, client, payment, attendance, or workflow change must update backend source records immediately and create an audit log.

## Revision History
- 2026-06-19: Rebuilt into role-centric AI knowledge architecture after conflict resolution and source normalization.
