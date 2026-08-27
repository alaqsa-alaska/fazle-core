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
    _candidate_identity_numbers,
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
    # 2026-08-27, Owner directive: canonical office-place spelling updated
    # to "আগ্রাপাড়া" -- the legacy spelling "আগ্রপাড়া" now normalizes TO
    # the canonical one (see _PLACE_SPELLING_ALIASES), not to itself.
    def test_aggrapara_bare(self):
        assert _normalize_place_token("আগ্রপাড়া") == "আগ্রাপাড়া"

    def test_aggrapara_with_locative(self):
        assert _normalize_place_token("আগ্রপাড়ায়") == "আগ্রাপাড়া"

    def test_aggrapara_genitive(self):
        assert _normalize_place_token("আগ্রপাড়ার") == "আগ্রাপাড়া"

    def test_canonical_spelling_normalizes_to_itself(self):
        assert _normalize_place_token("আগ্রাপাড়া") == "আগ্রাপাড়া"

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
        # 2026-08-27: phrasing changed from a compact "18-55" (single
        # ASCII-hyphen-joined token) to "18 to 55" (separate numbers).
        # Before the en-dash range-extraction fix, "18-55" and the
        # source's "১৮–৫৫" both (incorrectly) merged into one garbled
        # token and happened to match each other -- two wrongs, not a
        # real validation. After the fix, the source correctly splits
        # into two numbers (18, 55); a reply must cite them the same way
        # to validate -- ASCII-hyphen merging is intentionally preserved
        # for phone numbers (see TestRangeExtractionFix), so a compact
        # "18-55" is now (correctly) an unverifiable single number, not a
        # regression in the guard's safety.
        reply = "বয়সসীমা 18 to 55 বছর।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is True

    def test_age_limit_ascii_compact_hyphen_no_longer_validates(self, normalized_factcheck):
        # Documents the trade-off explicitly (see comment above): a
        # compact ASCII-hyphen range no longer accidentally validates.
        # This is a stricter outcome, not a weaker one -- the guard still
        # fails closed (blocks) rather than silently accepting it.
        reply = "বয়সসীমা 18-55 বছর।"
        assert _recruitment_reply_matches_source(reply, SOURCE_OF_TRUTH) is False

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


