"""Regression tests — recruitment fee/deposit canonical-text conflict
(2026-08-21 closure pass).

Governance basis: knowledge_base/00_governance/management_decisions.md's
2026-07-21 entry establishes ONE canonical deposit/fee text (also stored
as fazle_knowledge_base row b11_no_fee, is_active=True, live-verified) and
propagated it to modules.knowledge_base._FALLBACK, modules.social_auto_
reply.reply_rules, and modules.conversation_layer.playbooks --
"supersedes the older '~৳1,330 upfront, refund after 6 months' framing
everywhere it previously appeared." resources/ops/recruitment_source_of_
truth.txt (a later-created 5th grounding source, used by hermes_dispatch's
Hermes-customer-recruitment path) was missed by that rollout and still had
the stale, specific-figure framing plus an explicit "never say no fee"
instruction that actively contradicts the canonical policy. Fixed by
replacing that section with the exact canonical text.
"""
from modules.conversation_layer.playbooks import CANONICAL_FACTS

CANONICAL_FEE_TEXT = (
    "চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না। প্রসেসিং/জয়েনিং ফি অন্যান্য "
    "প্রতিষ্ঠানের মত এখানেও আছে, তবে অগ্রিম নেওয়ার বদলে মাসিক বেতন থেকে "
    "কিস্তিতে কাটা হয় — টাকার অভাবে কারো জয়েন বাঁধাগ্রস্ত হয় না। "
    "নির্দিষ্ট মানি-রিসিট ছাড়া কেউ টাকা চাইলে তা প্রতারণা, অফিসে যাচাই করুন।"
)

_STALE_FIGURES = ("৳৩,৫০০", "৳১,০০০", "৳৩৩০", "৳৫০০")
_STALE_NEVER_SAY_NO_FEE = 'কখনো বলবে না "কোনো ভর্তি ফি নেই"'


def test_playbooks_canonical_fact_matches_governance_decision():
    """Sanity check the fixture text above still matches the live rule-reply
    module (catches this test drifting from the real source, not the other
    way around)."""
    assert CANONICAL_FACTS["fee"] == CANONICAL_FEE_TEXT


def test_source_of_truth_file_no_longer_has_stale_specific_figures():
    from modules.hermes_dispatch import _RECRUITMENT_SOURCE_PATH

    text = _RECRUITMENT_SOURCE_PATH.read_text(encoding="utf-8")
    for figure in _STALE_FIGURES:
        assert figure not in text, f"stale superseded figure {figure!r} still present"
    assert _STALE_NEVER_SAY_NO_FEE not in text, "stale contradictory instruction still present"


def test_source_of_truth_file_now_carries_the_canonical_policy_text():
    from modules.hermes_dispatch import _RECRUITMENT_SOURCE_PATH

    text = _RECRUITMENT_SOURCE_PATH.read_text(encoding="utf-8")
    # Same distinctive sentences as the canonical b11_no_fee text — not the
    # exact same paragraph breaks (the source-of-truth file wraps lines for
    # readability), so check the load-bearing phrases individually.
    assert "কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না" in text
    assert "টাকা নেই" in text and "বাঁধা হবে না" in text
    assert "01958-122300" in text


def test_build_recruitment_context_includes_canonical_fee_text_on_fee_question():
    import modules.hermes_dispatch as hd

    hd._recruitment_source_cache = None  # force a fresh read for this test
    ctx = hd.build_recruitment_context("জয়েনিং ফি কত?")
    assert "কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না" in ctx
    for figure in _STALE_FIGURES:
        assert figure not in ctx


def test_build_recruitment_context_omits_fee_section_on_non_fee_question():
    import modules.hermes_dispatch as hd

    hd._recruitment_source_cache = None
    ctx = hd.build_recruitment_context("অফিসের ঠিকানা কী?")
    assert "ভর্তি ফি / ডিপোজিট প্রশ্নের বাধ্যতামূলক উত্তরনীতি" not in ctx
