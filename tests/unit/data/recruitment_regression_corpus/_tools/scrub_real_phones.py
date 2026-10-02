# -*- coding: utf-8 -*-
"""Privacy-protection pass for the Phase A regression corpus.

Removes real candidate phone numbers from all CORPUS-TRACKED files.
Real phones remain only in the offline author tool (`_tools/author_corpus.py`)
and the offline rebuild tool (`_tools/build_baseline.py`) — those are NOT
part of the committed corpus under `tests/unit/data/recruitment_regression_corpus/cases/`
or `tests/unit/data/recruitment_regression_corpus/_meta/corpus_meta.json`.

This pass:
  - For every case dir, write `input.json` and `_meta.json` WITHOUT a
    `thread_phone_real` field. Case files only carry `thread_phone_masked`.
  - For `_meta/corpus_meta.json`, REMOVE the `phone_mask_examples` mapping
    entirely (it maps real -> masked, which is itself PII). Replace with
    a pointer to the offline tool.
  - Confirm by re-scanning: every real phone (candidate + admin) appears
    nowhere in the corpus tree outside this tool's directory.

Run from corpus root:
  /home/azim/.venv/bin/python _tools/scrub_real_phones.py
"""
from __future__ import annotations
import json, os, re
from pathlib import Path

CORPUS = Path("/home/azim/core/tests/unit/data/recruitment_regression_corpus")
TOOLS = CORPUS / "_tools"
CASES = CORPUS / "cases"

# Real phones (must remain only in _tools/, never in CASES/* or _meta/*)
REAL_PHONES_CANDIDATE = [
    "8801904291594", "8801317110473", "8801712318054",
    "8801832365139", "8801917931220", "8801931562049",
    "8801805529211",
]
REAL_PHONES_ADMIN = ["8801958122300", "8801880446111", "8801958122304"]
ALL_REAL = set(REAL_PHONES_CANDIDATE + REAL_PHONES_ADMIN)


def scrub_case(case_dir):
    for fname in ("input.json", "_meta.json"):
        path = case_dir / fname
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "thread_phone_real" in data:
            data.pop("thread_phone_real", None)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


def scrub_corpus_meta():
    path = CORPUS / "_meta" / "corpus_meta.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if "phone_mask_examples" in data:
        data.pop("phone_mask_examples", None)
        data["phone_mask_examples_pointer"] = (
            "see _tools/author_corpus.py for the salt-based mapping; "
            "do NOT reintroduce the real->masked map in this file (PII)."
        )
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main():
    print("Scrubbing case files...")
    for cd in sorted(CASES.iterdir()):
        if cd.is_dir():
            scrub_case(cd)
    print("Scrubbing _meta/corpus_meta.json ...")
    scrub_corpus_meta()
    print()
    print("Re-scan: real phones must NOT appear in corpus tree outside _tools/:")
    leaks = []
    for p in sorted(CORPUS.rglob("*")):
        if p.is_file() and "_tools" not in p.parts:
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for ph in ALL_REAL:
                if ph in text:
                    leaks.append((p, ph))
    if not leaks:
        print("  OK: no real phone leaks in corpus trees outside _tools/")
    else:
        print("  LEAKS:")
        for p, ph in leaks[:50]:
            print(f"    {p}: {ph}")
    return 0 if not leaks else 1


if __name__ == "__main__":
    raise SystemExit(main())
