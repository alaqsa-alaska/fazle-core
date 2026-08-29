"""
Fazle Core — Recruitment Funnel (async version)
Adapted from resources/recruitment.py for asyncpg DB interface.

Tables used:
  fazle_recruitment_sessions — tracks conversation state per phone
    wbom_candidates — canonical candidate identity linked to each session
    wbom_candidate_conversations — append-only inbound conversation audit
  (falls back gracefully if table doesn't exist yet)

Steps: name → age → area → job_preference → experience → phone_confirm
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.database import db_conn, fetch_one, execute, fetch_val

log = logging.getLogger("fazle.recruitment")
SESSION_TTL = timedelta(hours=24)


class _ConversationAuditUnavailable(RuntimeError):
    pass


OPERATIONAL_ROLES = frozenset({
    "admin", "accountant", "employee", "supervisor", "family",
    "escort_client", "client_escort_buyer", "vip_client", "repeat_client",
    "vendor", "known_contact",
})
OPERATIONAL_INTENTS = frozenset({
    "attendance", "leave", "payment_due", "advance_request",
    "client_order", "slip_submission",
})
# "salary_query" and "escort_duty" are deliberately excluded: by the time
# this set is consulted in
# recruitment_eligibility(), role has already been confirmed non-operational
# (unknown/new_lead/candidate) -- a real employee's salary question never
# reaches this check at all, it's already rejected by the role check above.
# Candidate/unknown questions about escort salary or duty are legitimate
# recruitment topics and must reach the recruitment detector. Actual escort
# orders still fail recruitment detection and continue to the escort-order
# gate in modules.message_router.

# ── Intake trigger keywords ────────────────────────────────────────────────────
INTAKE_KEYWORDS: set[str] = {
    "চাকরি", "vacancy", "apply", "hire",
    "recruit", "নিয়োগ", "কাজের",
    "পদ", "পারব", "নেবেন", "জয়েন",
    "cv", "joining",  # P17-FIX-3: added for "cv dibo" / "joining kobe" test cases
    "office location", "office address", "contact number", "whatsapp number",
    "অফিস কোথায়", "অফিস কোথায়", "অফিসের ঠিকানা", "যোগাযোগ নম্বর",
}

# 2026-08-06 fix: "job", "interested", and their Bangla equivalent
# "আগ্রহী" were removed from the set below -- confirmed live, a bare
# substring match on any of the three fires on ordinary non-recruitment
# text (an internal test message "run job health_summary", and a generic
# business inquiry "I am interested in learning more about your business"
# both silently opened a recruitment funnel session). The Bangla word is
# included even though only the two English words were reported live --
# it's the direct translation of "interested" and carries the identical
# false-positive risk. All three now only count as a trigger inside a
# job-shaped phrase (RECRUITMENT_TRIGGER_PHRASES, below) -- matching the
# specificity every other entry here already has on its own (চাকরি/
# vacancy/recruit/cv/... are distinctive enough as bare words;
# job/interested/আগ্রহী were not).

VALID_POSITIONS: dict[str, str] = {
    "1": "Escort",
    "2": "Survey Scout",
    "3": "Security Guard",
    "4": "Security Supervisor",
    "5": "Assistant Supervisor",
    "6": "Operation Officer",
    "7": "Security In-Charge",
    "8": "Marketing Officer",
    "9": "Ghat Supervisor",
    "escort": "Escort",
    "survey": "Survey Scout",
    "guard": "Security Guard",
    "security": "Security Guard",
    "supervisor": "Security Supervisor",
    "assistant supervisor": "Assistant Supervisor",
    "in-charge": "Security In-Charge",
    "incharge": "Security In-Charge",
    "marketing": "Marketing Officer",
    "ghat supervisor": "Ghat Supervisor",
    "ghat": "Ghat Supervisor",
    "operation": "Operation Officer",
}

COLLECTION_STEPS: list[str] = [
    "name", "age", "area", "job_preference", "experience", "phone_confirm",
]

# ── Post-intake conversation state (Owner directive, 2026-08-27) ────────────
# db/migrations/072_wbom_candidates_post_intake_status.sql. Set the moment
# intake completes (_finalize_candidate); progressed by _classify_post_
# intake_turn()'s best-effort semantic classification (never guessed --
# see that function's own docstring). Kept to exactly this set per the
# Owner's directive -- no redundant states.
POST_INTAKE_STATUSES: frozenset[str] = frozenset({
    "awaiting_agreement", "agreed", "joining_ready",
    "salary_objection", "not_ready_now", "declined", "future_contact",
})
# Only "declined" stops treating the candidate as an active post-intake
# conversation (get_active_session()'s scored branch returns None so a
# later message from them is no longer auto-routed as session continuation
# -- it can still reach recruitment via the normal keyword/detector path).
# "future_contact"/"not_ready_now" are deliberately NOT terminal: the
# candidate messaging again IS the future contact/readiness signal, and
# must resume the same grounded post-intake conversation, not be treated
# as a brand-new inquiry.
POST_INTAKE_TERMINAL_STATUSES: frozenset[str] = frozenset({"declined"})

# ── Turn-intent classification (2026-08-23, see ──────────────────────────────
# RECRUITMENT_REPLY_QUALITY_IMPLEMENTATION_PLAN_2026-08-23.md): let
# route_recruitment_message() recognize when an inbound message is an
# informational question instead of a pure intake-field answer, so the
# answer-first flow can fire BEFORE intake_message() consumes the turn.
# Pure keyword heuristic only — the existing recruitment semantic analysis
# (analyze_recruitment_signals / is_recruitment_trigger) remains the
# authoritative domain check; this just splits "is this turn asking for
# information" from "is this turn providing an intake value".

# Informational question markers — single token/ punctuation check.
# Intentionally conservative: a question mark OR one of the canonical
# question particles is enough to mark a turn as PURE_QUESTION or MIXED.
_QUESTION_MARK_RE = re.compile(r"[?？]|(?:\?|।)$")
_QUESTION_WORDS_RE = re.compile(
    r"(?:কি|কী|কত|কোথায়|কোথায়|কখন|কিভাবে|কীভাবে|কেন|কোনটি|কোনটা|"
    r"কি কাজ|কি সুবিধা|কি কাজ|কোথায় অফিস|অফিস কোথায়|"
    r"salary|salary koto|what|where|how|when|why|which|"
    r"ঠিকানা কোথায়|ভর্তি ফি|জয়েনিং ফি|ডিপোজিট|"
    r"থাকা|খাওয়া|বেতন কত|training|duty|join)",
    re.IGNORECASE,
)

def _hash_phone_shadow(phone: str) -> str:
    import hashlib as _hl
    return _hl.sha1((phone or '').encode('utf-8')).hexdigest()[:10]


# ── Phase C v2 wiring (2026-08-23) ──────────────────────────────────
async def _maybe_decide_v2(
    *,
    phone: str,
    text: str,
    current_step: Optional[str],
    session: Optional[dict],
    identity: Optional[dict],
) -> Optional["RecruitmentTurnDecision"]:
    """Optional semantic-decision layer (Phase C).

    Returns None when recruitment_turn_decision_v2_enabled is False
    (default OFF in app/config.py -- legacy classify_turn_intent
    path remains byte-identical). When ON, returns a validated
    RecruitmentTurnDecision (possibly AMBIGUOUS).

    Imports are deferred to avoid top-level cycles.
    """
    try:
        from app.config import get_settings
        if not bool(get_settings().recruitment_turn_decision_v2_enabled):
            return None
    except Exception:
        return None
    try:
        from modules.recruitment_turn_decision import (
            decide as _rtd_decide,
            propose as _rtd_propose,
        )
        from modules.conversation_canonical import (
            build_canonical_history as _build_history,
        )
        from modules.hermes_dispatch import (
            build_recruitment_context as _build_sot,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[recruitment_flow] Phase C import failed, falling back to legacy: %s", exc)
        return None
    try:
        history = await _build_history(phone, n=10)
        proposals = _rtd_propose(text, current_step, (session or {}))
        source_of_truth = _build_sot(text)
        decision = await _rtd_decide(
            text=text,
            current_step=current_step,
            session=session or {},
            identity=identity or {},
            history=history,
            intake_proposals=proposals,
            source_of_truth=source_of_truth,
            flag_enabled=True,
        )
        return decision
    except Exception as exc:  # noqa: BLE001
        log.warning("[recruitment_flow] Phase C decide() failed, falling back to legacy: %s", exc)
        return None


# Intake step expected-format patterns. Used to detect "this turn is
# answering the current intake field" without invoking intake_message().
_STEP_HAS_VALUE_RE = {
    "name": re.compile(r"[A-Za-zঀ-৿]{2,}", re.UNICODE),
    "age": re.compile(r"(?:\d{1,2}|০|১|২|৩|৪|৫|৬|৭|৮|৯)"),
    "area": re.compile(r"[A-Za-zঀ-৿]{2,}", re.UNICODE),
    "job_preference": re.compile(
        r"(?:\d|escort|guard|supervisor|officer|marketer|ghat|security|"
        r"সিকিউরিটি|এসকর্ট|গার্ড|সুপারভাইজার|অফিসার|মার্কেটিং|ঘাট|"
        r"operation|marketing|assistant|survey)",
        re.IGNORECASE,
    ),
    "experience": re.compile(r"(?:\d|০|১|২|৩|৪|৫|৬|৭|৮|৯|নতুন|কোন)"),
    "phone_confirm": re.compile(r"(?:\d{8,}|০|১|২|৩|৪|৫|৬|৭|৮|৯)"),
}


def classify_turn_intent(text: str, current_step: Optional[str]) -> str:
    """Classify an inbound recruitment turn for answer-first routing.

    Returns ONE of:
        "pure_question"   — turn contains a question marker; no intake value
        "mixed"           — turn has both an intake value AND a question
        "pure_intake"     — turn is just an intake-field answer / continuation
        "unrelated"       — empty / single-character / off-domain

    This is intentionally a lightweight heuristic (one pass over the
    string). The authoritative semantic gate already proved the message
    is recruitment-domain upstream — here we only need to decide whether
    the LLM/Hermes layer should ALSO be invoked this turn, not whether
    to route to recruitment at all.

    Add new question-word triggers conservatively (prefer false-negative
    on "mixed" — better to under-detect questions than to misclassify a
    real intake answer and lose the field value).
    """
    if not text or not text.strip():
        return "unrelated"
    stripped = text.strip()
    # Very short messages without any question marker are almost always
    # intake answers, never informational questions.
    has_question = bool(
        _QUESTION_MARK_RE.search(stripped)
        or _QUESTION_WORDS_RE.search(stripped)
    )
    if not has_question:
        # No question detected — even if it contains a value, the answer
        # layer has nothing to say, so treat as a pure intake turn.
        return "pure_intake"
    # Question detected — now check whether the same turn ALSO contains
    # a value matching the current intake step's expected format.
    if current_step and current_step in _STEP_HAS_VALUE_RE:
        if _STEP_HAS_VALUE_RE[current_step].search(stripped):
            return "mixed"
    return "pure_question"


# Safe fee response — used until Owner resolves the source-of-truth
# contradiction between "no fee" (line 54) and "processing fee exists"
# (line 56) of resources/ops/recruitment_source_of_truth.txt. PENDING
# OWNER DECISION (2026-08-23). States only the confirmed claim and
# defers the contradictory part to the office contact number, avoiding
# either fabrication or silent selection of one side.
SAFE_FEE_PENDING_OWNER_DECISION = (
    "চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না। "
    "নির্দিষ্ট প্রসেসিং/জয়েনিং ফি সংক্রান্ত তথ্যের জন্য অনুগ্রহ করে "
    "অফিসে যোগাযোগ করুন: 01958-122300।"
)

STEP_QUESTIONS: dict[str, str] = {
    "name": (
        "স্বাগতম। আমাদের কাছে আবেদন করার জন্য আপনাকে ধন্যবাদ।\n"
        "আপনার পুরো নাম কি?"
    ),
    "age": "আপনার বয়স কত বছর?",
    "area": "আপনি কোন জেলায় থাকেন? (বর্তমান ঠিকানা)",
    "job_preference": (
        "আপনি কোন পদে কাজ করতে চান?\n\n"
        "১. Escort\n"
        "২. Survey Scout (জাহাজে)\n"
        "৩. Security Guard\n"
        "৪. Security Supervisor\n"
        "৫. Assistant Supervisor\n"
        "৬. Operation Officer\n"
        "৭. Security In-Charge\n"
        "৮. Marketing Officer\n"
        "৯. Ghat Supervisor\n\n"
        "নম্বর বা পদের নাম লিখুন।"
    ),
    "experience": (
        "এই ক্ষেত্রে আপনার কত বছরের অভিজ্ঞতা আছে?\n"
        "(নতুন হলে 0 লিখুন)"
    ),
    "phone_confirm": "আপনার কনফার্মেশনের জন্য: আপনার মোবাইল নম্বর কত? (যেটায় কল করতে পারব)",
}

INTAKE_COMPLETE_MSG: str = (
    "আপনার তথ্য সফলভাবে সংগ্রহ করা হয়েছে।\n\n"
    "আমাদের টিম শীঘ্রই আপনার সাথে যোগাযোগ করবে।\n"
    "আরো জানতে: 01958 122300\n\n"
    "আবেদনের জন্য ধন্যবাদ।"
)

ALREADY_APPLIED_MSG: str = (
    "আপনার আবেদন ইতিমধ্যে প্রক্রিয়াধীন রয়েছে।\n"
    "আমাদের টিম শীঘ্রই যোগাযোগ করবে।"
)

# ── Post-intake conversation (Owner directive, 2026-08-27) ──────────────────
# Last-resort fallback ONLY -- reached when both the Hermes/KB layer and its
# conversation_layer fallback return nothing at all for a scored candidate's
# message (see _route_post_intake_message). Deliberately an open invitation,
# not a hard stop, and states no business fact of its own -- matches the
# same "never fabricate, defer to the office" posture as
# SAFE_FEE_PENDING_OWNER_DECISION above.
POST_INTAKE_FALLBACK_MSG: str = (
    "আপনার আবেদন সম্পন্ন হয়েছে। কোনো প্রশ্ন থাকলে জিজ্ঞেস করুন, অথবা সরাসরি "
    "অফিসে যোগাযোগ করুন: 01958 122300।"
)

# Subject naming for admin_directives -- kept as module constants (not a
# free-form string at each call site) so recruitment_flow's own directive
# lookup and the escalation request text handed to Hermes always agree on
# the exact key. subject_key values are plain free text (no DB-level
# registration needed, see modules.admin_directives.save_directive) --
# these are simply this module's own convention.
RECRUITMENT_POLICY_SUBJECT_TYPE: str = "recruitment_policy"
RECRUITMENT_FEE_POLICY_SUBJECT_KEY: str = "recruitment_joining_fee"

# Structured classification prompt for _classify_post_intake_turn(). Kept
# conservative: "none" (no state change) is the correct output whenever the
# candidate's turn is a question, an information request, or otherwise
# doesn't clearly match one category -- classification must never guess,
# matching this codebase's existing verify_employee_claim() precedent.
_POST_INTAKE_CLASSIFY_SYSTEM_PROMPT: str = (
    "তুমি recruitment candidate-এর reply classify করছ। প্রার্থী ইতিমধ্যে "
    "প্রাথমিক তথ্য জমা দিয়েছে (scored) এবং এখন joining/training শর্ত নিয়ে "
    "কথোপকথন চলছে। এই বার্তাটি নিচের category-গুলোর মধ্যে কোনটির সাথে "
    "স্পষ্টভাবে মেলে তা ঠিক কর:\n"
    "  agreed — প্রার্থী শর্ত মেনে এগিয়ে যেতে স্পষ্টভাবে রাজি (যেমন \"হ্যাঁ আমি রাজি\")\n"
    "  joining_ready — প্রার্থী এখনই যোগদানের জন্য প্রস্তুত/কাগজপত্র নিয়ে আসতে চায়\n"
    "  salary_objection — প্রার্থী বেতন/ভাতা নিয়ে আপত্তি করছে কিন্তু সম্পূর্ণ প্রত্যাখ্যান করেনি\n"
    "  not_ready_now — প্রার্থী আগ্রহী কিন্তু এখন প্রস্তুত না\n"
    "  declined — প্রার্থী স্পষ্টভাবে চাকরি না করার সিদ্ধান্ত জানিয়েছে\n"
    "  future_contact — প্রার্থী পরে যোগাযোগ করতে বলেছে\n"
    "  none — উপরের কোনোটির সাথেই স্পষ্টভাবে মেলে না (প্রশ্ন, তথ্য অনুরোধ, অস্পষ্ট বার্তা)\n"
    "শুধু স্পষ্ট ক্ষেত্রে category দাও; সামান্যতম অনিশ্চয়তা থাকলে \"none\" দাও, "
    "কখনো অনুমান করো না। কোনো ব্যাখ্যা ছাড়া শুধুমাত্র এই JSON ফরম্যাটে উত্তর দাও:\n"
    '{"post_intake_status": "<category>", "confidence": <0.0-1.0>}'
)
_POST_INTAKE_CLASSIFY_CONFIDENCE_FLOOR: float = 0.60

# ── Responsible/supervisory-role experience verification (Owner directive,
# 2026-08-27) ─────────────────────────────────────────────────────────────
# Scope confirmed by Owner: these role names (exact stored job_preference
# values from VALID_POSITIONS, plus two narrative-only names the KB may
# describe even though intake never stores them literally today —
# "Supervisor"/"Ghat In-Charge" — kept for forward compatibility) require a
# role-specific experience question before the agreement question, instead
# of accepting a bare generic years-of-experience answer. Deliberately does
# NOT include Escort, Survey Scout, Security Guard, Marketing Officer —
# ordinary non-supervisory roles are unaffected by this gate.
_RESPONSIBLE_ROLES: frozenset[str] = frozenset({
    "Security Supervisor", "Assistant Supervisor", "Supervisor",
    "Ghat Supervisor", "Ghat In-Charge", "Operation Officer",
    "Security In-Charge",
})


def _is_responsible_role(job_preference: Optional[str]) -> bool:
    return bool(job_preference) and job_preference.strip() in _RESPONSIBLE_ROLES


# Additional extraction instructions appended to _POST_INTAKE_CLASSIFY_
# SYSTEM_PROMPT (same call, same JSON response — NOT a second LLM
# round-trip) when the active candidate is in a responsible role and has
# not yet supplied a checkable experience claim. Never guesses — matches
# the base prompt's own "sammantatam anishchayata thakle none dao" posture.
_EXPERIENCE_EXTRACTION_INSTRUCTIONS_TMPL: str = (
    "\n\nএছাড়াও, এই প্রার্থী \"{role}\" (দায়িত্বশীল/সুপারভাইজরি) পদে আগ্রহী এবং "
    "এই নির্দিষ্ট পদের জন্য প্রাসঙ্গিক অভিজ্ঞতা এখনো স্পষ্টভাবে যাচাইযোগ্য নয়। "
    "কথোপকথনের ইতিহাস ও এই বার্তা থেকে (শুধুমাত্র স্পষ্টভাবে বলা থাকলে -- কখনো "
    "অনুমান কোরো না) নিচের তথ্যও বের কর:\n"
    "  claimed_company -- কোন প্রতিষ্ঠান/কোম্পানিতে এই ধরনের কাজ করেছে "
    "(স্পষ্ট না থাকলে null)\n"
    "  claimed_duration -- কতদিন/বছর কাজ করেছে, প্রার্থীর নিজের ভাষায় "
    "(যেমন \"১০ বছরের বেশি\") (স্পষ্ট না থাকলে null)\n"
    "  reference_name -- reference/contact person-এর নাম (স্পষ্ট না থাকলে null)\n"
    "  reference_phone -- reference/contact person-এর নম্বর (স্পষ্ট না থাকলে null)\n"
    "  no_relevant_experience -- true শুধু যদি প্রার্থী স্পষ্টভাবে বলে যে তার এই "
    "নির্দিষ্ট ধরনের কাজে কোনো প্রাসঙ্গিক অভিজ্ঞতা নেই, নাহলে false\n"
    "JSON output-এ এই key-গুলোও যোগ কর (তথ্য না থাকলে null/false দাও, বাদ দিও না): "
    '"claimed_company", "claimed_duration", "reference_name", "reference_phone", '
    '"no_relevant_experience"।'
)

# Marker-delimited block stored inside wbom_candidates.notes (existing,
# previously-unused free-text column — no schema change). Owner directive
# (2026-08-27): claimed role/employer/duration/reference must be labeled
# and distinguished, and verification_status must never be set to
# "verified"/"rejected" by automated code — only "unverified" (default) and
# "awaiting_admin_review" (set once, after sufficient detail triggers the
# Admin notification) are ever written here. "verified"/"rejected" exist in
# the vocabulary for a human to set manually; nothing in this codebase
# writes them.
_EXPERIENCE_CLAIM_BLOCK_RE = re.compile(
    r"\[EXPERIENCE_CLAIM\].*?\[/EXPERIENCE_CLAIM\]", re.DOTALL,
)
_EXPERIENCE_CLAIM_FIELDS: tuple[str, ...] = (
    "claimed_role", "claimed_company", "claimed_duration",
    "reference_name", "reference_phone", "verification_status",
)
# Sentinel written into claimed_company when the candidate has explicitly
# confirmed they have no relevant experience for this specific responsible
# role — distinguishes "resolved, no claim to verify" from "not asked yet".
_NO_RELEVANT_EXPERIENCE_SENTINEL: str = "প্রাসঙ্গিক অভিজ্ঞতা নেই (candidate confirmed)"


def _format_experience_claim_block(
    *, claimed_role=None, claimed_company=None, claimed_duration=None,
    reference_name=None, reference_phone=None, verification_status="unverified",
) -> str:
    return (
        "[EXPERIENCE_CLAIM]\n"
        f"claimed_role: {claimed_role or '—'}\n"
        f"claimed_company: {claimed_company or '—'}\n"
        f"claimed_duration: {claimed_duration or '—'}\n"
        f"reference_name: {reference_name or '—'}\n"
        f"reference_phone: {reference_phone or '—'}\n"
        f"verification_status: {verification_status}\n"
        "[/EXPERIENCE_CLAIM]"
    )


def _parse_experience_claim_block(notes: Optional[str]) -> Optional[dict]:
    """Read-only: extract the (most recent) [EXPERIENCE_CLAIM] block from
    wbom_candidates.notes. Returns None if absent/empty. Never raises."""
    if not notes:
        return None
    matches = _EXPERIENCE_CLAIM_BLOCK_RE.findall(notes)
    if not matches:
        return None
    data: dict = {}
    for line in matches[-1].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in _EXPERIENCE_CLAIM_FIELDS:
            data[key] = None if value == "—" else value
    return data or None


def _experience_claim_resolved(claim: Optional[dict]) -> bool:
    """True when no further experience-verification question is needed
    this turn — either a checkable claim (company + duration) is already
    captured, the candidate confirmed no relevant experience, or the claim
    already moved past "unverified" (e.g. awaiting_admin_review)."""
    if not claim:
        return False
    if claim.get("verification_status") in ("awaiting_admin_review", "verified", "rejected"):
        return True
    company = claim.get("claimed_company") or ""
    if _NO_RELEVANT_EXPERIENCE_SENTINEL in company:
        return True
    return bool(company) and bool(claim.get("claimed_duration"))


async def _upsert_experience_claim(candidate_id: int, **fields) -> Optional[dict]:
    """Best-effort merge new experience-claim fields into wbom_candidates.
    notes, replacing any prior [EXPERIENCE_CLAIM] block in place (other
    note content, if any, is preserved untouched) and never overwriting an
    already-known field with a blank/falsy new value — supports the
    candidate answering across several separate messages (Owner directive:
    extract conversationally, don't require one exact formatted reply).

    Returns the merged claim dict (so callers can immediately check
    sufficiency without a second DB read), or None on any DB error
    (fails closed, never raises)."""
    try:
        row = await fetch_one(
            "SELECT notes FROM wbom_candidates WHERE candidate_id=$1", candidate_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[recruit] experience_claim read failed candidate=%s: %s", candidate_id, exc)
        return None
    existing_notes = (row or {}).get("notes") or ""
    merged = dict(_parse_experience_claim_block(existing_notes) or {})
    for key, value in fields.items():
        if value:
            merged[key] = value
    merged.setdefault("verification_status", "unverified")
    block = _format_experience_claim_block(
        claimed_role=merged.get("claimed_role"),
        claimed_company=merged.get("claimed_company"),
        claimed_duration=merged.get("claimed_duration"),
        reference_name=merged.get("reference_name"),
        reference_phone=merged.get("reference_phone"),
        verification_status=merged.get("verification_status"),
    )
    if _EXPERIENCE_CLAIM_BLOCK_RE.search(existing_notes):
        new_notes = _EXPERIENCE_CLAIM_BLOCK_RE.sub(lambda _m: block, existing_notes, count=1)
    else:
        new_notes = f"{existing_notes}\n\n{block}".strip() if existing_notes else block
    try:
        await execute(
            "UPDATE wbom_candidates SET notes=$1, updated_at=NOW() WHERE candidate_id=$2",
            new_notes, candidate_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[recruit] experience_claim write failed candidate=%s: %s", candidate_id, exc)
        return None
    return merged

# Soft-ask prepended to the first recruitment reply only when identity_brain
# found NO signal at all for this number (role="unknown" -- distinct from
# role="candidate", which already means an explicit job-related keyword
# matched). Covers the narrow case of an existing employee texting from a
# genuinely new number with zero prior cash/attendance/escort/contact
# history on it -- identity_brain's phone-only secondary-evidence lookups
# (already run before this point) would have resolved a known number to
# "employee" already, so this only fires for a true first-ever contact.
# This is a soft ask, not a gate -- the funnel proceeds either way.
UNKNOWN_SENDER_SOFT_ASK: str = (
    "আপনাকে চিনতে পারছি না। আপনি কি আমাদের পুরনো employee?\n"
    "হলে দয়া করে আপনার নাম এবং পুরনো নম্বর জানান।\n\n"
)


# ── Parsers ────────────────────────────────────────────────────────────────────

_BENGALI_DIGITS_OUT = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def _bn_digits(n: int) -> str:
    """ASCII int -> Bengali-numeral string, for candidate-facing replies
    (2026-08-19) -- an admin-directive age override arrives as an ASCII
    number (remember_business_rule's directive_text) but every other
    number in this funnel's Bangla messages uses Bengali numerals; without
    this the rejection reply would visibly mix "23" into an otherwise
    Bengali-numeral sentence."""
    return str(n).translate(_BENGALI_DIGITS_OUT)


_BENGALI_TO_ASCII = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_BN_ASCII_RE = re.compile(r"[0-9০-৯]{1,3}")


def _looks_like_dob(text: str) -> bool:
    """Phase B (2026-08-23) deterministic DOB detector.

    Returns True when the input text contains a plausible date-of-birth
    pattern: digit digits separated by '/' or '-' or '.', with at least
    one 4-digit year. Heuristics deliberately narrow to avoid false
    positives on salary figures (e.g. "৳10,000-15,000") or phone numbers.

    Per Phase B scope: this is a DETERMINISTIC gate, not a semantic
    workflow. It only REFUSES age parsing when triggered. It does NOT
    invent a new DOB intake step (the existing intake contract has
    none). When the candidate supplies a DOB at the age step, the
    response asks for the bare age instead.
    """
    if not text:
        return False
    # 4-digit year, optionally preceded/followed by / - .: yyyy or dd/mm/yyyy etc.
    if re.search(r"(?:^|[^০-৯0-9])(\d{4})(?:$|[^০-৯0-9])", text):
        # A 4-digit token alone is ambiguous (could be a salary or a year).
        # Require a date-like pattern with separator OR an explicit DOB keyword.
        if re.search(r"\d{1,2}\s*[/\-.\u0964\u0965]\s*\d{1,2}\s*[/\-.\u0964\u0965]\s*\d{2,4}", text):
            return True
        if re.search(r"\d{4}\s*[/\-.\u0964\u0965]\s*\d{1,2}\s*[/\-.\u0964\u0965]\s*\d{1,2}", text):
            return True
    # Explicit DOB keywords (case-insensitive)
    text_lower = text.lower()
    if any(kw in text_lower for kw in ("জন্ম", "তারিখ", "dob", "date of birth", "জন্মদিন", "জন্ম তারিখ")):
        # require a digit in the same message — so "জন্মদিন আজ" alone isn't a DOB
        if re.search(r"[০-৯0-9]", text):
            return True
    return False


def _parse_age(text: str, age_range: tuple[int, int] = (18, 55)) -> Optional[int]:
    """Phase B (2026-08-23): extracted age from a candidate message at the
    age step.

    Deterministic contract:
      - DOB-flagged text returns None (caller asks for bare age instead).
      - Bengali-numeral runs in text are transliterated to ASCII digits
        before extraction.
      - The first numeric run in [age_range.lo, age_range.hi] wins;
        candidates below or above the range return None.
      - A 4-digit year token alone is rejected (would otherwise interpret
        "জন্ম তারিখ ২০০০" as age-2000).
    """
    if not text or not text.strip():
        return None
    if _looks_like_dob(text):
        return None
    ascii_text = text.translate(_BENGALI_TO_ASCII)
    m = _BN_ASCII_RE.search(ascii_text)
    if not m:
        return None
    raw = m.group(0)
    # Skip pure 4-digit "year"-shaped runs (>=4 digits) when there is no
    # age-shaped integer nearby -- keeps "জন্ম তারিখ ১৯৯০" from being
    # parsed as age 1990 even when _looks_like_dob somehow misses.
    if len(raw) >= 4 and "/" not in text and "-" not in text and "." not in text and "তারিখ" not in text:
        # Allow only if the message has an age-shaped context marker
        if not re.search(r"(?:বয়স|age|বছর|years)", text, re.IGNORECASE):
            return None
    try:
        val = int(raw)
    except ValueError:
        return None
    lo, hi = age_range
    return val if lo <= val <= hi else None


async def _get_effective_age_range() -> tuple[int, int]:
    """Admin-directive override for the recruitment minimum age (2026-08-19,
    Item 3 of the Hermes->Earth rename+autonomy pass). Mirrors the exact
    fallback shape of social_auto_reply/reply_rules.py's
    get_effective_office_numbers(): check the most recent active directive
    for subject_key="recruitment_minimum_age" first, fall back to the
    hardcoded 18-55 default on no row, a malformed value, or any error --
    never let a bad directive value widen/invert the range or raise. Written
    via Earth's remember_business_rule tool or the /admin/directives
    endpoint (see core/.claude/CLAUDE.md's admin_directives bullet)."""
    try:
        from modules.admin_directives import list_directives
        rows = await list_directives(subject_key="recruitment_minimum_age", status="active", limit=1)
        if rows and rows[0].get("directive_text"):
            m = re.search(r"\d{1,3}", rows[0]["directive_text"])
            if m:
                val = int(m.group())
                if 0 < val <= 55:
                    return (val, 55)
    except Exception:
        pass
    return (18, 55)


_NAME_REJECT_KEYWORDS: tuple[str, ...] = (
    "hsc", "ssc", "এইচএসসি", "এসএসসি", "শিক্ষাগত", "যোগ্যতা", "ডিগ্রি",
    "অনার্স", "মাস্টার্স", "পাস করেছি", "শেষ করেছি", "কোন পদ", "পদ আছে",
    "চাকরি আছে", "চাকরি খুঁজছি", "বেতন কত", "কত টাকা", "বেতন কি",
)


def _looks_like_name(text: str) -> bool:
    """Reject obvious non-name free text at the 'name' collection step
    (2026-08-21, Owner Correction 2 / regression case 9694: a candidate's
    education statement/question -- "আমি HSC শেষ করেছি, এই শিক্ষাগত
    যোগ্যতায় কোন পদ আছে" -- was stored verbatim as full_name because this
    step accepted ANY text unconditionally, corrupting the field). Wired
    the same way as the existing age/job_preference validate-or-reask
    steps below -- not a new mechanism.

    Deliberately conservative: a real name is short, plain, and has no
    question mark or education/position/salary vocabulary. Anything
    ambiguous still passes (fails OPEN toward accepting a real name) --
    this only catches text that unmistakably is not a name. The raw text
    is never lost even when rejected: _audit_candidate_conversation()
    still records it verbatim in wbom_candidate_conversations, which is
    the "structured conversation/context metadata" this fact belongs in
    instead of full_name."""
    stripped = text.strip()
    if not stripped or len(stripped) > 60:
        return False
    if "?" in stripped or "؟" in stripped:
        return False
    lower = stripped.lower()
    return not any(kw in lower for kw in _NAME_REJECT_KEYWORDS)


def _parse_job_preference(text: str) -> Optional[str]:
    norm = text.strip().lower()
    for key, val in sorted(VALID_POSITIONS.items(), key=lambda item: len(item[0]), reverse=True):
        if key in norm:
            return val
    return None


# Bengali number WORDS for common small experience-duration claims (Owner
# directive 2026-08-27). Bengali/ASCII DIGITS ("১০ বছর", "10+ বছর") already
# parsed correctly via to_ascii_digits() + the \d{1,2} regex below — this
# table only covers the gap: word forms like "দশ বছরের বেশি" (a real
# candidate case, 8801718415009, that was silently zeroed by the old
# digit-only parser). Deliberately small/common values only, not
# exhaustive — anything outside this table falls through to the
# preserve-raw-text path rather than a guess.
_BN_EXPERIENCE_NUMBER_WORDS: dict[str, int] = {
    "শূন্য": 0, "এক": 1, "দুই": 2, "তিন": 3, "চার": 4, "পাঁচ": 5,
    "ছয়": 6, "সাত": 7, "আট": 8, "নয়": 9, "দশ": 10,
    "এগারো": 11, "বারো": 12, "তেরো": 13, "চৌদ্দ": 14, "পনেরো": 15,
    "ষোল": 16, "সতেরো": 17, "আঠারো": 18, "উনিশ": 19, "বিশ": 20,
    "পঁচিশ": 25, "ত্রিশ": 30, "চল্লিশ": 40,
}
_NO_EXPERIENCE_RE = re.compile(
    r"নতুন|কোনো\s*অভিজ্ঞতা\s*নেই|অভিজ্ঞতা\s*নাই|no\s*experience", re.IGNORECASE,
)


def _parse_experience(text: str) -> Optional[int]:
    """Extract claimed years-of-experience as an int, or None when the
    claim cannot be safely reduced to a single number (Owner directive
    2026-08-27: never silently convert an unparseable free-text claim to
    0 — callers must preserve the raw text instead, e.g. via
    _upsert_experience_claim(); do not invent a duration here).

    Returns 0 only when the text unambiguously states zero/none/new
    ("0", "নতুন", "কোনো অভিজ্ঞতা নেই"). Handles ASCII digits, Bengali
    digits (transliterated the same way _parse_phone() already does —
    "১০+ বছর" -> "10+ বছর", still matches), and a small set of common
    Bengali number WORDS. Anything else (an amount too irregular to
    safely normalize) returns None — the caller is responsible for
    preserving the raw claim text rather than guessing."""
    if not text or not text.strip():
        return None
    stripped = text.strip()
    from modules.phone_normalizer import to_ascii_digits
    m = re.search(r"\b(\d{1,2})\b", to_ascii_digits(stripped))
    if m:
        return int(m.group(1))
    if _NO_EXPERIENCE_RE.search(stripped):
        return 0
    for word, value in _BN_EXPERIENCE_NUMBER_WORDS.items():
        if word in stripped:
            return value
    return None


def _parse_phone(text: str) -> Optional[str]:
    # 2026-08-21 fix: transliterate Bengali numerals to ASCII first, reusing
    # modules.phone_normalizer's canonical transliteration helper (real
    # candidate case: "০১৭১৬০৮১৯২৫" -- a valid number, just Bengali digits
    # -- previously never parsed here, repeatedly re-asking for a phone
    # number the candidate had already given). Output format/contract is
    # unchanged (still an 11-digit 01XXXXXXXXX-shaped string, not the
    # normalizer's own 13-digit 8801XXXXXXXXX canonical form) -- this only
    # reuses the digit-transliteration step, not the full normalizer, so
    # every existing caller of this function keeps seeing the same shape.
    from modules.phone_normalizer import to_ascii_digits
    m = re.search(r"(0?1[3-9]\d{8})", re.sub(r"\D", "", to_ascii_digits(text)))
    return m.group(1) if m else None


# ── Scoring ────────────────────────────────────────────────────────────────────

def _compute_score(session: dict) -> tuple[int, str]:
    score = 0
    exp = session.get("experience_years") or 0
    if exp >= 6:
        score += 60
    elif exp >= 3:
        score += 40
    elif exp >= 1:
        score += 20
    if session.get("job_preference") in ("Escort / Survey Scout", "Security Guard"):
        score += 20
    required = ["full_name", "age", "area", "job_preference"]
    if all(session.get(f) for f in required):
        score += 20
    score = min(score, 100)
    bucket = "hot" if score >= 70 else ("warm" if score >= 40 else "cold")
    return score, bucket


# ── DB helpers (graceful fallback if table missing) ───────────────────────────

async def _get_session(phone: str) -> Optional[dict]:
    try:
        return await fetch_one(
            "SELECT * FROM fazle_recruitment_sessions WHERE phone = $1", phone
        )
    except Exception as e:
        log.debug(f"[recruit] session lookup failed: {e}")
        return None


async def _create_session(phone: str, source_msg: str, source: str) -> Optional[int]:
    try:
        sid = await fetch_val(
            """INSERT INTO fazle_recruitment_sessions
                   (phone, collection_step, funnel_stage, source_message, source_bridge, updated_at)
               VALUES ($1, 'name', 'collecting', $2, $3, NOW())
               RETURNING id""",
            phone, source_msg[:500], source,
        )
        return sid
    except Exception as e:
        log.warning(f"[recruit] create session failed: {e}")
        return None


async def _update_session(phone: str, fields: dict, *, conn: Any = None) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc)
    set_parts = []
    vals = []
    for i, (k, v) in enumerate(fields.items(), 1):
        set_parts.append(f"{k} = ${i}")
        vals.append(v)
    vals.append(phone)
    sql = f"UPDATE fazle_recruitment_sessions SET {', '.join(set_parts)} WHERE phone = ${len(vals)}"
    try:
        if conn is not None:
            await conn.execute(sql, *vals)
        else:
            await execute(sql, *vals)
    except Exception as e:
        if conn is not None:
            raise
        log.warning(f"[recruit] update session failed: {e}")


async def _ensure_candidate_for_session(
    phone: str,
    session_id: int,
    source_message: str,
    source: str,
) -> Optional[int]:
    try:
        candidate_id = await fetch_val(
            """INSERT INTO wbom_candidates
                   (phone, funnel_stage, collection_step, source_message, updated_at)
               VALUES ($1, 'collecting', 'name', $2, NOW())
               ON CONFLICT (phone) DO NOTHING
               RETURNING candidate_id""",
            phone, source_message[:500],
        )
        if candidate_id is None:
            candidate_id = await fetch_val(
                "SELECT candidate_id FROM wbom_candidates WHERE phone = $1",
                phone,
            )
        if candidate_id is not None:
            await execute(
                """UPDATE fazle_recruitment_sessions
                   SET candidate_id = $1, updated_at = NOW()
                   WHERE id = $2""",
                candidate_id, session_id,
            )
            return int(candidate_id)
    except Exception as e:
        log.warning(
            "[recruit] candidate link failed phone=%s session=%s source=%s: %s",
            phone, session_id, source, e,
        )
    return None


async def _audit_candidate_conversation(
    candidate_id: int,
    step: str,
    message: str,
    source: str,
    message_id: Optional[str] = None,
    *,
    direction: str = "inbound",
    conn: Any = None,
) -> Optional[bool]:
    """Append one row to wbom_candidate_conversations (append-only audit).

    direction (2026-08-29, lifecycle-closure GAP 2): 'inbound' (default,
    every existing caller) for a candidate's own message; 'outbound' for a
    message the system sent (e.g. a batch invitation); 'event' for a
    lifecycle fact that did not come from a WhatsApp message at all
    (employee conversion, an authenticated Admin phone-call note). The
    (source_bridge, message_id) dedup index still applies -- 'event' rows
    pass message_id=None so they are never deduped against each other.
    """
    try:
        sql = """INSERT INTO wbom_candidate_conversations
                     (candidate_id, step, direction, message_text, source_bridge, message_id)
                 VALUES ($1, $2, $5, $3, $4, $6)
                 ON CONFLICT (source_bridge, message_id)
                     WHERE message_id IS NOT NULL
                 DO NOTHING
                 RETURNING conv_id"""
        # message_id stays the LAST positional arg (existing callers/tests
        # rely on args[-1] being the message_id / None).
        args = (candidate_id, step, message, source, direction, message_id or None)
        if conn is not None:
            conv_id = await conn.fetchval(sql, *args)
        else:
            conv_id = await fetch_val(sql, *args)
        return conv_id is not None
    except Exception as e:
        if conn is not None:
            raise
        log.warning(
            "[recruit] conversation audit failed candidate=%s step=%s: %s",
            candidate_id, step, e,
        )
        return None


async def _finalize_candidate(candidate_id: int, session: dict, *, conn: Any = None) -> None:
    try:
        # Owner directive (2026-08-27): intake completion transitions
        # straight into the post-intake conversation instead of ending it --
        # COALESCE so a hypothetical re-finalize of an already-progressed
        # candidate (post_intake_status already set to something else) can
        # never clobber it back to the starting state.
        sql = """UPDATE wbom_candidates
                 SET full_name = $1, age = $2, area = $3,
                     job_preference = $4, experience_years = $5,
                     funnel_stage = 'scored', collection_step = NULL,
                     score = $6, score_bucket = $7,
                     post_intake_status = COALESCE(post_intake_status, 'awaiting_agreement'),
                     last_contact_at = NOW(), updated_at = NOW()
                 WHERE candidate_id = $8"""
        args = (
            session.get("full_name"), session.get("age"), session.get("area"),
            session.get("job_preference"), session.get("experience_years") or 0,
            session.get("score") or 0, session.get("score_bucket") or "cold",
            candidate_id,
        )
        if conn is not None:
            await conn.execute(sql, *args)
        else:
            await execute(sql, *args)
    except Exception as e:
        if conn is not None:
            raise
        log.warning("[recruit] candidate finalize failed candidate=%s: %s", candidate_id, e)


async def _apply_audited_transition(
    phone: str,
    candidate_id: int,
    step: str,
    message: str,
    source: str,
    message_id: Optional[str],
    update: dict,
    final_session: Optional[dict] = None,
) -> Optional[bool]:
    try:
        async with db_conn() as conn:
            async with conn.transaction():
                try:
                    inserted = await _audit_candidate_conversation(
                        candidate_id=candidate_id,
                        step=step,
                        message=message,
                        source=source,
                        message_id=message_id,
                        conn=conn,
                    )
                except Exception as e:
                    raise _ConversationAuditUnavailable from e
                if inserted is False:
                    return False
                await _update_session(phone, dict(update), conn=conn)
                if final_session is not None:
                    await _finalize_candidate(candidate_id, final_session, conn=conn)
        return True
    except _ConversationAuditUnavailable as e:
        log.warning(
            "[recruit] conversation audit failed; continuing without audit "
            "candidate=%s step=%s: %s",
            candidate_id, step, e,
        )
        try:
            async with db_conn() as conn:
                async with conn.transaction():
                    await _update_session(phone, dict(update), conn=conn)
                    if final_session is not None:
                        await _finalize_candidate(candidate_id, final_session, conn=conn)
            return None
        except Exception as fallback_error:
            log.warning(
                "[recruit] audit fail-open transition rolled back "
                "candidate=%s step=%s: %s",
                candidate_id, step, fallback_error,
            )
            return False
    except Exception as e:
        log.warning(
            "[recruit] audited transition rolled back candidate=%s step=%s: %s",
            candidate_id, step, e,
        )
        return False


# ── Main intake function ───────────────────────────────────────────────────────

async def _advance_past_locked_step(
    session: dict,
    candidate_id: Optional[int],
    phone: str,
    step: str,
    raw_message: str,
    source: str,
    message_id: Optional[str],
):
    """Phase B (2026-08-23) deterministic advance helper used by the
    field-locking rule. When the candidate re-sends a value that the
    session already has stored, intake_message() short-circuits here.

    Behavior:
      - records the redundant inbound in wbom_candidate_conversations
        for auditability (so no message is lost)
      - bumps the session's collection_step to the NEXT step (does NOT
        keep the same step)
      - replies with the natural next-step STEP_QUESTIONS prompt

    Does NOT itself call the LLM / Hermes layer. Does NOT advance more
    than one step.
    """
    try:
        idx = COLLECTION_STEPS.index(step)
    except ValueError:
        idx = len(COLLECTION_STEPS) - 1
    next_step = COLLECTION_STEPS[idx + 1] if idx + 1 < len(COLLECTION_STEPS) else "complete"
    update = {"collection_step": next_step}
    if candidate_id is not None:
        try:
            await _apply_audited_transition(
                phone, candidate_id, step, raw_message, source, message_id, update,
            )
        except Exception:
            try:
                await _update_session(phone, update)
            except Exception:
                pass
    else:
        try:
            await _update_session(phone, update)
        except Exception:
            pass
    if next_step == "complete":
        # All collection steps done -- fall through to scoring below.
        # We return a no-op reply here and the caller chain is expected
        # to call back into intake_message with the new step. For safety,
        # we use a brief next-step-acknowledgement string.
        return {
            "reply": "ধন্যবাদ, আপনার আবেদন শেষ হয়েছে।",
            "action": "collecting",
            "session_id": session["id"],
        }
    return {
        "reply": STEP_QUESTIONS[next_step],
        "action": "collecting",
        "session_id": session["id"],
    }


async def intake_message(
    phone: str,
    message: str,
    source: str = "whatsapp",
    role: str = "unknown",
    message_id: Optional[str] = None,
    trigger_text: Optional[str] = None,
) -> dict:
    """
    Process one inbound WhatsApp message for recruitment funnel.

    role: identity_brain's resolved role for this sender, e.g. "unknown" or
    "candidate". Only used to decide whether to prepend a soft ask to the
    very first reply for a brand-new session (see UNKNOWN_SENDER_SOFT_ASK) --
    never changes funnel behavior otherwise.

    Returns:
      {
        "reply": str,       — text to send back (or "" to ignore)
        "action": str,      — created | collecting | scored | ignored | already_applied
        "session_id": int | None
      }
    """
    phone = phone.strip()
    raw_message = message
    text = raw_message.strip()
    lower = (trigger_text if trigger_text is not None else text).lower()

    session = await _get_session(phone)

    # ── New visitor ────────────────────────────────────────────────────────────
    if session is None:
        # 2026-08-21 fix (Owner Correction 1 / regression case ...1609): this
        # used to check only the bare INTAKE_KEYWORDS set, which is NARROWER
        # than RECRUITMENT_TRIGGER_PHRASES — e.g. "কাজ করতে চাই" ("I want to
        # work") matches RECRUITMENT_TRIGGER_PHRASES (so recruitment_
        # eligibility() correctly calls this "explicit_recruitment", highest
        # confidence) but contains no INTAKE_KEYWORDS substring at all, so a
        # brand-new session here silently returned action="ignored" for
        # exactly the message type that should most reliably open a lead.
        # is_recruitment_trigger() is the superset (INTAKE_KEYWORDS OR
        # RECRUITMENT_TRIGGER_PHRASES) — using it here instead keeps this
        # gate at least as permissive as every caller that already decided
        # this message is a genuine recruitment trigger, and callers that
        # only relied on the narrower bare-keyword match still pass (it's a
        # superset, never narrower).
        if not is_recruitment_trigger(lower):
            return {"reply": "", "action": "ignored", "session_id": None}

        sid = await _create_session(phone, text, source)
        if sid is not None:
            candidate_id = await _ensure_candidate_for_session(phone, sid, text, source)
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id=candidate_id,
                    step="intake",
                    message=raw_message,
                    source=source,
                    message_id=message_id,
                )
        reply = STEP_QUESTIONS["name"]
        if (role or "").strip().lower() == "unknown":
            reply = UNKNOWN_SENDER_SOFT_ASK + reply
            # KNOWN GAP (2026-07-11): we send a soft-ask but do NOT
            # automatically cross-reference against employee records.
            # An existing employee texting from a new SIM is
            # indistinguishable from a stranger until admin manually
            # links the number. To fix: implement
            # secondary_evidence_match() in identity_brain that checks
            # cash_transactions/attendance/escort_roster by name hint
            # from the soft-ask reply. Tracked as a separate task.
        return {"reply": reply, "action": "created", "session_id": sid}

    # ── Already past collection ────────────────────────────────────────────────
    stage = session.get("funnel_stage", "collecting")
    if stage not in ("collecting", "new"):
        return {
            "reply": ALREADY_APPLIED_MSG,
            "action": "already_applied",
            "session_id": session["id"],
        }

    # ── Collecting answers step by step ───────────────────────────────────────
    step = session.get("collection_step") or "name"
    candidate_id = session.get("candidate_id")
    if candidate_id is None:
        candidate_id = await _ensure_candidate_for_session(
            phone,
            session["id"],
            session.get("source_message") or text,
            session.get("source_bridge") or source,
        )
    update: dict = {}

    # ── Field-locking rule (Phase B, 2026-08-23) ──────────────────────────────
    # When the candidate re-sends the same value the session already has
    # stored, treat that as a no-op (no DB write, no re-prompt). This
    # closes cf_R013's deterministic "re-prompted age-limit four times"
    # leg without collapsing conversation semantics: a *new* value still
    # flows through normal extract/validation; an unparseable message
    # still hits the existing re-prompt paths below.
    _locked_already = {
        "full_name": session.get("full_name"),
        "age": session.get("age"),
        "area": session.get("area"),
        "job_preference": session.get("job_preference"),
        "experience_years": session.get("experience_years"),
        "confirmed_phone": session.get("confirmed_phone"),
    }

    # Phase 7 (2026-08-26): Generic no-duplicate-question invariant.
    # Normalized comparator (Bangla vs ASCII digits, whitespace/punctuation)
    # used to detect "same value re-sent" even when the per-step parser
    # produces a slightly different representation (e.g. "৩২" vs "32",
    # "Dhaka ." vs "Dhaka"). Falls back to the per-step exact-match
    # _locked_already check below — Phase 7 only ADDS a normalized
    # variant, never replaces the original. Read-only.
    _phase7_skip_step = None
    try:
        if step == "age":
            _phase7_candidate = _parse_age(text, await _get_effective_age_range())
            if _phase7_candidate is not None and is_already_answered(
                session, step, str(_phase7_candidate),
                correction_text=raw_message,
            ):
                _phase7_skip_step = step
        elif step == "job_preference":
            _phase7_candidate = _parse_job_preference(text)
            if _phase7_candidate and is_already_answered(
                session, step, _phase7_candidate,
                correction_text=raw_message,
            ):
                _phase7_skip_step = step
        elif step == "experience":
            _phase7_candidate = _parse_experience(text)
            _phase7_value = _phase7_candidate if _phase7_candidate is not None else 0
            if is_already_answered(
                session, step, str(_phase7_value),
                correction_text=raw_message,
            ):
                _phase7_skip_step = step
        elif step in ("name", "area", "phone_confirm"):
            if is_already_answered(
                session, step, text,
                correction_text=raw_message,
            ):
                _phase7_skip_step = step
    except Exception as _phase7_exc:  # noqa: BLE001 — fail closed
        log.debug("[recruit] Phase 7 is_already_answered skipped (non-fatal): %s", _phase7_exc)
        _phase7_skip_step = None
    if _phase7_skip_step:
        return await _advance_past_locked_step(
            session, candidate_id, phone, _phase7_skip_step,
            raw_message, source, message_id,
        )

    if step == "name":
        if not _looks_like_name(text):
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id, step, raw_message, source, message_id,
                )
            return {
                "reply": "দুঃখিত, বুঝতে পারিনি। দয়া করে শুধু আপনার পুরো নাম লিখুন (যেমন: Md Karim)।",
                "action": "collecting",
                "session_id": session["id"],
            }
        # Field-lock check: if the existing full_name is the same as the
        # trimmed input, accept silently and advance step.
        proposed_name = text.strip()[:100]
        if _locked_already["full_name"] and _locked_already["full_name"] == proposed_name:
            return await _advance_past_locked_step(
                session, candidate_id, phone, step, raw_message, source, message_id,
            )
        update["full_name"] = proposed_name
    elif step == "age":
        age_range = await _get_effective_age_range()
        val = _parse_age(text, age_range)
        if val:
            # Field-lock: if the session already records the same age,
            # accept silently without re-asking. cf_R013 closes here.
            if _locked_already["age"] == val:
                return await _advance_past_locked_step(
                    session, candidate_id, phone, step, raw_message, source, message_id,
                )
            update["age"] = val
        else:
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id, step, raw_message, source, message_id,
                )
            return {
                "reply": f"সাধারণ বয়সসীমা {_bn_digits(age_range[0])}–{_bn_digits(age_range[1])} বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)",
                "action": "collecting",
                "session_id": session["id"],
            }
    elif step == "area":
        proposed = text.strip()[:100]
        if _locked_already["area"] and _locked_already["area"] == proposed:
            return await _advance_past_locked_step(
                session, candidate_id, phone, step, raw_message, source, message_id,
            )
        update["area"] = proposed
    elif step == "job_preference":
        pos = _parse_job_preference(text)
        if pos:
            if _locked_already["job_preference"] and _locked_already["job_preference"] == pos:
                return await _advance_past_locked_step(
                    session, candidate_id, phone, step, raw_message, source, message_id,
                )
            update["job_preference"] = pos
        else:
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id, step, raw_message, source, message_id,
                )
            return {
                "reply": "দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন।\n" + STEP_QUESTIONS["job_preference"],
                "action": "collecting",
                "session_id": session["id"],
            }
    elif step == "experience":
        val = _parse_experience(text)
        new_exp = val if val is not None else 0
        if _locked_already["experience_years"] is not None and _locked_already["experience_years"] == new_exp:
            return await _advance_past_locked_step(
                session, candidate_id, phone, step, raw_message, source, message_id,
            )
        update["experience_years"] = new_exp
        if val is None and candidate_id is not None:
            # Owner directive (2026-08-27): _parse_experience() couldn't
            # safely reduce this free-text claim to a number — preserve
            # the raw text as an unverified claim (wbom_candidates.notes)
            # instead of letting it silently read as "confirmed zero".
            # The responsible-role post-intake flow reads this back later
            # via _parse_experience_claim_block(). Best-effort, never
            # blocks intake.
            try:
                await _upsert_experience_claim(
                    candidate_id,
                    claimed_role=session.get("job_preference"),
                    claimed_duration=text.strip()[:200],
                )
            except Exception as exc:  # noqa: BLE001
                log.debug(
                    "[recruit] intake experience claim preserve failed candidate=%s: %s",
                    candidate_id, exc,
                )
    elif step == "phone_confirm":
        phone_parsed = _parse_phone(text)
        if phone_parsed:
            if _locked_already["confirmed_phone"] and _locked_already["confirmed_phone"] == phone_parsed:
                return await _advance_past_locked_step(
                    session, candidate_id, phone, step, raw_message, source, message_id,
                )
            update["confirmed_phone"] = phone_parsed
        # Accept even if not parsed — use original phone

    # Advance to next step
    try:
        next_idx = COLLECTION_STEPS.index(step) + 1
    except ValueError:
        next_idx = len(COLLECTION_STEPS)

    if next_idx < len(COLLECTION_STEPS):
        next_step = COLLECTION_STEPS[next_idx]
        update["collection_step"] = next_step
        if candidate_id is not None:
            audit_inserted = await _apply_audited_transition(
                phone, candidate_id, step, raw_message, source, message_id, update,
            )
            if audit_inserted is False:
                return {
                    "reply": STEP_QUESTIONS.get(step, STEP_QUESTIONS["name"]),
                    "action": "collecting",
                    "session_id": session["id"],
                }
        else:
            await _update_session(phone, update)
        reply = STEP_QUESTIONS[next_step]
        return {"reply": reply, "action": "collecting", "session_id": session["id"]}

    # ── Collection complete — score ────────────────────────────────────────────
    merged = {**dict(session), **update}
    score, bucket = _compute_score(merged)
    # 2026-08-21 fix: fazle_recruitment_sessions.collection_step is NOT NULL
    # (default 'name') -- setting it to None here has always violated that
    # constraint, silently rolling back the entire completion transition
    # (caught by _apply_audited_transition's own except-and-log-warning, or
    # by _update_session's identical fail-open behavior in the no-candidate
    # branch below). Confirmed live, 2026-08-21: no candidate has ever
    # actually reached funnel_stage='scored' via this path -- every
    # completion attempt fell back to STEP_QUESTIONS.get(step, ...),
    # silently re-asking the LAST question forever instead of finishing.
    # 'complete' is a non-null sentinel, never treated as a real collection
    # step: every read site (`session.get("collection_step") or "name"`) is
    # only ever reached while funnel_stage is still 'collecting'/'new' --
    # once funnel_stage='scored', the ALREADY_APPLIED_MSG branch returns
    # before any of those reads happen, so the sentinel's exact value is
    # never interpreted as a step name in practice.
    update["collection_step"] = "complete"
    update["funnel_stage"] = "scored"
    update["score"] = score
    update["score_bucket"] = bucket
    final_session = {**merged, **update}
    if candidate_id is not None:
        audit_inserted = await _apply_audited_transition(
            phone, candidate_id, step, raw_message, source, message_id, update, final_session,
        )
        if audit_inserted is False:
            return {
                "reply": STEP_QUESTIONS.get(step, STEP_QUESTIONS["name"]),
                "action": "collecting",
                "session_id": session["id"],
            }
    else:
        await _update_session(phone, update)

    log.info(f"[recruit] {phone} scored={score} ({bucket}) — {merged.get('full_name','?')}")
    return {
        "reply": INTAKE_COMPLETE_MSG,
        "action": "scored",
        "session_id": session["id"],
    }


