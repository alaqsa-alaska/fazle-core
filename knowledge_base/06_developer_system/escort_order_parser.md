---
title: Escort Order Parser Source Of Truth
owner: Fazle Core Admin
status: active
last_verified: 2026-07-05
runtime_index: true
source_module: modules/escort
---

# Escort Order Parser Source Of Truth

`modules/escort` contains the deterministic escort-order parser and message-direction gates. This article is the developer-facing source of truth for how escort intake and confirmation parsing work in production.

## Parser Philosophy

Primary signal:
- Master Mobile Number

Secondary signals:
- Mother vessel block
- Lighter vessel block
- Importer
- Cargo
- Destination
- Capacity

**This list is a set of signal types, not a fixed sequence.** Real client
messages do not present these in this order, and the parser must not assume
they do. Observed real-world positioning (2026-07-15):
- Destination is identified by content — a Bangladeshi place name in full or
  short-hand form (e.g. `local`, `CTG`, `N.Bari`) — not by its position in the
  message.
- Capacity commonly appears **after** the lighter vessel name/mobile (end of
  the lighter line), not in a fixed slot.
- Importer/product/cargo commonly appears **after the mother vessel name or
  before the lighter vessel name** — i.e. in the free-text zone between the
  two vessel blocks — and may be wrapped in WhatsApp markdown (`*bold*`),
  which the importer/cargo extraction must tolerate rather than treat as a
  line terminator.

Important rules:
- Mobile always anchors the lighter block
- The nearest vessel name to that mobile is the lighter vessel
- A block with a mobile number cannot be the mother vessel
- `MV`, `M.V.`, `Mv`, and similar labels are helper signals only
- `MV` may appear before both mother and lighter vessels
- Never rely on `MV` label alone
- If `Mother Vessel:` label is present, that value wins for mother vessel
- If `Lighter Vessel:` or `Lighter Name:` label is present, that value wins for lighter vessel
- In multi-line compact blocks, `MV` line + nearby capacity + nearby mobile is a lighter candidate
- A trailing parenthesized MV line (e.g. `(MV HORDA)`) is treated as mother vessel when no mobile is attached

## Message Direction Gates

### Draft Intake Gate

Accept only:
- sender in `ESCORT_CLIENT_PHONES`
- recipient is bridge `8801880446111` or `8801958122300`

Minimum required fields:
- `mother_vessel`
- `lighter_vessel`
- `master_mobile`

If minimum fields are missing:
- ignore message
- do not create draft

If minimum fields are present:
- parse best effort
- create one draft row
- keep it unconfirmed

### Confirmation Gate

Accept only:
- sender is bridge `8801880446111` or `8801958122300`
- recipient in `ESCORT_CLIENT_PHONES`

Required confirmation fields:
- `mother_vessel`
- `lighter_vessel`
- `escort_name`
- `escort_mobile`
- `start_date`

If required confirmation fields are missing:
- ignore message as confirmation

If present:
- treat message as source of truth
- update matching draft when found
- create confirmed row only when no draft exists
- never create duplicates

## Supported Parsing Modes

- Block-based parsing has priority
- Line-based parsing remains supported

Typical mother vessel block:
- mother vessel name
- importer
- cargo
- account / A/C
- sometimes date

Typical lighter block:
- lighter vessel
- master mobile
- destination
- capacity

## Field Detection Rules

### Lighter Vessel

Accepted Bangladesh mobile formats:
- `017...`
- `018...`
- `019...`
- `88017...`
- `+88017...`

Each mobile belongs to one lighter block.

Serial numbers such as:
- `8. Haji Salim`
- `9. Banglar Odhinayok`
- `1.sheikh enterprise 2-017...`

must be stripped so only the vessel name remains.

### Mother Vessel

Mother vessel is usually the nearby vessel block without a mobile number.

Supporting clues:
- `MV`
- `M.V.`
- `Mother Vessel`
- `At-O/A`
- importer / cargo / account fields

Hard rule:
- if a candidate block contains a mobile, it is not the mother vessel

Mother/lighter disambiguation hard rule:
- `Mother Vessel: MV Horda` must remain mother vessel
- `Lighter Vessel: MV Al Balad-3` must remain lighter vessel
- `M.V Al Balad-3` + `2200 M.T` + `01716...` is a lighter block, not mother
- `(MV HORDA)` at the end with no mobile remains mother vessel

### Importer

Usually near the mother vessel block.

Supported labels include:
- `A/C`
- `A/c`
- `Account`
- `Importer`

### Cargo

Cargo belongs to the mother vessel block when possible.

Fallback:
- search globally
- longest cargo keyword wins

### Destination

Destination belongs to the lighter block nearest the mobile.

Normalization:
- `Local` and `CTG` -> `Chattogram`
- `Rupshi` -> `Narayanganj`
- `N.Bari` -> `Nagarbari`

### Capacity

Capacity belongs to the lighter block nearest the mobile.

Normalization:
- `700 m.t`
- `700 MT`
- `700mt`
- all become `700 MT`

## Confirmation Overwrite Rules

When bridge confirmation matches an existing draft, confirmation may overwrite:
- `mother_vessel`
- `lighter_vessel`
- `master_mobile`
- `destination`
- `cargo_type`
- `importer`
- `capacity`
- `escort_name`
- `escort_mobile`
- `start_date`
- `shift`
- `status`

Destination is normalized before save.

If the live schema does not expose `cargo_type` or `importer` as physical columns, those values are preserved in `remarks` instead of forcing a schema change.

## Duplicate Prevention

Matching priority:
1. master mobile
2. mother vessel
3. client phone
4. start date

Rules:
- one client request creates at most one draft
- bridge confirmation updates that row when matched
- confirmed duplicates must not be created

