"""Phase C v2 — Tier A extract (propose) tests.

Verifies that Phase B deterministic parsers still produce IntakeProposal
objects with correct semantics, and that NEW keywords/regexes are NOT
added in Phase C (Owner directive correction 2).
"""
from __future__ import annotations

from typing import Optional

from modules.recruitment_flow import STEP_QUESTIONS
from modules.recruitment_turn_decision.extract import propose


def _locked(
    age: Optional[int] = 30,
    area: Optional[str] = "Narayanganj",
    job: Optional[str] = "Operation Officer",
    exp: Optional[int] = 0,
    phone: Optional[str] = "01712318054",
    name: Optional[str] = "Mohd Ashikur Rahman",
):
    return {
        "full_name": name,
        "age": age,
        "area": area,
        "job_preference": job,
        "experience_years": exp,
        "confirmed_phone": phone,
    }


# --- AGE step ---

def test_propose_age_bengali_multidigit_accepted():
    proposals = propose("*বয়স:৩০বছর", current_step="age", locked_fields=_locked(age=None))
    assert any(p.field == "age" and p.value == 30 for p in proposals), proposals


def test_propose_age_dob_returns_no_age_proposal():
    """Phase B's _looks_like_dob gate still prevents DOB from being parsed
    as age. Phase C honors this via Tier A."""
    proposals = propose("-ভাই আমার জন্ম তারিখ : ৩১/১২/১৯৯৬ সাল,,,সেই হিসাবে আমার বয়স এখন ৩০বছর।।",
                       current_step="age", locked_fields=_locked(age=None))
    # DOB proposal is surfaced but the age proposal is not extracted.
    assert any(p.field == "dob" for p in proposals)
    assert not any(p.field == "age" for p in proposals)


def test_propose_age_re_assertion_marks_semantic_review():
    proposals = propose("৩০", current_step="age", locked_fields=_locked(age=30))
    age = next((p for p in proposals if p.field == "age"), None)
    assert age is not None
    assert age.requires_semantic_review is True


# --- JOB step ---

def test_propose_job_preference_accepted():
    proposals = propose("৬ operation officer", current_step="job_preference",
                       locked_fields=_locked(job=None))
    assert any(p.field == "job_preference" and p.value == "Operation Officer"
               for p in proposals)


def test_propose_job_preference_re_assertion():
    proposals = propose("Operation Officer", current_step="job_preference",
                       locked_fields=_locked(job="Operation Officer"))
    jp = next((p for p in proposals if p.field == "job_preference"), None)
    assert jp is not None
    assert jp.requires_semantic_review is True


# --- PHONE step ---

def test_propose_phone_accepted():
    proposals = propose("01871031067", current_step="phone_confirm",
                       locked_fields=_locked(phone=None))
    assert any(p.field == "phone_confirm" and p.value == "01871031067"
               for p in proposals), (
        f"phone proposal missing; got {proposals}"
    )


# --- NAME step ---

def test_propose_name_marks_semantic_review():
    """Phase C Tier A ALWAYS marks name proposals as requires_semantic_review.
    Only Tier B (the LLM-decision layer) may accept a name value."""
    proposals = propose("Mohd Ashikur Rahman", current_step="name",
                       locked_fields=_locked())
    name = next((p for p in proposals if p.field == "name"), None)
    assert name is not None
    assert name.requires_semantic_review is True


def test_propose_name_question_marker_surfaces_rejection():
    proposals = propose("চাকরি কি হবে??", current_step="name",
                       locked_fields=_locked())
    name = next((p for p in proposals if p.field == "name"), None)
    # Tier A surfaces a low-confidence proposal flagged for Tier B review.
    assert name is not None
    assert name.requires_semantic_review is True
    assert name.confidence < 0.5


# --- Phase C directive correction 2: NO new keywords ---

def test_no_new_keywords_added_in_phase_c_extract():
    """The extract module reuses Phase B parsers and adds ZERO new
    Bengali keywords / regex rules / sender-specific exceptions."""
    import inspect
    src = inspect.getsource(propose)
    # No new regex compilation in this module
    assert "re.compile" not in src
    # No keyword-list (the OWNER-PROHIBITED pattern)
    for forbidden in [
        "ভর্তি", "জয়েনিং", "অগ্রিম", "টাকা লাগ", "লাগবে", "করবেন",
    ]:
        # The ONLY allowed occurrence of these would be in code-comments
        # describing what Phase C does NOT do. They must NOT appear as
        # data literals.
        assert forbidden not in src.split('"""')[-1], (
            f"Phase C Tier A may not add {forbidden!r} as a literal; "
            "see Owner directive correction 2"
        )


# --- DOB guard independent of step ---

def test_dob_guard_fires_regardless_of_step():
    proposals = propose("জন্ম তারিখ ৩১/১২/১৯৯৬",
                       current_step="name", locked_fields=_locked())
    assert any(p.field == "dob" for p in proposals)


# --- Empty / whitespace ---

def test_propose_empty_returns_empty():
    proposals = propose("", current_step="name", locked_fields=_locked())
    assert proposals == []


def test_propose_whitespace_returns_empty():
    proposals = propose("   \n\t  ", current_step="age", locked_fields=_locked())
    assert proposals == []


# --- Step-keyword proposal fields ---

def test_step_questions_known():
    """Tier A only proposes fields for the active step. The set of
    known step names must match Phase B's COLLECTION_STEPS."""
    expected = {"name", "age", "area", "job_preference", "experience", "phone_confirm"}
    assert set(STEP_QUESTIONS.keys()) == expected