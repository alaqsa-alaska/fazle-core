"""Unit tests — modules.payment_relay deterministic owner→accountant relay.

Proves the exact rule and its boundaries:

  source  = 8801817201596   (ACTUAL transport sender, never a body phone number)
  dest    = 8801844836824   ("Mamun Vai Bkash Nagod")
  bridge  = bridge2         (pinned, no failover)
  body    = verbatim original inbound body
  Hermes / LLM decision  = none
  direct fpe_cash_transactions / employee-ledger mutation = none
  dedup   = transport-level (inbound bridge + inbound message id)
"""
from __future__ import annotations

import pytest

import modules.payment_relay as pr

pytestmark = pytest.mark.unit


OWNER = "8801817201596"
DEST = "8801844836824"

SINGLE = "ID: 01998846707 Maruf islam 01998846707(N) 200/-"
MULTI = (
    "ID: 01998846707 Maruf islam 01998846707(N) 200/-\n"
    "ID: 01318182022 MD. Nasir SG 01318182022(B) 200/-\n"
    "ID: 01881894579 MD: Billal Mia 01881894579(B) 1000/-\n"
    "ID: 01301241783 Shadat islam 01907602458(B) 500/-"
)
VERBATIM = "ID: 01858898790   Monirul  Alam 01840429666(B)  1,000/-"


class _FakeEnqueue:
    """Mimics modules.outbound.enqueue: records calls and honours the
    idempotency_key ON CONFLICT DO NOTHING contract (returns None on a repeat
    key, an incrementing id otherwise)."""

    def __init__(self):
        self.calls: list[dict] = []
        self._seen: set[str] = set()
        self._next = 1000

    async def __call__(self, recipient, body, *, source_bridge="bridge2",
                        fallback_channel=None, purpose=None,
                        idempotency_key=None, meta=None):
        self.calls.append({
            "recipient": recipient, "body": body, "source_bridge": source_bridge,
            "fallback_channel": fallback_channel, "purpose": purpose,
            "idempotency_key": idempotency_key, "meta": meta,
        })
        if idempotency_key and idempotency_key in self._seen:
            return None
        if idempotency_key:
            self._seen.add(idempotency_key)
        self._next += 1
        return self._next


@pytest.fixture
def fake_enqueue(monkeypatch):
    fe = _FakeEnqueue()
    monkeypatch.setattr("modules.outbound.enqueue", fe)
    return fe


@pytest.fixture(autouse=True)
def _enable_relay(monkeypatch):
    monkeypatch.setenv("PAYMENT_RELAY_ENABLED", "true")
    for k in ("PAYMENT_RELAY_SOURCE_NUMBER", "PAYMENT_RELAY_DEST_NUMBER",
              "PAYMENT_RELAY_BRIDGE", "PAYMENT_RELAY_INBOUND_BRIDGE",
              "PAYMENT_RELAY_RECEIVING_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    # Authoritative bridge→number map (matches the running production process
    # verified 2026-08-30). Stubbed so the defensive label↔number cross-check
    # in is_relay_inbound_bridge() is deterministic and does not depend on the
    # test host's cached app.config settings.
    _MAP = {"bridge1": "8801958122300", "bridge2": "8801880446111", "bridge3": "8801805529211"}
    monkeypatch.setattr(pr, "_configured_bridge_number",
                        lambda name: _MAP.get(str(name or "").strip().lower()))


# ── Positive ─────────────────────────────────────────────────────────────────

async def test_positive_single_line(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.AAA",
    )
    assert res is not None and res["relayed"] is True and res["status"] == "enqueued"
    assert len(fake_enqueue.calls) == 1
    call = fake_enqueue.calls[0]
    assert call["recipient"] == DEST
    assert call["source_bridge"] == "bridge2"
    assert call["fallback_channel"] is None
    assert call["body"] == SINGLE


async def test_positive_multiline(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=MULTI, message_id="wamid.MULTI",
    )
    assert res["relayed"] is True
    assert len(fake_enqueue.calls) == 1
    assert fake_enqueue.calls[0]["body"] == MULTI
    assert fake_enqueue.calls[0]["recipient"] == DEST


async def test_positive_sender_as_jid(fake_enqueue):
    """Canonical sender may arrive as a JID; only the normalized number matters."""
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=f"{OWNER}@s.whatsapp.net", body=SINGLE,
        message_id="wamid.JID",
    )
    assert res["relayed"] is True
    assert fake_enqueue.calls[0]["recipient"] == DEST