RECRUITMENT_TRIGGER_PHRASES: tuple[str, ...] = (
    "কাজ চাই", "কাজ করতে চাই", "কাজ আছে", "কাজ পাব", "কাজ করতে আগ্রহী",
    "looking for work", "need a job", "job available", "job interest",
    # "job" in job-seeking context (was a bare INTAKE_KEYWORDS entry)
    "a job", "any job", "job vacancy", "job apply", "apply for job",
    "job korte chai", "job er jonno", "job ache", "job lagbe",
    "job pete chai", "job dorkar", "job chai",
    # "interested" in job-seeking context (was a bare INTAKE_KEYWORDS entry;
    # its Bangla equivalent "আগ্রহী" keeps working via the existing
    # phrase above and via "চাকরি"/"নিয়োগ" (still bare keywords) --
    # no new Bangla phrase added here, see comment on INTAKE_KEYWORDS above.
    "interested in this job", "interested in the job", "interested to apply",
    "interested in working", "interested in joining", "job e interested",
    "interested for the post", "interested in this post",
    "interested in recruitment", "interested in the vacancy",
)


def is_recruitment_trigger(text: str) -> bool:
    """Quick check: does this message look like a job inquiry?"""
    lower = text.lower()
    if any(kw in lower for kw in INTAKE_KEYWORDS):
        return True
    return any(phrase in lower for phrase in RECRUITMENT_TRIGGER_PHRASES)


