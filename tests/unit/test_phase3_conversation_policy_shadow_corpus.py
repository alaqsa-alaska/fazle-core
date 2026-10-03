"""
Phase 3 (Owner directive 2026-08-30) — Hermes Conversation Policy SHADOW engine.

Representative corpus. Every case asserts the PROPOSED shadow decision only —
nothing here touches a live reply. Promotion to live authority is a separate
owner-gated step after this corpus is reviewed.

Corpus coverage (owner's required list):
  1. recruitment progression
  2. goodbye after recruitment
  3. repeated same question
  4. genuine clarification after a previous answer
  5. employee instruction
  6. employee motivation / praise
  7. client order / request
  8. escort_worker vs escort_client
  9. payment topic then unrelated duty topic
 10. general knowledge question
 11. technical question requiring plain Bengali
"""
from __future__ import annotations

import pytest

from modules.conversation_policy import (
    compute_shadow_topic_decision, is_terminal_message, detect_topic,
    relationship_for_role, tone_for_relationship,
    STATUS_OPEN, STATUS_WAITING, STATUS_COMPLETED, STATUS_CLOSED,
    REL_CLIENT, REL_EMPLOYEE, TONE_APNI, TONE_TUMI,
)


# 1 ── recruitment progression ─────────────────────────────────────────────
def test_recruitment_progression_advances_one_step_and_asks_one_question():
    d = compute_shadow_topic_decision(
        text="সিকিউরিটি গার্ড পদে চাকরি করতে চাই, বেতন কত?",
        role="candidate",
        active_topic="recruitment", topic_status="open",
        information_given={"recruit:role_interest": True},
    )
    assert d.detected_topic == "recruitment"
    assert d.relationship_type == REL_EMPLOYEE  # candidate handled with tumi/manager voice
    assert d.proposed_topic_status in (STATUS_OPEN, STATUS_WAITING)
    assert d.proposed_next_best_action.startswith("recruitment_advance:")
    assert d.proposed_terminal is False
    # exactly one next step, not a paragraph dump
    assert d.proposed_followup_question is None or isinstance(d.proposed_followup_question, str)


def test_recruitment_all_stages_done_marks_completed_and_stops():
    given = {f"recruit:{s}": True for s in
             ["role_interest", "duties", "salary_conditions", "suitability",
              "documents", "office_location", "invitation"]}
    d = compute_shadow_topic_decision(
        text="ঠিক আছে, আমি অফিসে আসব",
        role="candidate", active_topic="recruitment", topic_status="waiting",
        information_given=given,
    )
    assert d.proposed_topic_status == STATUS_COMPLETED
    assert d.proposed_next_best_action == "recruitment_topic_complete_stop"
    assert d.proposed_followup_question is None


# 2 ── goodbye after recruitment ──────────────────────────────────────────
def test_goodbye_after_recruitment_closes_topic_no_restart_no_question():
    d = compute_shadow_topic_decision(
        text="ঠিক আছে ধন্যবাদ, খোদা হাফেজ",
        role="candidate", active_topic="recruitment", topic_status="waiting",
        information_given={"recruit:role_interest": True, "recruit:duties": True},
    )
    assert d.proposed_terminal is True
    assert d.proposed_topic_status == STATUS_CLOSED
    assert d.proposed_followup_question is None
    assert d.proposed_next_best_action == "send_one_brief_closing"
    assert "no recruitment restart" in d.reason


# 3 ── repeated same question ─────────────────────────────────────────────
def test_repeated_question_gets_gentle_reminder_not_full_reanswer():
    d = compute_shadow_topic_decision(
        text="আচ্ছা বেতন কত হবে বললেন যেন?",
        role="candidate", active_topic="recruitment", topic_status="open",
        information_given={"q:pay_amount": "answered earlier", "recruit:role_interest": True},
    )
    assert d.is_repeated_question is True
    assert d.proposed_next_best_action == "gentle_reminder_reference_previous_answer"
    assert d.proposed_followup_question is None


# 4 ── genuine clarification after a previous answer ──────────────────────
def test_clarification_is_not_flagged_as_repeat():
    # asks about a DIFFERENT aspect (documents), pay was the previously answered one
    d = compute_shadow_topic_decision(
        text="কী কী কাগজপত্র লাগবে সেটা একটু বুঝিয়ে বলবেন?",
        role="candidate", active_topic="recruitment", topic_status="open",
        information_given={"q:pay_amount": "answered earlier", "recruit:role_interest": True},
    )
    assert d.is_repeated_question is False
    assert d.proposed_next_best_action != "gentle_reminder_reference_previous_answer"


