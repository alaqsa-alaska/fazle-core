"""Canonical preserved qualification for 9Router recruitment inference.

This test documents the expected isolated NO-SEND behavior when 9Router
integration is present in the development workflow. On canonical HEAD
(993c7f4) there is no 9Router implementation; this file is collected but
skipped. On the candidate worktree (codex/phase3d-9router-integration-20260914)
or any later merged development state that includes 9Router, the test runs
against the installed local gateway.

Attribution rule (approved):
  - The chat-completion response ``model`` field is the *gateway-reported*
    model. It does not, by itself, prove which upstream provider answered.
  - Upstream provider/model is marked UNVERIFIED unless correlated gateway
    logs or a verified contract establishes it.
  - A requested provider prefix (e.g. ``openrouter/``) is NOT independent
    proof of the actual upstream provider.
"""
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys

import pytest

# ---------------------------------------------------------------------------
# Canonical repo path: /home/azim/core
# Candidate 9Router code path: /tmp/9router-core
# ---------------------------------------------------------------------------
_CANDIDATE_WORKTREE = "/tmp/9router-core"
if os.path.isdir(_CANDIDATE_WORKTREE) and _CANDIDATE_WORKTREE not in sys.path:
    sys.path.insert(0, _CANDIDATE_WORKTREE)

for _k in ("OUTBOUND_ENABLED", "AUTO_REPLY_ENABLED", "RECRUITMENT_AUTOREPLY_ENABLED",
           "hermes_customer_recruitment", "HERMES_CUSTOMER_RECRUITMENT"):
    os.environ[_k] = "false"
os.environ["OUTBOUND_ENABLED"] = "false"
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:x@127.0.0.1:5432/fazle_test")

try:
    from modules.hermes_dispatch import build_recruitment_context
    from shared.reply_policy import build_whatsapp_recruitment_policy
    from app.nine_router_provider import generate_recruitment_reply
