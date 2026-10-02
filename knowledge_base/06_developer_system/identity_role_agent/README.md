---
title: Identity-Role Agent Contract
owner: Fazle Core Admin
status: design-contract
visibility: developer-admin-only
runtime_index: true
---

# Identity-Role Agent

This document is the small coordination contract for identity, role, intent,
domain, visibility, response, and audit decisions. It does not replace the
existing code-based workflows and it does not grant an AI agent direct write
access to business tables.

## Scope

The agent handles the message decision layer in this order:

1. Resolve the sender identity and confidence.
2. Resolve one canonical intent and business domain.
3. Apply role, visibility, safety, and disclosure rules.
4. Use the existing code workflow when a parser or business function owns the
   decision.
5. Ask Hermes to compose an eligible conversational reply.
6. Fall back to the local Ollama Hermes-family model if Hermes fails.
7. Record the decision, model/fallback result, and final disposition.

Payroll, cash, attendance, escort roster, escort release, and other financial
or operational writes remain owned by their existing code functions.

## Bounded responsibilities

These are responsibilities, not five new independent runtime services.

| Responsibility | Authority | Must not do |
|---|---|---|
| Identity resolver | `modules.identity_brain.detect_identity()` and verified identity data | Guess a trusted role from message text alone |
| Intent/domain resolver | Existing deterministic parsers first; Hermes semantic fallback; local Ollama fallback | Write payroll, cash, roster, or identity records |
| Policy/visibility guard | Role permissions, response rules, disclosure boundary, confidence, holds | Let an LLM override a code gate |
| Response composer | Existing templates/workflows, then Hermes, then local Ollama Hermes | Invent business facts or bypass a workflow |
| Memory/audit reader | Canonical message history, decision trace, admin read-only reports | Expose internal data to public users |

## Canonical domains

The following names are the target vocabulary. Existing aliases must map to one
of these names before authorization or reporting:

| Domain | Existing owner |
|---|---|
| `cash_transaction` | payment ingest and FPE accounting |
| `employee_ledger` | FPE employee and transaction ledger |
| `payroll` | payroll engine and payroll logic |
| `security_attendance` | attendance and attendance parser |
| `escort_program` | escort parser and escort roster |
| `escort_release` | escort lifecycle/release verification |
| `escort_client` | escort client/order workflow |
| `client_security` | client/security-service policy |
| `family_personal` | family/friend identity and safe personal reply |
| `company_policy` | knowledge base and policy retrieval |
| `recruitment` | recruitment flow and candidate intake |
| `message_parser` | extraction and parser modules |
| `employee_request` | salary, advance, complaint, leave, and release request flow |
| `admin_hermes` | Admin-Hermes relay and Admin-only data reads |
| `general_unknown` | safe clarification or no-action path |

## Role boundaries

- `admin`: may request approved read-only business reports and issue gated
  commands; never trust an unverified sender as Admin.
- `accountant`: payment/accounting conversation; sensitive payment actions
  remain code-gated and review-controlled.
- `employee`: salary, payment-due, attendance, advance, leave, and release
  conversations; complaints and financial actions remain review-controlled.
- `candidate` or `unknown`: recruitment intent only; use the recruitment
  funnel, not employee or payroll disclosure.
- `escort` or `escort_client`: use the escort workflow; orders and release
  confirmation remain draft/review or code-authorized actions.
- `family` or `friend`: personal-safe response only; no business data.
- `client` or `security_service_client`: client policy and approved service
  information only; no employee/payroll disclosure.

Role assignment, phone verification, conversation scope, and authorization
version must come from the identity/authorization layer. A name, keyword, or
LLM claim is not sufficient evidence for a privileged role.

## Response priority

1. Fixed code reply or parser-owned workflow.
2. Existing deterministic business-rule response.
3. Hermes response using the role/domain visibility boundary.
4. Local Ollama Hermes-family fallback.
5. Safe holding response or admin draft when no eligible answer exists.

The model may explain an approved result; it may not create the result of a
payment, payroll, attendance, escort, release, or other business mutation.

## Disposition rules

- Safe recruitment and informational employee replies may be eligible for
  auto-send only after current identity, domain, and outbound authorization
  checks pass.
- Salary complaints, payment disputes, legal issues, advances, release
  estimates, escort orders, and client confirmations default to draft, review,
  or the existing workflow.
- Accountant, VIP-client, escort-client, and other draft-always roles keep
  their existing draft policy.
- Every outbound queue row must carry a current authorization snapshot. Legacy
  rows without one remain manual-review only.

## Channel boundary

The Meta/social auto-reply system remains a separate channel pipeline with its
own classifier, risk flagger, rate limiter, and queue. It may reuse identity and
visibility policy, but it must not silently merge with the internal WhatsApp
bridge workflow.

## Knowledge visibility

- Developer/admin documents, identity rules, contact registries, audit files,
  payroll details, and internal prompts are never customer-facing context.
- Candidate-facing retrieval is limited to approved recruitment and company
  policy content.
- Employee-facing retrieval is limited to the employee's permitted context.
- Admin business reads are read-only, authenticated, bounded, and audited.

## Rollout gates

1. Keep current payroll and escort workflows unchanged.
2. Reconcile the canonical role/domain names with runtime code.
3. Populate and verify identity links and scoped role assignments.
4. Wire authorization snapshots into every eligible outbound enqueue path.
5. Dry-run old queue rows and keep legacy rows manual-review only.
6. Test Hermes failure and local Ollama fallback without sending a message.
7. Release recruitment first, then employee informational replies, then any
   separately approved channel or role.

This README is a coordination contract only. Runtime behavior is defined by
the referenced code, database authorization state, and active business rules;
editing this document alone does not enable sending or grant access.

## Primary references

- `03_ai_identity/identity_overview.md`
- `06_developer_system/identity_brain.md`
- `06_developer_system/identity_integration.md`
- `06_developer_system/role_permissions.md`
- `04_business_rules/ai_response_rules.md`
- `06_developer_system/workflow_engine.md`
- `06_developer_system/social_auto_reply_system.md`
