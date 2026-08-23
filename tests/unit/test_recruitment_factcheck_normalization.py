"""
Tests for recruitment fact-check normalization (2026-08-23).
See RECRUITMENT_REPLY_QUALITY_IMPLEMENTATION_PLAN_2026-08-23.md
section "Fact-Check False Positives".

When recruitment_factcheck_normalized=True (in app/config.py),
_recruitment_reply_matches_source() should accept equivalent formatting
of APPROVED source-of-truth while still rejecting fabricated numbers /
places.

Tests that exercise the normalized path monkeypatch the Settings to
force recruitment_factcheck_normalized=True; the legacy path is
exercised separately.
"""
from __future__ import annotations

import pytest

from modules.hermes_dispatch import (
    _normalize_number_token,
    _normalize_place_token,
    _recruitment_reply_matches_source,
)


@pytest.fixture
def normalized_factcheck(monkeypatch):
    """Force recruitment_factcheck_normalized=True by monkeypatching
    get_settings() to return a Settings object with that field True.
    Settings is a pydantic model — class-level monkeypatching fails
    because pydantic rejects unknown attrs — so we patch the result
    of get_settings() instead. Each call returns a fresh object whose
    field is set True. _recruitment_reply_matches_source() reads
    get_settings() per-call (already coded that way), so the patched
    value is picked up automatically.
    """
    import app.config as _cfg

    class _FakeSettings:
        recruitment_factcheck_normalized = True
        # Other settings read by the function under test (none currently,
        # but kept for forward compatibility).
        recruitment_answer_first_enabled = False
        recruitment_structured_logging_enabled = False

    monkeypatch.setattr(_cfg, "get_settings", lambda: _FakeSettings())
    yield
    # monkeypatch auto-restores


# An excerpt of recruitment_source_of_truth.txt content as it would be
# loaded by build_recruitment_context(). Used as the kb_context input
# across the SUPPORTED and REJECTED test classes below.
SOURCE_OF_TRUTH = (
    "অফিস ঠিকানা: আগ্রপাড়া, Victoria Gate #1, Khokoner Building, "
    "AK Khan Mor, Chittagong। "
    "সর্বশেষ ও একমাত্র recruitment WhatsApp/যোগাযোগ নম্বর: 01958-122300। "
    "সাধারণ বয়সসীমা ১৮–৫৫ বছর। "
    "training period: ৪৫ দিন। "
    "training honorarium: ৳১০,০০০–১৫,০০০। "
    "প্রশিক্ষণকাল (৪৫ দিন): ১০,০০০ – ১৫,০০০ টাকা। "
    "probation salary: ৳১০,০০০–১৩,০০০। "
    "সিকিউরিটি গার্ড permanent salary ৳২৪,৭০০।"
)


# ─── Number-token normalization unit tests ────────────────────────────────


class TestNormalizeNumberToken:
    def test_bangla_with_comma(self):
        assert _normalize_number_token("১০,০০০") == "10000"

    def test_ascii_with_comma(self):
        assert _normalize_number_token("10,000") == "10000"

    def test_bangla_with_en_dash(self):
        assert _normalize_number_token("১০,০০০–১৫,০০০") == "1000015000"

    def test_phone_hyphenated(self):
        assert _normalize_number_token("01958-122300") == "01958122300"

    def test_phone_no_hyphen(self):
        assert _normalize_number_token("01958122300") == "01958122300"

    def test_phone_with_trailing_dot(self):
        # Trailing punctuation stripped (already via _NON_DIGIT_RE)
        assert _normalize_number_token("01958-122300.") == "01958122300"

    def test_single_digit(self):
        assert _normalize_number_token("9") == "9"

    def test_empty(self):
        assert _normalize_number_token("") == ""

    def test_pure_punctuation(self):
        assert _normalize_number_token("--") == ""


# ─── Place-token normalization unit tests ─────────────────────────────────


