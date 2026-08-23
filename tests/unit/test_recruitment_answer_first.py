"""
Tests for recruitment turn-intent classification (2026-08-23).
See RECRUITMENT_REPLY_QUALITY_IMPLEMENTATION_PLAN_2026-08-23.md
section "Answer-First" + "Question Detection".

Classifies inbound recruitment turns into:
  - pure_question  (informational question, no intake value)
  - mixed          (intake value + informational question)
  - pure_intake    (just an intake answer / continuation)
  - unrelated      (empty / off-domain)
"""
from __future__ import annotations

import pytest

from modules.recruitment_flow import classify_turn_intent


class TestPureQuestion:
    """Turns that are only informational questions."""

    @pytest.mark.parametrize("text", [
        "বেতন কত?",
        "সিকিউরিটি পদে বেতন কতো হতে পারে",
        "কোথায় অফিস?",
        "আপনার অফিসের ঠিকানা কোথায়",
        "টাকা জামানত দিতে হবে?",
        "থাকা-খাওয়ার ব্যবস্থা আছে?",
        "Salary koto?",
        "Where is your office?",
        "How long is training?",
        "ভর্তি ফি কত?",
        "জয়েনিং ফি আছে?",
    ])
    def test_classifies_as_pure_question(self, text: str):
        assert classify_turn_intent(text, current_step="age") == "pure_question"


class TestMixed:
    """Turns that combine an intake value with a question."""

    def test_age_and_salary(self):
        assert classify_turn_intent(
            "আমার বয়স ২৮, বেতন কত?", current_step="age"
        ) == "mixed"

    def test_name_and_accommodation(self):
        assert classify_turn_intent(
            "নাম মোঃ করিম। থাকা খাওয়ার ব্যবস্থা আছে?",
            current_step="name",
        ) == "mixed"

    def test_age_arabic_and_salary(self):
        assert classify_turn_intent(
            "28, বেতন কত?", current_step="age"
        ) == "mixed"

    def test_phone_and_question(self):
        assert classify_turn_intent(
            "01912345678 কোথায় অফিস?", current_step="phone_confirm"
        ) == "mixed"


class TestPureIntake:
    """Turns that are only intake-field answers."""

    @pytest.mark.parametrize("text", [
        "২৫",
        "28",
        "২৮ বছর",
        "Md Alomgir miah",
        "হবিগঞ্জ সদর",
        "২",
        "Escort",
        "Security Guard",
        "3 বছর",
        "01912345678",
        "নতুন",
    ])
    def test_classifies_as_pure_intake(self, text: str):
        # current_step chosen to match the content's domain
        step = "name"
        if any(ch.isdigit() for ch in text):
            step = "age"
        if "বছর" in text or "বছরের" in text:
            step = "experience"
        if text.isdigit() and len(text) >= 4:
            step = "phone_confirm"
        if "জেলা" in text or "সদর" in text or "ঢাকা" in text:
            step = "area"
        if any(k in text for k in ["Escort", "Guard", "Officer", "Supervisor", "২", "৩"]):
            step = "job_preference"
        assert classify_turn_intent(text, current_step=step) == "pure_intake"


class TestUnrelated:
    """Empty or off-domain content."""

    @pytest.mark.parametrize("text", ["", "   ", "\n", None])
    def test_empty_is_unrelated(self, text):
        assert classify_turn_intent(text, current_step="age") == "unrelated"


class TestNoCurrentStep:
    """When there is no active intake step, even a question is just a question."""

    def test_pure_question_no_step(self):
        assert classify_turn_intent(
            "বেতন কত?", current_step=None
        ) == "pure_question"

    def test_value_without_step_does_not_become_mixed(self):
        # No current step → no step-format check → just a question turn
        assert classify_turn_intent(
            "২৮ বেতন কত?", current_step=None
        ) == "pure_question"


class TestBanglaAndAsciiDigits:
    """Number-format independence for age detection."""

    def test_bangla_age_with_question(self):
        assert classify_turn_intent(
            "বয়স ২৮ বেতন কত?", current_step="age"
        ) == "mixed"

    def test_ascii_age_with_question(self):
        assert classify_turn_intent(
            "Age 28 salary koto?", current_step="age"
        ) == "mixed"