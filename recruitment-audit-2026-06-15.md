# Recruitment Pipeline Audit — 2026-06-15
**Post-fix edition — verified live state as of 17:20 CEST**

---

## Executive Summary

Four confirmed failures were found, fixed, and verified during this session. The system is now
operating cleanly: zero queued or dead-lettered messages, all sessions expired correctly, and
both code paths that could silently drop or hallucinate recruitment replies have been hardened.
Two historical incidents (3 false replies sent May 17, 2 Meta inquiries lost June 14–15) remain
as informational findings; root causes are now mitigated.

**Overall health: HEALTHY** (was: DEGRADED pre-session)

---

## Runtime State (verified 17:20 CEST)

| Component | State | Detail |
|---|---|---|
| fazle-core | Running | PID 3764512, started 17:17 CEST (patches loaded) |
| Bridge 1 | Connected | Polling, no errors |
| Bridge 2 | Connected | Reconnected ~13:00 CEST, delivering normally |
| Outbound queue | Clean | 218 sent · 0 pending · 0 failed · 0 DLQ |
| Recruitment sessions | Clean | 10 sessions, all `expired` · 0 collecting |
| LLM provider | Active | 42 calls in past 24h, latest 14:13 UTC |
| AUTO\_REPLY\_ENABLED | `false` | Safe mode — only recruitment path is live |
| RECRUITMENT\_AUTOREPLY\_ENABLED | `true` | Explicit-keyword messages auto-queued |
| OUTBOUND\_ENABLED | `true` | Sweep worker active |

---

## Confirmed Findings and Resolutions

---

### FINDING 1 — RESOLVED: Bridge 2 Transport Failure (25 DLQ entries)

**Severity:** Critical
**Root cause:** Bridge 2's WhatsApp WebSocket keepalive timed out. The Go binary process
remained running but the connection was dead, causing every outbound HTTP request to Bridge 2
to return HTTP 400. Over the preceding ~6 hours, 25 messages accumulated in the DLQ as
`max_attempts` was exhausted.

**Evidence:**
- Bridge 2 logs contained repeated: `Keepalive timed out`
- All 25 DLQ entries had `last_error` matching Bridge 2 HTTP 400 responses
- Bridge 1 was unaffected throughout

**Fix applied:**
```bash
sudo systemctl restart whatsapp-bridge2.service
# Bridge reconnected: "Successfully authenticated", "Connected to WhatsApp"
# Session store intact at /home/azim/whatsapp2/store/ — no QR scan needed
```

Then reset and retried DLQ:
```sql
UPDATE fazle_outbound_queue
SET status='pending', attempts=0, next_retry_at=NOW(), last_error=NULL
WHERE status='dlq';
```

**Verified:** All 25 messages delivered within one sweep cycle. Queue: 218 sent, 0 DLQ.

---

### FINDING 2 — RESOLVED: Silent Exception Drop in `_should_recruitment_autoreply`

**Severity:** Critical (silent data loss)
**File:** `app/main.py` — `_should_recruitment_autoreply()`
**Root cause:** A single broad `except Exception` block covered both `detect_identity()` and
`recruitment_eligibility()`. When `detect_identity` raised any exception (DB timeout,
identity table unavailable, network error), the function returned `False` immediately
without calling `recruitment_eligibility`. This meant explicit recruitment messages from
unknown senders were silently dropped to draft or lost, even when they contained unambiguous
keywords like "চাকরি" or "job".

**Evidence:**
- LLM log showed `detect_identity` failures in prior hours
- Two confirmed Meta inquiries (8801958122307, 8801975733944) had no queue or draft entry,
  consistent with this path returning False before `_save_draft` was even reached

**Fix applied — split into two independent try/except blocks:**

```python
async def _should_recruitment_autoreply(sender_clean: str, text: str) -> bool:
    if not settings.recruitment_autoreply_enabled:
        return False
    from modules.phone_normalizer import normalize_phone
    sender_clean = normalize_phone(sender_clean) or sender_clean
    if sender_clean in _admin_numbers_set():
        return False

    # Step 1: identity lookup — degrade gracefully to 'unknown' on any error.
    role = "unknown"
    try:
        from modules.identity_brain import detect_identity
        identity = await detect_identity(sender_clean, text)
        role = identity.get("identity_role") or identity.get("role") or "unknown"
    except Exception as e:
        log.warning(
            "[RECRUIT-GATE] identity check failed for %s, using role='unknown': %s",
            sender_clean, e,
        )

    # Step 2: eligibility — explicit keywords still fire when identity is unknown.
    try:
        from modules.intent import classify as classify_intent
        from modules.recruitment_flow import recruitment_eligibility
        decision = await recruitment_eligibility(
            sender_clean, text, role=role, intent=classify_intent(text),
        )
        return bool(decision["autosend"])
    except Exception as e:
        log.warning(
            "[RECRUIT-GATE] eligibility check failed for %s: %s", sender_clean, e,
        )
        return False
```