class TestNormalizePlaceToken:
    def test_aggrapara_bare(self):
        assert _normalize_place_token("আগ্রপাড়া") == "আগ্রপাড়া"

    def test_aggrapara_with_locative(self):
        assert _normalize_place_token("আগ্রপাড়ায়") == "আগ্রপাড়া"

    def test_aggrapara_genitive(self):
        assert _normalize_place_token("আগ্রপাড়ার") == "আগ্রপাড়া"

    def test_uppercase(self):
        # Lowercase normalization for ASCII comparisons
        assert _normalize_place_token("AK KHAN MOR") == "ak khan mor"

    def test_empty(self):
        assert _normalize_place_token("") == ""


# ─── Full fact-check end-to-end tests ─────────────────────────────────────


class TestSupportedFactCheck:
    """Approved facts with equivalent formatting MUST validate."""

    def test_phone_with_hyphen(self, normalized_factcheck):
        # Source has 01958-122300 (hyphenated); reply has same.
        reply = "আমাদের অফিসে কল করুন: 01958-122300।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_phone_no_hyphen(self, normalized_factcheck):
        # Source: 01958-122300 → tokens [1, (from "Victoria Gate #1,"),
        # 01958-122300] → normalized {1, 01958122300}.
        # Reply: 01958122300 → tokens [01958122300] → normalized
        # {01958122300}. Subset holds → MATCHES. The hyphenated source
        # token normalizes identically to the no-hyphen reply token,
        # so the normalization handles this case correctly.
        reply = "আমাদের অফিসে কল করুন: 01958122300।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_salary_bangla_digits(self, normalized_factcheck):
        # Source has ১০,০০০ – ১৫,০০০ → tokens [১০,০০০, ১৫,০০০] → normalized
        # {10000, 15000}. Reply has ১০,০০০ → normalized {10000}. Subset ✓
        reply = "প্রশিক্ষণকাল বেতন ১০,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_salary_ascii_digits(self, normalized_factcheck):
        # Source has Bangla digits → normalized 10000/15000. Reply has
        # ASCII 10,000 → normalized 10000. Subset ✓
        reply = "প্রশিক্ষণকাল বেতন 10,000 টাকা।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_age_limit_bangla(self, normalized_factcheck):
        reply = "বয়সসীমা ১৮–৫৫ বছর।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_age_limit_ascii(self, normalized_factcheck):
        reply = "বয়সসীমা 18-55 বছর।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_training_duration_bangla(self, normalized_factcheck):
        reply = "প্রশিক্ষণকাল ৪৫ দিন।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_office_address(self, normalized_factcheck):
        reply = "আমাদের অফিস আগ্রপাড়া, চট্টগ্রামে অবস্থিত।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_office_address_with_inflection(self, normalized_factcheck):
        reply = "আপনাকে আগ্রপাড়ায় আসতে হবে।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True


class TestRejectedFactCheck:
    """Fabricated numbers / places MUST still be rejected."""

    def test_fabricated_salary(self, normalized_factcheck):
        reply = "বেতন ৯৯,৯৯৯ টাকা।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is False

    def test_fabricated_phone(self, normalized_factcheck):
        reply = "আমাদের নম্বর 01700-000000।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is False

    def test_fabricated_training_duration(self, normalized_factcheck):
        reply = "প্রশিক্ষণকাল ৬০ দিন।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is False

    def test_fabricated_fee_amount(self, normalized_factcheck):
        reply = "ভর্তি ফি ৫,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is False

    def test_fabricated_unsupported_place_keyword(self, normalized_factcheck):
        # _PLACE_RE matches `<chars><suffix>[<inflection>]`. The chars
        # class `[\wঀ-৿-]{2,}` does NOT include space, so "খুলনা জেলায়"
        # (with a space) wouldn't trigger the regex at all. Use the
        # suffixed form without a space — খুলনাজেলায় — which is the
        # closest a candidate might write that the regex WILL catch.
        reply = "আমাদের অফিস খুলনাজেলায় অবস্থিত।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is False


class TestEmptyKbContext:
    """No kb_context means no fact should have been stated at all."""

    def test_any_number_rejected(self, normalized_factcheck):
        reply = "বেতন ১০,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, "") is False

    def test_any_phone_rejected(self, normalized_factcheck):
        reply = "নম্বর 01958-122300।"
        assert _recruitment_reply_matches_source(reply, "") is False