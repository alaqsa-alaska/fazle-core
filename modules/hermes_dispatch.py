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
from typing import Optional

import httpx

from app.config import get_settings
from shared.reply_policy import build_whatsapp_reply_policy, build_whatsapp_recruitment_policy

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


_NUMBER_RE = re.compile(r"[0-9০-৯][0-9০-৯,.\-–+]*")
_PLACE_RE = re.compile(
    r"[\wঀ-৿-]{2,}(?:পাড়া|ঘাট|এলাকা|জেলা|বন্দর)[ঀ-৿]{0,3}"
)
_PLACE_INFLECTION_SUFFIXES = ("য়ে", "য়", "তে", "র", "ে", "এ")


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
    no context means no fact should have been stated at all."""
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
    shape in fazle-mcp."""
    settings = get_settings()
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
    existed."""
    message = build_whatsapp_reply_policy(
        source=source,
        user_message=user_message,
        role=role,
        intent=intent,
        db_context=db_context,
        history=history,
    )
    return await _call_hermes(
        message, sender_phone=sender_phone,
        log_label=f"generate_reply role={role} intent={intent} source={source}",
    )


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
    modules.conversation_layer.generate_recruitment_reply()."""
    message = build_whatsapp_recruitment_policy(
        source=source,
        user_message=text,
        kb_context=kb_context,
        history=history,
        contact_context=contact_context,
    )
    reply = await _call_hermes(
        message, sender_phone=phone,
        log_label=f"generate_recruitment_reply source={source}",
    )
    if reply and not _recruitment_reply_matches_source(reply, kb_context):
        return None
    return reply