async def get_active_session(phone: str) -> Optional[dict]:
    """Return a fresh collecting session for phone, or the candidate's
    active post-intake session, or None.

    Owner directive (2026-08-27): a scored candidate whose
    post_intake_status is not terminal (see POST_INTAKE_TERMINAL_STATUSES)
    counts as an active recruitment conversation, same as a mid-intake
    session -- this is the single point recruitment_eligibility() consults
    for its strongest signal (active_session_continuation, checked before
    any keyword/intent check), so extending it here is enough to route
    every subsequent message from a scored-but-open candidate back into the
    recruitment flow without a second routing mechanism. route_recruitment_
    message() distinguishes the two cases via the post_intake_active key
    this function adds -- it never runs field-collection logic for a
    post-intake session.

    Collecting/new branch is BYTE-IDENTICAL to before this change (same TTL
    expiry, same return shape) -- only a new branch for funnel_stage=
    'scored' was added below it."""
    session = await _get_session(phone)
    if not session:
        return None

    stage = session.get("funnel_stage")

    if stage in ("collecting", "new"):
        updated_at = session.get("updated_at") or session.get("created_at")
        if updated_at:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - updated_at > SESSION_TTL:
                await execute(
                    """UPDATE fazle_recruitment_sessions
                       SET funnel_stage='expired'
                       WHERE phone=$1 AND funnel_stage IN ('collecting','new')""",
                    phone,
                )
                log.info("[recruit] expired stale session phone=%s updated_at=%s", phone, updated_at)
                return None
        return session

    if stage == "scored":
        candidate_id = session.get("candidate_id")
        if candidate_id is None:
            return None
        try:
            status = await fetch_val(
                "SELECT post_intake_status FROM wbom_candidates WHERE candidate_id=$1",
                candidate_id,
            )
        except Exception as e:
            log.debug(f"[recruit] post_intake_status lookup failed candidate={candidate_id}: {e}")
            return None
        if status in POST_INTAKE_TERMINAL_STATUSES:
            return None
        session = dict(session)
        # NULL means "scored before migration 072 / not yet processed" --
        # treated the same as the status _finalize_candidate() sets by
        # default, per that migration's own no-backfill note.
        session["post_intake_status"] = status or "awaiting_agreement"
        session["post_intake_active"] = True
        return session

    return None