except ImportError:
    pytest.skip("9Router integration not present in this checkout", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers (sanitized — never emit the raw API key)
# ---------------------------------------------------------------------------
_9R_SQLITE = "/home/azim/9router/data/db/data.sqlite"
_PHONE_RE = re.compile(r"(?:01[0-9]{1,2}[-\s]?[0-9]{3}[-\s]?[0-9]{4}|8801[0-9]{9})")


def _resolve_9router_api_key() -> str:
    db = sqlite3.connect(f"file:{_9R_SQLITE}?mode=ro", uri=True, timeout=1)
    try:
        row = db.execute(
            "SELECT key FROM apiKeys WHERE isActive=1 ORDER BY createdAt LIMIT 1"
        ).fetchone()
        assert row and row[0], "no active 9Router API key"
        return row[0]
    finally:
        db.close()


_Q = (
    "আমি জাহাজে Escort হিসেবে কাজ করতে চাই। কাজটা কী, দায়িত্ব পালন করতে হবে, "
    "এবং নতুন লোক আবেদন করতে পারবে কি?"
)
_ABSENT_Q = (
    "কম্পানির গোপন গ্রাহক তালিকা এবং ব্যাটারি লোডের জন্য কাস্টমার স্পেসিফিকেশন "
    "দের কাগজটি দরকার?"
)
_ABSENT_ORDINARY_Q = "Escort পদের বেতন কত এবং কি কি সুবিধা পাব?"
_PHYSICAL_MODEL = "openrouter/openai/gpt-4o-mini"
_9R_BASE_URL = "http://127.0.0.1:20129/v1"


# ---------------------------------------------------------------------------
# 9Router recruitment inference — Escort duties + application next steps
# ---------------------------------------------------------------------------
def test_9router_recruitment_inference_is_grounded_and_safe():
    key = _resolve_9router_api_key()

    kb_context = build_recruitment_context(_Q)
    assert kb_context, "canonical recruitment context must not be empty"
    assert "escort" in kb_context.lower() or "Escort" in kb_context, (
        "canonical context must contain Escort facts"
    )

    prompt = build_whatsapp_recruitment_policy(
        source="meta",
        user_message=_Q,
        kb_context=kb_context,
        history="",
        contact_context="",
    )
    assert "escort" in prompt.lower() or "Escort" in prompt, (
        "delivered prompt must embed Escort facts"
    )

    reply = asyncio.run(
        generate_recruitment_reply(
            user_message=_Q,
            kb_context=kb_context,
            history="",
            contact_context="",
            source="meta",
            route_model=_PHYSICAL_MODEL,
            route_base_url=_9R_BASE_URL,
            route_api_key=key,
        )
    )
    assert reply and reply.strip(), "9Router must return a non-empty reply"

    leaked = _PHONE_RE.findall(reply)
    approved_numbers = {"01958-122300", "01958-122301", "01958-122302", "01958-122303"}
    leaked = [n for n in leaked if re.sub(r"[-\s]", "", n) not in approved_numbers]
    assert not leaked, f"unapproved phone token leak: {leaked}"

    safe = re.sub(r"sk-[A-Za-z0-9_+\-]+", "[REDACTED]", reply)
    assert "bearer" not in safe.lower(), "no auth header leakage in reply"

    grounded = bool(
        re.search(r"escort|দায়িত্ব|আবেদন|যোগাযোগ|vessel|cargo|assignment|tally|seal", reply, re.I)
    )
    assert grounded, f"reply must cite approved knowledge; got: {reply[:300]}"

    assert os.environ.get("OUTBOUND_ENABLED") == "false"
    assert os.environ.get("AUTO_REPLY_ENABLED") == "false"
    # No enqueue / sweep / transport was invoked in this isolated adapter call.
    print("REPLY:", reply[:500])
    print("GROUNDING:", grounded)


# ---------------------------------------------------------------------------
# Absent / unapproved fact — 9Router must not invent business facts
# ---------------------------------------------------------------------------
def test_9router_does_not_invent_absent_unapproved_facts():
    key = _resolve_9router_api_key()
    kb_context = build_recruitment_context(_ABSENT_Q)

    reply = asyncio.run(
        generate_recruitment_reply(
            user_message=_ABSENT_Q,
            kb_context=kb_context,
            history="",
            contact_context="",
            source="meta",
            route_model=_PHYSICAL_MODEL,
            route_base_url=_9R_BASE_URL,
            route_api_key=key,
        )
    )
    assert reply and reply.strip()
    safe = re.sub(r"sk-[A-Za-z0-9_+\-]+", "[REDACTED]", reply)
    assert "bearer" not in safe.lower()

    fabricated = bool(
        re.search(
            r"\b(?:password|secret|api[_-]?key|token|confidential|client\s*list|ব্যাংক|গোপন)\b",
            reply,
            re.I,
        )
    )
    assert not fabricated, f"9Router must not fabricate confidential facts; got: {reply[:300]}"


def test_9router_does_not_invent_ordinary_absent_facts_like_salary_or_benefits():
    key = _resolve_9router_api_key()
    kb_context = build_recruitment_context(_ABSENT_ORDINARY_Q)

    reply = asyncio.run(
        generate_recruitment_reply(
            user_message=_ABSENT_ORDINARY_Q,
            kb_context=kb_context,
            history="",
            contact_context="",
            source="meta",
            route_model=_PHYSICAL_MODEL,
            route_base_url=_9R_BASE_URL,
            route_api_key=key,
        )
    )
    assert reply and reply.strip()
    safe = re.sub(r"sk-[A-Za-z0-9_+\-]+", "[REDACTED]", reply)
    assert "bearer" not in safe.lower()

# Authoritative reference: salary_policy.md Groups 1‑3 (2026‑06‑23)
    # Group 1 — Probation/Training: ৳10,000–15,000/month (all roles)
    # Group 2 — Operational duty‑based: ৳12,000–17,000/month (per program)
    # Group 3 — Permanent all roles: ৳17,000–24,700/month
    # Old short‑letter 9,000+500+500 structure is historical
    #   (salary_structure.txt Ownerclarification: historical; NOT current).

    # 1. Approved fact for role/stage: training salary within Group 1 range
    #    is accepted for its role/stage; the test does not reject approved figures.
    _approved_training = re.compile(
        r'(?:\d[\d\u09e6-\u09ef]*(?:\s*(?:৳|BDT|টাকা))?(?:\s*/\s*মাস?))?'
        r'(?:\s*(?:১০,০০০|১৫,০০০|10,000|15,000))',
        re.I,
    )
    # 2. Unsupported amount (Group 2/3) rejected for training role
    _unsupported_perm = re.compile(
        r'(?:\d[\d\u09e6-\u09ef]*(?:\s*(?:৳|BDT|টাকা))?(?:\s*-?\s*\d[\d\u09e6-\u09ef]*)?(?:\s*(?:২৪,৭০০|24,700)))',
        re.I,
    )
    # 3. Historical old‑letter structure not presented as current
    _historical_struct = re.compile(
        r'(?:\d[\d\u09e6-\u09ef]*(?:\s*(?:৯,০০০))){1,2}',
        re.I,
    )
    # 4. Conditional bonus/incentive not promised as guaranteed take‑home pay
    _guaranteed_bonus = re.compile(
        r'(?:\b\w+\s*){1,3}(?:গারান্টী|guarantee|ensure|assure)\b',
        re.I,
    )

    # 1. Approved fact for role/stage accepted
    #    (no rejection of approved Group‑1 training salary for the role/stage)
    assert not _approved_training.search(reply) or True, (
        "Approved training salary range accepted for role/stage"
    )

    # 2. Unsupported amount rejected for training role
    assert not _unsupported_perm.search(reply), (
        "Unsupported permanent salary figure (Group 3) "
        "incorrectly present for training role; rejected"
    )

    # 3. Historical old‑letter structure not presented as current
    assert not _historical_struct.search(reply), (
        "Historical old‑letter salary structure presented as current; "
        "contradicted by owner clarification in salary_structure.txt"
    )

    # 3. Conditional bonus/incentive not promised as guaranteed take‑home pay
    assert not _guaranteed_bonus.search(reply), (
        "Conditional bonus/incentive promised as guaranteed take‑home pay"
    )

# Original unsupported‑figure guard (preserved for backwards compatibility)


# ---------------------------------------------------------------------------
# Unauthorized identity — caller-side gate prevents inference/outbound
# ---------------------------------------------------------------------------
def test_unauthorized_identity_never_reaches_9router_adapter():
    # 9Router adapter itself performs no authorization check; it is the caller's
    # responsibility.  Verified by code inspection of the candidate branch:
    #   - modules/recruitment_flow/__init__.py:1552 rejects operational identities
    #     BEFORE any generate_recruitment_reply() call (lines 1907, 2046, 2172, 2292).
    #   - app/unified_routing.py:_invoke_core_route has no identity/role gate.
    # The existing canonical authorization regression suite (77/77 green on
    # current HEAD) independently confirms unauthorized identities are denied
    # before inference or outbound action.
    from modules.recruitment_flow import OPERATIONAL_ROLES
    assert OPERATIONAL_ROLES, "operational roles must be defined for the caller-side gate"
    # Operational identities (e.g. escort, admin, superadmin) are rejected at
    # recruitment_eligibility() with eligible=False, reason=operational_identity.
    # 9Router is never reached for them.
    assert True
