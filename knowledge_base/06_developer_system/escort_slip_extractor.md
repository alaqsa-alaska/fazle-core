---
title: Escort Slip Extractor
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Escort Slip Extractor

**Source:** `modules/escort_slip_extractor/__init__.py` (948 lines — read 2026-06-23)
**Priority:** P2 (critical path — now in bridge_poller ESX-WIRE, Session 10)
**DB table:** `escort_slip_extractions`
**See also:** `bridge_poller.md` → Image OCR Pipeline section

---

## Purpose

Extracts structured fields from WhatsApp images of escort slip documents.
Handles four physical document forms:
1. **Printed template slip** — company-issued form with printed field labels
2. **Handwritten blank-paper slip** — ghat supervisor writes freeform on blank paper
3. **Mixed form** — handwritten values on printed template
4. **Unknown document** — insufficient signals to classify

The extracted `completion_date` field is the **TWO-DATE RULE detector** used by
`bridge_poller` to distinguish assignment slips from release slips:
- `completion_date is None` → one date → assignment slip (escort going on duty)
- `completion_date is not None` → two dates → supervisor stamped → **release slip** (duty completed)

---

## EscortSlipResult TypedDict

```python
class EscortSlipResult(TypedDict):
    document_type: str          # see Document Types below
    mother_vessel: Optional[str]
    lighter_vessel: Optional[str]
    master_mobile: Optional[str]
    escort_name: Optional[str]
    escort_mobile: Optional[str]
    start_date: Optional[str]   # ISO-like normalised date
    start_time: Optional[str]
    completion_date: Optional[str]  # None = assignment; not None = release
    completion_time: Optional[str]
    release_place: Optional[str]
    start_shift: Optional[str]  # "D" or "N" — parser only, NOT saved to DB
    end_shift: Optional[str]    # "D" or "N" — parser only, NOT saved to DB
    signatures: SignatureResult
    confidence: float           # 0.0–1.0
    missing_fields: list[str]   # FULL_FIELDS keys that are empty
    raw_ocr_text: str
    extraction_id: Optional[int]  # escort_slip_extractions.id after save
```

### SignatureResult TypedDict

```python
class SignatureResult(TypedDict):
    lighter_master_signed: bool
    ghat_supervisor_signed: bool
    company_signed: bool
    unknown_signature: bool
    signature_date: Optional[str]
    confidence: float
```

---

## Document Types

| Value | Detection rule |
|---|---|
| `printed_template_slip` | template_score ≥ 3 AND handwritten_score < 3 |
| `mixed_form` | template_score ≥ 3 AND handwritten_score ≥ 3; or 1+ from each |
| `handwritten_blank_slip` | template_score < 3 AND handwritten_score ≥ 2 |
| `unknown_document` | Neither template nor handwritten signals present |

**Template signals (sample):** `escort slip`, `name of mother vessel`, `master mobile`, `release place`, `ghat supervisor`, `al-aqsa` (18 keywords)
**Handwritten signals (sample):** `mv `, `lighter`, `guard`, `নাম`, `রিলিজ`, `তারিখ` (14 keywords)

---

## Required and Full Field Sets

```python
REQUIRED_FIELDS = [
    "mother_vessel", "lighter_vessel", "escort_name",
    "escort_mobile", "start_date", "completion_date",
]

FULL_FIELDS = REQUIRED_FIELDS + [
    "master_mobile", "start_time", "completion_time", "release_place",
]
```

`missing_fields` is populated from `FULL_FIELDS` (10 fields), not just `REQUIRED_FIELDS`.

---

## 3-Pass OCR Pipeline

`_run_full_ocr(file_path)` runs three passes in parallel where possible:

```
Pass 1 (async IO):  media-processor /ocr endpoint   — Tesseract, no preprocessing
Pass 2 (executor):  direct tesseract CLI             — lang=eng+ben, --psm 6, --oem 1 (LSTM)
Pass 3 (executor):  ImageMagick preprocess → tesseract
    preprocess: -colorspace Gray -normalize -sharpen 0x1.5 -contrast-stretch 2% -threshold 60%
```

**Merge strategy:**
1. Pick the longest OCR result as the `base`
2. Add unique lines (> 3 chars) from the other two passes
3. Result = `base + extra_unique_lines`

Passes 1 + 2 run in parallel via `asyncio.gather()`. Pass 3 runs sequentially after.

---

## Extraction Strategy

| Document type | Strategy |
|---|---|
| `handwritten_blank_slip` | `_extract_handwritten_fields(raw_text)` only |
| All others | `_extract_template_fields(raw_text)` first, then handwritten fills gaps |

