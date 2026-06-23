"""
Fazle Core — OCR Image Processor (Phase 4D) + PDF Extractor (Phase 4F-Doc)

Calls the local media-processor service (port 8090) to extract text from images
and PDFs. Then parses the text to detect slip type or candidate document type
and extracts structured fields.

Supported slip types:
  escort_slip    — escort/security duty assignment
  release_slip   — end of duty / release form
  payment_slip   — salary/advance payment record
  unknown        — unrecognized document

Supported candidate document types (for PDF + image):
  cv             — resume / CV
  nid            — National Identity Card
  certificate    — education / training certificate
  chairman_cert  — chairman / ward certificate
  handwritten    — handwritten application
  passport_photo — passport-size photo
  passport       — travel passport

Flow:
  file_path → POST /ocr or /extract → raw text → classify → extract fields → return
"""

import logging
import re
import httpx
from typing import TypedDict, Optional

from app.config import get_settings
from modules.image_hash import check_and_register

log = logging.getLogger("fazle.ocr")


class OcrResult(TypedDict):
    raw_text: str
    slip_type: str          # escort_slip | release_slip | payment_slip | unknown
    is_duplicate: bool
    duplicate_of: Optional[int]
    confidence_score: int   # 0–100: OCR extraction quality estimate

    # Parsed fields (best-effort)
    employee_name: Optional[str]
    employee_id: Optional[str]
    date: Optional[str]
    vessel: Optional[str]
    location: Optional[str]
    amount: Optional[str]
    client: Optional[str]
    reference_no: Optional[str]

    # Suggested reply
    reply: str


class DocResult(TypedDict):
    extracted_text: str
    doc_type: str           # cv | nid | certificate | chairman_cert | handwritten | passport_photo | passport | unknown
    filename: str
    confidence_score: int   # 0–100
    reply: str              # safe acknowledgement in Bengali
    auto_send_safe: bool    # True = acknowledgement can be auto-sent


# ── Image OCR pipeline ─────────────────────────────────────────────────────────

async def process_image(file_path: str, message_id: Optional[int] = None) -> OcrResult:
    """Full pipeline: hash check → OCR → classify → parse → confidence → reply."""
    settings = get_settings()

    # Duplicate check
    hash_result = await check_and_register(file_path, message_id)
    if hash_result["is_duplicate"]:
        log.info(f"[ocr] Duplicate image detected for {file_path}")
        return OcrResult(
            raw_text="", slip_type="duplicate", is_duplicate=True,
            duplicate_of=hash_result["duplicate_of_message_id"],
            confidence_score=0,
            employee_name=None, employee_id=None, date=None,
            vessel=None, location=None, amount=None, client=None, reference_no=None,
            reply="এই স্লিপটি পূর্বে জমা হয়েছে। যাচাই চলছে।",
        )

    # OCR call
    raw_text = await _call_ocr(settings.media_processor_url, file_path)
    if not raw_text:
        return OcrResult(
            raw_text="", slip_type="unknown", is_duplicate=False,
            duplicate_of=None, confidence_score=0,
            employee_name=None, employee_id=None, date=None,
            vessel=None, location=None, amount=None, client=None, reference_no=None,
            reply="ছবিটি পড়া সম্ভব হয়নি। স্পষ্ট ছবি পাঠান অথবা টেক্সট আকারে তথ্য দিন।",
        )

    clean_text = _clean_ocr_text(raw_text)
    slip_type = _classify_slip(clean_text)
    fields = _extract_fields(clean_text, slip_type)
    confidence = _compute_confidence(clean_text, fields)
    reply = _build_reply(slip_type, fields, confidence)

    log.info(
        f"[ocr] slip_type={slip_type} conf={confidence} "
        f"name={fields.get('employee_name')} ref={fields.get('reference_no')}"
    )

    return OcrResult(
        raw_text=raw_text, slip_type=slip_type, is_duplicate=False,
        duplicate_of=None, confidence_score=confidence,
        reply=reply, **fields,
    )


# ── PDF / Document pipeline ────────────────────────────────────────────────────

