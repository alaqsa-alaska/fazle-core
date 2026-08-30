"""
Fazle Core — Owner → Accountant payment-message relay (deterministic, LLM-free).

ONE narrow rule. This is NOT a general automation and must not grow into one.

  inbound WhatsApp message (existing bridge/inbound pipeline)
    → arrived on RELAY_INBOUND_BRIDGE  (bridge2 = 8801880446111, the Owner /
      Super Admin account) — the SAME sender + body arriving on bridge1 or
      bridge3 must NOT activate this relay
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

PROTECTED SCOPE — fail closed (Owner directive 2026-08-30, part A):
  Once the inbound transport facts establish a "protected financial relay
  candidate" — the relay is enabled AND inbound bridge == the configured
  inbound bridge label AND canonical sender == RELAY_SOURCE_NUMBER AND body
  matches the payment shorthand shape — the message is CLAIMED no matter what
  happens next:
      success                      -> enqueue verbatim relay, STOP
      duplicate                    -> STOP
      bridge2 unavailable / enqueue failure -> held-failure claim, STOP
      internal relay exception     -> held-failure claim, STOP
      configuration mismatch/error -> held-failure claim, STOP
  It MUST NEVER fall through to generic routing (Office/Internal, recruitment,
  candidate routing, admin-review draft, Hermes, LLM, generic safety reply)
  merely because the relay implementation hit a problem. On a held failure
  this module still does NOT create an FPE transaction, invoke Hermes,
  generate an acknowledgement, or build an admin-review reply for the payment
  content — it records/returns the failure and STOPs.

  Messages OUTSIDE the protected scope are returned as None -> the caller's
  normal routing continues completely unchanged.

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
#
# Bridge topology (Owner-authoritative, verified against the running process
# environment 2026-08-30 — do NOT infer from labels/docs/memory):
#   bridge1 = 8801958122300  (HR)
#   bridge2 = 8801880446111  (OPS / Super Admin / Owner)   <- this rule's inbound
#   bridge3 = 8801805529211  (REC2)
# The real live message on 2026-08-30 (sender 8801817201596 -> 8801880446111,
# "ID: 01813225542 Anowar SG 01813225542(N) 200/-") arrived on bridge2.
RELAY_INBOUND_BRIDGE = "bridge2"
RELAY_RECEIVING_NUMBER = "8801880446111"
RELAY_SOURCE_NUMBER = "8801817201596"
RELAY_DEST_NUMBER = "8801844836824"            # "Mamun Vai Bkash Nagod"
RELAY_DEST_REFERENCE = "Mamun Vai Bkash Nagod"
RELAY_BRIDGE = "bridge2"                        # OUTBOUND — hard requirement, never bridge1/bridge3
RELAY_PURPOSE = "owner-accountant-payment-relay"


def _enabled() -> bool:
    return os.getenv("PAYMENT_RELAY_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _cfg(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def body_hash(body: str) -> str:
    """sha256 hex of the raw body — proof that inbound and outbound bodies are
    byte-for-byte identical. Never a routing input."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def is_payment_shape(body: str) -> bool:
    """Boolean shape check ONLY — never extracts a field for a decision.

    Delegates to the established owner cash-payment detector
    (modules.payment_ingest.is_admin_cash_shorthand). Used purely as
    True/False; the body stays opaque to this module.

    Fail-closed to "no match" on any error: a genuine non-payment operational
    message from the relay sender (e.g. "Bill korsi") MUST keep flowing to
    normal routing, so an unresolvable shape check is treated as "not a
    payment", not "protected".
    """
    if not body or not body.strip():
        return False
    try:
        from modules.payment_ingest import is_admin_cash_shorthand
        return bool(is_admin_cash_shorthand(body))
    except Exception as exc:  # noqa: BLE001 — fail closed to "no match"
        log.warning("[payment_relay] shape check errored, treating as no-match: %s", exc)
        return False


def _configured_bridge_number(bridge_name: Optional[str]) -> Optional[str]:
    """The WhatsApp number the given bridge is configured/paired to, read from
    live settings (app.config). Used only for the defensive label->number
    cross-check — never a routing input on its own."""
    try:
        from app.config import get_settings
        s = get_settings()
        return {
            "bridge1": getattr(s, "bridge1_number", "") or "",
            "bridge2": getattr(s, "bridge2_number", "") or "",
            "bridge3": getattr(s, "bridge3_number", "") or "",
        }.get(str(bridge_name or "").strip().lower())
    except Exception as exc:  # noqa: BLE001
        log.warning("[payment_relay] could not resolve bridge number for %s: %s", bridge_name, exc)
        return None


