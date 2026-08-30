"""
Fazle Core — Owner → Accountant payment-message relay (deterministic, LLM-free).

ONE narrow rule. This is NOT a general automation and must not grow into one.

  inbound WhatsApp message (existing bridge/inbound pipeline)
    → ACTUAL transport sender (normalize_phone of the canonical inbound
      sender — remoteJid / sender JID / normalized sender phone), NOT any
      phone number that appears inside the message body,
      == RELAY_SOURCE_NUMBER  (8801817201596)
    → message body has the owner's established cash-payment SHAPE
      (modules.payment_ingest.is_admin_cash_shorthand — used as a pure
      boolean; the body is opaque payload, no field is extracted for any
      decision)
    → enqueue the ORIGINAL inbound body, byte-for-byte, to
      RELAY_DEST_NUMBER  (8801844836824, "Mamun Vai Bkash Nagod")
      via bridge2  (modules.outbound.enqueue, source_bridge="bridge2",
      NO fallback_channel — this relay never fails over to bridge1/bridge3)
    → STOP

Deliberately NOT done here:
  * parse / extract business data for a decision
  * validate employee existence, payout-number ownership, balance, or
    whether the payment already exists
  * create / update / reverse fpe_cash_transactions
  * create / update any employee ledger entry
  * call Hermes / any LLM / any tool reasoning / any confirmation prompt

Downstream (unchanged, independent): once the relayed message lands in
bridge2's chat with the accountant as an is_from_me=1 message, the existing
Fazle Payroll Engine workers (modules.fazle_payroll_engine.historical_sync /
gap_scan / workers) ingest and process it on their own. This module has no
part in, and takes no ownership of, that processing.

Idempotency is transport-level only: keyed on the inbound bridge + inbound
message id, carried through modules.outbound.enqueue's own idempotency_key
(ON CONFLICT (idempotency_key) DO NOTHING). No accounting-level dedup here,
and never an employee+amount+date key.

Gated by env flag PAYMENT_RELAY_ENABLED (default: false).
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from modules.phone_normalizer import normalize_phone

log = logging.getLogger("fazle.payment_relay")

# Hard-coded per the Owner's exact rule; env vars below allow an override
# without a code change but default to exactly these values.
RELAY_SOURCE_NUMBER = "8801817201596"          # canonical 8801XXXXXXXXX
RELAY_DEST_NUMBER = "8801844836824"            # "Mamun Vai Bkash Nagod"
RELAY_DEST_REFERENCE = "Mamun Vai Bkash Nagod"
RELAY_BRIDGE = "bridge2"                        # hard requirement — never bridge1/bridge3
RELAY_PURPOSE = "owner-accountant-payment-relay"


def _enabled() -> bool:
    return os.getenv("PAYMENT_RELAY_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _cfg(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def body_hash(body: str) -> str:
    """sha256 hex of the raw body — used only as proof that the inbound and
    outbound bodies are byte-for-byte identical. Never a routing input."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def is_payment_shape(body: str) -> bool:
    """Boolean shape check ONLY — never extracts a field for a decision.

    Delegates to the established owner cash-payment detector
    (modules.payment_ingest.is_admin_cash_shorthand), which recognizes the
    ``ID: <phone> <name> <phone>(B/N/...) <amount>/-`` structure the owner
    has always used (one or more such lines per WhatsApp message). Used
    purely as True/False; the body stays opaque to this module.
    """
    if not body or not body.strip():
        return False
    try:
        from modules.payment_ingest import is_admin_cash_shorthand
        return bool(is_admin_cash_shorthand(body))
    except Exception as exc:  # noqa: BLE001 — fail closed to "no match"
        log.warning("[payment_relay] shape check errored, treating as no-match: %s", exc)
        return False


def is_relay_sender(raw_sender: Optional[str]) -> bool:
    """True iff the ACTUAL transport sender normalizes to RELAY_SOURCE_NUMBER.

    ``raw_sender`` MUST be the transport/canonical inbound sender (remoteJid /
    sender JID / normalized sender phone / the canonical inbound sender
    field) — NEVER a phone number lifted from inside the message body.
    """
    if not raw_sender or str(raw_sender).startswith("unresolved:"):
        return False
    want = normalize_phone(_cfg("PAYMENT_RELAY_SOURCE_NUMBER", RELAY_SOURCE_NUMBER)) \
        or RELAY_SOURCE_NUMBER
    got = normalize_phone(str(raw_sender))
    return bool(got) and got == want