# ── Inbound-bridge restriction (rule fires ONLY on bridge2 = 8801880446111) ──

async def test_bridge2_correct_sender_correct_shape_relays(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.B2OK",
    )
    assert res is not None and res["relayed"] is True
    assert len(fake_enqueue.calls) == 1
    assert fake_enqueue.calls[0]["recipient"] == DEST
    assert fake_enqueue.calls[0]["source_bridge"] == "bridge2"


async def test_bridge1_correct_sender_correct_shape_no_relay(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge1", sender=OWNER, body=SINGLE, message_id="wamid.B1",
    )
    assert res is None
    assert fake_enqueue.calls == []


async def test_bridge3_correct_sender_correct_shape_no_relay(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge3", sender=OWNER, body=SINGLE, message_id="wamid.B3",
    )
    assert res is None
    assert fake_enqueue.calls == []


async def test_bridge2_wrong_sender_same_body_no_relay(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender="8801700000000", body=SINGLE,
        message_id="wamid.B2WRONG",
    )
    assert res is None
    assert fake_enqueue.calls == []


async def test_inbound_bridge_paired_to_wrong_number_fails_closed(monkeypatch, fake_enqueue):
    """Defensive: if the bridge labeled 'bridge2' is paired to something other
    than 8801880446111 (config label↔number swap), the relay must NOT fire."""
    monkeypatch.setattr(pr, "_configured_bridge_number",
                        lambda name: "8801999999999" if name == "bridge2" else None)
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.SWAP",
    )
    assert res is None
    assert fake_enqueue.calls == []


def test_is_relay_inbound_bridge():
    assert pr.is_relay_inbound_bridge("bridge2") is True
    assert pr.is_relay_inbound_bridge("BRIDGE2") is True
    assert pr.is_relay_inbound_bridge("bridge1") is False
    assert pr.is_relay_inbound_bridge("bridge3") is False
    assert pr.is_relay_inbound_bridge("") is False
    assert pr.is_relay_inbound_bridge(None) is False


# ── Negative — wrong sender ──────────────────────────────────────────────────

async def test_negative_wrong_sender(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender="8801700000000", body=SINGLE,
        message_id="wamid.X",
    )
    assert res is None
    assert fake_enqueue.calls == []


async def test_negative_unresolved_sender(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender="unresolved:12345", body=SINGLE,
        message_id="wamid.X",
    )
    assert res is None
    assert fake_enqueue.calls == []


# ── Negative — wrong format ─────────────────────────────────────────────────

async def test_negative_wrong_format_ordinary_text(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER,
        body="ভাই কালকে অফিসে আসবেন? একটু কথা ছিল।", message_id="wamid.T",
    )
    assert res is None
    assert fake_enqueue.calls == []


# ── Critical number-confusion regression ────────────────────────────────────

async def test_number_confusion_body_contains_owner_number(fake_enqueue):
    """Actual sender is NOT the owner, but the BODY contains 8801817201596.
    The relay must key off transport metadata, not body text → no relay."""
    body = f"ID: {OWNER} Some Name {OWNER}(B) 200/-"
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender="8801555555555", body=body,
        message_id="wamid.CONF",
    )
    assert res is None
    assert fake_enqueue.calls == []


async def test_is_relay_sender_ignores_body_numbers():
    assert pr.is_relay_sender(OWNER) is True
    assert pr.is_relay_sender("01817201596") is True          # local form of owner
    assert pr.is_relay_sender("8801555555555") is False
    assert pr.is_relay_sender("") is False
    assert pr.is_relay_sender(None) is False


# ── Verbatim ────────────────────────────────────────────────────────────────

async def test_verbatim_passthrough(fake_enqueue):
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=VERBATIM, message_id="wamid.V",
    )
    inbound_body = VERBATIM
    outbound_body = fake_enqueue.calls[0]["body"]
    assert outbound_body == inbound_body
    # exact-content proof carried on the result + enqueue meta
    assert res["body_sha256"] == pr.body_hash(inbound_body)
    assert fake_enqueue.calls[0]["meta"]["body_sha256"] == pr.body_hash(inbound_body)
    # no prefix / header / footer / "Forwarded" / sender info added
    for marker in ("Forwarded", "From:", "প্রেরক", "---"):
        assert marker not in outbound_body


# ── No direct FPE / ledger mutation ─────────────────────────────────────────

