"""Regression tests — Bengali-numeral phone parsing (2026-08-21 closure
pass). Real candidate case: a recruitment reply of "০১৭১৬০৮১৯২৫" (a
genuinely valid number, just typed in Bengali digits) never parsed,
because Python's \\D already treats Bengali digits as digits (so they
survive re.sub(r"\\D", "", raw) unstripped) but every subsequent check
compared against ASCII literals, which a Bengali digit character can
never equal. Fixed by transliterating to ASCII first, in the one
canonical phone_normalizer module, reused (not duplicated) at the
recruitment_flow._parse_phone() call site.
"""
from modules.phone_normalizer import normalize_phone, to_ascii_digits


def test_bengali_digit_local_number():
    assert normalize_phone("০১৭১৬০৮১৯২৫") == "8801716081925"


def test_mixed_bengali_and_ascii_digits():
    assert normalize_phone("০১৭16081৯২৫") == "8801716081925"


def test_bengali_digits_with_spaces_and_dashes():
    assert normalize_phone("০১৭১৬-০৮১৯২৫") == "8801716081925"
    assert normalize_phone("০১৭১৬ ০৮১৯ ২৫") == "8801716081925"


def test_bengali_plus_880_form():
    assert normalize_phone("+৮৮০১৭১৬০৮১৯২৫") == "8801716081925"


def test_bengali_880_form_no_plus():
    assert normalize_phone("৮৮০১৭১৬০৮১৯২৫") == "8801716081925"


def test_bengali_too_short_is_invalid():
    assert normalize_phone("০১৭১৬০৮") is None


def test_bengali_too_long_is_invalid():
    assert normalize_phone("০১৭১৬০৮১৯২৫৬৭৮") is None


def test_ordinary_ascii_numbers_unchanged():
    assert normalize_phone("01716081925") == "8801716081925"
    assert normalize_phone("+8801716081925") == "8801716081925"
    assert normalize_phone("8801716081925") == "8801716081925"
    assert normalize_phone("01812-345678") == "8801812345678"
    assert normalize_phone("123") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


def test_to_ascii_digits_helper():
    assert to_ascii_digits("০১২৩৪৫৬৭৮৯") == "0123456789"
    assert to_ascii_digits("০১৭ abc 123") == "017 abc 123"
    assert to_ascii_digits("") == ""
    assert to_ascii_digits(None) is None