# Moved from modules.recruitment_ai (2026-07-11) — this was the only live
# import from that module; recruitment_ai's own reply-generation pipeline
# (generate_recruitment_reply etc.) is superseded by
# modules.conversation_layer.recruitment, called below via
# route_recruitment_message(). recruitment_ai has no live callers left.
_QUESTION_HINTS = (
    "who are you",
    "who r u",
    "আপনি কে",
    "তুমি কে",
    "কেন",
    "why",
    "am i asked for job",
    "asked for job",
    "lok lagbe",
    "লোক লাগবে",
    "কাজ আছে",
    "job ache",
)


def looks_like_recruitment_followup(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return False
    return any(hint in lower for hint in _QUESTION_HINTS)


async def recruitment_eligibility(
    phone: str,
    text: str,
    *,
    role: str = "unknown",
    intent: str = "unknown",
    source: str = "",
) -> dict:
    """Return the single recruitment routing/delivery decision.

    source (2026-08-21, urgent Facebook-boost recovery pass): the bridge
    this message arrived on, e.g. "bridge3". Two real gaps found live
    against today's actual Facebook-ad traffic:

    1. Once a candidate has an active collecting session, their next reply
       is very often free text answering the funnel's own question (a
       district name, "Any post", a bare age/height line) -- it will never
       match is_recruitment_trigger() or looks_like_recruitment_followup()'s
       keyword hints, so it silently fell out of the funnel mid-conversation
       and got answered by Route B's generic LLM reply instead, with no
       lead/session update at all. Confirmed live: a candidate ("Abuhanif")
       held a complete real conversation -- name, age, height, phone,
       district, education, position preference, all given -- entirely
       outside the funnel, zero lead/session rows, because no single reply
       after the first matched a keyword. Fix: ANY message from a phone
       with an active collecting/new session is eligible, unconditionally
       -- they already explicitly entered the funnel once; a session
       existing at all is a far stronger signal than any keyword.

    2. bridge3 (config label "REC2") is a dedicated, single-purpose
       recruitment channel -- not a general inquiry line -- confirmed by
       its own no-cross-bridge-fallback design elsewhere in this codebase.
       Its actual live traffic today is dominated by Meta/Facebook Click-
       to-WhatsApp ad opener text ("Hello! Can I get more info on this?"),
       which is a Meta-generated template, not the candidate's own words,
       and matches no recruitment keyword or detector layer -- yet on this
       specific number it is, for all practical purposes, always a
       recruitment inquiry. Fix: for a brand-new (no active session)
       unknown/new_lead/candidate sender on bridge3 specifically, treat
       the message as recruitment-eligible even with no keyword/detector
       match. Scoped to bridge3 only -- this does NOT change eligibility
       on bridge1/bridge2/meta, where "Hello, can you help me" is a
       genuinely ambiguous general inquiry, not a recruitment-line signal.
    """
    role = (role or "unknown").lower()
    intent = (intent or "unknown").lower()
    explicit = is_recruitment_trigger(text)
    active_session = await get_active_session(phone)

    if role in OPERATIONAL_ROLES or role not in ("unknown", "new_lead", "candidate"):
        return {"eligible": False, "autosend": False, "reason": "operational_identity",
                "active_session": active_session}
    if active_session:
        # 2026-08-25 recovery-pass fix: this check now runs BEFORE the
        # operational_intent check below (was after -- see git history).
        # A mid-funnel candidate's fee/location/etc. question can trip an
        # OPERATIONAL_INTENTS keyword (e.g. bare "টাকা" -> payment_due) even
        # though they are actively answering the funnel's own questions.
        # An existing active session is a far stronger signal than any
        # single-turn intent classification, so it must not be overridden
        # by one -- superset of the old "session_followup_draft" keyword-
        # hint check. Still draft-only (autosend=False) so an established
        # candidate's free-text answer never bypasses the normal review
        # posture, only the explicit-trigger/detector-entity paths below
        # autosend.
        return {"eligible": True, "autosend": False, "reason": "active_session_continuation",
                "active_session": active_session}
    if intent in OPERATIONAL_INTENTS:
        return {"eligible": False, "autosend": False, "reason": "operational_intent",
                "active_session": active_session}
    if explicit:
        return {"eligible": True, "autosend": True, "reason": "explicit_recruitment",
                "active_session": active_session}

    # 3-layer detector (2026-07-11): catches natural phrasing the flat
    # is_recruitment_trigger() keyword set above misses (e.g. "ভাই সুযোগ
    # দেন", "আমি available"). Conservative: only the fast, deterministic
    # entity layer autosends; the intent/context layers are lower-confidence
    # and go to draft review instead, same as active_session_continuation.
    from modules.recruitment_detector import detect_recruitment_intent

    detector = await detect_recruitment_intent(text=text, phone=phone, intent=intent)
    if detector["is_recruitment"]:
        return {
            "eligible": True,
            "autosend": detector["layer"] == "entity",
            "reason": f"detector_{detector['layer']}",
            "active_session": active_session,
            "detector": detector,
        }

    if source == "bridge3":
        return {"eligible": True, "autosend": True, "reason": "bridge3_dedicated_channel",
                "active_session": active_session}

    return {"eligible": False, "autosend": False, "reason": "no_recruitment_context",
            "active_session": active_session}


def _is_llm_priority_phone(phone: str) -> bool:
    """Phase 1B (2026-08-06, Owner-approved allowlist, see
    management_decisions.md "Recruitment Phase 1B — Active-Session LLM
    Priority Allowlist"). True only for phones explicitly approved to have
    the live KB/RAG/LLM reply layer (generate_recruitment_reply()) tried
    BEFORE intake_message() during an active recruitment session --
    everyone else keeps the unconditional intake_message()-first behavior
    in route_recruitment_message() unchanged. Deliberately a plain exact-
    string check, not phone-normalized (unlike e.g.
    modules.escort._escort_client_phone_list()'s normalize_bd_phone()
    usage) -- every phone already reaching route_recruitment_message() is
    already in this module's own canonical 8801XXXXXXXXX form (see
    fazle_recruitment_sessions.phone in knowledge_base/06_developer_system/
    recruitment_flow_system.md), so adding a second normalization step
    here would risk a silent format mismatch rather than prevent one.
    Config value is empty by default -- see recruitment_llm_priority_phones
    in app/config.py for the full comment and admission-criteria record."""
    from app.config import get_settings
    raw = getattr(get_settings(), "recruitment_llm_priority_phones", "") or ""
    allowlist = {p.strip() for p in raw.split(",") if p.strip()}
    return phone in allowlist


# ── Post-intake conversation helpers (Owner directive, 2026-08-27) ──────────

async def _classify_post_intake_turn(
    phone: str, text: str, *, history: str = "",
    extract_experience_role: Optional[str] = None,
) -> dict:
    """Best-effort semantic classification of a scored candidate's reply.
    Always returns {"post_intake_status": Optional[str], "experience":
    Optional[dict]} — NEVER guesses on either key (same fail-closed-on-
    ambiguity posture as this codebase's verify_employee_claim() tool).
    Reuses modules.hermes_dispatch.call_decision_llm() — the SAME
    structured-classification call shape modules.recruitment_turn_decision
    already uses for intake-turn decisions (caller_scope="customer", no
    tools, text in/text out) — deliberately not a new LLM integration.

    extract_experience_role (Owner directive, 2026-08-27): when given (a
    responsible-role job_preference string, see _is_responsible_role()),
    ONE extra instruction block is appended to the SAME system prompt and
    the SAME call — deliberately not a second LLM round-trip, to avoid
    doubling latency/timeout risk on an already-slow path — asking the
    model to also extract claimed_company/claimed_duration/reference_name/
    reference_phone/no_relevant_experience from this turn AND the recent
    history (candidates often answer across several messages). Returned
    under the "experience" key only when at least one field was extracted;
    None otherwise.

    Read-only w.r.t. the LLM call; the caller (_route_post_intake_message)
    owns all DB writes, same separation as every other decision-LLM caller
    in this codebase (the model classifies/extracts meaning, deterministic
    code persists state)."""
    result: dict = {"post_intake_status": None, "experience": None}
    try:
        from modules.hermes_dispatch import call_decision_llm
    except Exception as exc:  # noqa: BLE001 -- fail closed
        log.debug("[recruit] post_intake classify import failed: %s", exc)
        return result

    system_prompt = _POST_INTAKE_CLASSIFY_SYSTEM_PROMPT
    if extract_experience_role:
        system_prompt = system_prompt + _EXPERIENCE_EXTRACTION_INSTRUCTIONS_TMPL.format(
            role=extract_experience_role,
        )
    payload = (text or "")[:500]
    if extract_experience_role and history:
        payload = f"সাম্প্রতিক কথোপকথন:\n{history[-1500:]}\n\nবর্তমান বার্তা:\n{payload}"

    try:
        raw = await call_decision_llm(
            system_prompt=system_prompt, user_payload=payload, sender_phone=phone,
        )
    except Exception as exc:  # noqa: BLE001 -- fail closed, never raise
        log.debug("[recruit] post_intake classify call failed: %s", exc)
        return result
    if not raw:
        return result
    try:
        import json
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group(0)) if match else json.loads(raw)
        confidence = float(data.get("confidence") or 0.0)
    except Exception:
        log.debug("[recruit] post_intake classify: unparsable response, treating as no-op")
        return result

    status = str(data.get("post_intake_status") or "").strip()
    if status in POST_INTAKE_STATUSES and confidence >= _POST_INTAKE_CLASSIFY_CONFIDENCE_FLOOR:
        result["post_intake_status"] = status

    if extract_experience_role and confidence >= _POST_INTAKE_CLASSIFY_CONFIDENCE_FLOOR:
        exp: dict = {}
        for key in ("claimed_company", "claimed_duration", "reference_name", "reference_phone"):
            value = data.get(key)
            if isinstance(value, str) and value.strip() and value.strip().lower() not in ("null", "none", "n/a"):
                exp[key] = value.strip()[:200]
        if data.get("no_relevant_experience") is True:
            exp["no_relevant_experience"] = True
        if exp:
            result["experience"] = exp
    return result


