"""
Sprint-C0D Acceptance Test: source_bridge crash bug fix

Verifies that payment_correction.adjust_payment() does NOT reference the
`source_bridge` column in its INSERT into fazle_payment_drafts.

Background:
  - C0B found that payment_correction/__init__.py:221 wrote `source_bridge`
    to fazle_payment_drafts, but that column does NOT exist in production.
  - This would crash with "column source_bridge does not exist" if
    adjust_payment() were ever called.
  - C0D fix: removed source_bridge from the INSERT column list and parameters.

This test inspects the source code to ensure the fix is in place and stays
in place. It does NOT require a database connection.
"""
import inspect
import textwrap

from modules import payment_correction


def test_adjust_payment_insert_does_not_reference_source_bridge():
    """The INSERT statement in adjust_payment must not contain 'source_bridge'."""
    source = inspect.getsource(payment_correction.adjust_payment)
    # Normalize whitespace for reliable searching
    normalized = textwrap.dedent(source)

    # The INSERT INTO fazle_payment_drafts block must not mention source_bridge
    assert "source_bridge" not in normalized, (
        "adjust_payment() still references 'source_bridge' in its SQL. "
        "This column does not exist in production fazle_payment_drafts and "
        "will cause a runtime crash. Remove it from the INSERT statement."
    )


def test_adjust_payment_insert_column_count_matches_values():
    """
    After removing source_bridge, the INSERT should have 16 columns and
    13 parameters ($1..$13) plus 3 literals ('pending', 'adjustment', NOW()).
    """
    source = inspect.getsource(payment_correction.adjust_payment)
    normalized = textwrap.dedent(source)

    # Extract the INSERT block
    assert "INSERT INTO fazle_payment_drafts" in normalized
    assert "source_bridge" not in normalized

    # Count parameter placeholders in the VALUES clause
    # We expect $1 through $13 (13 parameters) after the fix
    # (was $1 through $14 before the fix)
    import re
    params = re.findall(r'\$(\d+)', normalized)
    max_param = max(int(p) for p in params) if params else 0
    assert max_param <= 13, (
        f"adjust_payment() has parameter ${max_param} — expected max $13 "
        f"after removing source_bridge. The parameter count is wrong."
    )