"""Stage G1/G27 (2026-08-28) -- modules.authority_lanes.resolve_authority_lane()."""
from __future__ import annotations

import pytest

from modules.authority_lanes import (
    resolve_authority_lane, LaneContext,
    AUTONOMOUS_OPERATIONAL, OWNER_SUPERVISED_DRAFT, TRUSTED_ADMIN_EXECUTION,
    AUTONOMOUS_MONITORING, GATED_SYSTEM_MODIFICATION,
)


class TestRecruitmentAndEmployeeAutonomy:
    def test_candidate_is_autonomous_operational(self):
        d = resolve_authority_lane(LaneContext(identity_role="candidate"))
        assert d.lane == AUTONOMOUS_OPERATIONAL

    @pytest.mark.parametrize("role", ["employee", "escort", "security_guard", "supervisor"])
    def test_employee_roles_are_autonomous_operational(self, role):
        d = resolve_authority_lane(LaneContext(identity_role=role))
        assert d.lane == AUTONOMOUS_OPERATIONAL


class TestClientAndOfficeSupervised:
    @pytest.mark.parametrize("role", ["vip_client", "security_service_client", "client_escort_buyer", "escort_client", "repeat_client"])
    def test_client_roles_are_supervised_draft(self, role):
        d = resolve_authority_lane(LaneContext(identity_role=role))
        assert d.lane == OWNER_SUPERVISED_DRAFT

    @pytest.mark.parametrize("role", ["internal_coordinator", "internal_operation", "employee_internal"])
    def test_office_internal_roles_are_supervised_draft(self, role):
        d = resolve_authority_lane(LaneContext(identity_role=role))
        assert d.lane == OWNER_SUPERVISED_DRAFT


class TestSafeDefaults:
    @pytest.mark.parametrize("role", ["unknown", "family", "family_ceo", "friend_relative", "vendor", "political_contact", "government_contact", "personal_business", "some_future_role"])
    def test_unclassified_roles_default_to_supervised_never_autonomous(self, role):
        """Stage G3: unknown/ambiguous identity must not receive
        autonomous treatment."""
        d = resolve_authority_lane(LaneContext(identity_role=role))
        assert d.lane == OWNER_SUPERVISED_DRAFT

    def test_unknown_identity_never_reaches_trusted_admin_execution(self):
        d = resolve_authority_lane(LaneContext(identity_role="unknown", sender_is_admin_number=False, channel_is_trusted_admin=False))
        assert d.lane != TRUSTED_ADMIN_EXECUTION


class TestGroupNeverTrustedAdmin:
    def test_group_conversation_is_always_supervised_even_with_admin_role(self):
        """Stage G6's explicit rule: GROUP MESSAGE != TRUSTED ADMIN
        COMMAND, even if the participant resolves as identity_role='admin'
        and sender_is_admin_number/channel_is_trusted_admin are both True."""
        d = resolve_authority_lane(LaneContext(
            identity_role="admin", is_group=True,
            sender_is_admin_number=True, channel_is_trusted_admin=True,
        ))
        assert d.lane == OWNER_SUPERVISED_DRAFT
        assert d.lane != TRUSTED_ADMIN_EXECUTION

    def test_group_conversation_with_client_role_still_supervised(self):
        d = resolve_authority_lane(LaneContext(identity_role="security_service_client", is_group=True))
        assert d.lane == OWNER_SUPERVISED_DRAFT


class TestInternalEchoNeverTrustedAdmin:
    @pytest.mark.parametrize("actor_type", ["system", "hermes", "automation"])
    def test_internal_actor_types_never_trusted_admin(self, actor_type):
        d = resolve_authority_lane(LaneContext(
            identity_role="admin", actor_type=actor_type,
            sender_is_admin_number=True, channel_is_trusted_admin=True,
        ))
        assert d.lane != TRUSTED_ADMIN_EXECUTION
        assert d.lane == OWNER_SUPERVISED_DRAFT


class TestTrustedAdminExecution:
    def test_verified_admin_on_trusted_channel_gets_execution_lane(self):
        d = resolve_authority_lane(LaneContext(
            identity_role="admin", is_group=False, actor_type="external_user",
            sender_is_admin_number=True, channel_is_trusted_admin=True,
        ))
        assert d.lane == TRUSTED_ADMIN_EXECUTION

    def test_admin_role_without_channel_match_is_not_execution_lane(self):
        d = resolve_authority_lane(LaneContext(
            identity_role="admin", sender_is_admin_number=True, channel_is_trusted_admin=False,
        ))
        assert d.lane != TRUSTED_ADMIN_EXECUTION

    def test_admin_role_without_phone_match_is_not_execution_lane(self):
        d = resolve_authority_lane(LaneContext(
            identity_role="admin", sender_is_admin_number=False, channel_is_trusted_admin=True,
        ))
        assert d.lane != TRUSTED_ADMIN_EXECUTION


class TestSystemModificationAndMonitoring:
    @pytest.mark.parametrize("action_type", [
        "file_mutation", "git_commit", "deploy", "migration", "restart",
        "production_write", "opencode_dispatch",
    ])
    def test_infra_action_types_are_always_gated_regardless_of_role(self, action_type):
        d = resolve_authority_lane(LaneContext(identity_role="admin", action_type=action_type))
        assert d.lane == GATED_SYSTEM_MODIFICATION

    def test_business_action_types_are_not_gated_system_modification(self):
        """Stage F's business_reply_draft/business_record_review action
        types must not be misclassified as infra-gated."""
        d = resolve_authority_lane(LaneContext(identity_role="candidate", action_type="business_reply_draft"))
        assert d.lane != GATED_SYSTEM_MODIFICATION

    def test_explicit_monitoring_domain(self):
        d = resolve_authority_lane(LaneContext(domain="system_monitoring"))
        assert d.lane == AUTONOMOUS_MONITORING


class TestAuditability:
    def test_every_decision_carries_a_nonempty_reason(self):
        for role in ("candidate", "employee", "security_service_client", "internal_operation", "unknown", "admin"):
            d = resolve_authority_lane(LaneContext(identity_role=role))
            assert d.reason and isinstance(d.reason, str)

    def test_decision_never_infers_from_message_text(self):
        """Structural proof: LaneContext has no 'text'/'message' field at
        all -- the resolver literally cannot see message content."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(LaneContext)}
        assert "text" not in field_names
        assert "message" not in field_names
        assert "message_body" not in field_names
