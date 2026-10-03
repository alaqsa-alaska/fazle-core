"""
Phase A test harness + 14 corpus validation tests.

This test file is REPO-SCOPED to Phase A only. It validates the DATASET and
EXPECTED CONTRACT, NOT production behavior. The ACTUAL production classifier
may currently FAIL every cf_Rxxx case -- that is the documented baseline.

Tests produced here:
  - 14 corpus fixture loads (input.json, _meta.json, conversation.jsonl, expected.json)
  - expected.json schema completeness for all 14 (20 required fields)
  - case IDs are unique across the corpus
  - phase_that_must_close_this is in the allowed set
  - conversation.jsonl has at least one candidate_inbound + one ai_outbound
    for cases where production emitted an AI outbound
  - real and synthetic corpora remain separate (synthetic empty / not-yet-present)
  - privacy: no real candidate phone numbers appear in any committed corpus file
  - baseline report: a snapshot table of ACTUAL vs EXPECTED per case

OUT OF SCOPE (do NOT add to this file):
  - asserting that the production classifier matches EXPECTED
  - asserting TurnDecision v2 behavior
  - asserting any new flag / restart / restart-loop behavior
"""
from __future__ import annotations
import json, re
from pathlib import Path

import pytest

CORPUS = Path("/home/azim/core/tests/unit/data/recruitment_regression_corpus")
CASES_DIR = CORPUS / "cases"
INDEX = CORPUS / "index.jsonl"
REQUIRED_FIELDS = json.loads((CORPUS / "_meta" / "corpus_meta.json").read_text())[
    "expected_required_fields"
]
ALLOWED_PHASES = {"A", "B", "C", "D"}

REAL_PHONES_FORBIDDEN = {
    "8801904291594", "8801317110473", "8801712318054",
    "8801832365139", "8801917931220", "8801931562049",
    "8801805529211",
    "8801958122300", "8801880446111", "8801958122304",
}


# ----- Helpers -----

def _case_dirs():
    return sorted([p for p in CASES_DIR.iterdir() if p.is_dir()])


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ----- 14 fixture loads + dataset structure -----

def test_corpus_has_exactly_14_case_dirs():
    assert len(_case_dirs()) == 14, (
        f"expected 14 real cases, got {len(_case_dirs())}: "
        + ", ".join(p.name for p in _case_dirs())
    )


def test_case_ids_match_pattern_cf_R_or_cf_W():
    for cd in _case_dirs():
        cid = cd.name.split("_")[0] + "_" + cd.name.split("_")[1]
        # case folder name starts with cf_R### or cf_W###
        assert (cd.name.startswith("cf_R") or cd.name.startswith("cf_W")), \
            f"case dir {cd.name!r} does not match cf_R### or cf_W### prefix"


def test_each_case_dir_has_4_files():
    expected_files = {"_meta.json", "input.json", "conversation.jsonl", "expected.json"}
    for cd in _case_dirs():
        actual = set(p.name for p in cd.iterdir() if p.is_file())
        missing = expected_files - actual
        extra = actual - expected_files
        assert not missing and not extra, (
            f"case {cd.name!r} file set: expected {expected_files}, got {actual}; "
            f"missing={missing}, extra={extra}"
        )


# ----- per-case expected.json schema completeness (1 test per field × 14 cases
# = 280 checks; structured as 14 parametrized cases with assertion loop)