async def maybe_relay_owner_payment(
    *,
    bridge_name: str,
    sender: str,
    body: str,
    message_id: str = "",
) -> Optional[dict]:
    """Deterministic relay gate.

    Returns a result dict when this inbound message was CLAIMED by the relay
    rule — the caller MUST then stop all further processing of the message
    (no intent classification, no recruitment, no Hermes, no reply). Returns
    ``None`` when the rule does not apply and the caller must continue its
    normal pipeline completely unchanged.

    Never raises. If the message already matched sender+shape but the send
    could not be enqueued, a ``status="enqueue_error"`` claim is returned
    (fail closed: the message is recorded/reported, never silently handed to
    the LLM path).
    """
    if not _enabled():
        return None
    if not is_relay_sender(sender):
        return None
    if not is_payment_shape(body):
        return None

    dest = normalize_phone(_cfg("PAYMENT_RELAY_DEST_NUMBER", RELAY_DEST_NUMBER)) \
        or RELAY_DEST_NUMBER
    bridge = _cfg("PAYMENT_RELAY_BRIDGE", RELAY_BRIDGE)
    bhash = body_hash(body)

    # Transport-level idempotency: inbound bridge + inbound message id.
    # Falls back to a bridge + body digest ONLY when the transport handed us
    # no id at all (never an employee/amount/date key).
    if message_id:
        idem = f"payment-relay:{bridge_name}:{message_id}"
    else:
        idem = f"payment-relay:{bridge_name}:nomsgid:{bhash[:32]}"

    log.info(
        "[payment_relay] MATCH inbound_bridge=%s inbound_msg_id=%s source=%s dest=%s "
        "outbound_bridge=%s body_sha256=%s body_len=%d",
        bridge_name, message_id or "(none)", RELAY_SOURCE_NUMBER, dest, bridge,
        bhash, len(body or ""),
    )

    try:
        from modules.outbound import enqueue
        # outbound_body = inbound_body  — verbatim, passed straight through.
        qid = await enqueue(
            dest,
            body,
            source_bridge=bridge,        # bridge2 — pinned
            fallback_channel=None,       # never fail this relay over to another bridge
            purpose=RELAY_PURPOSE,
            idempotency_key=idem,
            meta={
                "relay": "owner_accountant_payment",
                "inbound_bridge": bridge_name,
                "inbound_message_id": message_id or None,
                "source_number": RELAY_SOURCE_NUMBER,
                "dest_reference": RELAY_DEST_REFERENCE,
                "body_sha256": bhash,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "[payment_relay] ENQUEUE FAILED inbound_bridge=%s inbound_msg_id=%s "
            "dest=%s outbound_bridge=%s err=%s",
            bridge_name, message_id or "(none)", dest, bridge, exc,
        )
        return {
            "relayed": False,
            "status": "enqueue_error",
            "reason": str(exc),
            "inbound_message_id": message_id or None,
            "source_number": RELAY_SOURCE_NUMBER,
            "dest_number": dest,
            "outbound_bridge": bridge,
            "body_sha256": bhash,
        }

    if qid is None:
        log.info(
            "[payment_relay] DEDUP inbound_bridge=%s inbound_msg_id=%s idem=%s "
            "— already relayed, not re-sent",
            bridge_name, message_id or "(none)", idem,
        )
        status = "duplicate"
    else:
        log.info(
            "[payment_relay] ENQUEUED queue_id=%s inbound_bridge=%s inbound_msg_id=%s "
            "dest=%s outbound_bridge=%s",
            qid, bridge_name, message_id or "(none)", dest, bridge,
        )
        status = "enqueued"

    return {
        "relayed": qid is not None,
        "status": status,
        "queue_id": qid,
        "idempotency_key": idem,
        "inbound_message_id": message_id or None,
        "source_number": RELAY_SOURCE_NUMBER,
        "dest_number": dest,
        "dest_reference": RELAY_DEST_REFERENCE,
        "outbound_bridge": bridge,
        "body_sha256": bhash,
    }