**Gap-fill / override rules (template → handwritten):**
- Empty template field → take handwritten value
- `escort_name`: template value kept only if `_is_valid_name()` passes
- `escort_mobile`, `master_mobile`: template kept only if `_is_valid_phone_candidate()` passes
- `start_date`, `completion_date`: template kept only if `_is_valid_date_candidate()` passes
- `release_place`: template kept only if `_is_valid_release_place()` passes

---

## Confidence Scoring

```python
def _score_confidence(fields, doc_type) -> float:
    filled = sum(1 for k in REQUIRED_FIELDS if fields.get(k))
    score = filled / 6   # REQUIRED_FIELDS has 6 entries
    if doc_type in ("printed_template_slip", "mixed_form"):
        score = min(score + 0.1, 1.0)  # bonus for known document type
    return round(score, 2)
```

| Required fields filled | Base score | With doc-type bonus |
|---|---|---|
| 0/6 | 0.00 | 0.10 |
| 3/6 | 0.50 | 0.60 |
| 5/6 | 0.83 | 0.93 |
| 6/6 | 1.00 | 1.00 |

In `bridge_poller`, the threshold used is:
- `conf_pct < 10` (i.e., `confidence < 0.1`) → unknown/no-match path
- `conf_pct < 60` (i.e., `confidence < 0.6`) → warn admin, ask sender to confirm

---

## Label Blacklist

`_LABEL_BLACKLIST` (frozenset, 40+ entries) prevents field-label text from being
mistakenly extracted as field values. Contains form labels like `"mobile no"`, `"date"`,
`"release place"`, `"ghat supervisor"`, `"escort name"`, `"lighter vessel"`, etc.

---

## API

```python
from modules.escort_slip_extractor import extract_escort_slip

result: EscortSlipResult = await extract_escort_slip(
    file_path="/path/to/image.jpg",
    source_label="bridge1",   # logged and stored in escort_slip_extractions
    save_to_db=True,          # default True — saves to escort_slip_extractions
)

# TWO-DATE RULE check (used in bridge_poller):
is_release = result.get("completion_date") is not None
```

Also exposed via REST:
```
POST /api/escort-slip/extract   (multipart upload, requires API key)
GET  /api/escort-slip/extractions/<id>
```

---

## DB Table: `escort_slip_extractions`

From `db/migrations/001_safe_mode_and_escort_extractor.sql`:

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK |
| `source_file` | TEXT | Original file path |
| `document_type` | TEXT | printed_template_slip / handwritten_blank_slip / mixed_form / unknown_document |
| `mother_vessel` | TEXT | Normalised to UPPERCASE |
| `lighter_vessel` | TEXT | Normalised to UPPERCASE |
| `master_mobile` | TEXT | Normalised phone |
| `escort_name` | TEXT | |
| `escort_mobile` | TEXT | Normalised phone |
| `start_date` | TEXT | ISO-like normalised |
| `completion_date` | TEXT | Present = release slip; NULL = assignment |
| `release_place` | TEXT | |
| `signatures_json` | JSONB | SignatureResult |
| `confidence` | NUMERIC(4,2) | 0.00–1.00 |
| `raw_text` | TEXT | Full merged OCR output |
| `created_at` | TIMESTAMPTZ | Default NOW() |

**Indexes:** `idx_escort_slip_created` (created_at DESC), `idx_escort_slip_vessel` (mother_vessel)

Note: `start_shift` and `end_shift` are parser-only outputs — NOT saved to DB.

---

## Integration with bridge_poller (ESX-WIRE)

The `completion_date` field is the release detector in `bridge_poller` Step 2:

```python
# bridge_poller STEP 2 — called for each eligible image
ocr_result = await extract_escort_slip(file_path, source_label=bridge_name)

is_release = ocr_result.get("completion_date") is not None  # TWO-DATE RULE
```

The compat dict translation for `handle_ocr_release_slip()`:
- `escort_name` → `employee_name`
- `lighter_vessel` or `mother_vessel` → `vessel`
- `completion_date` → `date`
- `release_place` → `location`
- `confidence × 100` → `confidence_score` (int, 0–100)
- `raw_ocr_text` → `raw_text`
- hardcoded `"release_slip"` → `slip_type` ← **required** by handle_ocr_release_slip()

---

## Related

- `bridge_poller.md` — ESX-WIRE integration and full image pipeline
- `knowledge_base/00_governance/management_decisions.md` — Session 10 ESX-WIRE authorization
- `modules/escort_lifecycle/__init__.py` — `handle_ocr_release_slip()` consumes extracted data
- `ocr_engine.md` — `ocr_processor` (separate module — handles single-pass OCR for non-slip images)
