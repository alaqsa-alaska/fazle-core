"""Unit tests — modules/bridge_poller._is_system_generated_notification()

Escort-pipeline audit, Root Cause 7 (2026-07-19): a system-generated admin
review notification (modules.message_router._admin_review_note() and the
two hardcoded equivalents) quotes/instructs escort- or release-shaped
text, gets sent to the admin over the same bridge account
bridge_poller's outgoing-message scan reads back looking for real
admin-typed confirmations, and can false-positive match
is_completed_escort_draft() -- misreading the system's own notification
as the admin's authoritative confirmation reply.

Confirmed against the real production incident: queue item #1 in
wbom_unmatched_confirmations (2026-07-19, source_message_id
3EB0268977920939AB0342) is the actual text this guard is built to catch.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

# Verbatim text from the real production queue item #1 this bug produced.
REAL_INCIDENT_TEXT = (
    "⚠️ ESCORT COMPLAINT/ASSIGNMENT-LIKE MESSAGE — ADMIN REVIEW\n"
    "Sender: 8801817201596\n"
    "Role: employee\n\n"
    "Mother Vessel: MV Horda\n"
    "Lighter Vessel: Bushra Biva 01\n"
    "Master Mobile: 01821375926\n"
    "Destination: Outer to Local \n"
    "Escort Name: Md. Mohiuddin\n"
    "Escort Mobile: 01818388630\n"
    "Start Date: 19.07.2026 (9)\n"
    "Al-Aqsa Security Service\n\n"
    "Note: Client message contains escort assignment/slip/release/food/conveyance context. "
    "Do not create escort order automatically; admin must verify against DB/roster."
)

GENUINE_ADMIN_CONFIRMATION = (
    "Mother Vessel: MV Horda\n"
    "Lighter Vessel: Bushra Biva 01\n"
    "Master Mobile: 01821375926\n"
    "Destination: Outer to Local \n"
    "Escort Name: Md. Mohiuddin\n"
    "Escort Mobile: 01818388630\n"
    "Start Date: 19.07.2026 (9)\n"
    "Al-Aqsa Security Service"
)


def test_real_incident_text_is_recognized_as_system_generated():
    from modules.bridge_poller import _is_system_generated_notification
    assert _is_system_generated_notification(REAL_INCIDENT_TEXT) is True


def test_genuine_admin_confirmation_is_not_flagged():
    from modules.bridge_poller import _is_system_generated_notification
    assert _is_system_generated_notification(GENUINE_ADMIN_CONFIRMATION) is False


def test_automated_suffix_anchor_is_the_primary_signal():
    """The real origin marker (app.bridge._AUTOMATED_SUFFIX_ANCHOR), not
    just the title-prefix fallback -- this is what TASK 5 already appends
    to every message sent via BridgeClient.send()/send_strict(), the one
    path both the outbound queue and the direct-fallback notify path use.
    Catches this even with NONE of the known title prefixes present,
    proving it's a real signal independent of the secondary denylist."""
    from modules.bridge_poller import _is_system_generated_notification
    from app.bridge import _AUTOMATED_SUFFIX_ANCHOR, _AUTOMATED_SUFFIX

    text_with_no_known_prefix = (
        "Some brand-new notification wording that matches none of the "
        "known title prefixes at all" + _AUTOMATED_SUFFIX
    )
    assert _is_system_generated_notification(text_with_no_known_prefix) is True
    assert _AUTOMATED_SUFFIX_ANCHOR in _AUTOMATED_SUFFIX  # sanity: anchor is really in the suffix


@pytest.mark.asyncio
async def test_real_bridge_client_send_appends_the_marker():
    """Confirms the marker really is attached at the one send path both
    admin-notification routes ultimately use -- not just asserted in
    isolation from the constant."""
    from app.bridge import BridgeClient, _AUTOMATED_SUFFIX_ANCHOR

    captured = {}

    class _FakeResponse:
        status_code = 200
        text = ""

    class _FakeHTTPClient:
        async def post(self, url, json=None, **kwargs):
            if json is not None:
                captured["message"] = json.get("message")
            return _FakeResponse()

    client = BridgeClient.__new__(BridgeClient)
    client.label = "test"
    client.base_url = "http://fake"
    client._client = _FakeHTTPClient()

    async def _fake_set_send(_v):
        return None

    client._set_send = _fake_set_send

    await client.send_strict("8801700000000", "a genuine-looking notification body")
    assert _AUTOMATED_SUFFIX_ANCHOR in captured["message"]


@pytest.mark.parametrize("prefix", [
    "⚠️ ESCORT REVIEW DRAFT — ORDER নয়",
    "⚠️ ESCORT COMPLAINT/ASSIGNMENT-LIKE MESSAGE — ADMIN REVIEW",
    "⚠️ ESCORT CLIENT MESSAGE — STRICT ORDER MATCH হয়নি",
    "⚠️ COMPLETED ESCORT SLIP RECEIVED FROM NON-ADMIN",
    "⚠️ RELEASE REVIEW REQUIRED",
    "📩 DRAFT-ONLY CONTACT MESSAGE",
])
def test_every_known_notification_title_is_caught(prefix):
    from modules.bridge_poller import _is_system_generated_notification
    assert _is_system_generated_notification(f"{prefix}\nSender: x\n\nsome body text") is True


def test_leading_whitespace_does_not_bypass_the_guard():
    from modules.bridge_poller import _is_system_generated_notification
    assert _is_system_generated_notification("   \n⚠️ ESCORT REVIEW DRAFT — ORDER নয়\nbody") is True


def test_empty_text_is_not_flagged():
    from modules.bridge_poller import _is_system_generated_notification
    assert _is_system_generated_notification("") is False
    assert _is_system_generated_notification(None) is False


def test_real_incident_text_also_passes_is_completed_escort_draft():
    """Confirms the premise the guard exists for: the real incident text
    legitimately matches the pre-existing pattern-matcher (it does
    contain all 5 required fields plus the Al-Aqsa signature) -- without
    the guard, nothing else in the scan would have stopped it."""
    from modules.escort import is_completed_escort_draft
    assert is_completed_escort_draft(REAL_INCIDENT_TEXT) is True


def test_guard_call_precedes_handle_admin_escort_completion_in_source():
    """Structural proof the guard is actually wired into the scan, not
    just defined and unused -- inspects the real source rather than
    re-executing the scan loop (which needs a live bridge SQLite session
    to drive), matching this repo's existing "SELECT-only proof"-style
    source-inspection tests."""
    import inspect
    import modules.bridge_poller as bp

    source = inspect.getsource(bp)
    guard_call = source.index("_is_system_generated_notification(otext)")
    handler_call = source.index("await handle_admin_escort_completion(")
    assert guard_call < handler_call, (
        "the system-notification guard must be checked before "
        "handle_admin_escort_completion() is ever called in the outgoing scan"
    )