## Notes For Developers

- Do not reintroduce text-only heuristic parsing without direction gates.
- Do not treat client drafts as final truth.
- Do not let roster search/export use ambiguous joined columns.
- UI requests should omit empty params and surface real API errors.
- `modules/bridge_poller._requires_legacy_workflow()` gates whether a
  bridge1/bridge2 message reaches this parser at all before
  `SOCIAL_AUTO_REPLY_SINGLE_ENGINE` diverts it to the social-reply pipeline.
  This gate must treat a Bangladesh mobile-number match
  (`_BD_MOBILE_HINT_RE`) as a first-class trigger, not just literal
  `"m.v"`/`"mv "` substrings — MV-label spacing, punctuation, and case vary
  per client (`Mv.`, `MV `, `mv-`) and a literal-token-only gate previously
  caused valid orders to be silently dropped into the social pipeline before
  ever reaching this module (incident 2026-07-13/14, fixed 2026-07-15).

## Multi-Message + Bangla Extraction (Phase B, Shadow Mode) — 2026-07-19

This section documents `modules/conversation_memory`, a separate module
from `modules/escort` above. `parse_escort_message()` itself is
unmodified by any of this — verified by a dedicated regression test
(`test_existing_production_parser_unchanged`) that asserts its output is
byte-for-byte the same with or without the new module present.

**Why it exists:** `parse_escort_message()` is a pure single-message
parser with no Bangla label awareness. A real, verified gap: the message
`"ভেসেল নেম Horda"` was read as `"MV ভেসেল নেম HORDA"` (the Bangla label
itself leaking into the vessel name) instead of `"MV HORDA"`. Fixing this
inside `parse_escort_message()` would only fix that one string; Phase B
instead adds a reusable label-aware extraction layer
(`modules/conversation_memory/bangla_extraction.py`) plus multi-message
accumulation (`modules/conversation_memory/`), gated behind their own
feature flags and never touching this module's parsing logic.

**Bangla label list** (`bangla_extraction.py`), matched longest-label-first
so a specific label is never shadowed by a shorter one that's a substring
of it:

| Field | Labels |
|---|---|
| Mother vessel (specific, always high-confidence) | মাদার ভেসেলের নাম, মাদার ভেসেল, মাদার শিপ |
| Mother vessel (generic, context-sensitive — see below) | ভেসেল নেম, ভেসেলের নাম |
| Lighter vessel | লাইটার ভেসেল, লাইটার নাম, লাইটার |
| Master mobile | মাস্টার মোবাইল, মোবাইল নম্বর, মাস্টার |
| Destination | গন্তব্য, ডেস্টিনেশন |
| Trip | দ্বিতীয় ট্রিপ, ২য় ট্রিপ, ট্রিপ |
| Capacity | ক্যাপাসিটি, plus unit words টন / এমটি |

**Normalization applied:**
- Bangla digits (০-৯) -> ASCII digits, before any numeric extraction
  (mobile, capacity, trip number).
- WhatsApp markup characters (`*_~\``) stripped before value extraction.
- An existing `MV` / `M.V.` / `এমভি` prefix inside a captured value is
  stripped before re-applying the canonical `"MV " + UPPERCASE` form —
  the same canonical shape `parse_escort_message()` already produces for
  English input, so downstream code never has to special-case which
  parser a value came from.
- Capacity unit words (`টন`, `এমটি`, `mt`) all normalize to the existing
  `<N> MT` form.
- Trip suffix splitting is language-independent: `"Bushra Biva-1(2nd
  Trip)"` -> `lighter_vessel="Bushra Biva-1"` + a separate `trip="2nd
  Trip"` field, applied to whichever parser produced the lighter_vessel
  value.

**Context-sensitive generic label:** the generic `ভেসেল নেম` /
`ভেসেলের নাম` label doesn't say *which* vessel — Bangla client messages
use it for whichever vessel is being clarified at that point in the
conversation. It is extracted as a **low-confidence, non-explicit**
mother-vessel candidate by default. It is only promoted to a
high-confidence explicit label once the target conversation thread
already has both `lighter_vessel` and `master_mobile` set (i.e. the
mother vessel is the only thing left missing) — re-derived using the
actual thread's prior state, never guessed up front from the message
alone.

**Verified real example** (the mandatory Phase B test case,
`test_stage3_mandatory_example.py`):

```text
Message 1: "Bushra Biva-1(2nd Trip)\n1250MT\n01821375926\nOuter To Local\nAC:SST/NMT"
Message 2: "ভেসেল নেম Horda"

Result (shadow candidate, no production draft):
  Mother Vessel: MV HORDA
  Lighter Vessel: Bushra Biva-1
  Trip: 2nd Trip
  Capacity: 1250 MT
  Master Mobile: 01821375926
  Destination: Chattogram
  Complete: true, would_create_draft: true
  Actual wbom_escort_programs row created: false
```

**Rollout:** gated behind `BANGLA_SEMANTIC_PARSER_ENABLED` (off in
production), independent of `MULTI_MESSAGE_ESCORT_PARSER_ENABLED` (also
off) — verified by hand that with the multi-message flag on but this
flag off, the old buggy Bangla behavior still reproduces exactly, so the
fix cannot activate as an unintended side effect of the other flag.

## Verified Examples

Production tests cover:
- Dubai Eco example
- TRUONG MINH PROSPERITY example
- MV label on lighter vessel
- multiple serial numbers
- missing cargo
- missing destination
- missing importer
- `Local -> Chattogram`
- `Rupshi -> Narayanganj`
- draft updated by confirmation
- duplicate prevention
- wrong-direction confirmations ignored
