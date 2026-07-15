"""Unit tests — modules/bridge_poller._requires_legacy_workflow

Regression coverage for the 2026-07-14 silent-drop incident: two real
escort-order messages from an ESCORT_CLIENT_PHONES-listed number
("Mv.Wan minh 01/soyabean ext ... 1.Ak Sarkar 5-01730840247 local 800",
"Mv.Alondra/soyabean ext ... 1.Abdullah sayad-01725964055") never reached
modules.escort because the old literal-token gate ("m.v", "mv ") did not
match the client's actual "Mv." (no space) formatting, so
SOCIAL_AUTO_REPLY_SINGLE_ENGINE diverted them into the (disabled) social
pipeline instead of the legacy router. Root cause: escort_business_rules.md
states the mobile number is the primary parsing signal, independent of
MV-label spacing/punctuation/case — the gate did not follow that rule.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestRequiresLegacyWorkflow:
    def test_dropped_message_1_now_routes_to_legacy(self):
        from modules.bridge_poller import _requires_legacy_workflow

        text = "Mv.Wan minh 01/soyabean ext\n*A/c.kazi firm*\n1.Ak Sarkar 5-01730840247 local 800 "
        assert _requires_legacy_workflow(text, {}) is True

    def test_dropped_message_2_now_routes_to_legacy(self):
        from modules.bridge_poller import _requires_legacy_workflow

        text = "Mv.Alondra/soyabean ext\n*For oute\n*A/c.Aci godrej*\n1.Abdullah sayad-01725964055 "
        assert _requires_legacy_workflow(text, {}) is True

    @pytest.mark.parametrize("text", [
        "call me at 01712345678 please",
        "contact +880 1712-345678",
        "8801712345678 is the number",
        "no. 01712-345678 thanks",
    ])
    def test_any_bd_mobile_format_routes_to_legacy(self, text):
        from modules.bridge_poller import _requires_legacy_workflow

        assert _requires_legacy_workflow(text, {}) is True

    def test_existing_keyword_tokens_still_route_to_legacy(self):
        from modules.bridge_poller import _requires_legacy_workflow

        assert _requires_legacy_workflow("Mother Vessel: MV GOLDEN STAR", {}) is True
        assert _requires_legacy_workflow("release confirmed for the escort", {}) is True

    def test_message_with_no_mobile_and_no_keyword_does_not_route_to_legacy(self):
        from modules.bridge_poller import _requires_legacy_workflow

        assert _requires_legacy_workflow("Hello, how are you today?", {}) is False

    def test_image_message_still_routes_to_legacy(self):
        from modules.bridge_poller import _requires_legacy_workflow

        assert _requires_legacy_workflow("", {"media_type": "image"}) is True