class TestRangeExtractionFix:
    """2026-08-27 fix (live-diagnosed during the post-intake outreach
    dry-run): _NUMBER_RE must split an en-dash-joined range into its
    individual bounds instead of merging them into one garbled token.

    UNSPACED_RANGE_SOURCE deliberately mirrors resources/ops/
    recruitment_source_of_truth.txt's EXACT formatting -- no space
    around the en-dash ("৳১০,০০০–১৩,০০০।"). This matters: SOURCE_OF_TRUTH
    above (used by the pre-existing salary/age tests in this file) also
    happens to include a SPACED variant ("১০,০০০ – ১৫,০০০") that already
    routed around the bug even before this fix (a space breaks the
    greedy regex match on its own), which is why those tests were
    passing in production while the bug was live. These tests use the
    real file's actual unspaced formatting so they would have genuinely
    failed before this fix and genuinely prove it now.
    """

    UNSPACED_RANGE_SOURCE = (
        "probation salary: ৳১০,০০০–১৩,০০০। "
        "probation সফলভাবে শেষ করে permanent position পেলে salary ৳২৪,৭০০ হতে পারে। "
        "training honorarium: ৳১০,০০০–১৫,০০০। "
        "probation period পদ ও মূল্যায়ন অনুযায়ী ৩–৬ মাস। "
        "সাধারণ বয়সসীমা ১৮–৫৫ বছর।"
    )

    def test_lower_bound_of_bangla_salary_range_supported(self, normalized_factcheck):
        reply = "probation বেতন ১০,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is True

    def test_upper_bound_of_bangla_salary_range_supported(self, normalized_factcheck):
        reply = "probation বেতন সর্বোচ্চ ১৩,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is True

    def test_both_bounds_ascii_digits_supported(self, normalized_factcheck):
        reply = "প্রশিক্ষণকালীন সম্মানী 10,000 থেকে 15,000 টাকা।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is True

    def test_probation_range_lower_bound_supported(self, normalized_factcheck):
        reply = "probation সাধারণত ৩ মাস হতে পারে।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is True

    def test_probation_range_upper_bound_supported(self, normalized_factcheck):
        reply = "probation সর্বোচ্চ ৬ মাস পর্যন্ত হতে পারে।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is True

    def test_age_range_upper_bound_supported(self, normalized_factcheck):
        reply = "বয়সসীমা সর্বোচ্চ ৫৫ বছর।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is True

    def test_permanent_salary_single_number_still_supported(self, normalized_factcheck):
        # Non-range figure (২৪,৭০০) must still validate -- confirms the
        # fix doesn't regress plain single-number grounding.
        reply = "স্থায়ী হলে বেতন ২৪,৭০০ টাকা হতে পারে।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is True

    def test_phone_number_still_merges_as_one_token(self, normalized_factcheck):
        # ASCII hyphen (phone numbers) must be UNAFFECTED by the en-dash fix.
        source = "যোগাযোগ নম্বর: 01958-122300।"
        reply = "আমাদের নম্বর 01958-122300।"
        assert _recruitment_reply_matches_source(reply, source) is True

    def test_genuinely_unsupported_number_still_rejected(self, normalized_factcheck):
        # 2026-08-27, Owner fact-check policy update: ১৭,০০০ is now
        # correctly WITHIN the business-tolerance band of this source's
        # money-range anchors (১০,০০০-১৫,০০০) -- see
        # TestBusinessToleranceFactCheckPolicy for that positive case.
        # This test now proves a genuinely wild figure, far outside any
        # reasonable variation of any money anchor here, still blocks.
        reply = "probation বেতন ৯০,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, self.UNSPACED_RANGE_SOURCE) is False

    def test_number_extraction_splits_unspaced_range(self):
        # Direct regex-level proof (not just through the full fact-check
        # function): an unspaced en-dash range extracts as two tokens.
        from modules.hermes_dispatch import _NUMBER_RE
        assert _NUMBER_RE.findall("১০,০০০–১৩,০০০") == ["১০,০০০", "১৩,০০০"]
        assert _NUMBER_RE.findall("৩–৬ মাস") == ["৩", "৬"]

    def test_number_extraction_does_not_split_ascii_hyphen(self):
        # Phone-number/date formatting (plain ASCII hyphen) is completely
        # unaffected -- only the en-dash range separator changed.
        from modules.hermes_dispatch import _NUMBER_RE
        assert _NUMBER_RE.findall("01958-122300") == ["01958-122300"]
        assert _NUMBER_RE.findall("2026-08-21") == ["2026-08-21"]


