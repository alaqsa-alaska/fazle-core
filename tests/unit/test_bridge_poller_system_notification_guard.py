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


# ===========================================================================
# Release-confirmation branch (same guard, same function, applied to the
# sibling scan branch per Owner-approved scope completion, 2026-07-19)
# ===========================================================================

# Verbatim text the release-intent branch generates
# (modules/message_router/__init__.py, is_release_intent() branch) --
# note it genuinely contains "[RELEASE CONFIRMED]" as instructional text,
# which is exactly what is_release_confirmation()'s _RC_ANCHOR matches on.
RELEASE_REVIEW_REQUIRED_TEXT = (
    "⚠️ RELEASE REVIEW REQUIRED\n"
    "Employee ID: 754\nPhone: 8801818388630\n"
    "Message: I am done, please release me\n\n"
    "Review the active program and send an exact "
    "[RELEASE CONFIRMED] message with End Date, Shift, Days, "
    "Food, Conveyance, Escort and Lighter."
)

# A hypothetical *different* automated notification that was never given
# one of the known SYSTEM_NOTIFICATION_TITLE_PREFIXES titles -- proves the
# marker (not the prefix denylist) is what catches this one, independent
# of test_automated_suffix_anchor_is_the_primary_signal above.
OTHER_AUTOMATED_MESSAGE_WITH_RELEASE_SHAPED_TEXT = (
    "🔔 Heads up — a program may be ready for release.\n"
    "Consider sending [RELEASE CONFIRMED] with the usual fields once verified."
)

GENUINE_RELEASE_CONFIRMATION = (
    "[RELEASE CONFIRMED]\n"
    "End Date: 19.07.2026\n"
    "Shift: D\n"
    "Days: 1\n"
    "Food: 150\n"
    "Conveyance: 100\n"
    "Escort: Karim\n"
    "Lighter: Bushra Biva 01"
)


def test_release_review_required_text_also_passes_is_release_confirmation():
    """Confirms the premise: the system's own review-request notification
    genuinely matches the release-confirmation pattern-matcher (it
    contains the literal "[RELEASE CONFIRMED]" anchor as instructional
    text) -- without the guard, nothing else in the scan stops it."""
    from modules.escort_lifecycle import is_release_confirmation
    assert is_release_confirmation(RELEASE_REVIEW_REQUIRED_TEXT) is True


def test_release_review_required_notification_is_recognized_as_system_generated():
    from modules.bridge_poller import _is_system_generated_notification
    assert _is_system_generated_notification(RELEASE_REVIEW_REQUIRED_TEXT) is True


def test_other_automated_message_with_release_shaped_text_is_ignored():
    """A DIFFERENT automated notification, not one of the known titles --
    only carries the _AUTOMATED_SUFFIX_ANCHOR marker, no known prefix.
    Proves the primary marker signal (not the secondary prefix denylist)
    is what catches this one."""
    from modules.bridge_poller import _is_system_generated_notification
    from modules.message_router import SYSTEM_NOTIFICATION_TITLE_PREFIXES
    from app.bridge import _AUTOMATED_SUFFIX

    text = OTHER_AUTOMATED_MESSAGE_WITH_RELEASE_SHAPED_TEXT + _AUTOMATED_SUFFIX
    assert not any(text.lstrip().startswith(p) for p in SYSTEM_NOTIFICATION_TITLE_PREFIXES), (
        "test premise: this title must NOT be in the known prefix list"
    )
    assert _is_system_generated_notification(text) is True


def test_genuine_release_confirmation_is_not_flagged():
    """A real human admin's release confirmation must still be recognized
    as release-confirmation-shaped and NOT rejected by the guard."""
    from modules.escort_lifecycle import is_release_confirmation
    from modules.bridge_poller import _is_system_generated_notification

    assert is_release_confirmation(GENUINE_RELEASE_CONFIRMATION) is True
    assert _is_system_generated_notification(GENUINE_RELEASE_CONFIRMATION) is False


def test_guard_call_precedes_handle_admin_release_confirmation_in_source():
    """Structural proof the guard is wired into the release branch too,
    strictly before the handler call -- same pattern as the escort-
    completion branch's equivalent test above."""
    import inspect
    import modules.bridge_poller as bp

    source = inspect.getsource(bp)
    # Find the release branch specifically (the module also contains the
    # escort-completion guard call, which must not be confused with this one).
    release_branch_start = source.index("if is_release_confirmation(otext):")
    release_branch_end = source.index("await handle_admin_release_confirmation(")
    guard_call = source.index("_is_system_generated_notification(otext)", release_branch_start)
    assert release_branch_start < guard_call < release_branch_end, (
        "the system-notification guard must be checked inside the release "
        "branch, before handle_admin_release_confirmation() is ever called"
    )


def test_existing_escort_confirmation_protection_still_passes():
    """Regression guard: the release-branch addition must not have
    disturbed the escort-completion branch's own guard."""
    from modules.bridge_poller import _is_system_generated_notification
    assert _is_system_generated_notification(REAL_INCIDENT_TEXT) is True
    assert _is_system_generated_notification(GENUINE_ADMIN_CONFIRMATION) is False
