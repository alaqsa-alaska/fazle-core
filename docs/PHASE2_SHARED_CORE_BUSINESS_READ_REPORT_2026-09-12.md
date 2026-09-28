# Phase 2 — Shared Core business read capability

Date: 2026-09-12
State: `IMPLEMENTED / TARGETED_TESTED / NOT_DEPLOYED / NOT_LIVE_VERIFIED`

## Inventory and decision

Core already had narrow readers over `ai_read_*` views for contacts,
employees, recent messages, payroll, billing outstanding, escort programs,
attendance, advances/payments, recruitment, KB, and bridge status. Core-Hermes
also had domain-specific calls through assistant-backend. Observer-Hermes had
periodically refreshed `dom_*` mirrors for a subset of those views. The prior
paths were useful but did not provide equivalent broad historical access:
several were rolling-window or small-result views, and Observer lacked release
history, complete bills, full message history, and one unified report path.

The implemented shared path is:

`Hermes -> service bearer -> Core fixed-query API -> read-only transaction -> Core DB`

Core owns the dataset allowlist, SQL, filter allowlists, and a maximum page size
of 5,000. Callers can page larger authorized reports with `offset`; no caller
can submit SQL. Separate existing Runner and Observer service bearers authorize
the two consumers. No Core DB credential is supplied to either Hermes runtime.
The response explicitly retains the server-side human-disclosure requirement;
service access is not itself authority to disclose every returned field to a
human.

## Coverage

| Dataset | Core-Hermes | Observer-Hermes | Core authority | Read mechanism |
|---|---|---|---|---|
| Employee | Yes | Yes | `wbom_employees` | Fixed Core query; paged API |
| Attendance | Yes | Yes | `wbom_attendance` + employee identity | Fixed Core query; paged API |
| Duty | Yes | Yes | `wbom_escort_programs` | Fixed Core query; paged API |
| Payroll | Yes | Yes | `wbom_payroll_runs` | Fixed Core query; paged API |
| Salary | Yes | Yes | `wbom_employees.basic_salary` | Fixed Core query; paged API |
| Advances | Yes | Yes | canonical FPE cash transactions | Fixed Core query; paged API |
| Payments | Yes | Yes | canonical FPE cash transactions | Fixed Core query; paged API |
| Escort programs | Yes | Yes | `wbom_escort_programs` | Fixed Core query; paged API |
| Release records | Yes | Yes | confirmed `escort_evidence_event` stream | Fixed Core query; paged API; migration 083 prerequisite |
| Clients/contacts | Yes | Yes | `wbom_contacts` | Fixed Core query; paged API |
| Recruitment | Yes | Yes | `fazle_recruitment_sessions` | Fixed Core query; paged API |
| Bills/invoices | Yes | Yes | billing records + Core client profiles | Fixed Core query; paged API |
| Conversation/message history | Yes | Yes | canonical WhatsApp message store | Fixed Core query; full body; paged API |
| KB/policies | Yes | Yes | `fazle_knowledge_base` | Fixed Core query; paged API |
| Operational reports | Yes | Yes | Core report cache | Fixed Core query; paged API |

Core-Hermes exposes this API as the `read_core_business` MCP tool. Observer-
Hermes uses `fazle_observer.core_business_read` through the
`core-business-read` skill. Observer's existing `dom_*` mirror remains
available for low-latency, freshness-aware bounded lookups; it is no longer
the only business-read path.

## Verification

- Core: 22 selected tests pass against the guarded disposable `fazle_test`
  database (20 unit/compatibility and 2 endpoint integration). Authorized
  Runner and Observer bearer cases pass; missing, malformed,
  and wrong credentials fail closed with 403; unknown filters and arbitrary
  SQL-shaped input are rejected; every dataset compiles to fixed SELECT-only
  SQL inside a read-only transaction; 5,000-row pagination is accepted.
- Core-Hermes: 7 selected tests pass, including service bearer forwarding,
  broad pagination, rejection, and MCP registration, without transmitting SQL
  or DB credentials.
- Observer-Hermes: 5 selected tests pass for service bearer forwarding,
  rejection, and existing registry compatibility; client configuration
  contains no Core DB credential. Seven existing mirror DB tests were skipped
  when Docker was unavailable in the sandbox; this new endpoint's real SQL was
  independently exercised by Core's disposable-DB integration suite.

No production migration, deployment, restart, secret mutation, database write,
message send, or live read was performed. Release records depend on the
already-created but production-unapplied migration 083; therefore the phase is
not live-qualified.