**Impact:** If `detect_identity` fails, `role` degrades to `"unknown"`, which is still
eligible for the candidate path. Explicit keywords ("চাকরি", "job", "নিয়োগ", etc.) now fire
correctly regardless of identity DB health.

**Verified:** Code loaded in PID 3764512. `role='unknown'` passes the OPERATIONAL\_ROLES filter
and the `explicit_recruitment` path returns `autosend=True`.

---

### FINDING 3 — RESOLVED: Place-Name Hallucination Guard Missed Inflected Bengali Forms

**Severity:** High (factual hallucination risk)
**File:** `modules/recruitment_ai/__init__.py` — `enforce_recruitment_reply_policy()`
**Root cause:** The place-name regex tail `(?:য়|য়|ে)?` only caught 3 optional suffixes and
had a duplicate পাড়া pattern. Inflected Bengali place forms like "চরপাড়ার" (with "র" suffix)
and "জেলেপাড়ায়" were not matched, allowing hallucinated location claims to pass the policy
check. One confirmed false reply ("আমরা চরপাড়ার ঘাটে...") slipped through because
"চরপাড়ার" was not caught by the old regex.

**Fix applied:**

Old regex (broken):
```python
r"(?:পাড়া|পাড়া|ঘাট|এলাকা|জেলা|বন্দর)(?:য়|য়|ে)?"
```

New regex with inflection-absorbing tail and stem-stripping comparison:
```python
place_patterns = re.findall(
    r"[\wঀ-৿-]{2,}(?:পাড়া|ঘাট|এলাকা|জেলা|বন্দর)[ঀ-৿]{0,3}",
    cleaned,
)
_INFLECTION_SUFFIXES = ("য়ে", "য়", "তে", "র", "ে", "এ")

def _place_stem(token: str) -> str:
    for sfx in _INFLECTION_SUFFIXES:
        if token.endswith(sfx):
            return token[: -len(sfx)]
    return token

unsupported_places = [
    p for p in place_patterns
    if _place_stem(p).lower() not in source_context.lower()
]
```

**Why the stem check matters:** Without `_place_stem()`, "আগ্রপাড়ায়" (the office address
in inflected form) would be flagged as unsupported because the source text contains
"আগ্রপাড়া" (uninflected). The stem function strips the inflection before comparison so
legitimate office-location replies are not blocked.

**Verified with Python test:**
```
"আমরা চরপাড়ার ঘাটে নিয়োগ করছি" → BLOCKED (চরপাড়ার not in source)
"অফিস আগ্রপাড়ায় অবস্থিত"        → NOT BLOCKED (আগ্রপাড়া in source after stemming)
```

---

### FINDING 4 — RESOLVED: 9 Stale Sessions Stuck in `collecting` Stage

**Severity:** Medium
**Root cause:** `get_active_session()` only expires sessions lazily when a message arrives
for that phone. Nine sessions from May 2–31 had no follow-up message, so the 24h TTL never
triggered. These stale sessions would have caused returning senders to receive
"আপনার আবেদন ইতিমধ্যে প্রক্রিয়াধীন" instead of a fresh intake prompt.

**Sessions affected:** 9 phones, last activity 2026-05-02 through 2026-05-31

**Fix applied:**
```sql
UPDATE fazle_recruitment_sessions
SET funnel_stage = 'expired', updated_at = NOW()
WHERE funnel_stage IN ('collecting', 'new')
  AND updated_at < NOW() - INTERVAL '24 hours';
-- 9 rows updated
```

**Verified:** All 10 sessions now `expired`. `get_active_session()` returns `None` for all
these phones, ensuring fresh intake is offered if they message again.

---

## Historical Incidents (Root Cause Mitigated, Not Reversible)

---