def _on_configured_inbound_bridge(bridge_name: Optional[str]) -> bool:
    """Label-only test: did the message arrive on the bridge this rule is
    scoped to (RELAY_INBOUND_BRIDGE, default 'bridge2')? One of the facts that
    establish the *protected scope* — the paired-number cross-check is a
    failure mode inside that scope, not a scope gate."""
    want = _cfg("PAYMENT_RELAY_INBOUND_BRIDGE", RELAY_INBOUND_BRIDGE)
    return bool(bridge_name) and str(bridge_name).strip().lower() == want.strip().lower()


def _inbound_bridge_number_matches(bridge_name: Optional[str]) -> "tuple[bool, str]":
    """Defensive label<->number cross-check. Returns (ok, detail). ok is False
    ONLY on a proven mismatch — an unresolvable number (settings unreadable)
    is treated as ok=True so a config-read hiccup does not itself block a
    relay (the label match + exact-sender match already make this a
    deliberate, Owner-scoped message)."""
    want_num = normalize_phone(_cfg("PAYMENT_RELAY_RECEIVING_NUMBER", RELAY_RECEIVING_NUMBER))
    got_raw = _configured_bridge_number(bridge_name)
    got_num = normalize_phone(got_raw or "")
    if want_num and got_num and got_num != want_num:
        return False, f"bridge {bridge_name!r} paired to {got_num}, expected {want_num}"
    return True, "ok"


def is_relay_inbound_bridge(bridge_name: Optional[str]) -> bool:
    """Full inbound-bridge check: correct label AND paired to the expected
    number. Kept for callers/tests that want the combined answer."""
    if not _on_configured_inbound_bridge(bridge_name):
        return False
    ok, detail = _inbound_bridge_number_matches(bridge_name)
    if not ok:
        log.error("[payment_relay] inbound bridge check failed: %s", detail)
    return ok


def is_relay_sender(raw_sender: Optional[str]) -> bool:
    """True iff the ACTUAL transport sender normalizes to RELAY_SOURCE_NUMBER.

    raw_sender MUST be the transport/canonical inbound sender (remoteJid /
    sender JID / normalized sender phone / the canonical inbound sender
    field) — NEVER a phone number lifted from inside the message body.
    """
    if not raw_sender or str(raw_sender).startswith("unresolved:"):
        return False
    want = normalize_phone(_cfg("PAYMENT_RELAY_SOURCE_NUMBER", RELAY_SOURCE_NUMBER)) \
        or RELAY_SOURCE_NUMBER
    got = normalize_phone(str(raw_sender))
    return bool(got) and got == want


def is_protected_payment_candidate(
    bridge_name: Optional[str], sender: Optional[str], body: Optional[str],
) -> bool:
    """True iff this inbound message is within the PROTECTED financial-relay
    scope: relay enabled, arrived on the configured inbound bridge label,
    canonical transport sender is exactly RELAY_SOURCE_NUMBER, and body
    matches the cash-payment shorthand shape.

    A message that passes this MUST NOT fall through to generic routing on any
    relay failure — the caller holds it and STOPs. Pure transport facts + a
    boolean shape check; no body field is extracted, no LLM, no Hermes.
    Sync and never raises.
    """
    try:
        return bool(
            _enabled()
            and _on_configured_inbound_bridge(bridge_name)
            and is_relay_sender(sender)
            and is_payment_shape(body or "")
        )
    except Exception as exc:  # noqa: BLE001
        log.error("[payment_relay] is_protected_payment_candidate errored: %s", exc)
        return False


def _dest_number() -> str:
    return normalize_phone(_cfg("PAYMENT_RELAY_DEST_NUMBER", RELAY_DEST_NUMBER)) or RELAY_DEST_NUMBER


def _outbound_bridge() -> str:
    return _cfg("PAYMENT_RELAY_BRIDGE", RELAY_BRIDGE)


