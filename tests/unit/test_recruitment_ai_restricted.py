from pathlib import Path
from unittest.mock import AsyncMock, patch

from modules.recruitment_ai import (
    _RECRUITMENT_SOURCE,
    build_recruitment_source_context,
    enforce_recruitment_reply_policy,
)
from modules.recruitment_flow import (
    _parse_age,
    _parse_job_preference,
    get_active_session,
    is_recruitment_trigger,
    recruitment_eligibility,
)


def test_recruitment_source_is_inside_approved_ops_path():
    expected_parent = Path("/home/azim/core/resources/ops")
    assert _RECRUITMENT_SOURCE.parent == expected_parent
    assert _RECRUITMENT_SOURCE.name == "recruitment_source_of_truth.txt"


def test_recruitment_source_contains_final_authoritative_facts():
    office = build_recruitment_source_context("অফিস কখন খোলা?")
    escort = build_recruitment_source_context("Escort salary and age")
    age = build_recruitment_source_context("সাধারণ বয়সসীমা কত?")
    positions = build_recruitment_source_context("কতটি পদে নিয়োগ চলছে?")
    contact = build_recruitment_source_context("যোগাযোগ নম্বর কত?")
    assert "সকাল ১০টা থেকে বিকাল ৫টা" in office
    assert "৳১০,০০০–১৫,০০০" in escort
    assert "Mother Vessel" in escort
    assert "১৮–৫৫ বছর" in age
    assert "মোট ৯টি পদে নিয়োগ চলছে" in positions
    assert "01958-122322" in contact
    assert "আর্থিক কারণে বাদ" not in office + escort + age


def test_recruitment_source_has_no_retired_conflicting_facts():
    source = _RECRUITMENT_SOURCE.read_text(encoding="utf-8")
    for retired_fact in (
        "৬টি পদে",
        "১৮ থেকে ৪৫",
        "সকাল ৯টা",
        "01958-122300",
        "01958-122311",
        "01958-122327",
        "joining_fee_policy.txt",
    ):
        assert retired_fact not in source


def test_fee_policy_is_only_loaded_for_fee_questions():
    context = build_recruitment_source_context("জয়েনিং ফি কত?")
    assert "জয়েনিং ফি ৳৩,৫০০" in context
    non_fee_context = build_recruitment_source_context("অফিস কখন খোলা?")
    assert "জয়েনিং ফি ৳৩,৫০০" not in non_fee_context


def test_fee_question_allows_only_source_supported_amounts():
    context = build_recruitment_source_context("জয়েনিং ফি কত?")
    reply = enforce_recruitment_reply_policy(
        "জয়েনিং ফি কত টাকা?",
        "জয়েনিং ফি ৳৩,৫০০।",
        context,
    )
    assert reply == "জয়েনিং ফি ৳৩,৫০০।"


def test_non_fee_reply_is_cleaned_normally():
    context = build_recruitment_source_context("অফিস কখন খোলা?")
    reply = enforce_recruitment_reply_policy(
        "অফিস কখন খোলা?",
        "উত্তর: সকাল ১০টা থেকে বিকাল ৫টা।",
        context,
    )
    assert reply == "সকাল ১০টা থেকে বিকাল ৫টা।"


def test_unsupported_numeric_fact_is_blocked():
    context = build_recruitment_source_context("অফিস কখন খোলা?")
    reply = enforce_recruitment_reply_policy(
        "অফিস কখন খোলা?",
        "অফিস রাত ৭৭৭৭৭টা পর্যন্ত খোলা।",
        context,
    )
    assert reply == "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"


def test_unsupported_location_specific_hiring_claim_is_blocked():
    context = build_recruitment_source_context("চাকরি আছে?")
    reply = enforce_recruitment_reply_policy(
        "চরপাড়ার ঘাটে স্টাফ আসে নাই",
        "আমরা চরপাড়ার ঘাটে একজন Security Guard নিয়োগ করছি।",
        context,
    )
    assert reply == "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"


