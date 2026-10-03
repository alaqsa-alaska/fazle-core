"""Scoped Admin authorization for Hermes-proposed drafts (2026-09-30).

Owner decision: Bridge2 -> Bridge1 is the trusted Admin <-> Hermes control
conversation. Hermes acts on the Admin's requests; it must not supply the
authority for its own actions. These tests cover the enforcement half, in
fazle-core, where the authority actually lives.

The rules being pinned:

* only drafts Hermes itself proposed are grantable;
* a grant is bound to the Admin's canonical identity AND the conversation;
* a grant expires, and is single-use;
* approval still reads recipient and content from the stored row, so a grant
  can never authorize a substituted recipient or text;
* drafts Hermes did not propose are untouched, so every human/dashboard flow
  keeps working.
"""

import pytest

from modules.admin_hermes_action_grant import (
    DEFAULT_GRANT_TTL_SECONDS,
    DRAFT_CONVERSATION_KEY,
    GRANT_KEY,
    HERMES_INTENT,
    authorize_pending_hermes_drafts,
    build_grant,
    consume_draft_grant,
    is_hermes_proposed,
    verify_draft_grant,
)


ADMIN_CONVERSATION = "hermes_relay:bridge2:8801"


def _hermes_draft(meta=None, **kw):
    d = {"intent": HERMES_INTENT, "meta": meta or {}}
    d.update(kw)
    return d


def _grant_for(conversation=ADMIN_CONVERSATION, **over):
    g = build_grant(identity_id="1", conversation_key=conversation)
    g.update(over)
    return g


def _proposed_in(conversation=ADMIN_CONVERSATION, grant=None):
    """A draft proposed in `conversation`, carrying a grant for `grant`'s conversation."""
    meta = {DRAFT_CONVERSATION_KEY: conversation}
    if grant is not None:
        meta[GRANT_KEY] = grant
    return _hermes_draft(meta)


# ── who is eligible ────────────────────────────────────────────────────────

def test_hermes_intent_draft_is_grantable():
    assert is_hermes_proposed(_hermes_draft()) is True


def test_ordinary_draft_is_not_hermes_proposed():
    assert is_hermes_proposed({"intent": "attendance", "meta": {}}) is False
    assert is_hermes_proposed({"intent": "hermes_suggested_ish", "meta": {}}) is False


def test_non_hermes_drafts_are_always_allowed_through():
    """The grant gate must be a no-op for drafts Hermes did not propose."""
    ok, reason = verify_draft_grant({"intent": "attendance", "meta": {}})
    assert ok is True
    assert reason == "not_hermes_proposed"


# ── the happy path ─────────────────────────────────────────────────────────

def test_fresh_grant_verifies():
    grant = build_grant(identity_id="1", conversation_key="hermes_relay:bridge2:8801")
    ok, reason = verify_draft_grant(_proposed_in(grant=grant))
    assert ok is True
    assert reason == "admin_grant_valid"


def test_grant_records_the_admin_identity_and_conversation():
    """The grant is the Admin's evidence, not the model's assertion."""
    grant = build_grant(
        identity_id="42",
        role_names=("superadmin",),
        conversation_key="hermes_relay:bridge2:8801",
    )
    assert grant["identity_id"] == "42"
    assert grant["role_names"] == ["superadmin"]
    assert grant["conversation_key"] == "hermes_relay:bridge2:8801"


def test_grant_carries_an_expiry():
    grant = build_grant(identity_id="1", conversation_key="c")
    assert grant["expires_at"]
    assert DEFAULT_GRANT_TTL_SECONDS <= 600, "an Admin 'yes' must not be standing authority"


# ── fail-closed: every way a grant can be unusable ────────────────────────

def test_missing_grant_denied():
    ok, reason = verify_draft_grant(_proposed_in())
    assert ok is False
    assert reason == "grant_missing"