class TestCandidatePhoneIdentityFix:
    """2026-08-27 fix (Owner directive): a candidate's own phone number is
    contextual identity data, not a recruitment-policy fact -- echoing it
    back (e.g. "your number 01912345678 is noted") must not be blocked
    just because that exact number isn't in recruitment_source_of_truth.txt.
    Scoped narrowly via _candidate_identity_numbers(phone, text): only the
    sender's own WhatsApp phone, or a phone-shaped (>=10 digit) number the
    CANDIDATE themselves just typed this turn -- never a global allowlist,
    never anything Hermes introduces on its own."""

    NO_PHONE_SOURCE = (
        "probation salary: ৳১০,০০০–১৩,০০০। "
        "probation period পদ ও মূল্যায়ন অনুযায়ী ৩–৬ মাস।"
    )
    OFFICIAL_PHONE_SOURCE = "সর্বশেষ ও একমাত্র recruitment যোগাযোগ নম্বর: 01958-122300।"

    def test_candidates_own_sender_phone_echoed_is_allowed(self, normalized_factcheck):
        reply = "আপনার নম্বর 01912345678 পেয়েছি, ধন্যবাদ।"
        extra = _candidate_identity_numbers("01912345678", "")
        assert _recruitment_reply_matches_source(
            reply, self.NO_PHONE_SOURCE, extra_supported_numbers=extra,
        ) is True

    def test_candidates_own_phone_typed_this_turn_is_allowed(self, normalized_factcheck):
        # Sender phone differs from what they typed (e.g. session's
        # sender number vs. a number they just supplied) -- either way,
        # a number appearing in THIS turn's own text is the candidate's
        # own input, not Hermes inventing a fact.
        reply = "আপনার নম্বর 01865639673 নোট করা হলো।"
        extra = _candidate_identity_numbers("8801865639673", "আমার নম্বর 01865639673")
        assert _recruitment_reply_matches_source(
            reply, self.NO_PHONE_SOURCE, extra_supported_numbers=extra,
        ) is True

    def test_official_recruitment_phone_from_source_still_allowed(self, normalized_factcheck):
        # Regression check: the office's own number, already in the
        # authoritative source, validates with no extra_supported_numbers
        # needed at all -- this fix doesn't change that existing path.
        reply = "যোগাযোগ করুন: 01958-122300।"
        assert _recruitment_reply_matches_source(reply, self.OFFICIAL_PHONE_SOURCE) is True

    def test_arbitrary_third_party_phone_still_blocked(self, normalized_factcheck):
        # A phone number that is NEITHER the candidate's own (sender or
        # this-turn-typed) NOR in kb_context must still be rejected --
        # proves this fix does not become a general phone-number allowlist.
        reply = "এই নম্বরে যোগাযোগ করুন: 01700000000।"
        extra = _candidate_identity_numbers("8801912345678", "কোনো প্রশ্ন নেই")
        assert _recruitment_reply_matches_source(
            reply, self.NO_PHONE_SOURCE, extra_supported_numbers=extra,
        ) is False

    def test_unsupported_salary_number_still_blocked_alongside_phone_fix(self, normalized_factcheck):
        # A fabricated salary figure must still be blocked even when a
        # legitimate candidate-phone extra is present in the same reply.
        reply = "আপনার নম্বর 01912345678 পেয়েছি। বেতন ৫০,০০০ টাকা।"
        extra = _candidate_identity_numbers("01912345678", "")
        assert _recruitment_reply_matches_source(
            reply, self.NO_PHONE_SOURCE, extra_supported_numbers=extra,
        ) is False

    def test_36_misreading_of_range_still_blocked_alongside_phone_fix(self, normalized_factcheck):
        # The "৩–৬" -> "36" misreading (caught by the earlier range-
        # extraction fix) must remain blocked even with a phone-identity
        # extra present.
        reply = "আপনার নম্বর 01912345678 পেয়েছি। probation ৩৬ মাস।"
        extra = _candidate_identity_numbers("01912345678", "")
        assert _recruitment_reply_matches_source(
            reply, self.NO_PHONE_SOURCE, extra_supported_numbers=extra,
        ) is False

    def test_identity_numbers_exclude_short_digits(self):
        # Age/experience-length numbers in the candidate's own text must
        # NOT be smuggled in as "identity" -- only phone-length (>=10
        # digit) numbers qualify.
        result = _candidate_identity_numbers("", "আমার বয়স ২৫, অভিজ্ঞতা 3 বছর")
        assert result == set()

    def test_identity_numbers_includes_sender_phone_normalized(self):
        result = _candidate_identity_numbers("8801912345678", "")
        assert result == {"8801912345678"}

    def test_identity_numbers_empty_on_no_input(self):
        assert _candidate_identity_numbers("", "") == set()


