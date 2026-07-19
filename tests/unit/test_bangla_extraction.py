"""Unit tests — modules/conversation_memory/bangla_extraction (Phase B
Stage 3, 2026-07-19).

Pure-function tests, no DB needed. Covers the real, verified gap this
module fixes ("ভেসেল নেম Horda" -> "MV HORDA" instead of the old parser's
"MV ভেসেল নেম HORDA"), the required label list, digit/markup/MV-prefix
normalization, trip-suffix splitting, and the context-sensitive
generic-label promotion rule (Part 12).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class TestNormalization:

    def test_bangla_digits_normalize_to_ascii(self):
        from modules.conversation_memory.bangla_extraction import normalize_bangla_digits
        assert normalize_bangla_digits("০১৮২১৩৭৫৯২৬") == "01821375926"

    def test_whatsapp_markup_stripped(self):
        from modules.conversation_memory.bangla_extraction import strip_whatsapp_markup
        assert strip_whatsapp_markup("*Bushra Biva-1*") == "Bushra Biva-1"
        assert strip_whatsapp_markup("_MV Horda_") == "MV Horda"

    def test_mother_vessel_mv_prefix_not_doubled(self):
        from modules.conversation_memory.bangla_extraction import normalize_mother_vessel_value
        assert normalize_mother_vessel_value("MV Horda") == "MV HORDA"
        assert normalize_mother_vessel_value("M.V. Horda") == "MV HORDA"
        assert normalize_mother_vessel_value("এমভি Horda") == "MV HORDA"
        assert normalize_mother_vessel_value("Horda") == "MV HORDA"

    def test_capacity_unit_words_normalize(self):
        from modules.conversation_memory.bangla_extraction import normalize_capacity_value
        assert normalize_capacity_value("1250 টন") == "1250 MT"
        assert normalize_capacity_value("1250এমটি") == "1250 MT"
        assert normalize_capacity_value("১২৫০ টন") == "1250 MT"

    def test_trip_value_normalizes_ordinal(self):
        from modules.conversation_memory.bangla_extraction import normalize_trip_value
        assert normalize_trip_value("২য়") == "2nd Trip"
        assert normalize_trip_value("2") == "2nd Trip"

    def test_trip_suffix_split_from_vessel_name(self):
        from modules.conversation_memory.bangla_extraction import split_trip_suffix
        clean, trip = split_trip_suffix("Bushra Biva-1(2nd Trip)")
        assert clean == "Bushra Biva-1"
        assert trip == "2nd Trip"

    def test_trip_suffix_split_noop_when_absent(self):
        from modules.conversation_memory.bangla_extraction import split_trip_suffix
        clean, trip = split_trip_suffix("Bushra Biva-1")
        assert clean == "Bushra Biva-1"
        assert trip is None


class TestLabelExtraction:

    def test_the_verified_gap_is_fixed(self):
        """The exact real bug this module exists to fix: the OLD parser
        produces "MV ভেসেল নেম HORDA"; this module must produce
        "MV HORDA" -- with full completeness context (the real scenario)."""
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("ভেসেল নেম Horda", has_lighter=True, has_master_mobile=True)
        assert result["mother_vessel"]["value"] == "MV HORDA"
        assert result["mother_vessel"]["confidence"] >= 0.8
        assert result["mother_vessel"]["is_explicit_label"] is True

    def test_generic_label_without_context_is_low_confidence_ambiguous(self):
        """Part 12: without an existing lighter+master in the thread, the
        generic label must NOT be force-treated as high confidence."""
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("ভেসেল নেম Horda", has_lighter=False, has_master_mobile=False)
        assert result["mother_vessel"]["value"] == "MV HORDA"
        assert result["mother_vessel"]["confidence"] < 0.6
        assert result["mother_vessel"]["is_explicit_label"] is False

    def test_strong_mother_vessel_label_always_high_confidence(self):
        """A SPECIFIC label ("মাদার ভেসেল") is unambiguous regardless of
        conversation context -- unlike the generic "ভেসেল নেম" label."""
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("মাদার ভেসেল Horda", has_lighter=False, has_master_mobile=False)
        assert result["mother_vessel"]["value"] == "MV HORDA"
        assert result["mother_vessel"]["confidence"] >= 0.8

    def test_lighter_label_extracted(self):
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("লাইটার Bushra Biva-1", has_lighter=False, has_master_mobile=False)
        assert result["lighter_vessel"]["value"] == "Bushra Biva-1"

    def test_master_mobile_label_extracted_and_normalized(self):
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("মাস্টার মোবাইল ০১৮২১৩৭৫৯২৬", has_lighter=False, has_master_mobile=False)
        assert result["master_mobile"]["value"] == "01821375926"

    def test_destination_label_extracted(self):
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("গন্তব্য Chattogram", has_lighter=False, has_master_mobile=False)
        assert result["destination"]["value"] == "Chattogram"

    def test_capacity_label_extracted(self):
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("ক্যাপাসিটি ১২৫০ টন", has_lighter=False, has_master_mobile=False)
        assert result["capacity"]["value"] == "1250 MT"

    def test_specific_label_not_shadowed_by_generic_substring(self):
        """"মাদার ভেসেলের নাম" contains "ভেসেল" but must match the
        SPECIFIC (strong, high-confidence) label, not fall through to the
        generic/ambiguous one."""
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("মাদার ভেসেলের নাম Horda", has_lighter=False, has_master_mobile=False)
        assert result["mother_vessel"]["confidence"] >= 0.8
        assert result["mother_vessel"]["extraction_method"] != "bangla_label_generic_ambiguous"

    def test_no_label_no_field(self):
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields
        result = extract_labeled_fields("hello there", has_lighter=False, has_master_mobile=False)
        assert result == {}