async def maybe_relay_owner_payment(
    *,
    bridge_name: str,
    sender: str,
    body: str,
    message_id: str = "",
) -> Optional[dict]:
    """Deterministic relay gate.

    Returns a result dict when this inbound message was CLAIMED by the relay
    rule — the caller MUST then stop ALL further processing of the message
    (no intent classification, no recruitment, no Office/Internal, no
    admin-review draft, no Hermes, no LLM, no reply). Returns None ONLY when
    the message is outside the protected scope and the caller must continue
    its normal pipeline completely unchanged.

    Never raises, and — critically — NEVER returns None once the protected
    scope (enabled + inbound bridge label + exact sender + payment shape) is
    established. Every failure mode inside the protected scope
    (config mismatch, enqueue failure, duplicate, unexpected exception) is
    returned as a held-failure claim so the caller STOPs; the message is
    never silently handed to generic routing / Hermes / the LLM path.
    """
    if not _enabled():
        return None

    on_bridge = _on_configured_inbound_bridge(bridge_name)
    sender_ok = is_relay_sender(sender)
    shape_ok = is_payment_shape(body or "")

    if not (on_bridge and sender_ok and shape_ok):
        return None  # outside protected scope -> normal routing, unchanged

    # -- PROTECTED SCOPE ESTABLISHED — this message is CLAIMED no matter what --
    bhash = body_hash(body or "")
    dest = _dest_number()
    bridge = _outbound_bridge()
    if message_id:
        idem = f"payment-relay:{bridge_name}:{message_id}"
    else:
        idem = f"payment-relay:{bridge_name}:nomsgid:{bhash[:32]}"

    def _claim(status: str, *, relayed: bool = False, queue_id=None, reason: str = "") -> dict:
        return {
            "relayed": relayed,
            "claimed": True,
            "status": status,
            "reason": reason,
            "queue_id": queue_id,
            "idempotency_key": idem,
            "inbound_bridge": bridge_name,
            "inbound_message_id": message_id or None,
            "source_number": RELAY_SOURCE_NUMBER,
            "dest_number": dest,
            "dest_reference": RELAY_DEST_REFERENCE,
            "outbound_bridge": bridge,
            "body_sha256": bhash,
            "body_len": len(body or ""),
        }

    try:
        # Defensive label<->number cross-check — a proven mismatch is a HELD
        # failure (STOP), never a fall-through to generic routing.
        num_ok, num_detail = _inbound_bridge_number_matches(bridge_name)
        if not num_ok:
            log.error(
                "[payment_relay] HELD protected message — config mismatch inbound_bridge=%s "
                "inbound_msg_id=%s detail=%s body_sha256=%s (NOT routed, NOT relayed)",
                bridge_name, message_id or "(none)", num_detail, bhash,
            )
            return _claim("config_mismatch", reason=num_detail)

        log.info(
            "[payment_relay] MATCH inbound_bridge=%s inbound_msg_id=%s source=%s dest=%s "
            "outbound_bridge=%s body_sha256=%s body_len=%d",
            bridge_name, message_id or "(none)", RELAY_SOURCE_NUMBER, dest, bridge,
            bhash, len(body or ""),
        )

        from modules.outbound import enqueue
        # outbound_body = inbound_body — verbatim, passed straight through.
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
    except Exception as exc:  # noqa: BLE001 — protected message is HELD, never re-routed
        log.error(
            "[payment_relay] HELD protected message — relay error inbound_bridge=%s "
            "inbound_msg_id=%s body_sha256=%s err=%s (NOT routed, NOT relayed)",
            bridge_name, message_id or "(none)", bhash, exc,
        )
        return _claim("relay_error", reason=str(exc)[:300])

    if qid is None:
        log.info(
            "[payment_relay] DEDUP inbound_bridge=%s inbound_msg_id=%s idem=%s "
            "— already relayed, not re-sent (STOP)",
            bridge_name, message_id or "(none)", idem,
        )
        return _claim("duplicate")

    log.info(
        "[payment_relay] ENQUEUED queue_id=%s inbound_bridge=%s inbound_msg_id=%s "
        "dest=%s outbound_bridge=%s",
        qid, bridge_name, message_id or "(none)", dest, bridge,
    )
    return _claim("enqueued", relayed=True, queue_id=qid)