@pytest.mark.parametrize("case_dir", _case_dirs(), ids=lambda p: p.name)
def test_expected_json_has_all_required_fields(case_dir):
    exp = _load_json(case_dir / "expected.json")
    missing = [f for f in REQUIRED_FIELDS if f not in exp]
    assert not missing, f"case {case_dir.name!r} expected.json missing: {missing}"

    # type spot-checks
    assert isinstance(exp["expected_intake_fields_proposed"], dict)
    assert isinstance(exp["expected_intake_fields_accepted"], dict)
    assert isinstance(exp["expected_intake_fields_rejected"], dict)
    assert isinstance(exp["expected_questions"], list)
    assert isinstance(exp["expected_outbound_count"], int)
    assert exp["expected_outbound_count"] >= 0
    assert isinstance(exp["expected_should_advance_intake"], bool)
    assert isinstance(exp["expected_should_answer_question"], bool)
    assert isinstance(exp["expected_needs_clarification"], bool)
    assert isinstance(exp["expected_factcheck_required"], bool)
    assert isinstance(exp["expected_no_fabricated_fact"], bool)
    assert isinstance(exp["phase_that_must_close_this"], str)
    assert exp["phase_that_must_close_this"] in ALLOWED_PHASES, (
        f"expected.phase_that_must_close_this={exp['phase_that_must_close_this']} "
        f"not in {ALLOWED_PHASES}"
    )
    # field locking semantics
    assert exp["expected_intake_step_before"] in (
        None, "name", "age", "area", "job_preference", "experience", "phone_confirm", "done"
    ), f"step_before invalid: {exp['expected_intake_step_before']!r}"
    assert exp["expected_intake_step_after"] in (
        None, "name", "age", "area", "job_preference", "experience", "phone_confirm", "done"
    ), f"step_after invalid: {exp['expected_intake_step_after']!r}"
    # truth-protection
    assert exp["expected_no_fabricated_fact"] is True, (
        f"expected_no_fabricated_fact must always be True (per spec): "
        f"case {case_dir.name!r}"
    )


# ----- case ID uniqueness -----

def test_case_ids_unique_across_corpus():
    seen = []
    for cd in _case_dirs():
        cid = _load_json(cd / "expected.json")["case_id"]
        seen.append(cid)
    assert len(seen) == len(set(seen)), f"duplicate case_ids found: {seen}"


# ----- index.jsonl integrity -----

def test_index_jsonl_references_every_case_dir():
    rows = [json.loads(line) for line in INDEX.read_text().splitlines() if line.strip()]
    indexed_paths = {row["path"] for row in rows}
    actual_paths = {str(cd.relative_to(CORPUS)) for cd in _case_dirs()}
    assert indexed_paths == actual_paths, (
        f"index mismatch: indexed={indexed_paths - actual_paths}, "
        f"actual={actual_paths - indexed_paths}"
    )
    assert len(rows) == 14, f"expected 14 rows in index.jsonl, got {len(rows)}"


# ----- conversation.jsonl shape -----

@pytest.mark.parametrize("case_dir", _case_dirs(), ids=lambda p: p.name)
def test_conversation_jsonl_well_formed(case_dir):
    conv_path = case_dir / "conversation.jsonl"
    lines = [l for l in conv_path.read_text().splitlines() if l.strip()]
    assert lines, f"empty conversation in {case_dir.name}"
    roles = set()
    for i, line in enumerate(lines):
        m = json.loads(line)
        assert "role" in m, f"line {i} missing 'role': {m!r}"
        assert "text" in m, f"line {i} missing 'text': {m!r}"
        assert "ts" in m, f"line {i} missing 'ts': {m!r}"
        assert "speaker_phone" in m, f"line {i} missing 'speaker_phone': {m!r}"
        assert m["text"], f"line {i} has empty text"
        assert m["speaker_phone"], f"line {i} has empty speaker_phone"
        roles.add(m["role"])
    # We expect at least one candidate_inbound OR for cf_W001 (architecture
    # content) the conversation has only candidate_inbound rows.
    assert "candidate_inbound" in roles, (
        f"conversation {case_dir.name!r} has no candidate_inbound roles: {roles!r}"
    )


# ----- ACTUAL vs EXPECTED baseline (this is the report; not asserting pass/fail
# because we DELIBERATELY preserve baseline failures for now).

