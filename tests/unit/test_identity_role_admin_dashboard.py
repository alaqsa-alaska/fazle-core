from pathlib import Path


DASHBOARD = (Path(__file__).parents[2] / "app" / "static" / "dashboard.html").read_text()


def test_identity_role_dashboard_reuses_session_and_has_safe_controls():
    assert 'data-tab="identity-role"' in DASHBOARD
    assert 'id="tab-identity-role"' in DASHBOARD
    assert 'X-Internal-Key' in DASHBOARD
    assert '/admin/identity-role/search' in DASHBOARD
    assert 'Assign' in DASHBOARD and 'Revoke' in DASHBOARD
    assert 'Add link' in DASHBOARD and 'Relevant conversations' in DASHBOARD
    assert 'Identity–Role audit history' in DASHBOARD
    assert 'legacy_phone_blocks' in DASHBOARD or 'legacy_phone_block' in DASHBOARD
    assert 'Place hold' in DASHBOARD and 'Release hold' in DASHBOARD
    assert 'does not send' in DASHBOARD or 'no dispatch' in DASHBOARD
    assert 'send now' not in DASHBOARD.lower()
    assert 'retry now' not in DASHBOARD.lower()
