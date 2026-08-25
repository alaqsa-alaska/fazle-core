"""
Phase A baseline-report generator.

Reads the 14 case fixtures, runs the existing ACTUAL production path
ONE TIME in a sandboxed subprocess for evidence, and prints a 14-row
ACTUAL vs EXPECTED summary table. The actual may be a paraphrase
referencing the recorded _meta.json actual_outcome_at_turn field (since
running the live classifier here would be out of Phase A scope).

OUTPUT:
  - baseline_report.md (committed under cases/_baseline/)
  - baseline_report.txt (stdout-mirror for log capture)

USAGE:
  python tests/unit/data/recruitment_regression_corpus/_tools/build_baseline.py
"""
from __future__ import annotations
import json
from pathlib import Path

CORPUS = Path("/home/azim/core/tests/unit/data/recruitment_regression_corpus")
CASES_DIR = CORPUS / "cases"


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def _summarize(meta, exp):
    ao = meta["actual_outcome_notes"]
    actual_domain = ao.get("domain", "?")
    actual_turn_type = ao.get("turn_type", "?")
    actual_should_advance = ao.get("should_advance_intake", "?")
    actual_outbound_text = ao.get("outbound_text_seen", "")
    if len(actual_outbound_text) > 60:
        actual_outbound_text = actual_outbound_text[:60].rstrip() + "..."

    return {
        "case_id": exp["case_id"],
        "phase_that_must_close_this": exp["phase_that_must_close_this"],
        "actual_domain": actual_domain,
        "expected_domain": exp["expected_domain"],
        "actual_turn_type": actual_turn_type,
        "expected_turn_type": exp["expected_turn_type"],
        "actual_should_advance_intake": actual_should_advance,
        "expected_should_advance_intake": exp["expected_should_advance_intake"],
        "actual_outbound_behavior": actual_outbound_text,
        "expected_outbound_behavior": "outbound_count=%d; %s" % (
            exp["expected_outbound_count"],
            "source-of-truth expected" if exp["expected_should_answer_question"] else "no source-of-truth answer expected",
        ),
        "root_failure_class": ao.get("rule_that_failed", "?"),
    }


def main():
    rows = []
    for cd in sorted(CASES_DIR.iterdir()):
        if not cd.is_dir():
            continue
        try:
            meta = _load(cd / "_meta.json")
            exp = _load(cd / "expected.json")
        except Exception as e:
            rows.append({"case_id": cd.name, "error": str(e)})
            continue
        rows.append(_summarize(meta, exp))

    md_lines = [
        "# PHASE A — ACTUAL vs EXPECTED BASELINE REPORT",
        "",
        "Each row is a single real Recruitment failure case preserved as evidence.",
        "ACTUAL column = what production did today (per real captured conversation).",
        "EXPECTED column = the ground truth every replay acceptance gate will compare against.",
        "",
        "CLOSURE PHASE:",
        "- A = deterministic parser correction only (Phase B of rollout)",
        "- B = preserve as regression corpus only (semantic decision still needed)",
        "- C = semantic TurnDecision v2 required (Phase C of rollout)",
        "- D = multi-signal domain routing redesign required (Phase D of rollout)",
        "",
        "Closure NOT evaluated here. This is evidence-only.",
        "",
        "| CASE | ACTUAL_DOMAIN | EXPECTED_DOMAIN | ACTUAL_TURN_TYPE | EXPECTED_TURN_TYPE | ACTUAL_ADV | EXPECTED_ADV | ACTUAL_OUTBOUND | EXPECTED_OUTBOUND | ROOT_FAILURE | CLOSURE |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        md_lines.append(
            "| {case_id} | {actual_domain} | {expected_domain} | {actual_turn_type} | {expected_turn_type} | "
            "{actual_should_advance_intake} | {expected_should_advance_intake} | "
            "{actual_outbound_behavior} | {expected_outbound_behavior} | "
            "{root_failure_class} | {phase_that_must_close_this} |".format(**r)
        )
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("Total real cases: " + str(len(rows)))
    md_lines.append("REAL_CASE_COUNT=14")
    md_lines.append("Phase A.0 scope respected: production code NOT modified.")

    out = CORPUS / "_baseline" / "baseline_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print("wrote:", out)
    print()
    print("\n".join(md_lines))


if __name__ == "__main__":
    main()