class TestBusinessToleranceFactCheckPolicy:
    """2026-08-27, Owner fact-check policy update: category-based
    tolerance, not one uniform rule. Salary/training/probation ranges and
    candidate age are business-tolerant (within _BUSINESS_NUMBER_TOLERANCE
    of a known source figure, not an exact-bound match); the office/
    recruitment contact number and any other phone-length identity number
    remain exact-match only. Reuses the existing checker -- no new
    validation framework."""

    RANGE_SOURCE = (
        "probation salary: ৳১০,০০০–১৩,০০০। "
        "সাধারণ বয়সসীমা ১৮–৫৫ বছর। "
        "সর্বশেষ ও একমাত্র recruitment যোগাযোগ নম্বর: 01958-122300।"
    )

    # --- Salary tolerance ---

    def test_salary_range_upper_bound_extended_passes(self, normalized_factcheck):
        # source ১০,০০০–১৩,০০০ -> reply ১০,০০০–১৫,০০০ (Owner example)
        reply = "probation বেতন ১০,০০০ থেকে ১৫,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is True

    def test_salary_range_both_bounds_shifted_passes(self, normalized_factcheck):
        # source ১০,০০০–১৩,০০০ -> reply ১২,০০০–১৭,০০০ (Owner example)
        reply = "probation বেতন ১২,০০০ থেকে ১৭,০০০ টাকা।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is True

    def test_salary_wildly_different_figure_still_blocked(self, normalized_factcheck):
        # Far outside any reasonable business-tolerance band -- still a
        # fabricated/unrelated claim, must still block.
        reply = "probation বেতন ৯৯,৯৯৯ টাকা।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is False

    # --- Age tolerance ---

    def test_age_16_passes(self, normalized_factcheck):
        reply = "সর্বনিম্ন বয়স ১৬ বছর হতে পারে।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is True

    def test_age_17_passes(self, normalized_factcheck):
        reply = "সর্বনিম্ন বয়স ১৭ বছর হতে পারে।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is True

    def test_age_18_passes(self, normalized_factcheck):
        reply = "সর্বনিম্ন বয়স ১৮ বছর।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is True

    # --- Office phone: 100% strict ---

    def test_office_phone_exact_passes(self, normalized_factcheck):
        reply = "যোগাযোগ করুন: 01958-122300।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is True

    def test_office_phone_one_digit_changed_blocked(self, normalized_factcheck):
        # 01958-122301 vs canonical 01958-122300 -- one digit different.
        # Phone-length numbers are identity-class -- NEVER tolerant,
        # regardless of how close the digits are.
        reply = "যোগাযোগ করুন: 01958-122301।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is False

    def test_office_phone_unrelated_number_blocked(self, normalized_factcheck):
        reply = "যোগাযোগ করুন: 01700-000000।"
        assert _recruitment_reply_matches_source(reply, self.RANGE_SOURCE) is False

    # --- Candidate's own phone: contextual/trusted, not the office claim ---

    def test_candidates_own_phone_echoed_passes(self, normalized_factcheck):
        extra = _candidate_identity_numbers("01912345678", "")
        reply = "আপনার নম্বর 01912345678 নোট করা হলো।"
        assert _recruitment_reply_matches_source(
            reply, self.RANGE_SOURCE, extra_supported_numbers=extra,
        ) is True

    def test_unrelated_third_party_phone_blocked_even_with_candidate_extra(self, normalized_factcheck):
        # A DIFFERENT phone (not the candidate's own, not the office
        # number) must still be blocked even when a legitimate candidate
        # extra is present in the same call.
        extra = _candidate_identity_numbers("01912345678", "")
        reply = "এই নম্বরে যোগাযোগ করুন: 01611223344।"
        assert _recruitment_reply_matches_source(
            reply, self.RANGE_SOURCE, extra_supported_numbers=extra,
        ) is False

    # --- Place: canonical + legacy spelling ---

    def test_canonical_office_place_passes(self, normalized_factcheck):
        source = "অফিস ঠিকানা: আগ্রাপাড়া, Victoria Gate #1, Chittagong।"
        reply = "আমাদের অফিস আগ্রাপাড়ায় অবস্থিত।"
        assert _recruitment_reply_matches_source(reply, source) is True

    def test_legacy_office_place_spelling_normalizes_to_same_place(self, normalized_factcheck):
        # Source uses the NEW canonical spelling; reply uses the legacy
        # one -- must still match (same office, different spelling).
        source = "অফিস ঠিকানা: আগ্রাপাড়া, Victoria Gate #1, Chittagong।"
        reply = "আমাদের অফিস আগ্রপাড়ায় অবস্থিত।"
        assert _recruitment_reply_matches_source(reply, source) is True

    def test_legacy_source_canonical_reply_also_matches(self, normalized_factcheck):
        # And the reverse direction -- source still has the legacy
        # spelling (not yet updated everywhere), reply uses the new one.
        source = "অফিস ঠিকানা: আগ্রপাড়া, Victoria Gate #1, Chittagong।"
        reply = "আমাদের অফিস আগ্রাপাড়ায় অবস্থিত।"
        assert _recruitment_reply_matches_source(reply, source) is True

    def test_unrelated_invented_office_place_still_blocked(self, normalized_factcheck):
        source = "অফিস ঠিকানা: আগ্রাপাড়া, Victoria Gate #1, Chittagong।"
        reply = "আমাদের অফিস খুলনাজেলায় অবস্থিত।"
        assert _recruitment_reply_matches_source(reply, source) is False
