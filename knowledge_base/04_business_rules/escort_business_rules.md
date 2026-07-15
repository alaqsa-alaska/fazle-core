---
title: Escort Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-07-05
runtime_index: true
---

# Escort Business Rules

## Message Direction Rules

Production escort-order parsing is direction-gated first. Parsing does not begin until the sender and recipient match one of the approved flows below.

### Client -> Bridge

Allowed bridge numbers:
- `8801880446111`
- `8801958122300`

Allowed client numbers:
- `ESCORT_CLIENT_PHONES` from `.env`

Meaning:
- The client is requesting escort service.
- This message may create a draft only.
- It is never the source of truth.

Minimum required fields:
- Mother Vessel
- Lighter Vessel
- Master Mobile

If any minimum field cannot be identified with reasonable confidence:
- Ignore the message
- Create no draft
- Create no roster side effect

If the minimum fields are present:
- Parse with best effort
- Create one draft order only
- Keep status unconfirmed / waiting confirmation
- Never auto-confirm from the client message

### Bridge -> Escort Client

Allowed bridge numbers:
- `8801880446111`
- `8801958122300`

Allowed recipients:
- `ESCORT_CLIENT_PHONES` from `.env`

Meaning:
- Official escort confirmation
- This is the only source of truth for final escort assignment data

Required confirmation fields:
- Mother Vessel
- Lighter Vessel
- Escort Name
- Escort Mobile
- Start Date

If any required confirmation field is missing:
- Ignore the message as escort confirmation
- Do not update a draft
- Do not create a confirmed row

If all required confirmation fields are present:
- Match an existing draft by client phone, master mobile, mother vessel, and start date
- Update the existing row when matched
- Overwrite incorrect draft values with confirmation values
- Create one confirmed row only when no draft exists
- Never create duplicate rows

## Source Of Truth Policy

Not source of truth:
- Client order text
- AI parser guess
- Draft row

Source of truth:
- Bridge -> Escort Client confirmation message

Conflict rule:
- If draft data conflicts with confirmation data, confirmation always wins

## Parsing Rules

Primary signal:
- Master Mobile Number

Secondary signals:
- Mother vessel block
- Lighter vessel block
- Importer
- Cargo
- Destination
- Capacity

This is a list of signal types, not the order they appear in a message.
Destination is recognized by content (a Bangladeshi place name, full or
short-hand), not position. Capacity commonly trails the lighter vessel name.
Importer/cargo commonly sits after the mother vessel name or before the
lighter vessel name, and may be wrapped in WhatsApp `*bold*` markup — see
`../06_developer_system/escort_order_parser.md` Parser Philosophy for the
full positional notes.

Rules:
- Every valid Bangladesh mobile belongs to one lighter block
- The vessel nearest that mobile is the lighter vessel
- Destination and capacity nearest that mobile belong to that lighter
- A block containing a mobile number cannot be the mother vessel
- `MV`, `M.V.`, `Mv`, and similar labels are helper signals only
- Those labels may appear before both mother vessels and lighter vessels
- Never classify mother vessel from `MV` label alone
- `Mother Vessel:` label, when present, is the highest-priority mother-vessel source
- `Lighter Vessel:` / `Lighter Name:` label, when present, is the highest-priority lighter-vessel source
- In compact multiline formats, an `MV` line with nearby `MT/M.T` capacity and mobile belongs to lighter
- A trailing parenthesized `MV` without mobile (for example `(MV HORDA)`) is a mother-vessel candidate

Supported parsing modes:
- Block-based parsing has higher priority
- Line-based parsing remains supported as fallback

## Normalization Rules

Destination:
- `Local` and `CTG` -> `Chattogram`
- `Rupshi` -> `Narayanganj`
- `N.Bari` -> `Nagarbari`

Capacity:
- `700 m.t`
- `700 MT`
- `700mt`
- all normalize to `700 MT`

## Validation And Duplicate Prevention

Before saving any escort order row, the parser must have:
- Mother Vessel
- Lighter Vessel
- Master Mobile

If any of the three are missing:
- Discard the message
- Do not save draft data

Duplicate-prevention match priority:
1. Master Mobile
2. Mother Vessel
3. Client Phone
4. Start Date

## Roster Synchronization Rule

`wbom_escort_programs` is the operational parent.

`escort_roster_entries` is the roster projection.

When a program becomes confirmed by source-of-truth confirmation:
- Program status becomes `confirmed`
- Roster status must not remain `draft`
- Roster status is synced to `confirmed`
- UI must show a clean final state without program/roster conflict

## Escort Client Configuration Rule

`ESCORT_CLIENT_PHONES` is the whitelist for escort-order directions.

Rules:
- Numbers are configured in `.env`
- Add and remove operations must normalize Bangladesh mobile format
- Invalid numbers are rejected
- Duplicate numbers are not re-added
- Updating the list clears the cached settings object immediately
- No service restart is required for whitelist changes

## Browser And Admin Output Rules

- Generated admin notification must include only one Automated Reply footer
- Search and export endpoints must qualify joined SQL columns with table aliases
- The roster UI must not send empty or malformed query parameters
- Optional missing fields should render as `—`, not throw runtime errors

## এসকর্ট পেমেন্ট ক্যালকুলেশন ফর্মুলা

### ডিফল্ট ডেইলি রেট
`DEFAULT_DAILY_RATE = ৳400/দিন` (PAY-01: ৳12,000 ÷ 30)

### ডেইলি রেট ক্যালকুলেশন
```
daily_rate = basic_salary ÷ 30
```
যদি `basic_salary` না থাকে বা শূন্য হয়: `daily_rate = 400` (৳400/দিন ব্যবহার হয়)

### গ্রস পেমেন্ট
```
gross_amount = round(duty_days × daily_rate, 2)
```

### নেট দেয় (ফ্লোর: শূন্য)
```
net_payable = max(gross_amount − food_bill − conveyance − advances, 0)
```

### গুরুত্বপূর্ণ নিয়ম
- নেট পেমেন্ট কখনো ০-এর নিচে যাবে না (`max(..., 0)` দিয়ে নিশ্চিত)
- খাদ্য বিল কর্তন (`food_bill`): প্রোগ্রাম টেবিল থেকে, ডিফল্ট ০
- যাতায়াত কর্তন (`conveyance`): প্রোগ্রাম টেবিল থেকে, ডিফল্ট ০
- অ্যাডভান্স কর্তন: ওই এসকর্ট প্রোগ্রাম ও চলতি মাসের `final` স্ট্যাটাস অ্যাডভান্স মাত্র
- ডিউটি দিন: রোস্টারে দিন গণনা; না থাকলে প্রোগ্রামের তারিখের পার্থক্য + 1

## Cross References

- `../05_workflows/escort_workflow.md`
- `../05_workflows/escort_source_of_truth_workflow.md`
- `../06_developer_system/escort_order_parser.md`
- `../06_developer_system/escort_roster_system.md`