async def _resolve_fee_policy_reply(text: str) -> Optional[str]:
    """Fee-question guard. Only fires when this turn itself looks like a
    fee question (never volunteers -- same trigger as the pre-existing
    SAFE_FEE_PENDING_OWNER_DECISION use at this module's answer-first call
    site). Owner directive (2026-08-27) extends the prior hardcoded-only
    behavior with a directive-check-first step, reusing modules.
    admin_directives (the existing Owner business-rule mutation mechanism)
    instead of a second, parallel policy store:

      1. An active Owner directive on RECRUITMENT_FEE_POLICY_SUBJECT_KEY
         already exists -> answer from it directly (latest Owner decision
         wins over older/conflicting static KB content).
      2. Otherwise -> return the existing safe, non-committal holding
         reply (byte-identical text to before) and best-effort trigger a
         one-time (cooldown-gated) Owner/Admin WhatsApp clarification via
         modules.admin_directives.router.trigger_recruitment_policy_
         clarification() -- never blocks or delays the candidate's reply
         if the trigger call is slow/fails.

    Returns None when the turn isn't a fee question at all (caller then
    proceeds to the normal KB/Hermes reply path unchanged)."""
    from modules.hermes_dispatch import _looks_like_fee_question
    if not _looks_like_fee_question(text):
        return None

    from modules import admin_directives
    try:
        directives = await admin_directives.list_directives(
            subject_key=RECRUITMENT_FEE_POLICY_SUBJECT_KEY, status="active", limit=1,
        )
    except Exception as exc:  # noqa: BLE001 -- fail open to the safe default below
        log.warning("[recruit] fee policy directive lookup failed: %s", exc)
        directives = []
    if directives:
        return directives[0].get("directive_text") or SAFE_FEE_PENDING_OWNER_DECISION

    try:
        from modules.admin_directives.router import trigger_recruitment_policy_clarification
        await trigger_recruitment_policy_clarification(
            subject_type=RECRUITMENT_POLICY_SUBJECT_TYPE,
            subject_key=RECRUITMENT_FEE_POLICY_SUBJECT_KEY,
            conflict_summary=(
                "Live recruitment KB has conflicting joining/training-fee "
                "framing -- some sources say no mandatory upfront payment "
                "(salary-deducted only), at least one live KB row and one "
                "static policy doc state a mandatory upfront/non-refundable "
                "fee. A candidate just asked about the fee."
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- never block the candidate reply
        log.warning("[recruit] fee policy escalation trigger failed: %s", exc)
    return SAFE_FEE_PENDING_OWNER_DECISION


async def _route_post_intake_message(
    phone: str,
    text: str,
    *,
    source: str,
    message_id: Optional[str],
    active_session: dict,
    contact_context: str,
    history: str,
    decision: dict,
) -> dict:
    """Owner directive (2026-08-27): the post-intake counterpart of the
    active-session branch above -- reused entry point is route_recruitment_
    message() (see its call site), never a second router. A scored, non-
    terminal candidate's message NEVER runs field-collection logic
    (intake_message()) -- there is nothing left to collect -- it always
    goes through the same Hermes/KB recruitment-reply machinery every
    other recruitment answer uses, with the post-intake session-state
    block (modules.hermes_dispatch._build_post_intake_state_block, wired
    into the existing _build_recruitment_session_state_block()) supplying
    the already-collected facts and current post_intake_status so Hermes
    never re-asks name/age/etc. and continues the conversation naturally
    instead of stopping at 'our team will contact you'.

    State transition (best-effort, never blocks the reply): a semantic
    classification of THIS turn (_classify_post_intake_turn) may update
    wbom_candidates.post_intake_status BEFORE the reply is generated, so
    the reply itself is grounded in the up-to-date status."""
    candidate_id = active_session.get("candidate_id")

    # Owner directive (2026-08-27): for a responsible-role candidate whose
    # experience claim isn't resolved yet, ask the SAME classify call to
    # also extract role-specific experience detail from this turn (one
    # call, not two — see _classify_post_intake_turn's own docstring).
    job_preference = active_session.get("job_preference")
    extract_role = None
    if candidate_id is not None and _is_responsible_role(job_preference):
        try:
            notes_row = await fetch_one(
                "SELECT notes FROM wbom_candidates WHERE candidate_id=$1", candidate_id,
            )
        except Exception as exc:  # noqa: BLE001 -- fail closed, treat as unresolved
            log.debug("[recruit] experience_claim pre-check failed candidate=%s: %s", candidate_id, exc)
            notes_row = None
        existing_claim = _parse_experience_claim_block((notes_row or {}).get("notes"))
        if not _experience_claim_resolved(existing_claim):
            extract_role = job_preference

    classify_result = await _classify_post_intake_turn(
        phone, text, history=history, extract_experience_role=extract_role,
    )
    new_status = classify_result.get("post_intake_status")
    if new_status and candidate_id is not None and new_status != active_session.get("post_intake_status"):
        try:
            await execute(
                "UPDATE wbom_candidates SET post_intake_status=$1, updated_at=NOW() WHERE candidate_id=$2",
                new_status, candidate_id,
            )
            log.info(
                "[recruit] post_intake_status %s -> %s candidate=%s",
                active_session.get("post_intake_status"), new_status, candidate_id,
            )
            active_session = dict(active_session)
            active_session["post_intake_status"] = new_status
            # Lifecycle-closure GAP 3/E: stamp WHEN readiness was first
            # confirmed so first-N invitation ordering has a real
            # "oldest qualifying readiness timestamp" and not just
            # updated_at. Evidence = the candidate's own message.
            if new_status in _READY_RANK:
                await record_candidate_lifecycle_event(
                    candidate_id, "readiness_confirmed",
                    f"{new_status}: {text[:300]}", source=source,
                    message_id=message_id, direction="inbound",
                )
        except Exception as exc:  # noqa: BLE001 -- reply generation still proceeds
            log.warning("[recruit] post_intake_status update failed candidate=%s: %s", candidate_id, exc)

    # Lifecycle-closure G: an invited candidate saying "রওনা দিয়েছি" /
    # "অফিসের পথে" / "আচ্ছা আসব" is post-invitation progression, not a new
    # inquiry — record the event so history stays continuous. Never restarts
    # intake (this is already the post-intake path).
    if candidate_id is not None:
        _post_invite = classify_post_invitation_signal(text)
        if _post_invite:
            try:
                await record_candidate_lifecycle_event(
                    candidate_id, _post_invite, text[:500], source=source,
                    message_id=message_id, direction="inbound",
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("[recruit] post-invitation event record failed candidate=%s: %s", candidate_id, exc)

    if candidate_id is not None:
        await _audit_candidate_conversation(candidate_id, "post_intake", text, source, message_id)

    exp = classify_result.get("experience")
    if extract_role and exp and candidate_id is not None:
        if exp.get("no_relevant_experience"):
            await _upsert_experience_claim(
                candidate_id, claimed_role=job_preference,
                claimed_company=_NO_RELEVANT_EXPERIENCE_SENTINEL,
            )
        else:
            merged = await _upsert_experience_claim(
                candidate_id, claimed_role=job_preference,
                claimed_company=exp.get("claimed_company"),
                claimed_duration=exp.get("claimed_duration"),
                reference_name=exp.get("reference_name"),
                reference_phone=exp.get("reference_phone"),
            )
            # Owner directive: notify Admin ONLY once, ONLY when enough
            # reference detail exists (company + duration; reference
            # name/phone included in the notification "where available",
            # never required to trigger it) — never on a partial answer.
            if (
                merged
                and merged.get("verification_status") == "unverified"
                and merged.get("claimed_company")
                and merged.get("claimed_duration")
            ):
                merged = await _upsert_experience_claim(
                    candidate_id, verification_status="awaiting_admin_review",
                ) or merged
                try:
                    from modules.admin_directives.router import trigger_candidate_reference_review
                    await trigger_candidate_reference_review(
                        candidate_id=candidate_id, phone=phone, claim=merged,
                    )
                except Exception as exc:  # noqa: BLE001 -- never block the reply
                    log.warning(
                        "[recruit] reference review trigger failed candidate=%s: %s",
                        candidate_id, exc,
                    )

    fee_reply = await _resolve_fee_policy_reply(text)
    if fee_reply:
        return {
            "eligible": True,
            "autosend": bool(decision.get("autosend")),
            "reason": "post_intake_fee_policy",
            "reply": fee_reply,
        }

    reply = None
    from modules.preflight_guard import is_feature_active
    if is_feature_active("hermes_customer_recruitment", default=False):
        from modules.hermes_dispatch import (
            generate_recruitment_reply as _hermes_recruitment_reply,
            build_recruitment_context_with_directives as _build_recruitment_context,
        )
        kb_context = await _build_recruitment_context(text)
        reply = await _hermes_recruitment_reply(
            phone=phone, text=text, source=source,
            kb_context=kb_context, contact_context=contact_context, history=history,
        )
    # Owner directive (2026-08-27, production-readiness pass, Step 6): if
    # the primary Hermes path fails/returns nothing here, do NOT fall
    # through to modules.conversation_layer.generate_recruitment_reply()
    # -- that function is the PRE-INTAKE funnel-entry reply generator (it
    # has no idea this candidate is already scored/post-intake) and can
    # ask for name/age/area/job_preference/experience again, destroying
    # context for a candidate who already answered all of it. Live-
    # confirmed during Phase-A regeneration: candidate 8801403472618 hit
    # exactly this path and got "আগ্রহী হলে নাম, বয়স, শিক্ষা... পাঠান" even
    # though those fields were already on file. POST_INTAKE_FALLBACK_MSG
    # is already the correct, context-preserving, fact-free fallback for
    # this branch -- go straight there instead.
    if not reply or not reply.strip():
        reply = POST_INTAKE_FALLBACK_MSG

    return {
        "eligible": True,
        "autosend": bool(decision.get("autosend")),
        "reason": "post_intake_conversation",
        "reply": reply,
    }


async def route_recruitment_message(
    phone: str,
    text: str,
    *,
    source: str,
    message_id: Optional[str] = None,
    role: str = "unknown",
    intent: str = "unknown",
    history: str = "",
    contact_context: str = "",
    force_eligible: bool = False,
) -> dict:
    """Canonical recruitment route: eligibility + live reply in one place.

    force_eligible (2026-08-22, Task-14B, keyword-only, default False):
    when True, skips the deterministic recruitment_eligibility() ineligible
    early-return below and proceeds as though decision["eligible"] were
    True (reason overridden to "semantic_gate_override" for auditability).
    recruitment_eligibility() is still called normally either way -- only
    its "eligible" verdict is overridden; decision["active_session"] is
    still the real active-session row, so active-session continuation,
    candidate/session lifecycle (intake_message()), and the fact-checked
    LLM informational reply below are entirely UNCHANGED for this call.
    The only caller of this flag is
    modules.message_router._task13_authoritative_dispatch()'s
    candidate/new_lead/unknown branch, itself gated by the separate
    task13_candidate_recruitment_authoritative flag (default OFF) and used
    only when a validated central SemanticDecision(domain=
    "recruitment_conversation", confidence>=0.6) exists for a message the
    deterministic keyword/detector layer alone did not already recognize
    as eligible. Every pre-existing caller (message_router's legacy §7/8
    branch, modules.route_b_handoff) is completely unaffected -- neither
    passes this kwarg, so their behavior is byte-identical to before this
    parameter existed. This does NOT build a second Recruitment funnel --
    every session/candidate/reply mechanism below is the same one
    force_eligible=False callers already use."""
    decision = await recruitment_eligibility(phone, text, role=role, intent=intent, source=source)
    if force_eligible and not decision.get("eligible"):
        decision = dict(decision)
        decision["eligible"] = True
        decision["reason"] = "semantic_gate_override"
    if not decision.get("eligible"):
        return {
            "eligible": False,
            "autosend": bool(decision.get("autosend")),
            "reason": decision.get("reason", "no_recruitment_context"),
            "reply": None,
        }

    active_session = decision.get("active_session")

    # Owner directive (2026-08-27): a scored, non-terminal candidate (see
    # get_active_session()'s scored branch) is an active post-intake
    # conversation, not a mid-intake one -- route it through the dedicated
    # helper (Hermes/KB reply + post-intake session-state, never field-
    # collection) before any of the intake-specific logic below runs.
    if active_session and active_session.get("post_intake_active"):
        return await _route_post_intake_message(
            phone, text, source=source, message_id=message_id,
            active_session=active_session, contact_context=contact_context,
            history=history, decision=decision,
        )

    # Phase 1B (2026-08-06, Owner-approved allowlist): for an explicitly
    # allowlisted phone only, try the live KB/RAG/LLM reply layer FIRST,
    # even during an active session -- falling through to the unchanged
    # intake_message()-first logic below if it returns nothing (same
    # empty-reply fallback semantics as the non-active-session path
    # further down). This is the one case where the 2026-07-22 rule right
    # below does not apply unconditionally; every non-allowlisted phone
    # (i.e. everyone, while the allowlist is empty) is completely
    # unaffected by this block.
    # Task 13 (2026-08-22): the Task-13 semantic-gate call site in
    # modules.message_router.process_message is SHADOW-ONLY -- it computes
    # and logs a SemanticDecision for observability but never calls
    # route_recruitment_message() or any other handler (see that module's
    # own Task-13 block comment). This function therefore has exactly the
    # same two callers, and the same behavior, it had before Task 13:
    # the legacy recruitment-role branch further down in message_router.
    # The `_is_llm_priority_phone` allowlist below is restored unchanged
    # from its Phase 1B (2026-08-06, Owner-approved) original -- it is
    # always empty by default, so in production this block never runs for
    # any real sender; it is intentionally dead-by-design until the Owner
    # populates the allowlist.
    if active_session and _is_llm_priority_phone(phone):
        priority_reply = None
        from modules.preflight_guard import is_feature_active
        if is_feature_active("hermes_customer_recruitment", default=False):
            # KB gap fix (2026-08-12, Owner-confirmed), targeted revision
            # (2026-08-12): resources/ops/recruitment_source_of_truth.txt
            # is the deliberate single authoritative source for
            # recruitment answers (see its own header). This call site
            # originally passed no kb_context at all (Hermes correctly
            # declined to invent position details rather than
            # hallucinate). A first fix tried the same generic RAG fetch
            # (k=2, min_score=2.0) conversation_layer/recruitment.py's own
            # _safe_rag_context() uses -- live-tested and found it doesn't
            # reliably surface this file for natural phrasings, so it's
            # read whole instead (short file, ~4KB, cached after first
            # read). "Finding E" revision (2026-08-12, Owner-directed):
            # the fee/deposit section is now included only when this
            # message itself looks like a fee question (matches the
            # source file's own "don't volunteer fee info" instruction),
            # and hermes_dispatch.generate_recruitment_reply() itself now
            # fact-checks the reply against this same kb_context before
            # returning it -- see hermes_dispatch.build_recruitment_context
            # / _recruitment_reply_matches_source().
            from modules.hermes_dispatch import (
                generate_recruitment_reply as _hermes_recruitment_reply,
                build_recruitment_context_with_directives as _build_recruitment_context,
            )
            _kb_context = await _build_recruitment_context(text)
            priority_reply = await _hermes_recruitment_reply(
                phone=phone, text=text, source=source,
                kb_context=_kb_context, contact_context=contact_context, history=history,
            )
        if not priority_reply:
            from modules.conversation_layer import generate_recruitment_reply as _llm_priority_reply
            priority_reply = await _llm_priority_reply(
                phone=phone, text=text, source=source,
                contact_context=contact_context, history=history,
            )
        if priority_reply:
            candidate_id = active_session.get("candidate_id")
            if candidate_id is None:
                candidate_id = await _ensure_candidate_for_session(
                    phone,
                    active_session["id"],
                    active_session.get("source_message") or text,
                    active_session.get("source_bridge") or source,
                )
            if candidate_id is not None:
                await _audit_candidate_conversation(
                    candidate_id=candidate_id,
                    step=active_session.get("collection_step") or "name",
                    message=text,
                    source=source,
                    message_id=message_id,
                )
            return {
                "eligible": True,
                "autosend": bool(decision.get("autosend")),
                "reason": "llm_priority_session_continuation",
                "reply": priority_reply,
            }
        # LLM layer produced nothing (empty or safety-restricted) -- fall
        # through to the unchanged intake_message()-first logic below,
        # same as every non-allowlisted phone.

    # 2026-07-22 (Final Instruction Section 4 — recruitment audit found
    # this was FAIL for multi-message continuation): an in-progress funnel
    # must always advance via intake_message() first. Previously
    # intake_message() was only reached as a fallback when
    # generate_recruitment_reply() returned an empty string, which it
    # almost never does (its KB/rule/LLM layer nearly always produces some
    # informational text) -- so collection_step effectively never advanced
    # past the first question in practice, even with an active session.
    if active_session:
        # 2026-08-23 (Answer-First, gated by recruitment_answer_first_enabled
        # — see RECRUITMENT_REPLY_QUALITY_IMPLEMENTATION_PLAN_2026-08-23.md):
        # when the candidate's inbound turn contains an informational
        # question (PURE_QUESTION or MIXED), the LLM/Hermes layer is
        # consulted FIRST so the candidate gets a real answer; intake
        # still advances on the same turn for MIXED turns (the answer is
        # appended with a natural continuation prompt, never replaces the
        # intake-field save). When the flag is OFF, this branch is a
        # no-op and the original intake-first behavior is preserved
        # byte-identically.
        _answer_first_active = False
        _turn_intent = "pure_intake"
        try:
            from app.config import get_settings
            _answer_first_active = bool(get_settings().recruitment_answer_first_enabled)
        except Exception:  # noqa: BLE001 — fail closed to legacy behavior
            _answer_first_active = False

        current_step = active_session.get("collection_step") if isinstance(active_session, dict) else None
        _turn_intent = classify_turn_intent(text, current_step) if _answer_first_active else "pure_intake"

        # Phase C v2 (2026-08-23): in shadow mode, compute V2 decision and
        # emit a structured log line. NO reply-pathing, NO state mutation.
        # The default OFF behavior (recruitment_turn_decision_v2_enabled=False)
        # makes this block a single log line at most.
        try:
            from app.config import get_settings
            _v2_flag = bool(get_settings().recruitment_turn_decision_v2_enabled)
        except Exception:
            _v2_flag = False
        if _v2_flag:
            try:
                _v2_decision = await _maybe_decide_v2(
                    phone=phone,
                    text=text,
                    current_step=current_step,
                    session=active_session if isinstance(active_session, dict) else None,
                    identity={"role": role or "unknown", "confidence": 0, "source": "shadow_only"},
                )
                if _v2_decision is not None:
                    log.info(
                        "[recruitment_decision_v2_shadow] thread=%s msg=%s sender=%s step_before=%s "
                        "semantic_domain=%s turn_type=%s confidence=%.2f accepted=%d rejected=%d "
                        "questions=%d advance=%s answer=%s human_review=%s multi_domain=%s "
                        "reason_codes=%s",
                        (active_session.get('id') if isinstance(active_session, dict) else None),
                        (message_id or 'none'),
                        _hash_phone_shadow(phone),
                        current_step or 'none',
                        _v2_decision.semantic_domain,
                        _v2_decision.turn_type,
                        _v2_decision.confidence,
                        len(_v2_decision.accepted_proposals),
                        len(_v2_decision.rejected_proposals),
                        len(_v2_decision.questions),
                        _v2_decision.should_advance_intake,
                        _v2_decision.should_answer_question,
                        _v2_decision.needs_human_review,
                        _v2_decision.multi_domain_review_required,
                        list(_v2_decision.reason_codes),
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("[recruitment_flow] V2 shadow invocation failed: %s", exc)

        if _answer_first_active and _turn_intent in ("pure_question", "mixed"):
            # Try the informational reply first — KB/Hermes/conversation_layer
            # (in the same precedence order as the non-active-session branch
            # further down). If they produce something, wrap it with the
            # intake continuation prompt and ALSO save the intake field (MIXED
            # only) before returning. If they produce nothing, fall through
            # to the original intake_message()-first logic below.
            _info_reply = None
            _fact_check_pass = True
            from modules.preflight_guard import is_feature_active
            if is_feature_active("hermes_customer_recruitment", default=False):
                from modules.hermes_dispatch import (
                    generate_recruitment_reply as _hermes_recruitment_reply,
                    build_recruitment_context_with_directives as _build_recruitment_context,
                )
                _kb_context = await _build_recruitment_context(text)
                _info_reply = await _hermes_recruitment_reply(
                    phone=phone, text=text, source=source,
                    kb_context=_kb_context, contact_context=contact_context, history=history,
                )
            if not _info_reply:
                from modules.conversation_layer import generate_recruitment_reply
                _info_reply = await generate_recruitment_reply(
                    phone=phone, text=text, source=source,
                    contact_context=contact_context, history=history,
                )
            if _info_reply and _info_reply.strip():
                # 2026-08-23: safe-fee override while Owner resolves
                # source-of-truth contradiction (see SAFE_FEE_PENDING_OWNER_
                # DECISION above). Only when the candidate's turn itself
                # looks like a fee question — never volunteer. 2026-08-27:
                # now goes through _resolve_fee_policy_reply() so an active
                # Owner directive (set via the escalation flow below) is
                # used instead of the static safe-pending text once the
                # conflict is actually resolved, and so a still-unresolved
                # conflict triggers the same cooldown-gated Owner/Admin
                # clarification the post-intake path uses — one shared
                # mechanism, not two.
                _fee_override = await _resolve_fee_policy_reply(text)
                if _fee_override:
                    _info_reply = _fee_override
                    _fact_check_pass = False  # bypassed by owner-decision gate
                # For MIXED turns we still need to advance intake so the
                # candidate's provided value is saved. intake_message()
                # below handles that — we don't short-circuit here.
                if _turn_intent == "pure_question":
                    # Append the natural continuation prompt — current
                    # intake state is preserved by intake_message()
                    # regardless; we only add a soft ask so the candidate
                    # knows the application can still continue.
                    if current_step and current_step in STEP_QUESTIONS:
                        _next_q = STEP_QUESTIONS[current_step]
                        _info_reply = (
                            _info_reply.rstrip()
                            + "\n\nআপনি আবেদন চালিয়ে যেতে চাইলে — "
                            + _next_q.split("\n")[0]
                        )
                    # Structured observability log (no PII — phone hashed).
                    try:
                        from app.config import get_settings as _gs
                        if _gs().recruitment_structured_logging_enabled:
                            import hashlib as _hl
                            log.info(
                                "[recruitment_turn] thread=%s msg=%s sender=%s step_before=%s intent=%s question=yes reply_path=answer_first fact_check=%s step_after=%s",
                                (active_session.get("id") if isinstance(active_session, dict) else None),
                                (message_id or "none"),
                                _hl.sha1(phone.encode("utf-8")).hexdigest()[:10],
                                current_step or "none",
                                _turn_intent,
                                "pass" if _fact_check_pass else "owner_gate",
                                current_step or "none",
                            )
                    except Exception:  # noqa: BLE001 — logging is best-effort
                        pass
                    return {
                        "eligible": True,
                        "autosend": bool(decision.get("autosend")),
                        "reason": "answer_first_question",
                        "reply": _info_reply,
                    }

        intake = await intake_message(
            phone, text, source, role=role, message_id=message_id,
        )
        intake_reply = (intake.get("reply") or "").strip()
        if intake_reply:
            # MIXED turns already had their informational answer prepared
            # above — append it to the intake reply so the candidate gets
            # BOTH the field saved AND their question answered.
            if _answer_first_active and _turn_intent == "mixed" and "_info_reply" in locals() and _info_reply and _info_reply.strip():
                intake_reply = _info_reply.rstrip() + "\n\n" + intake_reply
            # Structured observability log (no PII — phone hashed).
            try:
                from app.config import get_settings as _gs2
                if _gs2().recruitment_structured_logging_enabled:
                    import hashlib as _hl2
                    log.info(
                        "[recruitment_turn] thread=%s msg=%s sender=%s step_before=%s intent=%s question=%s reply_path=%s fact_check=pass step_after=%s",
                        (active_session.get("id") if isinstance(active_session, dict) else None),
                        (message_id or "none"),
                        _hl2.sha1(phone.encode("utf-8")).hexdigest()[:10],
                        current_step or "none",
                        _turn_intent,
                        "yes" if _turn_intent in ("pure_question", "mixed") else "no",
                        "mixed_with_answer" if _turn_intent == "mixed" else "intake_only",
                        current_step or "none",
                    )
            except Exception:  # noqa: BLE001 — logging is best-effort
                pass
            return {
                "eligible": True,
                "autosend": bool(decision.get("autosend")),
                "reason": decision.get("reason", "session_continuation"),
                "reply": intake_reply,
            }
        # intake_message() produced nothing (e.g. an "already_applied"
        # template that happened to be empty) — fall through to the
        # informational reply below as a safety net, same as before.

    # Hermes customer-dispatch Phase 1 (2026-08-12, Minimal Modification
    # Plan item 2): flag-gated substitution, default OFF, same
    # fall-through contract as every other migrated call site. The
    # deterministic intake_message()/STEP_QUESTIONS fallback below is
    # completely unaffected either way — it only runs when `reply` ends
    # up empty from either engine.
    reply = None
    from modules.preflight_guard import is_feature_active
    if is_feature_active("hermes_customer_recruitment", default=False):
        # KB gap fix (2026-08-12, Owner-confirmed), targeted revision
        # (2026-08-12) — see the identical comment at this function's
        # other call site above.
        from modules.hermes_dispatch import (
            generate_recruitment_reply as _hermes_recruitment_reply,
            build_recruitment_context_with_directives as _build_recruitment_context,
        )
        _kb_context = await _build_recruitment_context(text)
        reply = await _hermes_recruitment_reply(
            phone=phone,
            text=text,
            source=source,
            kb_context=_kb_context,
            contact_context=contact_context,
            history=history,
        )
    if not reply:
        from modules.conversation_layer import generate_recruitment_reply
        reply = await generate_recruitment_reply(
            phone=phone,
            text=text,
            source=source,
            contact_context=contact_context,
            history=history,
        )

    # 2026-08-21 (urgent Facebook-boost recovery pass): route_recruitment_
    # message() only ever reaches this point after recruitment_eligibility()
    # already authoritatively decided decision["eligible"] is True (checked
    # at the top of this function) -- but intake_message()'s own "New
    # visitor" gate independently re-derives eligibility from the raw text
    # via is_recruitment_trigger(), a NARROWER check than what
    # recruitment_eligibility() itself now supports (active-session
    # continuation, bridge3's dedicated-channel rule, the context/intent
    # detector layers). Confirmed live: a bridge3 candidate's real, complete
    # conversation (name/age/height/education/position all given) held
    # entirely outside the funnel -- zero lead/session rows -- because no
    # single message ever happened to contain a bare recruitment keyword,
    # even though every one of those messages was correctly judged eligible
    # upstream. Passing this forcing trigger_text makes intake_message()
    # trust the eligibility decision this function's caller already made,
    # instead of re-deriving (and disagreeing with) it from raw text.
    _FORCE_RECRUITMENT_TRIGGER_TEXT = "চাকরি"

    if not reply:
        step = active_session.get("collection_step") if isinstance(active_session, dict) else None
        if step:
            reply = STEP_QUESTIONS.get(step, STEP_QUESTIONS["name"])
        else:
            intake = await intake_message(
                phone, text, source, role=role, message_id=message_id,
                trigger_text=_FORCE_RECRUITMENT_TRIGGER_TEXT,
            )
            reply = (intake.get("reply") or "").strip() or STEP_QUESTIONS["name"]
    elif not active_session and decision.get("autosend"):
        # 2026-07-22: a genuinely new, high-confidence job inquiry (explicit
        # keyword trigger or the entity-layer detector) must actually open
        # a fazle_recruitment_sessions row in the background, not just
        # receive an informational answer -- otherwise there is nothing for
        # the NEXT message to continue via the branch above, and the
        # candidate never enters the real data-collection funnel at all.
        # Best-effort only: never overrides the informational reply already
        # computed above. (2026-08-21: no longer gated a second time by
        # intake_message()'s own INTAKE_KEYWORDS/is_recruitment_trigger()
        # check -- see _FORCE_RECRUITMENT_TRIGGER_TEXT above -- this branch
        # only runs when decision.get("autosend") is already True, i.e.
        # recruitment_eligibility() already made the high-confidence call.)
        try:
            await intake_message(
                phone, text, source, role=role, message_id=message_id,
                trigger_text=_FORCE_RECRUITMENT_TRIGGER_TEXT,
            )
        except Exception as e:
            log.debug(f"[recruit] background session-open failed: {e}")

    return {
        "eligible": True,
        "autosend": bool(decision.get("autosend")),
        "reason": decision.get("reason", "explicit_recruitment"),
        "reply": reply,
    }


# Phase 7 (2026-08-26): Generic no-duplicate-question invariant ────────
# Used by callers (intake_message + the LLM/Hermes reply path) to suppress
# a re-ask when the user already supplied a valid value for this step in
# a prior turn. The LLM path's session-state block (Phase 3) already lists
# "already collected"; this gives the deterministic intake path a
# normalized comparator that handles:
#   - exact match (e.g. "Dhaka" == "Dhaka")
#   - Bangla vs ASCII digit equivalence ("৩২" == "32" == 32)
#   - whitespace + trailing punctuation normalization
# The exemption for "user is correcting" is handled by the caller (the
# existing _locked_already path; the LLM prompt instructs to re-ask on
# correction; this helper returns False when correction markers appear).
#
# Read-only. Never mutates any session row. Falls through to the existing
# behavior if the comparator can't determine a normalized match.
#
# Schema mapping: intake_message's `update` dict (and the existing per-step
# branch) writes to specific columns in fazle_recruitment_sessions. Mirror
# that mapping locally so is_already_answered() reads the correct column
# for each step. This MUST stay in sync with intake_message's update dict
# (lines 895, 906, 923, 931, 949, 957). If you add a step to
# COLLECTION_STEPS + STEP_QUESTIONS, also add it to _STEP_TO_COLUMN.
_STEP_TO_COLUMN: dict[str, str] = {
    "name": "full_name",
    "age": "age",
    "area": "area",
    "job_preference": "job_preference",
    "experience": "experience_years",
    "phone_confirm": "confirmed_phone",
}

_CORRECTION_MARKERS = (
    "ভুল", "সংশোধন", "আগেরটা", "আগের", "আগে", "ওটা না",
    "wrong", "correction", "actually",
)


def _normalize_for_compare(value: str) -> str:
    """Lowercase, collapse whitespace, strip leading/trailing punctuation
    AND internal Bengali/ASCII punctuation. Bangla digits translated to
    ASCII so "৩২" matches "32". Used by the Phase 7 comparator only —
    never reaches candidate-facing text."""
    s = (value or "").strip()
    s = s.translate(_BENGALI_TO_ASCII)
    s = re.sub(r"\s+", " ", s)
    # Strip both leading/trailing AND internal punctuation so "Dhaka,
    # Bangladesh." normalizes to "dhaka bangladesh" for matching.
    s = re.sub(r"[।.,?!:;]", "", s)
    return s.strip().lower()


def is_already_answered(
    session_row: dict,
    step_name: str,
    candidate_value: str,
    *,
    correction_text: str = "",
) -> bool:
    """Return True iff the user has already supplied a valid (normalized-
    match) value for this step AND this turn is NOT a correction.

    Caller-supplied `session_row` is the existing active-session dict
    (the same dict intake_message and the LLM prompt already have
    access to). step_name is one of COLLECTION_STEPS. candidate_value is
    the freshly-parsed inbound value (may be Bangla digits / with
    whitespace / with punctuation).

    correction_text: the raw inbound text (passed in so we can detect
    "ভুল হয়েছে" / "আগেরটা সংশোধন" type corrections that should
    bypass the invariant).

    Returns False (i.e. "not answered yet, ask again") when:
      - session_row is missing/empty.
      - the step's stored column is empty / None.
      - the normalized candidate_value doesn't match the stored value.
      - the raw inbound text contains a correction marker.
    """
    if not session_row or not candidate_value:
        return False
    if correction_text:
        norm_corr = correction_text.strip().lower()
        for marker in _CORRECTION_MARKERS:
            if marker in norm_corr:
                return False
    col = _STEP_TO_COLUMN.get(step_name)
    if not col:
        return False
    stored = session_row.get(col)
    if stored is None:
        return False
    # Numeric fields: compare as integers (handles Bangla vs ASCII digits).
    if col in ("age", "experience_years"):
        try:
            stored_n = int(stored)
            candidate_n = int(re.sub(r"\D", "", _normalize_for_compare(candidate_value)) or "0")
            return stored_n == candidate_n
        except (ValueError, TypeError):
            return False
    # String fields: normalized comparison.
    return _normalize_for_compare(str(stored)) == _normalize_for_compare(str(candidate_value))


# ══════════════════════════════════════════════════════════════════════════════
# Recruitment lifecycle closure (Owner directive, 2026-08-29)
# ══════════════════════════════════════════════════════════════════════════════
#
# GAP 1  candidate -> employee canonical link  (link_candidate_to_employee)
# GAP 2  post-invitation lifecycle events      (record_candidate_lifecycle_event)
# GAP 3  deterministic shortlist + invite batch(get_willing_candidates,
#                                               resolve_invite_batch, send_invite_batch)
# GAP 4  authenticated phone-call admin context(record_admin_phone_call_note)
# G      post-invitation message understanding (classify_post_invitation_signal)
#
# Design rules honoured here:
#   * NO change to the frozen wbom_candidates.post_intake_status vocabulary.
#     Operational lifecycle facts are rows in wbom_candidate_conversations
#     (existing append-only audit table, step column, no CHECK constraint).
#   * Reuse: outbound.enqueue (idempotent), _audit_candidate_conversation,
#     shared.phone. No new engine, table-family, memory system or broadcast
#     platform.
#   * Events are evidence-backed only: a candidate's own message, an
#     authenticated Admin instruction, or an authoritative employee-creation
#     action. Never inferred from silence.

from shared.phone import phone_key9 as _phone_key9  # noqa: E402
try:
    from app.database import fetch_all as _fetch_all  # noqa: E402
except Exception:  # pragma: no cover - defensive
    _fetch_all = None  # type: ignore


# Lifecycle event `step` values written to wbom_candidate_conversations with
# direction='event' (system/admin origin) or 'inbound' (candidate evidence)
# or 'outbound' (invitation we sent). Kept to exactly this set; anything
# outside it is rejected by record_candidate_lifecycle_event().
LIFECYCLE_EVENT_STEPS: frozenset[str] = frozenset({
    "invited",
    "invitation_acknowledged",
    "phone_confirmed",
    "travelling",
    "arrived_office",
    "joined",
    "deferred_after_invitation",
    "did_not_arrive",
    "phone_call_note",          # authenticated Admin offline-context note
    "readiness_confirmed",      # timestamp marker when status -> agreed/joining_ready
})

# Readiness states that qualify a candidate for the "ready to work" shortlist
# and for batch invitation. joining_ready outranks agreed (Owner directive E).
_READY_RANK: dict[str, int] = {"joining_ready": 0, "agreed": 1}
# Softer interest — included only when the caller explicitly asks for
# "interested but not yet agreed" (Owner directive, shortlist section).
_INTERESTED_STATUSES: frozenset[str] = frozenset({
    "awaiting_agreement", "salary_objection", "not_ready_now", "future_contact",
})


async def record_candidate_lifecycle_event(
    candidate_id: int,
    event: str,
    evidence_text: str,
    *,
    source: str = "system",
    message_id: Optional[str] = None,
    direction: str = "event",
    conn: Any = None,
) -> Optional[bool]:
    """Append one lifecycle event to wbom_candidate_conversations.

    event must be in LIFECYCLE_EVENT_STEPS. evidence_text is stored verbatim
    in message_text (the candidate's message, the Admin note, or a short
    system description like 'employee #42 created'). Returns True if a row
    was inserted, False if deduped, None on error (best-effort; never
    raises unless conn is supplied and inside a caller transaction).
    """
    if event not in LIFECYCLE_EVENT_STEPS:
        log.warning("[recruit] rejected lifecycle event=%r candidate=%s (not in LIFECYCLE_EVENT_STEPS)", event, candidate_id)
        return None
    return await _audit_candidate_conversation(
        candidate_id, event, (evidence_text or "")[:2000], source,
        message_id, direction=direction, conn=conn,
    )


async def link_candidate_to_employee(
    employee_mobile: str,
    employee_id: int,
    *,
    employee_name: Optional[str] = None,
    conn: Any = None,
) -> dict:
    """GAP 1 — link the originating candidate record to a newly created
    employee, exactly once, keyed by canonical phone (last-9 match, since
    wbom_candidates.phone and wbom_employees.employee_mobile use
    inconsistent 01.../8801... formats in production).

    Best-effort and non-fatal: the caller (employee-creation path) must not
    fail employee creation if this returns an error status.

    Returns a dict:
      {"linked": bool, "candidate_id": int|None, "reason": str,
       "needs_admin_review": bool}

    Fail-closed rules (Owner directive):
      * only links a candidate whose linked_employee_id IS NULL
      * never silently repoints a candidate already linked to a *different*
        employee -> needs_admin_review=True, linked=False
      * if the phone matches more than one candidate row -> ambiguous,
        needs_admin_review=True, linked=False (should not happen: phone is
        UNIQUE on wbom_candidates, but the last-9 match is not)
      * records a 'joined' lifecycle event on success
    """
    key9 = _phone_key9(employee_mobile or "")
    if not key9:
        return {"linked": False, "candidate_id": None, "reason": "unresolvable_mobile", "needs_admin_review": False}

    _q = conn.fetch if conn is not None else (_fetch_all if _fetch_all else None)
    if _q is None:
        return {"linked": False, "candidate_id": None, "reason": "fetch_all_unavailable", "needs_admin_review": False}

    try:
        rows = await _q(
            """SELECT candidate_id, phone, linked_employee_id, full_name, post_intake_status
                 FROM wbom_candidates
                WHERE right(regexp_replace(phone, '\\D', '', 'g'), 9) = $1""",
            key9,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[recruit] link_candidate_to_employee lookup failed mobile=%s: %s", employee_mobile, e)
        return {"linked": False, "candidate_id": None, "reason": f"lookup_error:{e}", "needs_admin_review": False}

    rows = [dict(r) for r in rows]
    if not rows:
        return {"linked": False, "candidate_id": None, "reason": "no_matching_candidate", "needs_admin_review": False}
    if len(rows) > 1:
        # Already linked to THIS employee on one of them counts as done.
        already = [r for r in rows if r["linked_employee_id"] == employee_id]
        if already:
            return {"linked": True, "candidate_id": int(already[0]["candidate_id"]),
                    "reason": "already_linked_same_employee", "needs_admin_review": False}
        log.warning("[recruit] link_candidate_to_employee AMBIGUOUS mobile=%s matched %d candidates -> admin review",
                    employee_mobile, len(rows))
        return {"linked": False, "candidate_id": None, "reason": "ambiguous_multiple_candidates", "needs_admin_review": True}

    row = rows[0]
    cid = int(row["candidate_id"])
    existing = row["linked_employee_id"]
    if existing is not None:
        if existing == employee_id:
            return {"linked": True, "candidate_id": cid, "reason": "already_linked", "needs_admin_review": False}
        log.warning("[recruit] link_candidate_to_employee CONFLICT candidate=%s already linked to employee=%s, "
                    "refusing to repoint to %s -> admin review", cid, existing, employee_id)
        return {"linked": False, "candidate_id": cid, "reason": "already_linked_different_employee", "needs_admin_review": True}

    _exec = conn.execute if conn is not None else execute
    try:
        await _exec(
            """UPDATE wbom_candidates
                  SET linked_employee_id = $1, updated_at = NOW()
                WHERE candidate_id = $2 AND linked_employee_id IS NULL""",
            employee_id, cid,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[recruit] link_candidate_to_employee update failed candidate=%s employee=%s: %s", cid, employee_id, e)
        return {"linked": False, "candidate_id": cid, "reason": f"update_error:{e}", "needs_admin_review": False}

    evidence = f"employee #{employee_id} created" + (f" ({employee_name})" if employee_name else "")
    await record_candidate_lifecycle_event(cid, "joined", evidence, source="employee_creation", conn=conn)
    log.info("[recruit] linked candidate=%s -> employee=%s (mobile=%s)", cid, employee_id, employee_mobile)
    return {"linked": True, "candidate_id": cid, "reason": "linked", "needs_admin_review": False}


async def get_willing_candidates(
    *,
    include_interested: bool = False,
    limit: Optional[int] = None,
) -> list[dict]:
    """Read-only shortlist for "যারা চাকরি করতে রাজি তাদের দেখাও".

    Default: only candidates whose canonical evidence supports readiness —
    post_intake_status IN ('joining_ready','agreed'), never merely 'scored'.
    include_interested=True additionally returns the softer
    _INTERESTED_STATUSES with honest labels (Owner directive: Admin may ask
    "যারা আগ্রহী কিন্তু এখনো রাজি হয়নি").

    Ordering is deterministic (Owner directive E):
      1. joining_ready before agreed  (then interested, if included)
      2. earliest readiness_confirmed event, else updated_at (oldest first)
      3. candidate_id ASC
    Never uses recruitment score for ordering.

    Returns bounded fields only (no NID, no private payroll data).
    """
    if _fetch_all is None:
        return []
    statuses = ["joining_ready", "agreed"]
    if include_interested:
        statuses += sorted(_INTERESTED_STATUSES)
    try:
        rows = await _fetch_all(
            """
            SELECT c.candidate_id, c.phone, c.full_name, c.job_preference,
                   c.post_intake_status, c.area, c.linked_employee_id,
                   c.updated_at, c.notes,
                   (SELECT min(cc.collected_at)
                      FROM wbom_candidate_conversations cc
                     WHERE cc.candidate_id = c.candidate_id
                       AND cc.step = 'readiness_confirmed') AS ready_since,
                   (SELECT max(cc.collected_at)
                      FROM wbom_candidate_conversations cc
                     WHERE cc.candidate_id = c.candidate_id) AS last_interaction
              FROM wbom_candidates c
             WHERE c.post_intake_status = ANY($1::text[])
               AND c.linked_employee_id IS NULL
            """,
            statuses,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[recruit] get_willing_candidates query failed: %s", e)
        return []

    def _rank(r: dict) -> tuple:
        st = r.get("post_intake_status") or ""
        rank = _READY_RANK.get(st, 2 + sorted(_INTERESTED_STATUSES).index(st) if st in _INTERESTED_STATUSES else 99)
        ts = r.get("ready_since") or r.get("updated_at")
        # normalise to a sortable value
        ts_key = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
        return (rank, ts_key, int(r["candidate_id"]))

    out = sorted((dict(r) for r in rows), key=_rank)
    if limit is not None:
        out = out[: max(0, int(limit))]
    result: list[dict] = []
    for r in out:
        result.append({
            "candidate_id": int(r["candidate_id"]),
            "name": r.get("full_name"),
            "mobile": r.get("phone"),
            "interested_role": r.get("job_preference"),
            "readiness": r.get("post_intake_status"),
            "area": r.get("area"),
            "ready_since": (r.get("ready_since") or r.get("updated_at")),
            "last_interaction": r.get("last_interaction"),
            "unresolved_requirement": _unresolved_requirement(r),
        })
    return result


def _unresolved_requirement(row: dict) -> Optional[str]:
    """Small helper: the single most relevant missing thing for a shortlist
    row, derived from already-collected fields. Not a new schema."""
    if not row.get("full_name"):
        return "name_missing"
    if not row.get("job_preference"):
        return "role_missing"
    if row.get("post_intake_status") == "salary_objection":
        return "salary_objection_open"
    if row.get("post_intake_status") in ("not_ready_now", "future_contact"):
        return "not_ready_yet"
    return None


async def resolve_invite_batch(
    candidate_ids: list[int],
    *,
    limit: Optional[int] = None,
) -> list[dict]:
    """Deterministically resolve + freeze a batch-invitation recipient set.

    Given an explicit list of candidate_ids (from a shortlist the Admin is
    acting on), return an ordered, immutable list of
    {candidate_id, mobile, name, role, readiness, source_bridge}.

    Ordering (Owner directive E / F):
      1. joining_ready before agreed
      2. earliest readiness_confirmed (else updated_at)
      3. candidate_id ASC
    limit (the N in "প্রথম ৫ জন") is applied AFTER ordering. The returned
    list IS the frozen recipient set — callers must not re-resolve or
    expand it. Candidates already linked to an employee, already
    'declined', or with no resolvable phone are dropped (and logged), never
    silently swapped for someone else.
    """
    if not candidate_ids or _fetch_all is None:
        return []
    uniq = sorted({int(c) for c in candidate_ids})
    try:
        rows = await _fetch_all(
            """
            SELECT c.candidate_id, c.phone, c.full_name, c.job_preference,
                   c.post_intake_status, c.linked_employee_id, c.updated_at,
                   (SELECT min(cc.collected_at) FROM wbom_candidate_conversations cc
                     WHERE cc.candidate_id = c.candidate_id AND cc.step = 'readiness_confirmed') AS ready_since,
                   (SELECT cc.source_bridge FROM wbom_candidate_conversations cc
                     WHERE cc.candidate_id = c.candidate_id AND cc.source_bridge IS NOT NULL
                     ORDER BY cc.collected_at DESC LIMIT 1) AS last_bridge,
                   (SELECT s.source_bridge FROM fazle_recruitment_sessions s
                     WHERE s.candidate_id = c.candidate_id ORDER BY s.updated_at DESC LIMIT 1) AS session_bridge
              FROM wbom_candidates c
             WHERE c.candidate_id = ANY($1::bigint[])
            """,
            uniq,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("[recruit] resolve_invite_batch query failed: %s", e)
        return []

    resolved: list[dict] = []
    for r in (dict(x) for x in rows):
        cid = int(r["candidate_id"])
        if r.get("linked_employee_id") is not None:
            log.info("[recruit] resolve_invite_batch drop candidate=%s (already an employee)", cid)
            continue
        if r.get("post_intake_status") == "declined":
            log.info("[recruit] resolve_invite_batch drop candidate=%s (declined)", cid)
            continue
        if not _phone_key9(r.get("phone") or ""):
            log.info("[recruit] resolve_invite_batch drop candidate=%s (no resolvable phone)", cid)
            continue
        st = r.get("post_intake_status") or ""
        rank = _READY_RANK.get(st, 5)
        ts = r.get("ready_since") or r.get("updated_at")
        ts_key = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
        resolved.append({
            "_sort": (rank, ts_key, cid),
            "candidate_id": cid,
            "mobile": r.get("phone"),
            "name": r.get("full_name"),
            "role": r.get("job_preference"),
            "readiness": st or None,
            "source_bridge": r.get("last_bridge") or r.get("session_bridge"),
        })
    resolved.sort(key=lambda d: d["_sort"])
    if limit is not None:
        resolved = resolved[: max(0, int(limit))]
    for d in resolved:
        d.pop("_sort", None)
    return resolved


async def send_invite_batch(
    recipients: list[dict],
    *,
    office_date: str,
    message_body: str,
    authorized_by: str,
    default_source_bridge: str = "bridge3",
    dry_run: bool = False,
    _enqueue: Any = None,
) -> dict:
    """GAP 3 — send a frozen batch of office invitations, individually.

    recipients: the EXACT frozen list from resolve_invite_batch(). This
    function never re-queries or expands it.
    office_date: an already-resolved ABSOLUTE date string (caller resolves
    "আগামী ৫ তারিখ" -> "2026-09-05" before calling). Included verbatim in
    the log and event evidence so the resolved date is visible.
    message_body: the full invitation text (Admin's instructions +
    approved office details). Sent as-is to every recipient — {date} is
    substituted with office_date if present.
    authorized_by: the trusted Admin phone that issued the one
    authorization for this bounded action (Owner directive M — no second
    per-message approval).

    Per-recipient idempotency: idempotency_key =
    f"recruit_invite:{candidate_id}:{office_date}" so a replay of the whole
    batch never re-sends an already-sent recipient, and a partial failure
    retry only sends the ones that failed.

    Returns {"office_date", "authorized_by", "total", "sent":[...],
    "skipped_duplicate":[...], "failed":[...], "dry_run"}.
    """
    if _enqueue is None:
        from modules import outbound as _outbound
        _enqueue = _outbound.enqueue

    body_tmpl = message_body or ""
    office_date = (office_date or "").strip()
    result: dict = {
        "office_date": office_date, "authorized_by": authorized_by,
        "total": len(recipients), "sent": [], "skipped_duplicate": [],
        "failed": [], "dry_run": dry_run,
    }
    for r in recipients:
        cid = r.get("candidate_id")
        mobile = r.get("mobile")
        if not cid or not mobile:
            result["failed"].append({"candidate_id": cid, "reason": "missing_id_or_mobile"})
            continue
        body = body_tmpl.replace("{date}", office_date) if "{date}" in body_tmpl else body_tmpl
        key = f"recruit_invite:{cid}:{office_date}"
        bridge = r.get("source_bridge") or default_source_bridge
        if dry_run:
            result["sent"].append({"candidate_id": cid, "mobile": mobile, "bridge": bridge, "idempotency_key": key, "dry_run": True})
            continue
        try:
            qid = await _enqueue(
                mobile, body, source_bridge=bridge,
                purpose="recruitment_invitation", idempotency_key=key,
                meta={"candidate_id": cid, "office_date": office_date, "authorized_by": authorized_by},
            )
        except Exception as e:  # noqa: BLE001 — isolate per-recipient failure
            log.warning("[recruit] send_invite_batch failed candidate=%s: %s", cid, e)
            result["failed"].append({"candidate_id": cid, "mobile": mobile, "reason": str(e)[:200]})
            continue
        if qid is None:
            result["skipped_duplicate"].append({"candidate_id": cid, "mobile": mobile, "idempotency_key": key})
            continue
        result["sent"].append({"candidate_id": cid, "mobile": mobile, "queue_id": qid, "bridge": bridge})
        try:
            await record_candidate_lifecycle_event(
                int(cid), "invited",
                f"office invitation for {office_date} (authorized_by {authorized_by})",
                source=f"admin:{authorized_by}", direction="outbound",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("[recruit] send_invite_batch event-record failed candidate=%s: %s", cid, e)
    log.info("[recruit] send_invite_batch date=%s auth=%s total=%d sent=%d dup=%d failed=%d dry_run=%s",
             office_date, authorized_by, result["total"], len(result["sent"]),
             len(result["skipped_duplicate"]), len(result["failed"]), dry_run)
    return result


# ── GAP 4: authenticated Admin phone-call / offline context ────────────────

_ADMIN_NOTE_READINESS_MAP: dict[str, str] = {
    # explicit Admin-stated readiness phrase -> post_intake_status
    "join_ready": "joining_ready", "joining_ready": "joining_ready",
    "will come": "joining_ready", "আসবে": "joining_ready",
    "agreed": "agreed", "রাজি": "agreed",
    "declined": "declined", "করবে না": "declined", "না করবে": "declined",
    "later": "future_contact", "পরে": "future_contact",
    "not now": "not_ready_now",
}


async def record_admin_phone_call_note(
    candidate_ref: str,
    note_text: str,
    *,
    admin_phone: str,
    readiness: Optional[str] = None,
) -> dict:
    """GAP 4 — record an authenticated Admin's offline/phone-call fact as
    candidate business context. Hermes never claims it heard a call.

    candidate_ref: a phone (any format) or a bare candidate_id string.
    note_text: the Admin's verbatim note.
    admin_phone: the authenticated trusted-Admin sender (attribution).
    readiness: optional explicit post_intake_status the Admin stated. If
    None, the note is recorded as context only and status is NOT changed.
    If given it MUST be one of POST_INTAKE_STATUSES.

    Returns {"recorded": bool, "candidate_id": int|None,
             "status_updated": str|None, "reason": str}.
    """
    if _fetch_all is None:
        return {"recorded": False, "candidate_id": None, "status_updated": None, "reason": "db_unavailable"}
    cid: Optional[int] = None
    ref = (candidate_ref or "").strip()
    if ref.isdigit() and len(ref) <= 9:
        try:
            row = await fetch_one("SELECT candidate_id FROM wbom_candidates WHERE candidate_id=$1", int(ref))
            if row:
                cid = int(row["candidate_id"])
        except Exception:  # noqa: BLE001
            cid = None
    if cid is None:
        key9 = _phone_key9(ref)
        if key9:
            try:
                rows = await _fetch_all(
                    "SELECT candidate_id FROM wbom_candidates "
                    "WHERE right(regexp_replace(phone, '\\D', '', 'g'), 9) = $1",
                    key9,
                )
                if len(rows) == 1:
                    cid = int(rows[0]["candidate_id"])
                elif len(rows) > 1:
                    return {"recorded": False, "candidate_id": None, "status_updated": None,
                            "reason": "ambiguous_candidate_phone"}
            except Exception as e:  # noqa: BLE001
                return {"recorded": False, "candidate_id": None, "status_updated": None, "reason": f"lookup_error:{e}"}
    if cid is None:
        return {"recorded": False, "candidate_id": None, "status_updated": None, "reason": "candidate_not_found"}

    await record_candidate_lifecycle_event(
        cid, "phone_call_note", note_text or "(no text)",
        source=f"admin:{admin_phone}", direction="event",
    )

    status_updated: Optional[str] = None
    if readiness:
        norm = readiness.strip()
        if norm not in POST_INTAKE_STATUSES:
            # try to map a free phrase
            norm = _ADMIN_NOTE_READINESS_MAP.get(norm.lower(), "")
        if norm in POST_INTAKE_STATUSES:
            try:
                await execute(
                    "UPDATE wbom_candidates SET post_intake_status=$1, updated_at=NOW() WHERE candidate_id=$2",
                    norm, cid,
                )
                status_updated = norm
                if norm in _READY_RANK:
                    await record_candidate_lifecycle_event(
                        cid, "readiness_confirmed", f"{norm} (via admin phone-call note by {admin_phone})",
                        source=f"admin:{admin_phone}", direction="event",
                    )
                log.info("[recruit] admin phone-call note candidate=%s status->%s by=%s", cid, norm, admin_phone)
            except Exception as e:  # noqa: BLE001
                log.warning("[recruit] admin phone-call note status update failed candidate=%s: %s", cid, e)
    return {"recorded": True, "candidate_id": cid, "status_updated": status_updated, "reason": "ok"}


# ── G: post-invitation message understanding ──────────────────────────────

_TRAVELLING_MARKERS: tuple[str, ...] = (
    "রওনা দি", "রওনা হ", "রওনা করে", "গাড়িতে", "বাসে আছি", "পথে আছি",
    "অফিসের পথে", "আসতেছি", "আসছি এখন", "আসতেছি এখন", "চলে আসছি",
    "on the way", "coming now", "on my way", "started for",
)
_ARRIVED_MARKERS: tuple[str, ...] = (
    "অফিসে এসেছি", "অফিসে আসছি এখন", "পৌঁছে গেছি", "পৌঁছেছি", "চলে এসেছি অফিসে",
    "গেটে আছি", "reached office", "at the office", "at your office", "i have arrived",
)
_ACK_MARKERS: tuple[str, ...] = (
    "আচ্ছা আসব", "ঠিক আছে আসব", "ঠিক আছে ৫ তারিখ", "ওকে আসব", "আসব ইনশাআল্লাহ",
    "okay i will come", "ok will come", "i will be there",
)


def classify_post_invitation_signal(text: str) -> Optional[str]:
    """Return a LIFECYCLE_EVENT_STEPS value when an invited candidate's
    message is clearly a post-invitation progression signal, else None.

    Used by the post-intake router: a match means "continue from invitation
    context, do NOT restart intake". Conservative — only fires on explicit
    markers, never on ambiguous text (matches this codebase's
    fail-closed-on-ambiguity posture).
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    if any(m in t for m in _ARRIVED_MARKERS):
        return "arrived_office"
    if any(m in t for m in _TRAVELLING_MARKERS):
        return "travelling"
    if any(m in t for m in _ACK_MARKERS):
        return "invitation_acknowledged"
    return None
