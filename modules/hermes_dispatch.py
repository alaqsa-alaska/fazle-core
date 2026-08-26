"""
Fazle Core — Hermes customer-dispatch helper (Phase 1, 2026-08-12)

Hands the AI-generated portion of specific WhatsApp reply categories
(Employee informational, Recruitment, Route B, general Tier-15 fallback)
to the real Hermes agent instead of app.llm.generate_reply()'s own
OmniRoute -> Groq -> Ollama -> Kimi chain. See the "Fazle Core <-> Hermes
— Minimal Modification Plan" (2026-08-12) for the full design rationale;
this module is item 5 of that plan.

Phase 1 scope is deliberately narrow: Hermes receives ZERO Fazle MCP
tools for this call shape -- investigation during implementation found
~/.hermes/config.yaml's mcp_servers.fazle-core block is a single static
declaration with no safe per-invocation override, so a smaller tool
subset can't be injected per call the way HERMES_RUNNER_MODEL/--provider
already are. Rather than build a fragile, partially-scoped workaround,
this phase gives Hermes no Fazle tool access at all: every one of the
call sites this module serves already pre-fetches its own context
(identity, history, KB/RAG hits, payroll data when applicable) before
calling app.llm.generate_reply() today -- that same context is handed to
Hermes as prompt text, built through the exact same
shared.reply_policy.build_whatsapp_reply_policy() structure every other
AI reply already uses, so the same guardrails (recruitment scope-lock,
role-based prompting) apply regardless of which model answers. Hermes
reasons over the given context and returns text; it does not query
anything itself. A real tool-using customer-facing Hermes (its own
separately-declared MCP server + toolset, its own scoping review) is a
later, separately-authorized phase — not this one.

Call shape: hermes-runner's /run endpoint, a NEW customer call shape
(caller_scope="customer") distinct from the existing admin-only
bearer-secret path — see hermes-runner/server.py's handling of
HERMES_RUNNER_CUSTOMER_SECRET and force_mode="CUSTOMER" (its own minimal
toolset, no BUILD/RUN possible, never touches the admin mode file at
all). Always stateless: no hermes_session_id is ever sent, so every call
is an independent turn — the existing get_recent_history()/
get_contact_context() calls the caller already makes are the real
continuity mechanism, not Hermes's own session memory.

Fails closed: any missing config, timeout, non-200 response, or empty
reply returns None — callers must fall through to
app.llm.generate_reply() unchanged, exactly like every other
fail-open/fail-closed provider attempt already in this codebase (see
app/llm.py's own tiered try/except shape). Never raises.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.config import get_settings
from shared.reply_policy import build_whatsapp_reply_policy, build_whatsapp_recruitment_policy
from modules.hermes_kernel.data_classes import SemanticDecision

log = logging.getLogger("fazle.hermes_dispatch")

# KB gap fix, part 2 (2026-08-12, Owner-approved targeted fix): the live
# test showed generic RAG top-k (modules.rag.safe_answer, k=2,
# min_score=2.0 -- the same call conversation_layer/recruitment.py's own
# _safe_rag_context() makes) does not reliably surface this file for
# natural phrasings ("পোষ্ট" vs the file's own "পদ"), even though the
# file's own header declares itself the sole authoritative source for
# recruitment facts ("অন্য কোনো KB, database context...থেকে recruitment
# factual answer নেওয়া যাবে না।"). It's short (112 lines, ~4KB, well
# under any prompt budget) so for the Hermes customer-dispatch call
# sites specifically, read it whole rather than rely on BM25 ranking to
# find the right chunk. Same path-resolution pattern as
# modules/recruitment_ai/__init__.py's _RECRUITMENT_SOURCE constant
# (that module is dead/superseded -- not imported from here, just the
# same path shape). Scope: only the two hermes_customer_recruitment call
# sites in modules/recruitment_flow/__init__.py use this; the existing
# conversation_layer/recruitment.py path (today's production path when
# the flag is off, and Hermes's own fallback when it fails) is
# unchanged, out of scope for this fix.
_RECRUITMENT_SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "ops"
    / "recruitment_source_of_truth.txt"
)
_recruitment_source_cache: Optional[str] = None


def _load_recruitment_source_raw() -> str:
    """Read + cache resources/ops/recruitment_source_of_truth.txt whole,
    unmodified. Cached at module level after the first successful read
    (process lifetime -- matches how the real fazle-core.service, a
    long-running process, would only pay this cost once). Fails closed
    to "" on any read error; never raises."""
    global _recruitment_source_cache
    if _recruitment_source_cache is not None:
        return _recruitment_source_cache
    try:
        text = _RECRUITMENT_SOURCE_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("[hermes_dispatch] failed to read recruitment_source_of_truth.txt: %s", exc)
        return ""
    _recruitment_source_cache = text
    return text


def load_recruitment_source_of_truth() -> str:
    """Back-compat alias for the raw, unconditional whole-file read.
    Prefer build_recruitment_context(message) at new call sites -- see its
    docstring for why unconditional inclusion of the fee section is
    undesirable."""
    return _load_recruitment_source_raw()


# Fee-question detection + fact-check guard (2026-08-12, Owner-directed
# targeted revision, "Finding E"): ported from modules/recruitment_ai/
# __init__.py's _looks_like_fee_question()/enforce_recruitment_reply_policy()
# -- that module is dead (zero live callers, per its own header) but this
# specific pair of guardrails was never wired into any live path (neither
# the pre-existing conversation_layer/recruitment.py path nor this
# module's first cut of the Hermes call sites had them). Reimplemented
# here rather than imported, matching this module's own docstring
# boundary and the same "adapt, don't cross-import a dead module" pattern
# conversation_layer/recruitment.py already uses for its own _safe_rag_context().
_FEE_PHRASES = (
    "ভর্তি ফি", "জয়েনিং ফি", "ট্রেনিং ফি", "প্রসেসিং ফি",
    "আবেদন ফি", "ফর্ম ফি", "joining fee", "training fee", "processing fee",
    "application fee", "form fee", "ডিপোজিট", "deposit", "টাকা লাগবে", "টাকা লাগে",
)
_FEE_WORD_RE = re.compile(r"(?:^|[\s,.;:!?।])(?:ফি|fee)(?:$|[\s,.;:!?।])", re.IGNORECASE)
_FEE_SECTION_RE = re.compile(
    r"\n## ভর্তি ফি / ডিপোজিট প্রশ্নের বাধ্যতামূলক উত্তরনীতি\n.*?(?=\n## |\Z)",
    re.S,
)


def _looks_like_fee_question(text: str) -> bool:
    lower = (text or "").lower()
    if any(phrase in lower for phrase in _FEE_PHRASES):
        return True
    return bool(_FEE_WORD_RE.search(lower))


def build_decision_context(decision) -> str:
    """Phase C (2026-08-23) Decision->LLM-context adapter.

    Converts a RecruitmentTurnDecision (modules.recruitment_turn_decision)
    into a short policy block the LLM uses to compose a fact-checked
    reply. The decision itself NEVER gets embedded verbatim into the
    outbound text -- the LLM is told what the candidate meant, what
    questions to answer, and which intake field was accepted.
    """
    lines = []
    lines.append("[decision_v2] turn_type=" + str(getattr(decision, "turn_type", "AMBIGUOUS")))
    lines.append("[decision_v2] semantic_domain=" + str(getattr(decision, "semantic_domain", "ambiguous")))
    lines.append("[decision_v2] confidence=" + str(round(float(getattr(decision, "confidence", 0.0) or 0.0), 3)))
    acc = getattr(decision, "accepted_proposals", []) or []
    rej = getattr(decision, "rejected_proposals", []) or []
    if acc:
        lines.append("[decision_v2] accepted_intake_fields=" + ",".join(
            str(getattr(p, "field", "?")) + "=" + str(getattr(p, "value", "")) for p in acc
        ))
    if rej:
        lines.append("[decision_v2] rejected_intake_fields=" + ",".join(
            str(getattr(p, "field", "?")) for p in rej
        ))
    qs = getattr(decision, "questions", []) or []
    for q in qs:
        topic = str(getattr(q, "topic", "?"))
        must = bool(getattr(q, "must_answer_from_source_of_truth", False))
        lines.append(
            "[decision_v2] question_topic=" + topic
            + " must_answer_from_source_of_truth=" + str(must)
        )
    if getattr(decision, "candidate_correction", False):
        lines.append("[decision_v2] candidate_correction=true (requires confirmation per state_rules)")
    if getattr(decision, "candidate_frustration", False):
        lines.append("[decision_v2] candidate_frustration=true (recap only; no intake advance)")
    if getattr(decision, "references_previous_answer", False):
        lines.append("[decision_v2] references_previous_answer=true")
    if getattr(decision, "manual_reply_already_answered", False):
        lines.append("[decision_v2] manual_reply_already_answered=true (skip duplicate answer)")
    if getattr(decision, "needs_clarification", False):
        lines.append("[decision_v2] needs_clarification=true")
    if getattr(decision, "needs_human_review", False):
        lines.append("[decision_v2] needs_human_review=true")
    if getattr(decision, "multi_domain_review_required", False):
        lines.append("[decision_v2] multi_domain_review_required=true")
    rcs = getattr(decision, "reason_codes", ()) or ()
    if rcs:
        lines.append("[decision_v2] reason_codes=" + ",".join(str(rc) for rc in rcs))
    return "\n".join(lines)


def build_recruitment_context(message: str) -> str:
    """recruitment_source_of_truth.txt content for a specific inbound
    message: the fee/deposit section is included ONLY when the message
    itself looks like a fee question, matching the source file's own
    instruction ("ফি সম্পর্কে প্রশ্ন না করলে নিজে থেকে এই তথ্য বলবে না।" --
    don't volunteer fee figures unless asked). Reads the cached raw file
    once (no repeated disk I/O) and strips the fee section per call via a
    cheap regex when not applicable. Fails closed to "" if the file
    itself failed to load."""
    full_text = _load_recruitment_source_raw()
    if not full_text:
        return ""
    if _looks_like_fee_question(message):
        return full_text
    return _FEE_SECTION_RE.sub("", full_text)


# Phase 3, 2026-08-25 recovery pass ("employee-info grounding gap"): the
# recruitment path above has a dedicated, reliably-read authoritative
# source (recruitment_source_of_truth.txt, wired 2026-08-12). The
# employee-conversation path had no equivalent -- general company-rules/
# leave/attendance/duty questions fell back to generic top-3 BM25 RAG
# over the whole undifferentiated corpus, the same mechanism this
# module's own comments above (2026-08-12) document as unreliable for
# surfacing a single authoritative file's content on natural phrasing.
# master_recruitment_employee_information.txt's অংশ-১ ("কোম্পানি নীতিমালা")
# section is exactly this content (attendance, leave, discipline, salary-
# payment dates/mechanics, resignation notice, provident fund, etc.) --
# extracted from the same file the recruitment path already treats as
# authoritative rather than duplicated into a second file, to avoid the
# multi-document-drift problem this same recovery pass found and fixed
# for the training-salary figures. অংশ-২ (a blank appointment-letter
# template) and the later "Recruitment Knowledge Library" section are
# explicitly candidate-facing, not employee-facing, so they're excluded.
_EMPLOYEE_POLICY_SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "ops"
    / "master_recruitment_employee_information.txt"
)
_employee_policy_cache: Optional[str] = None
_EMPLOYEE_POLICY_SECTION_RE = re.compile(r"অংশ-১[:：].*?(?=অংশ-২[:：])", re.S)


def _load_employee_policy_raw() -> str:
    """Read + cache just the অংশ-১ (company policy) section of
    master_recruitment_employee_information.txt. Mirrors
    _load_recruitment_source_raw()'s whole-file-read-and-cache pattern
    (cached at module level for process lifetime), scoped to this
    section only. Falls back to the whole file (with a warning) if the
    অংশ-১/অংশ-২ markers aren't found, rather than silently returning
    nothing. Fails closed to "" on any read error; never raises."""
    global _employee_policy_cache
    if _employee_policy_cache is not None:
        return _employee_policy_cache
    try:
        full = _EMPLOYEE_POLICY_SOURCE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning(
            "[hermes_dispatch] failed to read master_recruitment_employee_information.txt: %s",
            exc,
        )
        return ""
    match = _EMPLOYEE_POLICY_SECTION_RE.search(full)
    if match:
        text = match.group(0).strip()
    else:
        log.warning(
            "[hermes_dispatch] employee policy section markers (অংশ-১/অংশ-২) not "
            "found in master_recruitment_employee_information.txt — using whole "
            "file as fallback (may include non-employee-facing content)"
        )
        text = full.strip()
    _employee_policy_cache = text
    return text


def build_employee_context(message: str = "") -> str:
    """Authoritative employee company-policy context (attendance, leave,
    discipline, salary-payment dates/mechanics, resignation notice,
    provident fund, etc.) for a specific inbound message. Currently
    returns the full অংশ-১ section unconditionally -- unlike
    build_recruitment_context() there is no known sensitive sub-section
    that should only be included on direct question, but the message
    parameter is kept for signature symmetry and possible future
    topic-scoped filtering. Fails closed to "" if the source file failed
    to load."""
    return _load_employee_policy_raw()


async def build_recruitment_context_with_directives(message: str) -> str:
    """Phase 3, 2026-08-25 recovery pass ("general Owner business-rule
    mutation architecture"). Wraps build_recruitment_context() with every
    currently-active Owner directive unconditionally appended (see
    modules.admin_directives.get_active_directives_context()) -- closes
    the verified gap where a directive written on any subject_key other
    than the 2 pre-existing hardcoded read sites was invisible to every
    live answer path. A new async function, NOT a signature change to
    build_recruitment_context() itself -- that function stays synchronous
    and unchanged, so its 3 existing test call sites
    (test_phase_c_wireins.py, test_recruitment_fee_canonical_text.py)
    keep working exactly as before. Fails safe: if directive lookup
    errors, falls back to the base context alone (get_active_directives_
    context() itself never raises)."""
    base = build_recruitment_context(message)
    from modules.admin_directives import get_active_directives_context
    directives = await get_active_directives_context()
    if not directives:
        return base
    return f"{base}\n\n{directives}" if base else directives


async def build_employee_context_with_directives(message: str = "") -> str:
    """Employee-side counterpart to build_recruitment_context_with_
    directives() -- see its docstring for the full rationale. Wraps
    build_employee_context() (unchanged, synchronous) with active Owner
    directives appended."""
    base = build_employee_context(message)
    from modules.admin_directives import get_active_directives_context
    directives = await get_active_directives_context()
    if not directives:
        return base
    return f"{base}\n\n{directives}" if base else directives


_NUMBER_RE = re.compile(r"[0-9০-৯][0-9০-৯,.\-–+]*")
_PLACE_RE = re.compile(
    r"[\wঀ-৿-]{2,}(?:পাড়া|ঘাট|এলাকা|জেলা|বন্দর)[ঀ-৿]{0,3}"
)
_PLACE_INFLECTION_SUFFIXES = ("য়ে", "য়", "তে", "র", "ে", "এ")

# Bangla→ASCII digit map for number normalization (2026-08-23, see
# RECRUITMENT_REPLY_QUALITY_IMPLEMENTATION_PLAN_2026-08-23.md "Fact-Check
# False Positives"): lets an approved salary of "১০,০০০" in the source
# validate against "10,000" or "১০০০০" in the reply, and vice versa.
_BANGLA_DIGITS = "০১২৩৪৫৬৭৮৯"
_BANGLA_TO_ASCII = str.maketrans(_BANGLA_DIGITS, "0123456789")
_NON_DIGIT_RE = re.compile(r"[^\d]")


def _normalize_number_token(token: str) -> str:
    """Strip punctuation and normalize Bangla↔ASCII digits.

    "01958-122300."  → "01958122300"
    "১০,০০০"         → "১০০০০"    (then ASCII mapping if any Bangla) → "10000"
    "" (after strip) → ""
    """
    if not token:
        return ""
    ascii_form = token.translate(_BANGLA_TO_ASCII)
    digits_only = _NON_DIGIT_RE.sub("", ascii_form)
    return digits_only


def _normalize_place_token(token: str) -> str:
    """Strip inflection suffixes + lowercase for stem comparison.

    "আগ্রপাড়া"     → "আগ্রপাড়া"
    "আগ্রপাড়ায়"   → "আগ্রপাড়া"
    "আগ্রপাড়ার"    → "আগ্রপাড়া"
    """
    if not token:
        return ""
    stripped = token
    for sfx in _PLACE_INFLECTION_SUFFIXES:
        if stripped.endswith(sfx) and len(stripped) > len(sfx):
            stripped = stripped[: -len(sfx)]
            break
    return stripped.lower()


def _place_stem(token: str) -> str:
    for sfx in _PLACE_INFLECTION_SUFFIXES:
        if token.endswith(sfx):
            return token[: -len(sfx)]
    return token


def _recruitment_reply_matches_source(reply: str, kb_context: str) -> bool:
    """True if `reply` introduces no numeric fact or place-name absent
    from `kb_context`. Ported from recruitment_ai's
    enforce_recruitment_reply_policy() (see this module's own header
    comment). A conservative check, not a full faithfulness validator --
    it only catches invented numbers (salary/fee/age figures the model
    fabricated) and invented locations (a hiring claim for a place the
    candidate mentioned but the source never did). Empty kb_context makes
    every number/place "unsupported" by construction, which is correct:
    no context means no fact should have been stated at all.

    2026-08-23 normalization (gated by recruitment_factcheck_normalized
    setting, see app/config.py): when enabled, numbers are compared
    after stripping punctuation and normalizing Bangla↔ASCII digits, so
    equivalent formatting of APPROVED source-of-truth validates. Invented
    numbers/locations are still rejected. Default behavior unchanged
    when flag is off (substring matching as before).
    """
    # Lazy import to avoid circular imports at module load.
    try:
        from app.config import get_settings
        normalized_mode = bool(get_settings().recruitment_factcheck_normalized)
    except Exception:  # noqa: BLE001 — fail closed, no behavioral change
        normalized_mode = False

    if normalized_mode:
        reply_numbers = {
            _normalize_number_token(t) for t in _NUMBER_RE.findall(reply)
        }
        source_numbers = {
            _normalize_number_token(t) for t in _NUMBER_RE.findall(kb_context)
        }
        reply_numbers.discard("")
        if not reply_numbers.issubset(source_numbers):
            log.warning(
                "[hermes_dispatch] recruitment reply blocked -- unsupported numbers: %s",
                sorted(reply_numbers - source_numbers),
            )
            return False
        place_hits = _PLACE_RE.findall(reply)
        kb_lower = kb_context.lower()
        unsupported_places = [
            p for p in place_hits
            if _normalize_place_token(p) not in kb_lower
            and _place_stem(p).lower() not in kb_lower
        ]
        if unsupported_places:
            log.warning(
                "[hermes_dispatch] recruitment reply blocked -- unsupported places: %s",
                unsupported_places,
            )
            return False
        return True

    # Legacy path — preserved unchanged for staged rollout / rollback.
    reply_numbers = set(_NUMBER_RE.findall(reply))
    source_numbers = set(_NUMBER_RE.findall(kb_context))
    if not reply_numbers.issubset(source_numbers):
        log.warning(
            "[hermes_dispatch] recruitment reply blocked -- unsupported numbers: %s",
            sorted(reply_numbers - source_numbers),
        )
        return False
    place_hits = _PLACE_RE.findall(reply)
    kb_lower = kb_context.lower()
    unsupported_places = [p for p in place_hits if _place_stem(p).lower() not in kb_lower]
    if unsupported_places:
        log.warning(
            "[hermes_dispatch] recruitment reply blocked -- unsupported places: %s",
            unsupported_places,
        )
        return False
    return True

# Well under hermes-runner's own 150s ceiling. Unlike the admin WhatsApp
# relay (which replies asynchronously via a background task — see
# modules.admin_directives.router._deliver_hermes_reply), a customer is
# waiting synchronously inside process_message() for this call, so it
# needs to be short enough that a stuck Hermes call still lets the
# caller's own MESSAGE_ROUTE_TIMEOUT fall through to app.llm.generate_reply()
# with time to spare, not just time out the whole message.
_TIMEOUT_SECONDS = 45.0

# NOTE: the "you are answering a customer, not the Admin" system framing
# is intentionally NOT built here — hermes-runner's own run_hermes()
# already owns all persona/system-preamble logic for the admin path
# (SYSTEM_PREAMBLE) and must own the customer-path equivalent too, keyed
# off caller_scope="customer", so there is exactly one place that decides
# "what does Hermes believe its role is" rather than two call sites
# layering possibly-conflicting preambles on top of each other. This
# module sends only the task content (the reply_policy-built prompt).


def _configured():
    """Returns (base_url, secret) or (None, None) if either is unset —
    the one, single "is this feature usable at all" check every public
    function below shares, mirroring fazle_core_client.configured()'s own
    shape in fazle-mcp. Resilient to Settings construction failure
    (e.g. required database_url missing in a unit-test sandbox) — treats
    that exactly the same as "feature not configured" so callers can
    fall through to NO_MATCH or the legacy chain.
    """
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 — fail closed, not raise
        log.debug("[hermes_dispatch] _configured settings unavailable: %s", exc)
        return None, None
    base_url = (getattr(settings, "hermes_runner_url", "") or "").rstrip("/")
    secret = getattr(settings, "hermes_runner_customer_secret", "") or ""
    if not base_url or not secret:
        return None, None
    return base_url, secret


async def _call_hermes(message: str, *, sender_phone: str, log_label: str) -> Optional[str]:
    """Shared core of every hermes-runner call in this module (mirrors
    modules.admin_directives.router._call_hermes_readonly()'s own
    shared-core pattern for the admin relay). Returns None on any
    failure — never raises. Not part of this module's public surface;
    generate_reply()/generate_recruitment_reply() are."""
    base_url, secret = _configured()
    if not base_url or not secret:
        log.debug("[hermes_dispatch] not configured (missing hermes_runner_url or hermes_runner_customer_secret)")
        return None

    payload = {"message": message, "caller_scope": "customer"}
    if sender_phone:
        payload["readonly_key"] = f"customer:{sender_phone}"

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{base_url}/run",
                headers={"Authorization": f"Bearer {secret}"},
                json=payload,
            )
    except httpx.TimeoutException:
        log.warning("[hermes_dispatch] %s timed out after %.1fs", log_label, time.monotonic() - started)
        return None
    except Exception as exc:  # noqa: BLE001 — must never raise into the caller
        log.warning("[hermes_dispatch] %s request failed: %s", log_label, exc)
        return None

    if resp.status_code != 200:
        log.warning("[hermes_dispatch] %s hermes-runner returned %s", log_label, resp.status_code)
        return None

    try:
        data = resp.json()
    except ValueError:
        log.warning("[hermes_dispatch] %s non-JSON response", log_label)
        return None

    reply = (data.get("reply") or "").strip()
    if not reply:
        log.info("[hermes_dispatch] %s empty reply", log_label)
        return None

    log.info(
        "[hermes_dispatch] %s reply_len=%d elapsed=%.1fs",
        log_label, len(reply), time.monotonic() - started,
    )
    return reply


async def _minimax_primary_enabled() -> bool:
    """Task 13 MiniMax-primary promotion (2026-08-22): the one shared flag
    check every MiniMax-first call site in this module uses. Fails closed
    to False (hermes-runner-only, today's behavior) on any error reading
    the flag -- a broken feature-flag lookup must never newly enable a
    code path, only ever fail toward the already-proven-safe default."""
    try:
        from modules.preflight_guard import is_feature_active
        return is_feature_active("minimax_customer_primary", default=False)
    except Exception as exc:  # noqa: BLE001 — must never raise
        log.debug("[hermes_dispatch] minimax_customer_primary flag check failed: %s", exc)
        return False


def _shadow_check_reply_grounding(
    reply: Optional[str], db_context: str, *, role: str, intent: str, source: str,
) -> None:
    """Workstream 3, 2026-08-25 closure pass ("knowledge-grounded
    conversation, no hallucinated facts"): the recruitment path has
    _recruitment_reply_matches_source() as a live-enforced fact-check
    gate; generate_reply() (employee/general) had none at all. Reuses
    that same function -- its actual logic (numbers/places must appear in
    the supplied context) is not recruitment-specific, only its name and
    the flag it reads are.

    Deliberately SHADOW-ONLY here, not enforced: this path's db_context is
    far more heterogeneous than recruitment's curated source-of-truth file
    (ad hoc payroll numbers, generic RAG snippets, contact context), so
    blocking without first measuring the real false-positive rate risks
    degrading real employee replies -- exactly the "activate first,
    investigate after" mistake this closure pass is explicitly trying to
    avoid. Logs only; never changes the returned reply, never raises.
    Promote to enforced once shadow evidence shows an acceptably low
    false-positive rate, same staged pattern recruitment_factcheck_
    normalized itself used before going live."""
    if not reply or not db_context:
        return
    try:
        if not _recruitment_reply_matches_source(reply, db_context):
            log.warning(
                "[hermes_dispatch] generate_reply SHADOW fact-check would have "
                "blocked role=%s intent=%s source=%s (not enforced)",
                role, intent, source,
            )
    except Exception as exc:  # noqa: BLE001 — shadow check must never affect the real reply
        log.debug("[hermes_dispatch] generate_reply shadow fact-check error: %s", exc)


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
    source: str = "unknown",
    *,
    sender_phone: str = "",
) -> Optional[str]:
    """Same required call shape as app.llm.generate_reply() so it's a
    drop-in substitution at every migrated call site (Employee, Route B,
    general Tier-15 fallback) — callers check a preflight_guard feature
    flag, then either await this or fall through to
    app.llm.generate_reply() unchanged. Returns None (never raises) on
    any failure; the caller is responsible for falling through.

    sender_phone (keyword-only, optional): used only to build a
    per-sender readonly_key for hermes-runner's lock-bucket selection
    (_lock_key_for) so concurrent messages from DIFFERENT customers never
    serialize behind each other, while repeated messages from the SAME
    sender still process one at a time, in order. Omitted (empty string)
    falls back to hermes-runner's shared "new" bucket, same as before this
    existed.

    Task 13 MiniMax-primary promotion (2026-08-22, flag-gated by
    `minimax_customer_primary`, default OFF): when the flag is OFF this
    function's behavior is byte-identical to before -- hermes-runner only.
    When ON, MiniMax M3 (app.minimax_provider.generate_reply(), the exact
    same call shape) is tried first; any failure/timeout/empty response
    falls through to the existing hermes-runner call below unchanged."""
    if await _minimax_primary_enabled():
        try:
            from app.minimax_provider import generate_reply as _minimax_generate_reply
            minimax_reply = await _minimax_generate_reply(
                user_message, intent, db_context, history, role, source,
            )
        except Exception as exc:  # noqa: BLE001 — must never raise into the caller
            log.warning("[hermes_dispatch] generate_reply minimax call raised: %s", exc)
            minimax_reply = None
        if minimax_reply:
            log.info("[hermes_dispatch] generate_reply(minimax_primary) role=%s intent=%s source=%s", role, intent, source)
            _shadow_check_reply_grounding(minimax_reply, db_context, role=role, intent=intent, source=source)
            return minimax_reply
        log.info("[hermes_dispatch] generate_reply minimax unavailable/empty — falling back to hermes-runner")

    message = build_whatsapp_reply_policy(
        source=source,
        user_message=user_message,
        role=role,
        intent=intent,
        db_context=db_context,
        history=history,
    )
    reply = await _call_hermes(
        message, sender_phone=sender_phone,
        log_label=f"generate_reply role={role} intent={intent} source={source}",
    )
    _shadow_check_reply_grounding(reply, db_context, role=role, intent=intent, source=source)
    return reply


# Phase 3, 2026-08-26 — Recruitment session-state guard. Build a Bengali
# "## সেশন অবস্থা" block for the recruitment prompt from the candidate's
# current fazle_recruitment_sessions row + the canonical STEP_QUESTIONS
# schema. Iterates STEP_QUESTIONS keys (the schema source) rather than a
# hardcoded field list — new intake fields added to STEP_QUESTIONS in
# recruitment_flow.py automatically get the already-collected treatment.
#
# STEP_TO_COLUMN is the one minimal mapping between the human-readable
# intake step name (STEP_QUESTIONS key) and the actual fazle_recruitment_
# sessions column name. It's no more hardcoded than the existing per-step
# `if step == "name": ... elif step == "age": ...` chain inside
# recruitment_flow.intake_message() — both must agree by construction
# (and intake_message is the writer).
_STEP_TO_COLUMN: dict[str, str] = {
    "name": "full_name",
    "age": "age",
    "area": "area",
    "job_preference": "job_preference",
    "experience": "experience_years",
    "phone_confirm": "confirmed_phone",
}

_STEP_DISPLAY_NAME: dict[str, str] = {
    "name": "নাম",
    "age": "বয়স",
    "area": "এলাকা",
    "job_preference": "পদ-পছন্দ",
    "experience": "অভিজ্ঞতা",
    "phone_confirm": "মোবাইল নম্বর (কনফার্মেশন)",
}


async def _build_recruitment_session_state_block(phone: str) -> str:
    """Build a Bengali 'session state' block describing which intake
    fields are already collected, which are missing, and the current
    funnel step — so the conversational layer can avoid re-asking
    fields the candidate has already supplied.

    Read-only. Never writes to DB. Reads one row from
    fazle_recruitment_sessions via app.database.fetch_one; if the
    session/phone does not exist, returns "" (caller injects nothing).

    Generic over STEP_QUESTIONS keys + _STEP_TO_COLUMN mapping. A new
    intake field added to STEP_QUESTIONS (and the matching column added
    to intake_message's update dict) will be picked up automatically.

    Falls closed: any DB error returns "" and logs a warning.
    """
    try:
        # Imported lazily so this module's import-time cost is unchanged
        # for non-recruitment callers.
        from app.database import fetch_one
        from modules.recruitment_flow import COLLECTION_STEPS, STEP_QUESTIONS
    except Exception as exc:  # noqa: BLE001 — fail closed
        log.warning("[hermes_dispatch] session_state import failed: %s", exc)
        return ""

    try:
        row = await fetch_one(
            """
            SELECT full_name, age, area, job_preference,
                   experience_years, confirmed_phone,
                   collection_step, funnel_stage
            FROM fazle_recruitment_sessions
            WHERE phone = $1
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
            """,
            phone,
        )
    except Exception as exc:  # noqa: BLE001 — fail closed, never raise
        log.warning(
            "[hermes_dispatch] session_state lookup failed phone=%s: %s",
            _safe_phone_label(phone), exc,
        )
        return ""

    if not row:
        return ""

    funnel_stage = row.get("funnel_stage") or "new"
    # Past collection → session is in scored/applied/ignored territory,
    # no intake questions apply. Return empty (no state block) so the
    # prompt is unchanged.
    if funnel_stage not in ("collecting", "new"):
        return ""

    current_step = row.get("collection_step") or "name"
    if current_step == "complete":
        return ""

    # The set of step names recognized by the current prompt schema.
    # Anything not in this set must be filtered out (defensive against
    # schema drift — adding/removing a step from STEP_QUESTIONS must
    # not require a code change here).
    schema_steps = set(STEP_QUESTIONS.keys())

    collected: list[str] = []
    missing: list[str] = []
    for step_name in COLLECTION_STEPS:
        if step_name not in schema_steps:
            continue
        col = _STEP_TO_COLUMN.get(step_name)
        if not col:
            continue
        display = _STEP_DISPLAY_NAME.get(step_name, step_name)
        val = row.get(col)
        # Treat non-empty / non-null as collected. For age (int), falsy 0
        # is not a valid collected value — guard with `is not None` for
        # numeric fields and `val` truthiness for strings.
        if col in ("age", "experience_years"):
            is_collected = val is not None
        else:
            is_collected = bool(val)
        if is_collected:
            # age / experience are integers; render as Bengali digits for
            # consistency with the rest of the prompt.
            if col in ("age", "experience_years") and val is not None:
                from modules.recruitment_flow import _bn_digits
                collected.append(f"{display}={_bn_digits(int(val))}")
            elif col in ("age", "experience_years"):
                # numeric field with None value → not collected; skip.
                missing.append(display)
            else:
                collected.append(f"{display}={val}")
        else:
            missing.append(display)

    if not collected and not missing:
        return ""

    # If current_step is no longer in the schema (drift), fall back to
    # "name" rather than rendering an unknown display label.
    if current_step not in schema_steps:
        current_step = "name"
    current_display = _STEP_DISPLAY_NAME.get(current_step, current_step)
    lines: list[str] = [
        f"- বর্তমান ধা� (current step): {current_display}",
        "- ইতিমধ্যে সংগৃহীত (already collected): "
        + (", ".join(collected) if collected else "—"),
        "- এখনো প্রয়োজন (still missing): "
        + (", ".join(missing) if missing else "—"),
        "",
        "নিয়ম (rule): ইতিমধ্যে সংগৃহীত তথ্য আর জিজ্ঞেস করবে না। শুধুমাত্র "
        "\"এখনো প্রয়োজন\" তালিকায় �াকা তথ্য জিজ্ঞেস করবে। প্রার্থী যদি "
        "ইতিমধ্যে দেওয়া কোনো তথ্য সংশোধন করতে বলে, সেটি সংশোধন হিসেবে "
        "গ্রহণ করবে (এটি পুনঃপ্রশ্ন নয়)। শুধু অস্পষ্ট বা অবৈধ মান হলে "
        "পুনঃপ্রশ্ন করবে।",
    ]
    return "\n".join(lines)


def _safe_phone_label(phone: str) -> str:
    """Hashed short label for log lines (never raw phone in logs)."""
    try:
        import hashlib
        return hashlib.sha1((phone or "").encode("utf-8")).hexdigest()[:10]
    except Exception:  # noqa: BLE001
        return "?"


async def generate_recruitment_reply(
    *,
    phone: str,
    text: str,
    source: str,
    kb_context: str = "",
    contact_context: str = "",
    history: str = "",
) -> Optional[str]:
    """Same required call shape as
    modules.conversation_layer.generate_recruitment_reply() so it's a
    drop-in substitution at modules.recruitment_flow's two call sites.
    Uses shared.reply_policy.build_whatsapp_recruitment_policy() — the
    recruitment-specific scope-lock prompt (candidate funnel, "don't
    answer non-recruitment questions") — rather than the general-purpose
    policy generate_reply() above uses, so recruitment's own guardrails
    apply regardless of which model answers, exactly like every other
    reply path in this codebase. kb_context is the caller's
    responsibility to supply (e.g. via build_recruitment_context(text)) —
    this module does not fetch it itself, consistent with the rest of
    Phase 1 never querying anything on its own. Returns None (never
    raises) on any failure OR when the reply fails the post-generation
    fact-check against kb_context (Owner-directed "Finding E" guardrail,
    2026-08-12 — see _recruitment_reply_matches_source()); either way the
    caller is responsible for falling through to
    modules.conversation_layer.generate_recruitment_reply().

    Task 13 MiniMax-primary promotion (2026-08-22, flag-gated by
    `minimax_customer_primary`, default OFF): when OFF this function's
    behavior is byte-identical to before -- hermes-runner only, one
    fact-check. When ON, MiniMax M3 (app.minimax_provider.
    generate_recruitment_reply(), the exact same call shape) is tried
    first and is subject to the SAME fact-check against kb_context as the
    hermes-runner leg -- a MiniMax reply that fails the fact-check is
    treated exactly like any other unusable MiniMax response and falls
    through to hermes-runner, never returned bypassing the check."""
    if await _minimax_primary_enabled():
        try:
            from app.minimax_provider import generate_recruitment_reply as _minimax_recruitment_reply
            minimax_reply = await _minimax_recruitment_reply(
                user_message=text,
                kb_context=kb_context,
                history=history,
                contact_context=contact_context,
                source=source,
            )
        except Exception as exc:  # noqa: BLE001 — must never raise into the caller
            log.warning("[hermes_dispatch] generate_recruitment_reply minimax call raised: %s", exc)
            minimax_reply = None
        if minimax_reply and _recruitment_reply_matches_source(minimax_reply, kb_context):
            log.info("[hermes_dispatch] generate_recruitment_reply(minimax_primary) source=%s", source)
            return minimax_reply
        if minimax_reply:
            log.warning("[hermes_dispatch] generate_recruitment_reply minimax reply failed fact-check — falling back to hermes-runner")
        else:
            log.info("[hermes_dispatch] generate_recruitment_reply minimax unavailable/empty — falling back to hermes-runner")

    message = build_whatsapp_recruitment_policy(
        source=source,
        user_message=text,
        kb_context=kb_context,
        history=history,
        contact_context=contact_context,
        session_state=await _build_recruitment_session_state_block(phone),
    )
    reply = await _call_hermes(
        message, sender_phone=phone,
        log_label=f"generate_recruitment_reply source={source}",
    )
    if reply and not _recruitment_reply_matches_source(reply, kb_context):
        return None
    return reply


async def deliver_authoritative_recruitment_reply(
    *,
    phone: str,
    text: str,
    source: str,
    contact_context: str = "",
    history: str = "",
) -> Optional[str]:
    """Task 13 authoritative recruitment delivery (2026-08-22, flag-gated
    by `task13_recruitment_authoritative` [split same-day from the original
    single `task13_semantic_authoritative` flag], message_router-only call
    site — the flag check itself lives in message_router's
    _task13_authoritative_dispatch(), not in this function).

    Bypasses ONLY modules.recruitment_flow.recruitment_eligibility()'s
    OPERATIONAL_ROLES sender-role restriction, for a sender the semantic
    gate has confidently classified as recruitment_conversation (e.g. a
    known employee asking about a family member's job — Example A) — i.e.
    a sender recruitment_eligibility() would categorically reject before
    ever considering the message content. Does NOT bypass fact-checking,
    confidentiality, or source-of-truth machinery: reuses
    build_recruitment_context() + generate_recruitment_reply() exactly as
    the legacy Phase-1B allowlisted call site does, including
    _recruitment_reply_matches_source() (enforced inside
    generate_recruitment_reply() itself, MiniMax leg and hermes-runner leg
    alike).

    Deliberately does NOT call recruitment_eligibility() or
    route_recruitment_message() — kept fully separate from both so their
    existing behavior, for their two pre-existing callers, is provably
    unchanged by this function's existence. Returns None (never raises)
    on any failure or empty reply — the caller (message_router) is
    responsible for falling through to its own unchanged §7-15 dispatch
    chain."""
    kb_context = build_recruitment_context(text)
    try:
        reply = await generate_recruitment_reply(
            phone=phone,
            text=text,
            source=source,
            kb_context=kb_context,
            contact_context=contact_context,
            history=history,
        )
    except Exception as exc:  # noqa: BLE001 — must never raise into the router
        log.warning("[hermes_dispatch] authoritative recruitment delivery failed: %s", exc)
        return None
    return reply or None


# ── WS1 / Phase C real-LLM bridge (2026-08-25, Owner-Authority recovery
# pass) ─────────────────────────────────────────────────────────────────────
# recruitment_turn_decision.decide() needs a real DecisionLLMCall closure
# when the recruitment_turn_decision_v2_real_llm flag is on. To honor the
# directive's "do not create a parallel recruitment-decision AI stack that
# bypasses Hermes" rule, this module exposes make_decision_llm_call() —
# which routes through the SAME _call_hermes() call shape every other
# customer-facing Hermes call uses (caller_scope="customer", bearer
# HERMES_RUNNER_CUSTOMER_SECRET). Hermes (via hermes-runner) in turn
# forwards the structured decision prompt to the OmniRoute gateway
# (http://127.0.0.1:20128/v1), which is the EXACT same endpoint
# app.omniroute_provider talks to in fallback mode and the EXACT same
# endpoint the existing _SEMANTIC_GATE_POLICY semantic_gate() send uses.
# The model under the hood is the same. Failure modes (None-return
# timeout/config/error, fail-closed) match every other Hermes call site
# here. NEVER raises.
async def call_decision_llm(
    *,
    system_prompt: str,
    user_payload: str,
    sender_phone: str = "",
) -> Optional[str]:
    """Hermes-routed structured decision call (WS1, 2026-08-25).

    Returns Hermes's raw response text or None on any failure. The
    caller (modules.recruitment_turn_decision.decide) owns JSON parsing
    and the fail-closed AMBIGUOUS fallback. Bounded by the same
    _TIMEOUT_SECONDS as every other hermes-runner customer call.

    The system_prompt is the caller's responsibility (it is the
    DecisionLLMCall signature's `system` arg, sourced from
    recruitment_turn_decision.prompts.DECISION_SYSTEM_PROMPT). The
    user_payload is the multi-section structured payload the same
    prompts module builds. Together they form the full instruction.

    NOTE on chat-shape vs prompt-shape: hermes-runner's own prompt
    stacking in run_hermes() keys off caller_scope="customer" and adds
    the customer-facing system preamble upstream of whatever message
    string is supplied via `/run`'s `message` field, so
    caller-supplied 'system_prompt' must be passed THROUGH (not
    prepended), to avoid conflicting preamble layers. We pass a
    structured labeled payload (the same shape semantic_gate() uses
    with its [_SEMANTIC_GATE_POLICY] + "EVIDENCE (JSON):" + json).
    DecisionCaller handles its own prompt layering upstream of this
    function."""
    base_url, secret = _configured()
    if not base_url or not secret:
        log.debug("[hermes_dispatch] decision_llm not configured (missing hermes_runner_url or hermes_runner_customer_secret)")
        return None
    # Compose a single message string the same way semantic_gate()
    # composes its evidence payload -- the hermes-runner /run endpoint
    # takes ONE 'message' field; system instruction is folded into that
    # field with explicit separators so the LLM sees a labeled system-
    # instruction then a labeled evidence block.
    composed = (
        "[SYSTEM_INSTRUCTION]\n"
        f"{system_prompt}\n"
        "[/SYSTEM_INSTRUCTION]\n\n"
        "[TURN_PAYLOAD]\n"
        f"{user_payload}\n"
        "[/TURN_PAYLOAD]"
    )
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{base_url}/run",
                headers={"Authorization": f"Bearer {secret}"},
                json={
                    "message": composed,
                    "caller_scope": "customer",
                    "readonly_key": (f"customer:decision:{sender_phone}"
                                     if sender_phone else "customer:decision"),
                    # Decision path is structured classification only --
                    # caller-supplied persona/system is already in the
                    # composed message, so tell hermes-runner not to
                    # prepend any additional user-facing preamble.
                    "decision_only": True,
                },
            )
    except httpx.TimeoutException:
        log.warning("[hermes_dispatch] decision_llm timed out after %.1fs", time.monotonic() - started)
        return None
    except Exception as exc:  # noqa: BLE001 -- never raise into decide()
        log.warning("[hermes_dispatch] decision_llm request failed: %s", exc)
        return None

    if resp.status_code != 200:
        log.warning("[hermes_dispatch] decision_llm hermes-runner returned %s", resp.status_code)
        return None
    try:
        data = resp.json()
    except ValueError:
        log.warning("[hermes_dispatch] decision_llm non-JSON response")
        return None
    reply = (data.get("reply") or "").strip()
    if not reply:
        log.info("[hermes_dispatch] decision_llm empty reply")
        return None
    log.info(
        "[hermes_dispatch] decision_llm reply_len=%d elapsed=%.1fs",
        len(reply), time.monotonic() - started,
    )
    return reply


__all__ = [
    "SemanticDecision",
    "call_decision_llm",
]


# ---------------------------------------------------------------------------
# Semantic gate (Task 13, 2026-08-22 — Recruitment + Employee conversational
# semantic routing). Identity / keywords / recruitment_detector are EVIDENCE
# inside the gate input; they do NOT select the domain on their own. Hermes
# produces a structured SemanticDecision used by message_router to dispatch
# to the Recruitment handler, the Employee handler (or identity
# clarification when unresolved), or NO_MATCH (fall through to the existing
# deterministic router unchanged).
# ---------------------------------------------------------------------------

_SEMANTIC_GATE_POLICY = (
    "You are a domain classifier for an inbound WhatsApp message.\n"
    "Reply with EXACTLY ONE LINE of JSON and nothing else, in this shape:\n"
    '{"domain":"recruitment_conversation"|"employee_conversation"|"no_match",'
    '"subject":"...","intent":"...","entities":{},"requested_action":null|"...",'
    '"confidence":0.0-1.0,"rationale":"..."}\n\n'
    "RULES:\n"
    "- 'recruitment_conversation' ONLY when the message is a job-applicant /\n"
    "  vacancy / salary-for-a-position / interview / joining-process question,\n"
    "  EVEN if the sender is a known employee asking about someone else.\n"
    "- 'employee_conversation' ONLY when the message is an existing-employee /\n"
    "  salary-payment / advance / release / duty-status / leave question about\n"
    "  the SENDER THEMSELVES. NEVER if identity is unresolved and the subject\n"
    "  is salary/payment/advance/release — return no_match with a clarification\n"
    "  rationale so the system can ask for identity first.\n"
    "- 'no_match' when the message is an operations/location/admin/accountant\n"
    "  question, an escort client order, an attendance/slip submission, or any\n"
    "  other business-action message that should go to the deterministic router.\n"
    "- Confidence MUST reflect how certain you are about the DOMAIN choice,\n"
    "  not about the answer.\n"
    "- If identity is unresolved AND the subject implies private employee data,\n"
    "  prefer no_match with rationale='identity_unresolved'."
)


def _try_parse_semantic_decision(raw: str) -> Optional[SemanticDecision]:
    """Strict JSON parse of the gate's one-line JSON reply. Returns None
    (not a decision) on ANY parse/validation failure -- empty response,
    malformed JSON, non-object, or invalid domain -- so a caller trying
    multiple providers (Task 13 MiniMax-primary promotion, 2026-08-22) can
    distinguish "this provider's response was unusable, try the next one"
    from "this provider legitimately decided no_match", which
    _parse_semantic_decision() below (used only for the final,
    no-more-fallback hermes-runner leg) deliberately cannot: it must always
    return SOME decision, never None, because there's nothing left to fall
    back to after it."""
    if not raw:
        return None
    text = raw.strip()
    # Strip ``` fences if a model adds them.
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    text = text.strip()
    try:
        import json
        obj = json.loads(text)
    except Exception as exc:  # noqa: BLE001 — must never raise
        log.warning("[hermes_dispatch] semantic decision JSON parse failed: %s; raw=%r", exc, raw[:200])
        return None
    if not isinstance(obj, dict):
        return None
    domain = obj.get("domain")
    if domain not in ("recruitment_conversation", "employee_conversation", "no_match"):
        return None
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return SemanticDecision(
        domain=domain,
        subject=str(obj.get("subject") or ""),
        intent=str(obj.get("intent") or ""),
        entities=dict(obj.get("entities") or {}) if isinstance(obj.get("entities"), dict) else {},
        requested_action=obj.get("requested_action") if obj.get("requested_action") else None,
        confidence=confidence,
        rationale=str(obj.get("rationale") or ""),
    )


def _parse_semantic_decision(raw: str) -> SemanticDecision:
    """Best-effort JSON parse of the gate's one-line JSON reply. Fails closed
    to a `no_match` decision with zero confidence on any parse/validation error
    so the caller falls through to the existing deterministic router.

    Thin wrapper around _try_parse_semantic_decision() that substitutes the
    specific historical failure-reason rationale strings
    (empty_response/json_parse_failed/non_object_response/invalid_domain)
    existing tests already assert on -- see that function for the actual
    parse logic, now shared with the MiniMax-primary path."""
    if not raw:
        return SemanticDecision(domain="no_match", confidence=0.0, rationale="empty_response")
    text = raw.strip()
    stripped_for_json = text
    if stripped_for_json.startswith("```"):
        stripped_for_json = stripped_for_json.strip("`")
        if "\n" in stripped_for_json:
            stripped_for_json = stripped_for_json.split("\n", 1)[1]
        if stripped_for_json.endswith("```"):
            stripped_for_json = stripped_for_json[:-3]
    stripped_for_json = stripped_for_json.strip()
    try:
        import json
        obj = json.loads(stripped_for_json)
    except Exception:
        return SemanticDecision(domain="no_match", confidence=0.0, rationale="json_parse_failed")
    if not isinstance(obj, dict):
        return SemanticDecision(domain="no_match", confidence=0.0, rationale="non_object_response")
    if obj.get("domain") not in ("recruitment_conversation", "employee_conversation", "no_match"):
        return SemanticDecision(domain="no_match", confidence=0.0, rationale="invalid_domain")
    decision = _try_parse_semantic_decision(raw)
    # decision cannot be None here -- every failure mode above already
    # returned early with the matching legacy rationale string.
    return decision  # type: ignore[return-value]


async def _semantic_gate_via_minimax(payload_text: str) -> Optional[SemanticDecision]:
    """Task 13 MiniMax-primary promotion (2026-08-22). Attempts the
    classifier prompt against MiniMax M3 via
    app.minimax_provider.generate_raw_completion() -- the same restricted
    evidence payload the hermes-runner leg builds, no tool access, no
    admin/system prompt, no privileged session (the MiniMax client is a
    plain OpenAI-compatible completion call; it was never given tools to
    begin with). Returns None -- not a decision -- on ANY failure
    (exception, empty response, invalid/malformed JSON, invalid domain) so
    the caller falls back to the existing hermes-runner path exactly as it
    worked before this promotion. Never raises."""
    try:
        from app.minimax_provider import generate_raw_completion as _minimax_raw
    except Exception as exc:  # noqa: BLE001 — must never raise into the gate
        log.warning("[hermes_dispatch] semantic_gate minimax import failed: %s", exc)
        return None
    try:
        raw = await _minimax_raw(payload_text, max_tokens=300, temperature=0.0)
    except Exception as exc:  # noqa: BLE001 — must never raise into the gate
        log.warning("[hermes_dispatch] semantic_gate minimax call raised: %s", exc)
        return None
    if not raw:
        log.info("[hermes_dispatch] semantic_gate minimax empty response; falling back")
        return None
    decision = _try_parse_semantic_decision(raw)
    if decision is None:
        log.warning("[hermes_dispatch] semantic_gate minimax invalid response; falling back raw=%r", raw[:200])
    return decision


async def semantic_gate(input_context: Dict[str, Any]) -> SemanticDecision:
    """Hermes-driven semantic decision for the Recruitment/Employee
    conversational target. The gate is OPT-IN: callers must pre-flight
    that the message was NOT conclusively claimed by a high-confidence
    business-action consumer (admin/escort/cash/attendance/slip/etc.).

    Returns a SemanticDecision. Never raises. On any hermes-runner failure,
    missing configuration, low confidence, or invalid response, returns
    `no_match` so message_router falls through to its existing dispatch
    chain unchanged.

    Task 13 MiniMax-primary promotion (2026-08-22, flag-gated by
    `minimax_customer_primary`, default OFF): when the flag is OFF, this
    function's behavior is UNCHANGED from before this promotion -- MiniMax
    is never attempted, only the existing hermes-runner path runs, in the
    same order, with the same payload. When the flag is ON, MiniMax M3 is
    tried FIRST with the identical evidence payload; only on MiniMax
    failure/timeout/rate-limit/invalid-JSON/empty-response does this fall
    back to the existing hermes-runner call below -- never both, and a
    total failure of both still returns SemanticDecision(no_match) exactly
    as it always has (fail-open, never raise)."""
    minimax_primary = False
    try:
        from modules.preflight_guard import is_feature_active
        minimax_primary = is_feature_active("minimax_customer_primary", default=False)
    except Exception as exc:  # noqa: BLE001 — flag check must never block the gate
        log.debug("[hermes_dispatch] semantic_gate minimax flag check failed: %s", exc)
        minimax_primary = False

    evidence = {
        "text": (input_context or {}).get("text", ""),
        "identity_reference": (input_context or {}).get("identity_reference", {}),
        "intent_hint": (input_context or {}).get("intent_hint", ""),
        "recruitment_detector_result": (input_context or {}).get("recruitment_detector_result", {}),
        "keyword_hints": (input_context or {}).get("keyword_hints", []),
        "history": (input_context or {}).get("history", ""),
        "db_ctx": (input_context or {}).get("db_ctx", ""),
        "kb_ctx": (input_context or {}).get("kb_ctx", ""),
        "business_data": (input_context or {}).get("business_data", {}),
        "source": (input_context or {}).get("source", ""),
        "language": (input_context or {}).get("language", ""),
    }
    import json as _json
    payload_text = (
        _SEMANTIC_GATE_POLICY
        + "\n\nEVIDENCE (JSON):\n" + _json.dumps(evidence, ensure_ascii=False, default=str)
    )
    sender_phone = (input_context or {}).get("sender_phone", "") or ""

    if minimax_primary:
        decision = await _semantic_gate_via_minimax(payload_text)
        if decision is not None:
            decision.identity_reference = evidence["identity_reference"]
            log.info(
                "[hermes_dispatch] semantic_gate(minimax_primary) domain=%s confidence=%.2f rationale=%s",
                decision.domain, decision.confidence, decision.rationale,
            )
            return decision
        log.info("[hermes_dispatch] semantic_gate minimax unavailable/invalid — falling back to hermes-runner")

    base_url, secret = _configured()
    if not base_url or not secret:
        log.debug("[hermes_dispatch] semantic_gate not configured; returning no_match")
        return SemanticDecision(domain="no_match", confidence=0.0, rationale="not_configured")

    raw_reply = await _call_hermes(
        payload_text,
        sender_phone=sender_phone,
        log_label=f"semantic_gate source={evidence['source']}",
    )
    decision = _parse_semantic_decision(raw_reply or "")
    decision.identity_reference = evidence["identity_reference"]
    log.info(
        "[hermes_dispatch] semantic_gate domain=%s confidence=%.2f rationale=%s",
        decision.domain, decision.confidence, decision.rationale,
    )
    return decision


# Fazle-Core business-rule validation layer (Task 13). Identity, keyword
# hints, and recruitment_detector output are EVIDENCE that has already been
# consumed by semantic_gate; this layer enforces the policy that no single
# evidence source independently selects the domain, and that protected
# business intents are routed to NO_MATCH so the deterministic handlers
# continue to own them.
_PROTECTED_ROLES = ("admin", "accountant", "escort_client")
_PROTECTED_INTENT_HINTS = {
    "escort_order",
    "payment",
    "attendance",
    "slip_submission",
    "admin_command",
    "accountant_command",
    "cash_transaction",
}


def validate_semantic_decision(
    decision: SemanticDecision,
    *,
    identity_reference: Dict[str, Any],
    intent_hint: str = "",
) -> SemanticDecision:
    """Apply Fazle-Core policy to the gate's SemanticDecision. Returns a
    possibly-mutated decision (typically downgrading to `no_match`) without
    raising. The caller (message_router) treats the returned value as the
    routing authority."""
    role = str((identity_reference or {}).get("role") or "")
    if role in _PROTECTED_ROLES:
        # Admin / Accountant / Escort Client: their traffic is owned by
        # business-action handlers, not the conversational semantic layer.
        return SemanticDecision(
            domain="no_match",
            confidence=decision.confidence,
            identity_reference=identity_reference,
            rationale=f"protected_role:{role}",
        )
    if intent_hint in _PROTECTED_INTENT_HINTS:
        return SemanticDecision(
            domain="no_match",
            confidence=decision.confidence,
            identity_reference=identity_reference,
            rationale=f"protected_intent:{intent_hint}",
        )
    if decision.confidence < 0.6:
        return SemanticDecision(
            domain="no_match",
            confidence=decision.confidence,
            identity_reference=identity_reference,
            rationale="low_confidence",
        )
    if decision.domain == "employee_conversation":
        linked_employee_id = (identity_reference or {}).get("employee_id") or (identity_reference or {}).get("linked_employee_id")
        is_unresolved = not linked_employee_id and role not in ("employee", "admin", "accountant")
        # When identity is unresolved but Hermes correctly inferred an
        # employee-context question, return no_match with rationale
        # 'identity_unresolved' so message_router routes to the existing
        # employee identity clarification (NOT to recruitment). The
        # clarification path asks for name/known-mobile/duty before any
        # financial data is disclosed.
        if is_unresolved:
            return SemanticDecision(
                domain="no_match",
                confidence=decision.confidence,
                identity_reference=identity_reference,
                requested_action="identity_clarification",
                rationale="identity_unresolved",
            )
    return decision