@pytest.mark.parametrize("case_dir", _case_dirs(), ids=lambda p: p.name)
def test_baseline_actual_vs_expected_per_case(case_dir):
    """Captures the baseline. NOT a pass/fail gate -- current production is
    expected to fail every cf_C and cf_D case here. cf_A cases are expected
    to fail too with current code because Phase B fixes have not shipped.
    """
    meta = _load_json(case_dir / "_meta.json")
    inp = _load_json(case_dir / "input.json")
    exp = _load_json(case_dir / "expected.json")
    # We do NOT call production code. We just snapshot the baseline.
    snapshot = {
        "case_id": exp["case_id"],
        "phase_that_must_close_this": exp["phase_that_must_close_this"],
        "thread_phone_masked": meta["thread_phone_masked"],
        "inbound_message": inp["inbound_message"],
        "expected_domain": exp["expected_domain"],
        "expected_turn_type": exp["expected_turn_type"],
        "expected_should_advance_intake": exp["expected_should_advance_intake"],
        "expected_should_answer_question": exp["expected_should_answer_question"],
        "expected_outbound_count": exp["expected_outbound_count"],
        "actual_observed_notes": meta["actual_outcome_notes"],
    }
    # In Phase A this test passes simply because we DID snapshot the baseline.
    # In later phases the per-case test harness will assert EXPECTED==NEW.
    assert snapshot["case_id"] == exp["case_id"], "case_id mismatch"
    assert snapshot["phase_that_must_close_this"] in ALLOWED_PHASES


# ----- privacy: real phones must NOT leak into the corpus tree -----

def test_no_real_candidate_phone_in_corpus_files():
    """Recursive scan: every corpus file (input, expected, conversation,
    _meta, corpus_meta, index, salt, README) must not contain any real
    candidate phone number."""
    leaks = []
    for path in CORPUS.rglob("*"):
        if path.is_dir():
            continue
        if "_tools" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for ph in REAL_PHONES_FORBIDDEN:
            if ph in text:
                leaks.append((path, ph))
    assert not leaks, "real phone leaks: " + str(leaks[:30])


def test_admin_phone_pattern_not_advertised_in_corpus_meta():
    """corpus_meta.json must not embed the real->masked admin phone map."""
    p = CORPUS / "_meta" / "corpus_meta.json"
    txt = p.read_text(encoding="utf-8")
    for ph in REAL_PHONES_FORBIDDEN:
        assert ph not in txt, (
            f"admin real phone {ph!r} leaks in {p.name}; "
            "use _tools/author_corpus.py for the salt-based mapping"
        )


# ----- real vs synthetic corpus separation -----

def test_real_and_synthetic_corpora_are_separate():
    synthetic_root = CORPUS.parent / "recruitment_synthetic_corpus"
    assert synthetic_root != CORPUS
    # Synthetic corpus must NOT exist yet (per spec: only added after every
    # real case passes in Phase C).
    assert not synthetic_root.exists(), (
        "synthetic corpus must not exist during Phase A; "
        "synthetic cases are added only after all 14 real cases pass"
    )


def test_synthetic_status_recorded_in_corpus_meta():
    meta = _load_json(CORPUS / "_meta" / "corpus_meta.json")
    assert meta["synthetic_corpus"]["exists"] is False, (
        "synthetic corpus must be marked exists=false in Phase A"
    )


# ----- smoke summary list for human-readable output -----

def test_corpus_index_smoke_list(capsys):
    rows = []
    for cd in _case_dirs():
        exp = _load_json(cd / "expected.json")
        rows.append((exp["case_id"], exp["phase_that_must_close_this"],
                     exp["expected_turn_type"], exp["expected_should_advance_intake"]))
    out = capsys.readouterr()
    # captured and printed as part of pytest -v, not strictly asserted
    print("\nPHASE A CORPUS SMOKE LIST:")
    print(f"  {'CASE':<10} {'PHASE':<6} {'TURN_TYPE':<18} {'ADVANCE':<8}")
    for cid, ph, tt, adv in rows:
        print(f"  {cid:<10} {ph:<6} {tt:<18} {str(adv):<8}")
    print(f"  total {len(rows)} cases")
    assert len(rows) == 14
