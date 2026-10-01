from types import SimpleNamespace

from modules.fazle_payroll_engine.bridge_alias import bridge_alias_for_phone


def _settings():
    return SimpleNamespace(
        bridge1_number="8801958122300",
        bridge2_number="8801880446111",
        bridge3_number="8801805529211",
    )


def test_configured_bridge_numbers_resolve_to_display_aliases():
    settings = _settings()
    assert bridge_alias_for_phone("01958122300", settings) == "Bridge1"
    assert bridge_alias_for_phone("+8801880446111", settings) == "Bridge2"
    assert bridge_alias_for_phone("01805529211", settings) == "Bridge3"


def test_arbitrary_phone_has_no_bridge_exception():
    assert bridge_alias_for_phone("8801700000001", _settings()) is None


def test_alias_lookup_does_not_depend_on_employee_or_admin_state():
    settings = SimpleNamespace(bridge1_number="8801958122300", bridge2_number="", bridge3_number="")
    assert bridge_alias_for_phone("8801958122300", settings) == "Bridge1"