def test_expired_grant_denied():
    from datetime import datetime, timedelta, timezone

    grant = build_grant(identity_id="1", conversation_key="c")
    grant["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    ).isoformat()
    ok, reason = verify_draft_grant(_proposed_in(grant=grant))
    assert ok is False
    assert reason == "grant_expired"


def test_consumed_grant_denied():
    """Single use: the same Admin 'yes' cannot approve twice."""
    grant = build_grant(identity_id="1", conversation_key="c")
    grant["consumed_at"] = "2026-09-30T00:00:00+00:00"
    ok, reason = verify_draft_grant(_proposed_in(grant=grant))
    assert ok is False
    assert reason == "grant_already_consumed"


def test_grant_without_identity_denied():
    grant = build_grant(identity_id="", conversation_key="c")
    ok, reason = verify_draft_grant(_proposed_in(grant=grant))
    assert ok is False
    assert reason == "grant_has_no_identity"


def test_grant_without_conversation_denied():
    grant = build_grant(identity_id="1", conversation_key="")
    ok, reason = verify_draft_grant(_proposed_in(grant=grant))
    assert ok is False
    assert reason == "grant_has_no_conversation"


def test_unparseable_expiry_denied():
    grant = build_grant(identity_id="1", conversation_key="c")
    grant["expires_at"] = "not-a-date"
    ok, reason = verify_draft_grant(_proposed_in(grant=grant))
    assert ok is False
    assert reason == "grant_expiry_unparseable"


def test_malformed_meta_is_tolerated_but_not_trusted():
    """meta arrives as a dict or a JSON string depending on the driver."""
    grant = build_grant(identity_id="1", conversation_key="c")
    # meta=None -> no grant at all
    assert verify_draft_grant({"intent": HERMES_INTENT, "meta": None})[0] is False
    # meta arriving as an unparseable string must not be trusted either
    assert verify_draft_grant({"intent": HERMES_INTENT, "meta": "not json"})[0] is False
    # meta arriving as a JSON string, as some drivers return it, IS honoured
    import json as _json
    ok, _ = verify_draft_grant({
        "intent": HERMES_INTENT,
        "meta": _json.dumps({
            DRAFT_CONVERSATION_KEY: ADMIN_CONVERSATION,
            GRANT_KEY: _grant_for(ADMIN_CONVERSATION),
        }),
    })
    assert ok is True


# ── conversation binding ───────────────────────────────────────────────────

def test_grant_is_bound_to_its_conversation():
    grant = build_grant(identity_id="1", conversation_key="hermes_relay:bridge2:8801")
    draft = _proposed_in(grant=grant)
    assert verify_draft_grant(draft, conversation_key=ADMIN_CONVERSATION)[0] is True
    ok, reason = verify_draft_grant(draft, conversation_key="hermes_relay:bridge1:9999")
    assert ok is False
    assert reason == "grant_conversation_mismatch"


# ── grant issuance is scoped to pending proposals ─────────────────────────

class _FakeConn:
    """Minimal asyncpg-shaped double; no database is touched."""

    def __init__(self, rows):
        self._rows = rows
        self.executed = []
        self.fetched = []

    async def fetch(self, sql, *a, **kw):
        self.fetched.append(sql)
        return self._rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


@pytest.mark.asyncio
async def test_grants_only_pending_hermes_drafts():
    conn = _FakeConn([{"id": 11, "meta": {}}, {"id": 12, "meta": {}}])
    granted = await authorize_pending_hermes_drafts(
        conn, conversation_key="c", identity_id="7"
    )
    assert granted == [11, 12]
    # the SELECT is constrained to the hermes intent and pending statuses
    sql = conn.fetched[0]
    assert "intent = $1" in sql
    assert "pending" in sql
    assert HERMES_INTENT not in sql, "the intent must be bound as a parameter"


@pytest.mark.asyncio
async def test_valid_existing_grant_is_not_extended():
    """Two consecutive Admin messages must not silently extend one 'yes'."""
    fresh = build_grant(identity_id="7", conversation_key="c")
    conn = _FakeConn([{"id": 11, "meta": {GRANT_KEY: fresh}}])
    granted = await authorize_pending_hermes_drafts(
        conn, conversation_key="c", identity_id="7"
    )
    assert granted == []
    assert conn.executed == []


@pytest.mark.asyncio
async def test_spent_grant_can_be_replaced_by_a_new_admin_message():
    spent = build_grant(identity_id="7", conversation_key="c")
    spent["consumed_at"] = "2026-09-30T00:00:00+00:00"
    conn = _FakeConn([{"id": 11, "meta": {GRANT_KEY: spent}}])
    granted = await authorize_pending_hermes_drafts(
        conn, conversation_key="c", identity_id="7"
    )
    assert granted == [11]


@pytest.mark.asyncio
async def test_grant_issuance_fails_closed_on_db_error():
    class Boom:
        async def fetch(self, *a, **kw):
            raise RuntimeError("db down")

    assert await authorize_pending_hermes_drafts(
        Boom(), conversation_key="c", identity_id="7"
    ) == []


# ── enforcement is wired into the approve endpoint ─────────────────────────

def test_approve_endpoint_enforces_the_grant():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "modules" / "drafts" / "routes.py").read_text()
    body = src.split("async def approve_draft", 1)[1].split("\n@router", 1)[0]
    assert "verify_draft_grant" in body, "approve no longer checks the Admin grant"
    assert "raise HTTPException(\n            status_code=403" in body