async def test_no_direct_fpe_or_ledger_mutation(monkeypatch, fake_enqueue):
    """The relay's only side effect is modules.outbound.enqueue. Any DB write
    or FPE-transaction call would blow up these tripwires."""
    import app.database as db

    async def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("relay touched the database directly")

    monkeypatch.setattr(db, "execute", _boom)
    monkeypatch.setattr(db, "fetch_one", _boom)
    monkeypatch.setattr(db, "fetch_all", _boom)
    monkeypatch.setattr(db, "fetch_val", _boom)

    import modules.payment_ingest.fpe_bridge as fb

    async def _boom_fpe(*a, **k):  # pragma: no cover
        raise AssertionError("relay created an FPE transaction")

    monkeypatch.setattr(fb, "create_fpe_transaction_from_ingest", _boom_fpe)

    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=MULTI, message_id="wamid.NOFPE",
    )
    assert res["relayed"] is True
    assert len(fake_enqueue.calls) == 1


def test_relay_module_code_has_no_fpe_or_hermes_writes():
    """Static guard (AST, code only — docstrings/comments excluded): the relay
    module must not import or reference ledger/transaction/Hermes/LLM write
    surfaces."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pr))
    imported: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)

    all_refs = imported | names
    forbidden = {
        "create_fpe_transaction_from_ingest", "create_transaction",
        "fpe_cash_transactions", "employee_ledger", "generate_reply",
        "ingest_admin_cash_entry", "ingest_payment_sms",
    }
    assert not (forbidden & all_refs), f"payment_relay references {forbidden & all_refs}"
    assert not any("hermes" in i.lower() for i in imported), imported
    assert not any(i.split(".")[0] in {"app"} and "llm" in i for i in imported), imported
    # the ONLY cross-module write surface it may touch is the outbound queue
    assert "modules.outbound" in imported
    assert "modules.payment_ingest" in imported  # boolean shape detector only


# ── No Hermes / LLM call ────────────────────────────────────────────────────

async def test_no_hermes_or_llm_invocation(monkeypatch, fake_enqueue):
    called = {"hit": False}

    # Tripwire the common LLM / Hermes entry points.
    import app.llm as _llm
    for name in [n for n in dir(_llm) if not n.startswith("_")]:
        obj = getattr(_llm, name)
        if callable(obj):
            def _mk(_n):
                def _f(*a, **k):
                    called["hit"] = True
                    raise AssertionError(f"relay invoked app.llm.{_n}")
                return _f
            try:
                monkeypatch.setattr(_llm, name, _mk(name))
            except Exception:
                pass

    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.NOLLM",
    )
    assert res["relayed"] is True
    assert called["hit"] is False


# ── Transport duplicate delivery ───────────────────────────────────────────

async def test_transport_duplicate_same_message_id_relays_once(fake_enqueue):
    kw = dict(bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.DUP")
    first = await pr.maybe_relay_owner_payment(**kw)
    second = await pr.maybe_relay_owner_payment(**kw)

    assert first["relayed"] is True and first["status"] == "enqueued"
    assert second["relayed"] is False and second["status"] == "duplicate"
    # enqueue() was invoked twice but its idempotency_key contract means only
    # one real queue row is created.
    assert len(fake_enqueue.calls) == 2
    assert fake_enqueue.calls[0]["idempotency_key"] == fake_enqueue.calls[1]["idempotency_key"]
    created = [c for c in fake_enqueue.calls]  # both share the key
    assert first["queue_id"] is not None and second["queue_id"] is None


async def test_idempotency_key_is_transport_identity_not_business_fields(fake_enqueue):
    await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.KEY",
    )
    key = fake_enqueue.calls[0]["idempotency_key"]
    assert key == "payment-relay:bridge2:wamid.KEY"
    # no amount / employee / date in the key
    assert "200" not in key and "01998846707" not in key


# ── Disabled flag ──────────────────────────────────────────────────────────

async def test_disabled_flag_is_noop(monkeypatch, fake_enqueue):
    monkeypatch.setenv("PAYMENT_RELAY_ENABLED", "false")
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.OFF",
    )
    assert res is None
    assert fake_enqueue.calls == []


# ── Fail-closed on enqueue error (never silently hand to LLM path) ──────────

async def test_enqueue_error_returns_failed_claim(monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("bridge2 unavailable")

    monkeypatch.setattr("modules.outbound.enqueue", _raise)
    res = await pr.maybe_relay_owner_payment(
        bridge_name="bridge2", sender=OWNER, body=SINGLE, message_id="wamid.ERR",
    )
    assert res is not None
    assert res["relayed"] is False
    assert res["status"] == "enqueue_error"
    assert res["outbound_bridge"] == "bridge2"