### INCIDENT A — 3 False Recruitment Replies to 8801817025576 (2026-05-17)

**Status:** Historical / sent messages cannot be recalled
**What happened:** Three outbound messages were delivered to this number with LLM-generated
content referencing locations not in the source-of-truth. The session (created 2026-05-17,
beyond 24h TTL) was not yet expired. At the time, `AUTO_REPLY_ENABLED` was likely `true`
(before safe-mode recovery), bypassing the recruitment gate entirely.

**Mitigation in place:**
- `AUTO_REPLY_ENABLED=false` — only recruitment path sends
- FINDING 3 fix blocks hallucinated place names before sending
- Session is now `expired`; this phone would receive fresh intake if they message again

---

### INCIDENT B — 2 Meta Inquiry Replies Lost (2026-06-14 23:57 and 2026-06-15 01:25)

**Status:** Historical / replies generated but never delivered
**What happened:** LLM log confirms Groq generated replies for 8801958122307 and 8801975733944.
No queue entry and no draft entry was found for either. Both Meta messages had empty
`source_message_ref`, making `_record_meta_delivery_state` a no-op. Most likely path:
`detect_identity` raised an exception → original single try/except returned `False` →
`recruit_gate=False` → draft path failed or was disabled at the time.

**Mitigation in place:**
- FINDING 2 fix (split try/except) prevents identity failure from blocking keyword path
- Both numbers were genuine inquiries; they can re-message and will be handled correctly

---

## Open Risk Register

| # | Risk | Severity | Owner |
|---|---|---|---|
| R-01 | Bridge keepalive timeout has no auto-recovery — if Bridge 2 loses WebSocket again, DLQ accumulates silently for hours until manual detection | High | Ops — add health check or systemd keepalive watchdog |
| R-02 | `_record_meta_delivery_state` is silent no-op when `message_id=""` — some Meta messages arrive without an ID, making delivery tracking impossible for those | Medium | Dev — log warning on empty ID; consider sender+timestamp fingerprint |
| R-03 | No alerting on DLQ growth — circuit-breaker alert exists but no threshold alert for sustained accumulation | Medium | Ops — add monitoring alert for DLQ count > 0 sustained > 15min |
| R-04 | Gap-scan warnings on startup — `fpe.gapscan` reports missing archive entries (bridge1: 515, bridge2: 4–6) — informational, not causing current failures | Low | Dev — investigate sqlite/postgres archive sync |

---

## Code Changes Made This Session

| File | Change | Risk |
|---|---|---|
| `app/main.py` | Split `_should_recruitment_autoreply` into two try/except blocks; Step 1 degrades identity failure to `role='unknown'` instead of returning False | Low — strictly more permissive for unknown senders with explicit keywords |
| `app/main.py` | Added `is_recruitment_trigger` warning branch in `_handle_meta_message` else path | Nil — logging only |
| `modules/recruitment_ai/__init__.py` | Hardened `enforce_recruitment_reply_policy` place-name regex; added `_place_stem()` for inflected form comparison | Low — strictly more restrictive (catches more hallucinations) |

---

## Database Operations Performed This Session

```sql
-- Retry 25 DLQ entries after Bridge 2 restart
UPDATE fazle_outbound_queue
SET status='pending', attempts=0, next_retry_at=NOW(), last_error=NULL
WHERE status='dlq';

-- Expire 9 stale sessions beyond 24h TTL
UPDATE fazle_recruitment_sessions
SET funnel_stage = 'expired', updated_at = NOW()
WHERE funnel_stage IN ('collecting', 'new')
  AND updated_at < NOW() - INTERVAL '24 hours';
```

---

## Final Checklist

- [x] Bridge 2 connected and delivering (HTTP 200, session store intact, no QR rescan needed)
- [x] 25 DLQ entries retried and delivered — queue at 218 sent / 0 DLQ / 0 pending
- [x] `_should_recruitment_autoreply` split try/except deployed and running (PID 3764512)
- [x] Place-name hallucination guard catches inflected Bengali forms; stem comparison prevents false positives on office address
- [x] All 10 recruitment sessions in `expired` state — no stale `collecting` rows
- [x] fazle-core restart clean — application startup complete, no errors in startup logs
- [ ] Bridge keepalive watchdog not implemented (R-01, open)
- [ ] DLQ alerting threshold not configured (R-03, open)