# 5 ── employee instruction ──────────────────────────────────────────────
def test_employee_instruction_uses_manager_tumi_voice():
    d = compute_shadow_topic_decision(
        text="আজকে ডিউটি কোথায় আমার?",
        role="escort", active_topic=None, topic_status=None,
    )
    assert d.relationship_type == REL_EMPLOYEE
    assert d.proposed_tone == TONE_TUMI
    assert d.detected_topic in ("escort_assignment", "general")


# 6 ── employee motivation / praise ──────────────────────────────────────
def test_employee_praise_still_manager_voice_no_forced_question():
    d = compute_shadow_topic_decision(
        text="স্যার কাজটা শেষ করে ফেলেছি",
        role="employee", active_topic="duty", topic_status="open",
    )
    assert d.proposed_tone == TONE_TUMI
    assert d.proposed_terminal is False
    # answering/acknowledging, not mechanically appending a question
    assert d.proposed_next_best_action in ("answer_and_stop", "answer_question")


# 7 ── client order / request ────────────────────────────────────────────
def test_client_order_uses_respectful_apni_voice():
    d = compute_shadow_topic_decision(
        text="M.V Ocean Pride এর জন্য ৪ জন এসকর্ট লাগবে",
        role="client_escort_buyer", active_topic=None, topic_status=None,
    )
    assert d.relationship_type == REL_CLIENT
    assert d.proposed_tone == TONE_APNI
    assert d.detected_topic == "escort_order"


# 8 ── escort_worker vs escort_client ────────────────────────────────────
def test_escort_worker_and_client_get_different_relationship_and_domain():
    worker = compute_shadow_topic_decision(
        text="কালকে কোন শিপে ডিউটি?", role="escort",
    )
    client = compute_shadow_topic_decision(
        text="এসকর্ট সার্ভিস লাগবে, রেট কত?", role="escort_client",
    )
    assert worker.relationship_type == REL_EMPLOYEE
    assert worker.proposed_tone == TONE_TUMI
    assert client.relationship_type == REL_CLIENT
    assert client.proposed_tone == TONE_APNI
    assert worker.detected_topic != "escort_order"
    assert client.detected_topic == "escort_order"


# 9 ── payment topic then unrelated duty topic ───────────────────────────
def test_new_duty_topic_does_not_reopen_closed_payment_topic():
    d = compute_shadow_topic_decision(
        text="আজকে ডিউটি কোথায়?",
        role="escort",
        active_topic="payment", topic_status="closed",
        information_given={"q:pay_amount": "paid"},
    )
    assert d.detected_topic == "escort_assignment"
    assert d.prior_topic == "payment"
    assert d.reopened_other_topic is False
    assert "prior stays closed" in d.reason
    assert d.proposed_topic_status == STATUS_OPEN


# 10 ── general knowledge question ───────────────────────────────────────
def test_general_knowledge_enables_general_ai_mode_no_business_followup():
    d = compute_shadow_topic_decision(
        text="বাংলাদেশের রাজধানী কবে ঢাকা হয়েছিল?",
        role="unknown",
    )
    assert d.proposed_general_ai_mode is True
    assert d.detected_topic == "general_knowledge"
    assert d.proposed_followup_question is None
    assert d.proposed_next_best_action == "answer_as_general_assistant_plain_bengali"


# 11 ── technical question requiring plain Bengali ───────────────────────
def test_technical_question_answered_in_plain_bengali_mode():
    d = compute_shadow_topic_decision(
        text="API মানে কি? কীভাবে কাজ করে একটু ব্যাখ্যা করেন",
        role="admin",
    )
    assert d.proposed_general_ai_mode is True or d.detected_topic in ("technical", "general_knowledge")
    assert d.proposed_next_best_action in (
        "answer_as_general_assistant_plain_bengali", "answer_and_stop", "answer_question",
    )


# ── unit-level guards ───────────────────────────────────────────────────
def test_terminal_detection_ignores_goodbye_with_a_real_question():
    assert is_terminal_message("খোদা হাফেজ") is True
    assert is_terminal_message("ধন্যবাদ, আর কিছু জানার নেই") is True
    assert is_terminal_message("ধন্যবাদ, কিন্তু অফিসের ঠিকানাটা আরেকবার দিবেন কোথায় আসব?") is False


def test_relationship_and_tone_maps():
    assert relationship_for_role("vip_client") == REL_CLIENT
    assert relationship_for_role("supervisor") == REL_EMPLOYEE
    assert tone_for_relationship(REL_CLIENT) == TONE_APNI
    assert tone_for_relationship(REL_EMPLOYEE) == TONE_TUMI
