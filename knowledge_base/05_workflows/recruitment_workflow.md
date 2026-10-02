---
title: Recruitment Conversation Workflow
owner: Fazle Core Admin
status: active
canonical: true
last_verified: 2026-09-26
runtime_index: false
authority_register: ../00_governance/recruitment_fact_authority.json
---

# Recruitment Conversation Workflow

This is the one canonical workflow document for Recruitment conversations.
It describes how Fazle understands, answers, continues, and safely hands off
Recruitment conversations. It is not a source of salary, fee, vacancy, office
hours, training, or benefit values. Those values require approval in the
Recruitment authority register.

## 1. Message sources

The workflow may receive a new or returning conversation from:

- WhatsApp personal messages and authorized WhatsApp group messages.
- Messenger and other authorized Meta messaging channels.
- Text and supported media after the existing channel parser and safety gate.
- A returning conversation with existing session, identity, or history context.

Channel authorization, identity checks, outbound restrictions, deduplication,
and draft/send controls remain owned by the existing router and outbound
workflow. This document does not authorize a new channel or an outbound send.

## 2. Identity and relationship

The router may classify a sender as a known applicant, potential applicant,
existing employee, former employee, office staff, administrator, client, or
unknown sender. A classification is context, not business-policy authority.

An unknown sender may receive approved public Recruitment information. Identity
and permission checks are required before disclosing employee, client, payroll,
operational, contact, or other sensitive information. A user's statement about
their identity or employment status does not establish company policy.

Reuse an authorized WhatsApp/database username, mobile number, and email when
actually available. A WhatsApp display name is not automatically a verified
legal name. Name and relevant information may also be extracted from an
applicant-supplied NID or CV, subject to existing access controls; unclear
fields require confirmation.

## 3. Intent understanding

Recruitment intent may include:

- job interest, role list, or current vacancy questions;
- role duties, eligibility, suitability, or experience;
- salary, fees, benefits, food, accommodation, training, or probation;
- office location, office hours, application, documents, interview, or joining;
- continuing an application or asking about application status;
- company employment-policy questions;
- asking to speak directly with the owner.

A single message may contain several intents. The system should answer the
actual approved question first, then ask only the next useful clarification or
intake question. A Recruitment question must not automatically create a new
application or restart an existing conversation.

## 4. Knowledge selection

After identity, domain, and intent resolution:

1. Select only relevant sections from the Consolidated Recruitment Policy.
2. Resolve candidate-facing facts through the authority register.
3. Retrieve relevant conversation history and current session state.
4. Apply the shared Fazle Persona and disclosure constraints.
5. Send the same prepared context to the selected provider.
6. Validate the generated reply before any existing authorized delivery path.

Retrieval is evidence discovery. It cannot promote historical, superseded,
unverified, or conflicting text into current policy. Conversation history and
experience memory are continuity context, not policy authority.

The currently approved Recruitment boundary includes the brand and full office
address, office hours 09:00–17:00 with Friday closed, public contact
01958-122300, the nine role types (not nine confirmed vacancies), eligibility
guidance, required documents, training/probation rules, approved salary
role/stage values, approved fee amounts and principles, and the approved duty
hours/shifts. Current vacancies, role-duty detail beyond the approved scope,
the Escort/Survey post-training range, the fee deduction/refund calendar,
overtime rate, leave, food, accommodation, and other benefits remain
unresolved or narrow-pending facts. Approved values do not establish
verification, selection, joining, or hiring automatically.

## 5. Recruitment conversation states

The existing live intake state is:

```text
new → name → age → area → job_preference → experience → phone_confirm → scored
```

The existing session and candidate/audit infrastructure remains the state
owner. A session may expire according to the existing runtime rule. The
workflow must also support ordinary informational questions before, during, or
after intake without forcing the user through the next form field.

For a mixed message, answer approved information and preserve valid intake
progress in the same turn. Do not re-request information already present and
appropriately verified in the current session or authorized history.

Ask only for missing age, education, address, experience, and interested role.
Do not ask again for a reliable existing mobile number.

## 6. Conversation continuation

Fazle should support incomplete questions, follow-up questions, topic changes,
role changes, multiple questions, returning applicants, and clarification.
When the topic changes, stale policy from the previous topic must not be
carried into the new answer. The current policy and authority status always
win over an older assistant reply.

Relevant memory consists of existing conversation history, identity context,
session state, approved candidate progress, and authorized experience notes.
It must remain available when the provider changes. Experience memory may
identify recurring confusion or missing knowledge but cannot become company
policy automatically.

## 7. Lifecycle handling

Recruitment follows the existing lifecycle:

```text
interest → applicant identification → role interest → eligibility → intake
→ documents → interview/review → selection → training/practical learning
→ probation/readiness → joining → assignment/deployment → active employment
```

Applicant claims do not establish selection, verification, joining, deployment,
or active employment. Office review and decision are required where the
authority register says so.

For joining, the applicant should bring all seven required documents on the
first office visit. If a document is missing, the office may allow additional
time; the AI must not invent a deadline or claim that an extension was approved.
School training is fifteen days and unpaid. Practical training/duty may begin
on day one at a vessel, security post, or Narayanganj ghat, and salary runs when
sent there. Initial probation is three months, extendable by three months, up
to six months total.

Field/Escort duty is approximately 6–8 hours depending on assignment. Guard
day shift is 08:00–20:00 and night shift is 20:00–08:00. Overtime may be
needed, but no overtime rate is approved.

## 8. Human handoff and owner contact

If someone asks to speak directly with the owner, Fazle should understand the
subject, urgency, and useful message before handing it to the existing
authorized workflow. It should preserve relevant context only where permitted.

Fazle must not claim that the owner is currently available, busy, has received
the message, or will call back unless that fact is confirmed by an authorized
source. It must not promise a response time.

## 9. Supported language behavior

Reply naturally in Bangla, English, or Banglish according to the user's
message. Preserve meaning when switching language across turns. Do not use
language choice to change policy, authority, privacy, or handoff rules.

## 10. Failure and provider fallback

If Hermes fails, the existing authorized fallback may use local Ollama or a
configured remote provider. Every provider receives the same current policy,
Persona, session context, and authorized history package. A provider failure
must not cause a new source of truth, a direct database mutation, or an
outbound send outside the existing delivery workflow.

## 11. Related implementation

- `modules/message_router`
- `modules/recruitment_flow`
- `modules/conversation_layer/recruitment.py`
- `modules/hermes_dispatch.py`
- `shared/reply_policy.py`
- `modules/recruitment_authority.py`
- `modules/rag`
- `knowledge_base/01_employee_knowledge/recruitment_policy.md`

Operational, employee, client, payroll, escort, Release Slip, and historical
documents may remain as scoped evidence. They are not competing candidate
Recruitment policy sources.
