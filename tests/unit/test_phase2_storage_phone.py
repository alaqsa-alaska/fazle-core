"""
tests/unit/test_phase2_storage_phone.py
=======================================

Pure unit tests for modules.phone_normalizer.storage_canonical_phone /
storage_phone_last10 — the write-time canonicalizer for
wbom_whatsapp_messages.canonical_phone (2026-09-01).
"""
from modules.phone_normalizer import storage_canonical_phone, storage_phone_last10


class TestStorageCanonicalPhone:
    def test_bd_forms_normalize(self):
        for raw in ("8801711111111", "01711111111", "1711111111",
                    "+8801711111111", "01711-111111", "০১৭১১১১১১১১"):
            assert storage_canonical_phone(raw) == "8801711111111", raw

    def test_jid_suffix_stripped(self):
        assert storage_canonical_phone("8801711111111@s.whatsapp.net") == "8801711111111"

    def test_lid_sentinels_become_none(self):
        assert storage_canonical_phone("unresolved:155539800895531") is None
        assert storage_canonical_phone("lid:155539800895531") is None

    def test_foreign_number_kept_digits_only(self):
        # a valid non-BD international number must NOT be nulled out
        assert storage_canonical_phone("+447911123456") == "447911123456"
        assert storage_canonical_phone("966512345678") == "966512345678"

    def test_facebook_psid_and_junk_rejected(self):
        assert storage_canonical_phone("1234567890123456789") is None  # 19 digits
        assert storage_canonical_phone("") is None
        assert storage_canonical_phone(None) is None
        assert storage_canonical_phone("hello") is None

    def test_last10_never_a_slice_of_a_lid(self):
        assert storage_phone_last10("unresolved:155539800895531") is None
        assert storage_phone_last10("8801711111111") == "1711111111"
        assert storage_phone_last10("01711111111") == "1711111111"
