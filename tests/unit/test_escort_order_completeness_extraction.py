"""
2026-07-22: modules.message_router._is_strict_escort_order_text() /
_looks_like_slip_or_complaint() / _has_client_supplied_escort_assignment()
were extracted to modules.escort as public functions (is_order_complete(),
looks_like_slip_or_complaint(), has_client_supplied_escort_assignment()) so
modules.route_b_handoff can reuse the same canonical check. message_router's
old names now delegate. This test proves the delegation is wired correctly
and behavior is unchanged, using the same sample texts already exercised in
test_escort_module.py.
"""
from __future__ import annotations

ORDER_TEXT = (
    "Mother Vessel: MV GOLDEN STAR\n"
    "02) MV SULTANA-02\n"
    "01711-273432\n"
    "Destination: N.Gonj\n"
    "Capacity: 1200"
)
SLIP_TEXT = "Release হয়েছে, food bill 500 taka, conveyance লাগবে"
CASUAL_TEXT = "ভাই কেমন আছেন?"


def test_message_router_delegates_to_escort_is_order_complete():
    import modules.escort as escort
    import modules.message_router as router

    assert router._is_strict_escort_order_text is escort.is_order_complete


def test_message_router_delegates_slip_and_assignment_helpers():
    import modules.escort as escort
    import modules.message_router as router

    assert router._looks_like_slip_or_complaint is escort.looks_like_slip_or_complaint
    assert router._has_client_supplied_escort_assignment is escort.has_client_supplied_escort_assignment


def test_order_complete_true_for_genuine_order():
    from modules.escort import is_order_complete

    assert is_order_complete(ORDER_TEXT) is True


def test_order_complete_false_for_slip_text():
    from modules.escort import is_order_complete

    assert is_order_complete(SLIP_TEXT) is False


def test_order_complete_false_for_casual_text():
    from modules.escort import is_order_complete

    assert is_order_complete(CASUAL_TEXT) is False


def test_message_router_looks_like_escort_order_unchanged():
    """The pre-existing public wrapper message_router uses internally
    (Tier 9's safety net) must still return identical results post-extraction."""
    from modules.message_router import _looks_like_escort_order

    assert _looks_like_escort_order(ORDER_TEXT) is True
    assert _looks_like_escort_order(SLIP_TEXT) is False
    assert _looks_like_escort_order(CASUAL_TEXT) is False
