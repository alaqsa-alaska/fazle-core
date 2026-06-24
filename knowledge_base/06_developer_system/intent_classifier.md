---
title: Intent Classifier
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Intent Classifier

**Source:** `modules/intent/__init__.py` (172 lines — read 2026-06-23)
**Priority:** P3
**Depends on:** `rapidfuzz` (fuzzy matching library)

---

## Purpose

Classifies any inbound WhatsApp message into one of 14 intent categories.
Used by `message_router` at Step 3 (before identity, before AI) to gate routing decisions —
financial draft gate, safe-autosend gate, and recruitment path all read the intent string.

---

## Classification Pipeline (3-pass)

```
Input text
   │
   ▼
1. REGEX rules       → highest priority; fires on structural patterns (e.g. "^MV ")
   │  (no match)
   ▼
2. Direct substring  → guaranteed win over fuzzy (score = 10000 + keyword_length)
   │  (no match)
   ▼
3. Fuzzy match       → rapidfuzz partial_ratio ≥ threshold (default 72)
   │  (no match)
   ▼
"unknown"            → caller uses Ollama LLM fallback
```

**Key rule:** Once a direct-substring match is found, no fuzzy match is ever considered.
Fuzzy matching only runs if zero direct matches exist across all intents.

---

## Intent Categories (14)

| Intent | Keywords (sample) | Regex rule |
|---|---|---|
| `recruitment` | চাকরি, job, apply, নিয়োগ, কাজ চাই | — |
| `salary_query` | বেতন, salary, আমার বেতন, কত পাব | regex: `আমার\s*বেতন` |
| `payment_due` | টাকা, payment, পাওনা, হিসাব, balance | regex: `^id\s*:`, `টাকা\s*কবে` |
| `escort_duty` | ডিউটি, vessel, MV, lighter, program | regex: `^(mv|m/v)\s+\w` |
| `attendance` | হাজিরা, attendance, উপস্থিত, present | — |
| `complaint` | অভিযোগ, complaint, প্রতারণা, abuse | — |
| `client_order` | লোক লাগবে, escort needed, নতুন প্রোগ্রাম | regex: `লোক\s*লাগবে` |
| `leave` | ছুটি, leave, অসুস্থ, হাসপাতাল | — |
| `join` | যোগদান, joining, জয়েন, ভর্তি হব | regex: `যোগদান`, `joining\s*date` |
| `slip_submission` | slip, স্লিপ, রিলিজ স্লিপ, document | — |
| `greeting` | সালাম, hello, menu, #menu, /menu | — |
| `office_location` | অফিস কোথায়, ঠিকানা, victoria gate | regex: `অফিস\s*কোথায়` |
| `voice_note` | (used for audio transcript results) | — |
| `unknown` | — | fallback; caller invokes Ollama |

---

## API

```python
from modules.intent import classify, is_admin_command

intent: str = classify(text, threshold=72)
# Returns one of the 14 intent strings above

is_cmd: bool = is_admin_command(text)
# True if text matches admin command patterns (id:, MV ..., release employee)
```

---

## Threshold Behaviour

`threshold=72` (default):
- `fuzz.partial_ratio(keyword, text) ≥ 72` → intent matched via fuzzy
- Direct substring always wins regardless of threshold
- If multiple fuzzy matches tie, the first matched intent string wins (dict iteration order)

---

## Where Called

| Caller | Usage |
|---|---|
| `modules/message_router/__init__.py` Step 3 | Primary intent for routing decisions |
| `modules/bridge_poller/__init__.py` | Copy for draft-gate decisions (financial, complaint) |
| `modules/social_auto_reply/classifier.py` | Social engine intent classification |
| `modules/recruitment_flow/__init__.py` | Guards recruitment_eligibility path |

---

## Downstream Effects of Intent

| Intent result | Gate triggered |
|---|---|
| `payment_due`, `salary_query` | `_FINANCIAL_DRAFT_INTENTS` → forced draft |
| `complaint` | `_COMPLAINT_PHRASES` check → forced draft |
| `recruitment` | `recruit_gate` → may bypass SAFE MODE |
| `unknown` | `AI_SAFE_MODE` may block if intent uncertain |

---

## Adding a New Intent

1. Add keyword list to `INTENT_KEYWORDS` dict
2. Add regex rule to `REGEX_INTENTS` if structural pattern exists
3. Update `_FINANCIAL_DRAFT_INTENTS` in `bridge_poller` if intent is financial
4. Update this article
