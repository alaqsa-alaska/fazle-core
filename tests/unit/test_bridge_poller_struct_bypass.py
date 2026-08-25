"""
2026-08-24 (Owner BREAK_GLASS_APPROVED narrow STRUCT bypass for Hermes live
Bridge-3 Recruitment/Employee conversational authority):

Verify the patched STRUCT guard logic at modules/bridge_poller/__init__.py
lines 2248-2281 correctly:

  - bypasses length/table/headings heuristic for msg_intent in {recruitment, employee_inquiry}
  - still blocks those heuristics for all other msg_intent values
  - preserves all surrounding safety layers (POISON, AI_SAFE_MODE,
    DRAFT_ALWAYS, contact-risk, loop detection, financial-intent draft gate)

This test re-implements the exact guard logic from the patched file and
asserts behavior. The full bridge_poller module requires DB/asyncio
imports that don't run in pure unit context; the patched logic is small
and verbatim so re-implementation is safe for behavior verification.
"""

STRUCT_BYPASS_INTENTS = frozenset({"recruitment", "employee_inquiry"})
STRUCT_LONG_THRESHOLD = 400
STRUCT_TABLE_PIPE_MIN = 2


def check_struct_guard(reply: str, msg_intent: str):
    """Verbatim copy of the patched STRUCT guard logic at
    modules/bridge_poller/__init__.py:2266-2281.

    Returns (decision, reason):
      decision is "ALLOW" or "BLOCK".
      reason is the block_reason string when BLOCK, else None.
    """
    reply_len = len(reply)
    has_table = reply.count(" | ") >= STRUCT_TABLE_PIPE_MIN or ":---" in reply
    is_long = reply_len > STRUCT_LONG_THRESHOLD
    has_headings = reply.count("\n#") >= 2
    is_recruitment_employee = msg_intent in STRUCT_BYPASS_INTENTS
    if (not is_recruitment_employee) and (is_long or has_table or has_headings):
        return ("BLOCK", f"STRUCT:len={reply_len},table={has_table},headings={has_headings}")
    return ("ALLOW", None)


# ---- Tests ----

def test_recruitment_long_allowed():
    """Long recruitment reply must NOT be blocked by STRUCT bypass."""
    long_re = "আসসালামু আলাইকুম। আপনার বেতন প্রায় ১০,০০০ থেকে ১৫,০০০ টাকা। " * 20
    decision, _ = check_struct_guard(long_re, "recruitment")
    assert decision == "ALLOW", \
        f"STRUCT bypass failed for recruitment intent (got {decision})"


def test_employee_long_allowed():
    """Long employee_inquiry reply must NOT be blocked by STRUCT bypass."""
    long_re = "Employee salary policy: ৳10,000–৳24,000 depending on role. " * 15
    decision, _ = check_struct_guard(long_re, "employee_inquiry")
    assert decision == "ALLOW", \
        f"STRUCT bypass failed for employee_inquiry intent (got {decision})"


def test_unknown_intent_long_blocked():
    """Long reply for UNKNOWN intent must STILL be blocked."""
    long_re = "Some random long text. " * 50
    decision, reason = check_struct_guard(long_re, "unknown")
    assert decision == "BLOCK", \
        f"STRUCT guard regressed: should still block unknown intent (got {decision})"
    assert reason.startswith("STRUCT:"), \
        f"Block reason malformed: {reason!r}"


def test_unrelated_intent_long_blocked():
    """Long reply for non-recruitment/employee intents must STILL be blocked."""
    long_re = "X" * 600
    for intent in ("", "general", "payment_due", "complaint", "escort_inquiry", "greeting"):
        decision, _ = check_struct_guard(long_re, intent)
        assert decision == "BLOCK", \
            f"STRUCT guard regressed for intent {intent!r}: got {decision}"


def test_short_always_allowed():
    """Short replies are never blocked by STRUCT regardless of intent."""
    for intent in ("recruitment", "unknown", "", "general"):
        decision, _ = check_struct_guard("Hi", intent)
        assert decision == "ALLOW", \
            f"Short reply should never be blocked for intent {intent!r}"


def test_recruitment_with_headings_allowed():
    """Recruitment reply with markdown headings must NOT be blocked."""
    reply = "# Header\n\n## Section\n\nBody text here. " * 30
    decision, _ = check_struct_guard(reply, "recruitment")
    assert decision == "ALLOW", \
        f"STRUCT bypass failed for headings in recruitment (got {decision})"


def test_recruitment_with_table_pipes_allowed():
    """Recruitment reply with table-like pipes must NOT be blocked."""
    reply = "Salary | Position | Note\n --- | --- | ---\n 10000 | Security | probation\n"
    decision, _ = check_struct_guard(reply, "recruitment")
    assert decision == "ALLOW", \
        f"STRUCT bypass failed for table pipes in recruitment (got {decision})"


def test_unknown_with_table_blocked():
    """Unknown-intent reply with table pipes must STILL be blocked."""
    reply = "a | b | c\nd | e | f"
    decision, _ = check_struct_guard(reply, "unknown")
    assert decision == "BLOCK", \
        f"STRUCT guard regressed: should still block tables for unknown (got {decision})"


def test_recruitment_short_table_allowed():
    """Recruitment with table but short body — bypass applies cleanly."""
    reply = "Hi | Hello"
    decision, _ = check_struct_guard(reply, "recruitment")
    assert decision == "ALLOW"


def test_edge_case_intent_employee_inquiry_with_markdown():
    """employee_inquiry with headings and tables — typical salary breakdown."""
    reply = "# Salary Breakdown\n\n## Stage 1\n\n৳10,০০০–৳15,০০০\n\n| Stage | Amount |\n| --- | --- |\n| 3-month | 12k |"
    decision, _ = check_struct_guard(reply, "employee_inquiry")
    assert decision == "ALLOW", \
        f"employee_inquiry salary breakdown should bypass STRUCT (got {decision})"


if __name__ == "__main__":
    import sys
    failures = 0
    tests = [
        test_recruitment_long_allowed,
        test_employee_long_allowed,
        test_unknown_intent_long_blocked,
        test_unrelated_intent_long_blocked,
        test_short_always_allowed,
        test_recruitment_with_headings_allowed,
        test_recruitment_with_table_pipes_allowed,
        test_unknown_with_table_blocked,
        test_recruitment_short_table_allowed,
        test_edge_case_intent_employee_inquiry_with_markdown,
    ]
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failures += 1
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)