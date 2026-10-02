# Recruitment Regression Corpus — Phase A (Freeze Evidence)

This corpus contains **14 real Recruitment-domain failure cases** extracted
from production traffic between 2026-08-22 and 2026-08-23.

It is the primary acceptance evidence for the Semantic Recruitment
Turn Understanding rollout. Real production failures are the primary
acceptance corpus; synthetic cases will be added in a separate
`recruitment_synthetic_corpus/` directory only after every real case
is passing — **not** mixed in with these.

## Layout

```
recruitment_regression_corpus/
  _meta/
    salt.txt                -- deterministic phone-mask anchor (NOT secret)
    corpus_meta.json        -- schema version, field list, phase labels
  cases/
    cf_R001_question_misclassified_mixed;_intake_advanced;_que/
      _meta.json            -- per-case admin notes + actual_outcome observations
      input.json            -- inbound + minimal context for replay
      conversation.jsonl    -- chronological canonical-history (role-labeled)
      expected.json         -- EXPECTED ground truth (Phase C/D contract)
    cf_R002_…
    cf_R003_…
    cf_R004_…
    cf_R005_…
    cf_R006_…
    cf_R007_…
    cf_R008_…
    cf_R009_…
    cf_R010_…
    cf_R011_…
    cf_R012_…
    cf_R013_…
    cf_W001_…
  index.jsonl               -- one line per case, {case_id, path}
  _tools/
    _author_corpus_DO_NOT_COMMIT.py  -- offline generator; keep OUT of git
    scrub_real_phones.py             -- offline privacy-pass
```

## Case ID conventions

- `cf_R001..cf_R013` — 13 real recruitment turn-classification failures
  observed in the prior shadow replay (RECORDED_PRODUCTION_EVIDENCE)
- `cf_W001` — 1 real wrong-domain-routing case (the OCR'd architecture
  thread routed to payment_due/slip_submission/client_order/attendance/leave)

Each `expected.json` declares `phase_that_must_close_this` ∈ {A, B, C, D}:

- `A` — deterministic parser correction (Phase B of rollout)
- `B` — preserve as regression corpus only (semantic decision still needed)
- `C` — semantic TurnDecision v2 required (Phase C of rollout)
- `D` — multi-signal domain routing redesign required (Phase D of rollout)

## Privacy

Real candidate phone numbers are NEVER committed in the corpus.
Phone numbers in case files appear only as `thread_phone_masked` (the
salted-hash derivative). Phone numbers that the candidate typed into
message bodies (e.g. `01871031067`) are PRESERVED VERBATIM in
`conversation.jsonl` because those are part of the failure-bearing
signal, not PII of the recorder.

The salt is in `_meta/salt.txt`. It is not secret — just an anchor
that makes the mask deterministic across replay runs.

The offline author tool (`_tools/_author_corpus_DO_NOT_COMMIT.py`)
contains the raw real-phone-to-case mapping in plain text. **Never
commit that file**. The `_DO_NOT_COMMIT` suffix is a reminder; a
`.gitignore` entry should also exclude the file.

## Phase-A boundary

Phase A does **not** modify production behavior. The 14 fixtures
exist so that future phases (B, C, D, E) have a stable acceptance set.

Phase A tests validate the **dataset** and the **EXPECTED contract**,
not the production classifier. A failing CURRENT classifier is
expected and must be preserved as baseline evidence.

`expected.json` is the authoritative ground truth. `actual_outcome_at_turn`
in each case's `_meta.json` records what production did; this is
reportable but not authoritative for replay acceptance.