def test_approve_endpoint_consumes_the_grant():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "modules" / "drafts" / "routes.py").read_text()
    body = src.split("async def approve_draft", 1)[1].split("\n@router", 1)[0]
    assert "consume_draft_grant" in body, "grant is not single-use"


def test_relay_stamps_the_grant_only_after_canonical_authorization():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "modules" / "admin_directives" / "router.py").read_text()
    # the grant call must come after the canonical decision and its denial return
    grant_at = src.index("authorize_pending_hermes_drafts(")
    decision_at = src.index("relay_auth = await resolve_relay_authorization(")
    denial_at = src.index("return _DENIAL_MSG", decision_at)
    assert decision_at < denial_at < grant_at, (
        "the grant is stamped before the Admin is authorized"
    )


def test_approve_still_binds_recipient_and_content():
    """The grant authorizes the ACTION, never a substituted recipient/text."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "modules" / "drafts" / "routes.py").read_text()
    body = src.split("async def approve_draft", 1)[1].split("\n@router", 1)[0]
    assert 'draft["reply_text"]' in body
    assert 'draft["recipient"]' in body


@pytest.mark.asyncio
async def test_consume_draft_grant_never_raises():
    class Boom:
        async def execute(self, *a, **kw):
            raise RuntimeError("db down")

    await consume_draft_grant(Boom(), 5)  # must not raise


# ── THE OWNER'S BOUNDARY: relay and customer conversation are never one
#    authority (2026-09-30). These are the tests that must never be deleted.
# ───────────────────────────────────────────────────────────────────────────

CUSTOMER_CONVERSATION = "customer_thread:bridge1:8801999999999"


def test_customer_draft_has_no_conversation_and_is_never_grantable():
    """A proposal from a customer conversation stamps no conversation key,
    so no Admin 'yes' can reach it."""
    draft = _hermes_draft({GRANT_KEY: _grant_for(ADMIN_CONVERSATION)})
    ok, reason = verify_draft_grant(draft)
    assert ok is False
    assert reason == "draft_has_no_conversation"


def test_admin_grant_cannot_approve_a_customer_draft():
    """The exact conflation that must be impossible: the Admin authorizes in
    the control thread, and a draft from a customer thread is approved."""
    draft = _proposed_in(
        conversation=CUSTOMER_CONVERSATION,
        grant=_grant_for(ADMIN_CONVERSATION),
    )
    ok, reason = verify_draft_grant(draft)
    assert ok is False
    assert reason == "grant_conversation_mismatch"


def test_one_conversation_cannot_authorize_another():
    """Even two relay conversations do not merge."""
    other_admin = "hermes_relay:bridge1:8801880446111"
    draft = _proposed_in(conversation=other_admin, grant=_grant_for(ADMIN_CONVERSATION))
    assert verify_draft_grant(draft)[0] is False


def test_grant_issuance_selects_only_its_own_conversation():
    """The grant query is scoped by conversation, so a customer-conversation
    draft is never even a candidate."""
    import inspect

    src = inspect.getsource(authorize_pending_hermes_drafts)
    assert "hermes_conversation_key" in src, "grant query is not conversation-scoped"
    assert "$2" in src, "conversation is not bound as a query parameter"


def test_model_cannot_choose_the_conversation():
    """draft_whatsapp_reply must take the conversation from the environment,
    never from a tool argument."""
    from pathlib import Path

    src = (
        Path("/home/azim/fazle-mcp/draft_tools.py")
        .read_text(encoding="utf-8")
    )
    assert "HERMES_RELAY_CONVERSATION_KEY" in src
    sig = src.split("def draft_whatsapp_reply", 1)[1].split("):", 1)[0]
    assert "conversation" not in sig.lower(), (
        "the conversation became a model-supplied parameter -- the model could "
        "then label a customer draft as an Admin one"
    )


def test_conversation_key_cannot_be_forged_in_the_relay_request():
    """hermes-runner only accepts a conversation key shaped like one fazle-core
    actually mints."""
    import sys

    sys.path.insert(0, "/home/azim/hermes-runner")
    try:
        import server as hr
    except Exception:  # noqa: BLE001 -- hermes-runner may not be importable here
        pytest.skip("hermes-runner not importable")
    fn = hr._parse_relay_conversation_key
    assert fn({"relay_conversation_key": ADMIN_CONVERSATION}) == ADMIN_CONVERSATION
    assert fn({}) is None, "a non-relay caller must produce no conversation"
    for bad in ("", "customer_thread:bridge1:1", "hermes_relay:", "x", None):
        assert fn({"relay_conversation_key": bad}) is None