async def process_document(file_path: str, filename: str = "") -> DocResult:
    """Full pipeline for PDF/document: extract → classify doc type → acknowledgement."""
    settings = get_settings()

    extracted_text = await _call_extract(settings.media_processor_url, file_path)
    fn_lower = (filename or "").lower()

    if not extracted_text:
        return DocResult(
            extracted_text="", doc_type="unknown", filename=filename,
            confidence_score=0,
            reply="ডকুমেন্টটি পাওয়া গেছে তবে পড়া সম্ভব হয়নি। অফিসে মূল কপি আনুন।",
            auto_send_safe=False,
        )

    doc_type = _classify_candidate_doc(extracted_text, fn_lower)
    confidence = min(100, max(10, len(extracted_text.split()) * 2))
    reply = _build_doc_acknowledgement(doc_type, filename)
    auto_send_safe = doc_type in (
        "cv", "nid", "certificate", "chairman_cert", "handwritten", "passport_photo", "passport"
    )

    log.info(f"[doc] doc_type={doc_type} conf={confidence} filename={filename}")

    return DocResult(
        extracted_text=extracted_text, doc_type=doc_type, filename=filename,
        confidence_score=confidence, reply=reply, auto_send_safe=auto_send_safe,
    )


# ── HTTP helpers ───────────────────────────────────────────────────────────────

async def _call_ocr(base_url: str, file_path: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/ocr",
                json={"file_path": file_path},
            )
            if r.status_code == 200:
                return r.json().get("text", "").strip()
            log.warning(f"[ocr] media-processor returned {r.status_code}")
    except Exception as e:
        log.error(f"[ocr] call error: {e}")
    return ""