def test_recruitment_age_policy_and_positions():
    assert _parse_age("আমার বয়স 18") == 18
    assert _parse_age("আমার বয়স 55") == 55
    assert _parse_age("আমার বয়স 17") is None
    assert _parse_age("আমার বয়স 56") is None
    assert _parse_job_preference("Marketing Officer") == "Marketing Officer"
    assert _parse_job_preference("Ghat Supervisor") == "Ghat Supervisor"
    assert _parse_job_preference("Escort") == "Escort"
    assert _parse_job_preference("Survey Scout") == "Survey Scout"


def test_unknown_sender_location_and_contact_questions_enter_recruitment_gate():
    assert is_recruitment_trigger("অফিস কোথায়?")
    assert is_recruitment_trigger("office location please")
    assert is_recruitment_trigger("যোগাযোগ নম্বর দিন")
    assert is_recruitment_trigger("contact number please")
    assert is_recruitment_trigger("একটা কাজ চাই")
    assert not is_recruitment_trigger("নয়টা ছয় মিনিট অফিসে কোন স্টাফ আসে নাই")
    assert not is_recruitment_trigger("এই কাজ শেষ হয়েছে")


async def test_meta_recruitment_safe_mode_bypass_uses_queue_compatible_channel():
    from app.main import _handle_meta_message

    enqueue = AsyncMock(return_value=321)
    with (
        patch("app.main._process_message", new=AsyncMock(return_value=("reply", None))),
        patch("app.main._should_recruitment_autoreply", new=AsyncMock(return_value=True)),
        patch("app.main._force_draft_by_saved_contact_name", new=AsyncMock(return_value=False)),
        patch("app.main._save_message", new=AsyncMock()),
        patch("app.main.record_heartbeat", new=AsyncMock()),
        patch("app.main.fetch_one", new=AsyncMock(return_value=None)),
        patch("app.main._record_meta_delivery_state", new=AsyncMock()),
        patch("app.main._social_auto_reply_single_engine", return_value=False),
        patch("modules.outbound.enqueue", new=enqueue),
    ):
        await _handle_meta_message(
            {
                "id": "wamid.test-recruitment",
                "from": "01999999999",
                "type": "text",
                "text": {"body": "চাকরি করতে চাই"},
            },
            {},
        )

    assert enqueue.await_args.kwargs["source_bridge"] == "meta"
    assert len(enqueue.await_args.kwargs["source_bridge"]) <= 10
    assert len(enqueue.await_args.kwargs["idempotency_key"]) <= 80


async def test_stale_session_is_expired_and_cannot_route_recruitment():
    from datetime import datetime, timedelta, timezone

    stale = {
        "phone": "8801817025576",
        "funnel_stage": "collecting",
        "updated_at": datetime.now(timezone.utc) - timedelta(days=2),
    }
    with (
        patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=stale)),
        patch("modules.recruitment_flow.execute", new=AsyncMock()) as execute,
    ):
        assert await get_active_session("8801817025576") is None
        decision = await recruitment_eligibility(
            "8801817025576",
            "নয়টা ছয় মিনিট অফিসে কোন স্টাফ আসে নাই",
            role="unknown",
            intent="attendance",
        )
    assert decision["eligible"] is False
    assert decision["reason"] == "operational_intent"
    execute.assert_awaited()


async def test_fresh_session_followup_is_draft_only_not_autosend():
    from datetime import datetime, timezone

    fresh = {
        "phone": "8801999999999",
        "funnel_stage": "collecting",
        "updated_at": datetime.now(timezone.utc),
    }
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=fresh)):
        decision = await recruitment_eligibility(
            "8801999999999", "আপনি কে", role="unknown", intent="unknown",
        )
    assert decision["eligible"] is True
    assert decision["autosend"] is False


async def test_common_outbound_meta_alias_dispatches_to_meta_whatsapp():
    from modules.outbound import _send_with_channel

    sender = AsyncMock(return_value="meta-message-id")
    with patch("modules.social_auto_reply.send_queue._send_meta_whatsapp", new=sender):
        result = await _send_with_channel("meta", "8801999999999", "reply")

    assert result == "meta-message-id"
    sender.assert_awaited_once_with("8801999999999", "reply")
