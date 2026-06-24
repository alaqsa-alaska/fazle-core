---
title: Parser Logic
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Parser Logic

## Purpose
Define internal parsing rules for escort order extraction and vessel classification.

## Scope
Level 3 developer/system knowledge. Never expose to clients or employees.

## Escort Parsing Approach
Escort order parsing should use deterministic, fault-tolerant extraction where possible before LLM interpretation.

Data to extract:
- mother vessel;
- lighter vessel;
- master's mobile number;
- destination/release point;
- capacity;
- product;
- importer/account context;
- requested escort count if present;
- start date and day/night shift if present.

## Mother Vessel Detection
Mother vessel usually has these traits:
- appears at beginning or end of order;
- no mobile number directly attached;
- often associated with importer/account/product context;
- may be English vessel name.

## Lighter Vessel Detection
Lighter vessel usually has these traits:
- associated with master's mobile number;
- often has serial number before vessel name;
- includes destination/release point;
- includes capacity such as MT amount;
- may include Bangla or mixed vessel naming.

## Destination Signals
Use known destination/release point keywords and variants, including examples such as Narayanganj, Rupshi, Nagorbari, Noapara, Ashuganj, and abbreviated spellings.

## Workflow
1. Normalize message text and phone numbers.
2. Detect mobile numbers.
3. Segment likely vessel lines.
4. Classify mother vessel vs lighter vessel.
5. Extract product, capacity, destination, and account context.
6. Produce admin draft for review.

## Business Rules
- Parser output is a draft, not final client confirmation.
- Admin review is required before escort assignment confirmation.
- Ambiguous extraction should route to admin/manual review.

## Cross References
- ../02_admin_system/escort_workflow.md
- ai_system_prompt.md
- identity_brain.md

## Revision History
- 2026-06-19: Created from vessel classification and escort parsing rules.