async def _call_extract(base_url: str, file_path: str) -> str:
    """Extract text from PDF/Office document via media-processor /extract endpoint."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/extract",
                json={"file_path": file_path},
            )
            if r.status_code == 200:
                return r.json().get("text", "").strip()
            log.warning(f"[doc] extract returned {r.status_code}")
    except Exception as e:
        log.error(f"[doc] extract error: {e}")
    return ""


# ── Context-based pre-filter (Phase 22, STEP 1) ───────────────────────────────

_RELEASE_CONTEXT_KW = [
    "release", "রিলিজ", "completion", "সমাপ্ত", "discharge",
    "duty শেষ", "duty sesh", "shesh", "শেষ", "cleared",
    "slip", "স্লিপ", "slip pathalam", "submit", "জমা",
    "release slip", "রিলিজ স্লিপ", "complete", "done", "finished",
]

_ESCORT_CONTEXT_KW = [
    "escort", "এস্কর্ট", "vessel", "ভেসেল", "lighter",
    "assignment", "নিয়োগ", "duty", "ডিউটি", "slip", "স্লিপ",
    "payment", "পেমেন্ট", "salary", "বেতন",
]


def classify_from_context(context_text: str) -> bool:
    """
    STEP 1 (Phase 22): Lightweight context-only pre-filter.
    Returns True if surrounding chat messages suggest the incoming image
    is probably a release or escort slip — without reading or OCR-ing the image.
    """
    if not context_text:
        return False
    t = context_text.lower()
    release_score = sum(1 for k in _RELEASE_CONTEXT_KW if k.lower() in t)
    escort_score = sum(1 for k in _ESCORT_CONTEXT_KW if k.lower() in t)
    return (release_score + escort_score) >= 2


def classify_slip_type(text: str) -> str:
    """Public wrapper for slip type classification. No OCR — text already extracted."""
    return _classify_slip(text)


# ── OCR text cleaning (Part E) ────────────────────────────────────────────────

def _clean_ocr_text(text: str) -> str:
    """Remove OCR garbage: duplicate lines, isolated punctuation, noise characters."""
    if not text:
        return ""
    lines = text.splitlines()

    # Deduplicate consecutive identical or near-identical lines
    seen: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            seen.append("")
            continue
        # Skip lines that are pure garbage: <3 non-whitespace chars but not Bengali
        non_ws = stripped.replace(" ", "")
        if len(non_ws) < 2:
            continue
        # Skip lines that are >80% punctuation/special chars
        alpha_count = sum(1 for c in stripped if c.isalnum() or "ঀ" <= c <= "৿")
        if len(stripped) >= 4 and alpha_count < len(stripped) * 0.2:
            continue
        # Dedup: skip if identical to last kept line
        if seen and seen[-1].strip() == stripped:
            continue
        seen.append(line)

    return "\n".join(seen).strip()


def _is_garbage_date(date_str: str) -> bool:
    """Return True if extracted date is clearly invalid (impossible month/day)."""
    if not date_str:
        return False
    parts = re.split(r"[./-]", date_str)
    if len(parts) < 3:
        return True
    try:
        nums = [int(p) for p in parts if p.isdigit()]
        if len(nums) < 3:
            return True
        # Detect which is year (4-digit or <100 = 2000+)
        year_candidates = [n for n in nums if n > 1900]
        if not year_candidates:
            return True  # no plausible year
        # Check month and day plausibility
        non_year = [n for n in nums if n not in year_candidates]
        if any(n > 31 or n < 1 for n in non_year):
            return True
        # Reject future dates more than 1 year out
        from datetime import date as _date
        today = _date.today()
        year = year_candidates[0] if year_candidates[0] > 100 else 2000 + year_candidates[0]
        if year > today.year + 1:
            return True
    except Exception:
        pass
    return False


# ── OCR confidence score (Part E) ─────────────────────────────────────────────

def _compute_confidence(text: str, fields: dict) -> int:
    """
    Heuristic confidence score 0–100 for OCR extraction quality.
    Factors: text length, field extraction rate, date validity.
    """
    if not text or len(text) < 20:
        return 10

    score = 0
    words = text.split()
    word_count = len(words)

    # Text volume
    if word_count >= 20:
        score += 30
    elif word_count >= 10:
        score += 20
    elif word_count >= 5:
        score += 10

    # Field extraction rate
    extracted = sum(1 for v in fields.values() if v)
    total_fields = len(fields)
    if total_fields > 0:
        rate = extracted / total_fields
        score += int(rate * 40)

    # Date validity
    date_val = fields.get("date")
    if date_val:
        if _is_garbage_date(date_val):
            score = max(0, score - 20)
        else:
            score += 10

    # OCR quality signal: check for excessive special chars (noise)
    total_chars = len(text)
    alpha_chars = sum(1 for c in text if c.isalnum() or "ঀ" <= c <= "৿")
    if total_chars > 0 and alpha_chars / total_chars < 0.3:
        score = max(0, score - 20)  # heavy noise
    else:
        score += 20

    return min(100, max(0, score))


# ── Slip classification ────────────────────────────────────────────────────────

def _classify_slip(text: str) -> str:
    t = text.lower()

    # PRIMARY RULE: two dates = duty-completion (release) document.
    # The physical slip is IDENTICAL for both assignment and release — the only
    # structural difference is that the supervisor stamps a second date+signature
    # when duty is completed. ONE date = still on duty. TWO dates = duty done.
    date_patterns = re.findall(r'\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b', t)
    has_two_dates = len(date_patterns) >= 2

    escort_kw = ["escort", "এস্কর্ট", "vessel", "ভেসেল", "mother vessel", "lighter",
                 "assignment", "নিয়োগ", "duty slip", "ডিউটি স্লিপ", "escort slip"]
    # Only words that actually appear on the physical slip or supervisor's stamp.
    # NOT included: food bill, conveyance, duty days, settlement — those are
    # system-calculated, not printed on the slip.
    release_kw = ["release", "রিলিজ", "রিলিজ তারিখ", "release date",
                  "completion", "সমাপ্ত", "discharge", "cleared",
                  "ছাড়পত্র", "relieved", "স্বাক্ষর"]
    payment_kw = ["payment", "পেমেন্ট", "salary", "বেতন", "advance", "অ্যাডভান্স",
                  "paid", "পরিশোধ", "bkash", "বিকাশ", "nagad", "নগদ", "amount", "টাকা"]

    escort_score = sum(1 for k in escort_kw if k in t)
    release_score = sum(1 for k in release_kw if k in t)
    payment_score = sum(1 for k in payment_kw if k in t)

    # TWO dates + any escort vocabulary → duty-completion document (release slip)
    if has_two_dates and escort_score >= 1:
        return "release_slip"

    best = max(escort_score, release_score, payment_score)
    if best == 0:
        return "unknown"
    # Release score checked before escort: if explicit release words appear, prefer it
    if release_score == best:
        return "release_slip"
    if escort_score == best:
        return "escort_slip"
    return "payment_slip"


# ── Candidate document classifier (Parts C+D) ─────────────────────────────────

_CV_KW = ["curriculum vitae", "resume", "cv", "work experience", "education",
          "skills", "objective", "references", "employment", "qualification",
          "জীবন বৃত্তান্ত", "শিক্ষাগত", "কর্ম অভিজ্ঞতা", "দক্ষতা"]

_NID_KW = ["national id", "nid", "national identity", "voter id",
           "জাতীয় পরিচয়", "ভোটার আইডি", "পরিচয়পত্র", "election commission",
           "জাতীয় পরিচয়পত্র"]

_CERT_KW = ["certificate", "সনদ", "সার্টিফিকেট", "passed", "awarded",
            "ssc", "hsc", "বোর্ড", "board", "grade", "gpa",
            "মাধ্যমিক", "উচ্চমাধ্যমিক", "শিক্ষা সনদ"]

_CHAIR_KW = ["chairman", "চেয়ারম্যান", "ward", "ওয়ার্ড", "union", "ইউনিয়ন",
             "village court", "গ্রাম আদালত", "পরিষদ", "parishad",
             "নাগরিকত্ব", "citizenship", "character certificate"]

_HANDWRITTEN_KW = ["আবেদন", "আবেদনপত্র", "application", "বরাবর", "মহোদয়",
                   "বিনীত নিবেদন", "নিবেদক", "আপনার বিশ্বস্ত",
                   "জনাব", "স্যার", "dear sir", "to the manager"]

_PASSPORT_PHOTO_KW = ["photo", "ফটো", "passport size", "ছবি"]

_PASSPORT_KW = ["passport", "পাসপোর্ট", "republic of bangladesh",
                "travel document", "visa", "immigration"]


def _classify_candidate_doc(text: str, filename: str) -> str:
    """Classify extracted text + filename as a candidate document type."""
    t = (text + " " + filename).lower()

    scores = {
        "cv": sum(1 for k in _CV_KW if k in t),
        "nid": sum(1 for k in _NID_KW if k in t),
        "certificate": sum(1 for k in _CERT_KW if k in t),
        "chairman_cert": sum(1 for k in _CHAIR_KW if k in t),
        "handwritten": sum(1 for k in _HANDWRITTEN_KW if k in t),
        "passport_photo": sum(1 for k in _PASSPORT_PHOTO_KW if k in t),
        "passport": sum(1 for k in _PASSPORT_KW if k in t),
    }

    best_type = max(scores, key=lambda k: scores[k])
    if scores[best_type] == 0:
        return "unknown"
    return best_type


_DOC_ACK: dict[str, str] = {
    "cv": (
        "আপনার সিভি আমরা পেয়েছি। ধন্যবাদ।\n"
        "মূল কাগজপত্র (সিভি, NID, সার্টিফিকেট) অফিসে নিয়ে আসবেন।\n"
        "যোগাযোগ হলে আমরা জানাব।"
    ),
    "nid": (
        "আপনার জাতীয় পরিচয়পত্রের কপি পেয়েছি।\n"
        "মূল NID অফিসে নিয়ে আসবেন। প্রিন্ট কপি সঙ্গে আনবেন।"
    ),
    "certificate": (
        "আপনার শিক্ষাগত সনদ পেয়েছি।\n"
        "মূল সার্টিফিকেট ও প্রিন্ট কপি অফিসে নিয়ে আসবেন।"
    ),
    "chairman_cert": (
        "আপনার চেয়ারম্যান সনদ / নাগরিকত্ব সনদ পেয়েছি।\n"
        "মূল কপি ও প্রিন্ট কপি অফিসে আনবেন।"
    ),
    "handwritten": (
        "আপনার আবেদনপত্র পেয়েছি।\n"
        "অফিসে মূল আবেদনপত্র ও প্রয়োজনীয় কাগজপত্র নিয়ে আসবেন।"
    ),
    "passport_photo": (
        "আপনার পাসপোর্ট সাইজ ছবি পেয়েছি।\n"
        "অফিসে ২ কপি পাসপোর্ট সাইজ ছবি নিয়ে আসবেন।"
    ),
    "passport": (
        "আপনার পাসপোর্টের কপি পেয়েছি।\n"
        "মূল পাসপোর্ট ও ফটোকপি অফিসে নিয়ে আসবেন।"
    ),
}


def _build_doc_acknowledgement(doc_type: str, filename: str) -> str:
    return _DOC_ACK.get(
        doc_type,
        f"ডকুমেন্টটি পেয়েছি ({filename or 'ফাইল'})।\n"
        "অফিসে মূল কাগজপত্র নিয়ে আসবেন।",
    )


# ── Field extraction ───────────────────────────────────────────────────────────

def _extract_fields(text: str, slip_type: str) -> dict:
    fields = {
        "employee_name": None, "employee_id": None, "date": None,
        "vessel": None, "location": None, "amount": None,
        "client": None, "reference_no": None,
    }

    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Date: common formats
    date_match = re.search(
        r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b", text
    )
    if date_match:
        candidate = date_match.group(1)
        if not _is_garbage_date(candidate):
            fields["date"] = candidate

    # Amount: digits with ৳ or Tk
    amount_match = re.search(r"[৳\bTk\.?]\s*(\d[\d,]+)", text, re.IGNORECASE)
    if amount_match:
        fields["amount"] = amount_match.group(1).replace(",", "")
    else:
        amt = re.search(r"\b(\d{3,6})\b", text)
        if amt:
            fields["amount"] = amt.group(1)

    # Reference number
    ref = re.search(r"(?:ref|sl|no|#|number)[.:\s#]*([A-Z0-9/-]{4,15})", text, re.IGNORECASE)
    if ref:
        fields["reference_no"] = ref.group(1)

    # Vessel name (for escort slips)
    vessel = re.search(
        r"(?:vessel|ভেসেল|mother vessel|lighter)[:\s]+([A-Za-z0-9\s]{3,30})", text, re.IGNORECASE
    )
    if vessel:
        raw_vessel = vessel.group(1).strip()
        fields["vessel"] = _normalize_vessel_name(raw_vessel)

    # Location / destination
    loc = re.search(
        r"(?:destination|location|port|বন্দর|গন্তব্য)[:\s]+([A-Za-z\s]{3,25})", text, re.IGNORECASE
    )
    if loc:
        fields["location"] = loc.group(1).strip()

    # Name: "Name:" or "নাম:" prefix
    name_match = re.search(r"(?:name|নাম)[:\s]+([A-Za-zঀ-৿\s.]{3,30})", text, re.IGNORECASE)
    if name_match:
        fields["employee_name"] = name_match.group(1).strip()

    # Employee ID
    id_match = re.search(r"(?:id|ic|emp)[.:\s#]*(\d{2,6})", text, re.IGNORECASE)
    if id_match:
        fields["employee_id"] = id_match.group(1)

    # Client / company name
    client_match = re.search(
        r"(?:client|company|company name|ক্লায়েন্ট)[:\s]+([A-Za-zঀ-৿\s&.]{3,40})",
        text, re.IGNORECASE,
    )
    if client_match:
        fields["client"] = client_match.group(1).strip()

    return fields


def _normalize_vessel_name(name: str) -> str:
    """Strip common OCR artifacts from vessel names."""
    if not name:
        return name
    # Remove leading/trailing punctuation and digits that look like OCR noise
    name = re.sub(r"^[\W\d]+|[\W\d]+$", "", name).strip()
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)
    return name or name


def _build_reply(slip_type: str, fields: dict, confidence: int = 100) -> str:
    name = fields.get("employee_name") or ""
    date = fields.get("date") or ""
    vessel = fields.get("vessel") or ""
    amount = fields.get("amount") or ""
    ref = fields.get("reference_no") or ""

    low_conf_note = " [⚠️ কম নির্ভরযোগ্য — যাচাই করুন]" if confidence < 40 else ""

    if slip_type == "escort_slip":
        parts = [f"স্লিপটি পাওয়া গেছে এবং প্রক্রিয়া করা হচ্ছে।{low_conf_note}"]
        if vessel:
            parts.append(f"ভেসেল: {vessel}।")
        if date:
            parts.append(f"তারিখ: {date}।")
        if name:
            parts.append(f"কর্মী: {name}।")
        return " ".join(parts)

    if slip_type == "release_slip":
        parts = [f"রিলিজ স্লিপটি পাওয়া গেছে।{low_conf_note}"]
        if date:
            parts.append(f"তারিখ: {date}।")
        if name:
            parts.append(f"কর্মী: {name}।")
        return " ".join(parts)

    if slip_type == "payment_slip":
        parts = [f"পেমেন্ট স্লিপটি পাওয়া গেছে।{low_conf_note}"]
        if amount:
            parts.append(f"পরিমাণ: ৳{amount}।")
        if date:
            parts.append(f"তারিখ: {date}।")
        if ref:
            parts.append(f"রেফারেন্স: {ref}।")
        return " ".join(parts)

    return "ডকুমেন্টটি পাওয়া গেছে। তবে ধরনটি চিহ্নিত করা যায়নি। অফিসে যোগাযোগ করুন।"
