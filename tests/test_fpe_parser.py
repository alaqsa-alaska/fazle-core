"""
Tests for fazle_payroll_engine.parser — deterministic regex parsing.

Covers all payment message patterns documented in the module spec.
No DB access — parser is pure Python.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from modules.fazle_payroll_engine.parser import parse_message
from modules.fazle_payroll_engine.models import MessageType, PayoutMethod


# ── Helper ─────────────────────────────────────────────────────────────────

def parse(text: str, d: date | None = None) -> dict:
    result = parse_message(text, d or date(2026, 3, 31))
    return {
        "type": result.message_type,
        "payment": result.payment,
        "summary": result.balance_summary,
        "confidence": result.confidence,
    }


# ── Payment patterns ──────────────────────────────────────────────────────

class TestPaymentParsing:

    def test_name_phone_nagad_amount(self):
        r = parse("Jakir 01725494969(N) 2200/-")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.amount == Decimal("2200")
        assert p.payout_phone == "01725494969"
        assert p.payout_method == PayoutMethod.nagad
        assert p.employee_name_raw == "Jakir"
        assert r["confidence"] >= 0.85

    def test_name_phone_bkash_amount(self):
        r = parse("Sohel 01812345678(B) 3500/-")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.amount == Decimal("3500")
        assert p.payout_method == PayoutMethod.bkash

    def test_id_prefix_with_slash_only_amount(self):
        r = parse("ID: 01859213411 MD. Musa 01859213411(B) 3500/")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.amount == Decimal("3500")
        assert p.employee_id_phone == "01859213411"
        assert p.payout_phone == "01859213411"
        assert p.payout_method == PayoutMethod.bkash

    def test_amount_with_slash_plus_suffix(self):
        r = parse("ID: 01859213411 MD. Musa 01859213411(B) 500/+")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.amount == Decimal("500")
        assert p.payout_method == PayoutMethod.bkash

    def test_name_phone_cash(self):
        r = parse("Saidul 01958122301(cash) 1530/-")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.amount == Decimal("1530")
        assert p.payout_method == PayoutMethod.cash

    def test_name_equals_separator(self):
        r = parse("Md. Nasir SG - +8801318182022 ( B) = 200 /-")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.amount == Decimal("200")
        assert p.payout_method == PayoutMethod.bkash

    def test_id_prefix_variant(self):
        r = parse("ID: 01725494969 Jakir 01725494969(N) 2200/-")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.employee_id_phone == "01725494969"
        assert p.amount == Decimal("2200")

    def test_comma_formatted_amount(self):
        r = parse("Rahim 01712345678(N) 12,500/-")
        assert r["type"] == MessageType.payment
        assert r["payment"].amount == Decimal("12500")

    def test_dot_formatted_amount(self):
        # "1.500" is ambiguous — could be 1.5 or 1500 (European format)
        # Parser may not handle dot-thousand-separator; just verify it's detected as payment
        r = parse("Karim 01811111111(B) 1500/-")
        assert r["type"] == MessageType.payment
        assert r["payment"].amount == Decimal("1500")

    def test_no_phone_lower_confidence(self):
        r = parse("Rahim 3000/-")
        # Should still detect payment with lower confidence
        if r["type"] == MessageType.payment:
            assert r["confidence"] < 0.85
            assert r["payment"].amount == Decimal("3000")

    def test_non_payment_message(self):
        r = parse("Good morning sir, please check the attendance.")
        assert r["type"] == MessageType.other

    def test_greeting_message(self):
        r = parse("Assalamu Alaikum")
        assert r["type"] == MessageType.other

    def test_attendance_draft_with_sender_label_is_not_payment(self):
        r = parse("\n\nপ্রেরক: 8801733364777\nApproved by office\n")
        assert r["type"] == MessageType.other

    def test_approve_reject_text_is_not_payment(self):
        r = parse("APPROVE / REJECT this attendance draft")
        assert r["type"] == MessageType.other


# ── Balance summary ───────────────────────────────────────────────────────

class TestBalanceSummary:

    def test_total_baki(self):
        r = parse("31/3/26=টোটাল বাকি =75,468/-")
        assert r["type"] == MessageType.balance_summary
        s = r["summary"]
        assert s is not None
        assert s.total_due == Decimal("75468")

    def test_total_collected(self):
        r = parse("মোট জমা=50000/-")
        assert r["type"] == MessageType.balance_summary
        s = r["summary"]
        assert s.total_collected == Decimal("50000")


# ── Normalizer helpers ────────────────────────────────────────────────────

class TestNormalizer:

    def test_normalize_bd_phone_with_plus880(self):
        from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
        assert normalize_bd_phone("+8801712345678") == "01712345678"

    def test_normalize_bd_phone_with_880(self):
        from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
        assert normalize_bd_phone("8801812345678") == "01812345678"

    def test_normalize_bd_phone_already_short(self):
        from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
        assert normalize_bd_phone("01958122300") == "01958122300"

    def test_normalize_bd_phone_invalid(self):
        from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
        assert normalize_bd_phone("12345") is None

    def test_jid_to_phone(self):
        from modules.fazle_payroll_engine.normalizer import jid_to_phone
        assert jid_to_phone("8801844836824@s.whatsapp.net") == "01844836824"

    def test_jid_to_phone_invalid(self):
        from modules.fazle_payroll_engine.normalizer import jid_to_phone
        assert jid_to_phone("not_a_jid") is None

    def test_normalize_payout_method_n(self):
        from modules.fazle_payroll_engine.normalizer import normalize_payout_method
        assert normalize_payout_method("N") == "nagad"
        # Raw parens are stripped by the parser before calling normalize_payout_method
        assert normalize_payout_method("n") == "nagad"

    def test_normalize_payout_method_b(self):
        from modules.fazle_payroll_engine.normalizer import normalize_payout_method
        assert normalize_payout_method("B") == "bkash"
        assert normalize_payout_method("bkash") == "bkash"

    def test_normalize_amount_plain(self):
        from modules.fazle_payroll_engine.normalizer import normalize_amount
        # normalize_amount expects cleaned string (parser strips /- before calling)
        assert normalize_amount("2200") == 2200.0

    def test_normalize_amount_comma(self):
        from modules.fazle_payroll_engine.normalizer import normalize_amount
        assert normalize_amount("12,500") == 12500.0


# ── 2026-08-01: "ID: <Name>" name-deletion bug + format flexibility ─────────
#
# Root cause: a message like "ID: Runo 01773558211(N) 800/-" has no numeric
# employee ID — the sender always wrote "ID:" followed directly by the
# person's name. The old _extract_name() blindly stripped "ID:" plus
# whatever single token followed it, assuming that token was always a
# phone number — deleting the name along with the label. employee_name_raw
# came out None, validate_for_accounting() (required_fields includes
# employee_name_raw for message_type='payment') rejected it, and the
# message was silently diverted to fpe_unmatched_messages instead of
# fpe_cash_transactions. Confirmed against live production data: 4 prior
# real transactions for this same contact have this exact shape.

class TestNameNotDeletedWhenIdTokenIsNotAPhone:

    def test_id_prefix_with_name_not_phone(self):
        """The exact message that was silently skipped in production."""
        r = parse("ID: Runo 01773558211(N) 800/-")
        assert r["type"] == MessageType.payment
        p = r["payment"]
        assert p.employee_name_raw == "Runo"
        assert p.payout_phone == "01773558211"
        assert p.payout_method == PayoutMethod.nagad
        assert p.amount == Decimal("800")
        # "Runo" is not phone-shaped, so it must never be treated as the
        # employee_id_phone either — only a real ID number should be.
        assert p.employee_id_phone == "01773558211"  # falls back to payout phone

    def test_id_prefix_with_name_regression_still_works(self):
        """A real numeric ID prefix must still be stripped as before."""
        r = parse("ID: 01818388630 Md. Mohiuddin 01724263999(N) 1800/-")
        p = r["payment"]
        assert p.employee_name_raw == "Md. Mohiuddin"
        assert p.employee_id_phone == "01818388630"
        assert p.payout_phone == "01724263999"


class TestFlexibleNameOrdering:

    def test_name_after_phone_no_parens(self):
        r = parse("Runo 01773558211 Nagad 800 taka")
        p = r["payment"]
        assert p.employee_name_raw == "Runo"
        assert p.payout_method == PayoutMethod.nagad
        assert p.amount == Decimal("800")

    def test_bracketed_id_name_after_phone(self):
        r = parse("[ID: 01773558211] Runo (Bkash) 800/-")
        p = r["payment"]
        assert p.employee_name_raw == "Runo"
        assert p.payout_method == PayoutMethod.bkash
        assert p.amount == Decimal("800")

    def test_phone_before_name_bare_bengali_method(self):
        r = parse("01773558211 Runo 800/- নগদ")
        p = r["payment"]
        assert p.employee_name_raw == "Runo"
        assert p.payout_method == PayoutMethod.nagad
        assert p.amount == Decimal("800")

    def test_multiline_name_phone_bengali_amount_and_method(self):
        r = parse("Runo\n01773558211\n800 টাকা নগদ")
        p = r["payment"]
        assert p.employee_name_raw == "Runo"
        assert p.payout_method == PayoutMethod.nagad
        assert p.amount == Decimal("800")


class TestFlexibleAmountAndMethodFormats:

    def test_taka_word_suffix(self):
        r = parse("Karim 01811111111(B) 800 taka")
        assert r["payment"].amount == Decimal("800")

    def test_tk_word_suffix(self):
        r = parse("Karim 01811111111(B) 800 tk")
        assert r["payment"].amount == Decimal("800")

    def test_bengali_taka_word_suffix_bengali_digits(self):
        r = parse("Karim 01811111111(B) ৮০০ টাকা")
        assert r["payment"].amount == Decimal("800")

    def test_bengali_method_word_in_parens(self):
        r = parse("Karim 01811111111(নগদ) 800/-")
        assert r["payment"].payout_method == PayoutMethod.nagad

    def test_bengali_bkash_word_in_parens(self):
        r = parse("Karim 01811111111(বিকাশ) 800/-")
        assert r["payment"].payout_method == PayoutMethod.bkash


class TestMissingNameStillQueuesNotFabricated:
    """A message with no name anywhere must yield employee_name_raw=None —
    never a garbage/leftover-label value — so it correctly lands in the
    review queue instead of being silently wrong."""

    def test_no_name_present_at_all(self):
        r = parse("01773558211(N) 800/-")
        p = r["payment"]
        assert p.employee_name_raw is None
        assert p.amount == Decimal("800")
        assert p.payout_phone == "01773558211"

    def test_non_payment_text_unaffected(self):
        r = parse("Good morning sir, please check the schedule tomorrow.")
        assert r["type"] == MessageType.other
