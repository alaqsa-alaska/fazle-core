from pathlib import Path
from unittest.mock import AsyncMock, patch

from modules.recruitment_ai import (
    _RECRUITMENT_SOURCE,
    _SAFE_FALLBACK,
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
    # 2026-08-04 Owner Decision (supersedes the earlier "01958-122322 is
    # final authoritative" fact this assertion previously encoded — see
    # knowledge_base/05_workflows/whatsapp_reply_workflow.md's "Office
    # Contact Numbers — C-08 Conflict Resolved" entry and
    # management_decisions.md for the full record): 01958-122300 is now
    # the current, Owner-confirmed office contact number.
    assert "01958-122300" in contact
    assert "আর্থিক কারণে বাদ" not in office + escort + age


def test_recruitment_source_has_no_retired_conflicting_facts():
    source = _RECRUITMENT_SOURCE.read_text(encoding="utf-8")
    for retired_fact in (
        "৬টি পদে",
        "১৮ থেকে ৪৫",
        "সকাল ৯টা",
        # 01958-122300 REMOVED from this list 2026-08-04 -- it is no
        # longer a retired/conflicting fact, it is the current Owner-
        # confirmed office number (see test above). 122311/122327 remain
        # genuinely retired -- neither is part of the new confirmed set
        # (01958-122300/122301/122302/122303).
        "01958-122311",
        "01958-122327",
        "joining_fee_policy.txt",
    ):
        assert retired_fact not in source


def test_fee_policy_is_only_loaded_for_fee_questions():
    # 2026-08-21 (recruitment fee/source-of-truth conflict closure): the
    # fee section no longer states a specific figure at all -- it was
    # rewritten to the same canonical, no-specific-numbers policy text
    # already live in fazle_knowledge_base row b11_no_fee /
    # recruitment_business_rules.md's "Approved Deposit/Fee canonical
    # text" (2026-07-21 Owner decision) / modules.conversation_layer.
    # playbooks.CANONICAL_FACTS["fee"] -- the old "জয়েনিং ফি ৳৩,৫০০" figure
    # this test used to assert was present had already been superseded
    # elsewhere and was simply never propagated to this file. See
    # test_recruitment_fee_canonical_text.py for dedicated coverage.
    context = build_recruitment_source_context("জয়েনিং ফি কত?")
    assert "কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না" in context
    assert "৳৩,৫০০" not in context
    non_fee_context = build_recruitment_source_context("অফিস কখন খোলা?")
    assert "ঘুষ বা অবৈধ জামানত" not in non_fee_context


def test_fee_question_reply_with_unsupported_number_is_blocked():
    # 2026-08-21: the canonical fee policy states no specific figure, so
    # ANY numeric fee reply is now unsupported by the source context and
    # must fail closed -- a strengthening of the old behavior (which
    # allowed the taka-3,500 figure specifically only because that stale
    # figure used to be present in the source file).
    context = build_recruitment_source_context("জয়েনিং ফি কত?")
    reply = enforce_recruitment_reply_policy(
        "জয়েনিং ফি কত টাকা?",
        "জয়েনিং ফি ৳৩,৫০০।",
        context,
    )
    assert reply == _SAFE_FALLBACK


def test_fee_question_reply_matching_canonical_policy_is_allowed():
    context = build_recruitment_source_context("জয়েনিং ফি কত?")
    reply = enforce_recruitment_reply_policy(
        "জয়েনিং ফি কত টাকা?",
        "কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না, বেতন থেকে কিস্তিতে কাটা হয়।",
        context,
    )
    assert reply == "কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না, বেতন থেকে কিস্তিতে কাটা হয়।"


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


# ===========================================================================
# 2026-08-06 fix: "job"/"interested" (and Bangla equivalent) over-broad bare
# substring match -- confirmed live to false-trigger on an internal test
# message ("run job health_summary") and a generic business inquiry ("I am
# interested in learning more about your business"), both of which silently
# opened a recruitment funnel session on a real WhatsApp number. See
# knowledge_base/06_developer_system/recruitment_flow_system.md for the
# documented trigger-keyword table this fix also updates.
# ===========================================================================

def test_bare_job_and_interested_no_longer_false_trigger():
    """The two exact real-incident messages must NOT open a recruitment
    session anymore."""
    assert not is_recruitment_trigger("run job health_summary")
    assert not is_recruitment_trigger(
        "I am interested in learning more about your business"
    )
    assert not is_recruitment_trigger("job well done, thanks for the update")
    assert not is_recruitment_trigger("just checking, are you interested in coffee?")


def test_genuine_job_seeking_phrases_still_trigger_english():
    """Legitimate English job-seeking phrasing must still work -- "job" and
    "interested" remain triggers when they appear in job-shaped context."""
    assert is_recruitment_trigger("I need a job")
    assert is_recruitment_trigger("looking for work")
    assert is_recruitment_trigger("any job available?")
    assert is_recruitment_trigger("I want to apply for job")
    assert is_recruitment_trigger("I am interested in this job")
    assert is_recruitment_trigger("interested in the vacancy, how to apply?")
    assert is_recruitment_trigger("job chai")
    assert is_recruitment_trigger("ami ekta job korte chai")


def test_genuine_job_seeking_phrases_still_trigger_bangla():
    """Legitimate Bangla job-seeking phrasing must still work -- চাকরি/নিয়োগ/পদ
    remain bare keywords (unaffected by this fix), and the existing
    "কাজ করতে আগ্রহী" phrase still covers the Bangla আগ্রহী case."""
    assert is_recruitment_trigger("চাকরি করতে চাই")
    assert is_recruitment_trigger("চাকরিতে আগ্রহী")  # via bare চাকরি keyword
    assert is_recruitment_trigger("কাজ করতে আগ্রহী")  # pre-existing phrase
    assert is_recruitment_trigger("নিয়োগ চলছে কি?")
    assert is_recruitment_trigger("cv dibo")
    assert is_recruitment_trigger("joining kobe")


def test_existing_non_recruitment_behavior_unchanged():
    """Sanity check that nothing outside the job/interested change
    regressed -- reuses the pre-existing gate test's own assertions."""
    assert is_recruitment_trigger('অফিস কোথায়?')
    assert is_recruitment_trigger('যোগাযোগ নম্বর দিন')
    assert not is_recruitment_trigger('নয়টা ছয় মিনিট অফিসে কোন স্টাফ আসে নাই')
    assert not is_recruitment_trigger('এই কাজ শেষ হয়েছে')


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
