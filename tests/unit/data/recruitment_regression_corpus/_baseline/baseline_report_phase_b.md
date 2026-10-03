# Phase B — 14-Case Diagnostic Report

Diagnostic-mode. Phase B did NOT modify any production classifier's disposition for semantic cases. Phase A EXPECTED preserved verbatim.

| CASE | STEP | TEXT (excerpt) | phase | expected_turn_type | phase B deterministic notes |
|---|---|---|---|---|---|
| cf_R001 | name | -চাকরি কি হবে?? | C | pure_question | name_step:name=rejected-as-non-name |
| cf_R002 | name | কাজের দায়িত্ব ও ডিটেইলস জানতে চায় | C | pure_question | name_step:name=accepted-as-name |
| cf_R003 | name | এটা কি সুধু বাংলাদেশ এ থাকতে হবে ? | C | pure_question | name_step:name=rejected-as-non-name |
| cf_R004 | age | সিকিওরিটি হলে করবো | C | mixed | looks_like_dob=False; expected_age_after=None; phase_b_action_kind=re-prompt: বয়সসীমা ... দয়া করে সঠিক বয়স লিখুন; phase_b_age_dob_block=True; phase_b_field_lock_match=False |
| cf_R005 | job_preference | -আমার বডিফিটনেস দেখে বলেন,,আমাকে কোন পদে দেওয়া যায়?? | C | pure_question |  |
| cf_R006 | done | -ধন্যবাদ 🌹🌹 | C | acknowledgement |  |
| cf_R007 | name | Hello! Can I get more info on this? | C | pure_question | name_step:name=rejected-as-non-name |
| cf_R008 | age | *বয়স:৩০বছর | B | pure_intake | looks_like_dob=False; expected_age_after=30; phase_b_action_kind=advance+update; phase_b_age_dob_block=False; phase_b_field_lock_match=False |
| cf_R009 | age | -ভাই আমার জন্ম তারিখ : ৩১/১২/১৯৯৬ সাল,,,সেই হিসাবে আমার বয়স... | B | pure_question (DOB is not a free-form age; clarify) | looks_like_dob=True; expected_age_after=None; phase_b_action_kind=re-prompt: বয়সসীমা ... দয়া করে সঠিক বয়স লিখুন; phase_b_age_dob_block=True; phase_b_field_lock_match=False |
| cf_R010 | job_preference | ??? | C | frustration |  |
| cf_R011 | experience | Koibar bolsi | C | frustration |  |
| cf_R012 | phone_confirm | আর না হলে ৭ নাম্বার হলে হবে সিকিউরিটি ইনচাজ | C | mixed |  |
| cf_R013 | age | -আমার বয়স: ৩০বছর।। | C | pure_intake | looks_like_dob=False; expected_age_after=30; phase_b_action_kind=advance+update; phase_b_age_dob_block=False; phase_b_field_lock_match=False |
| cf_W001 | None | Here's the extracted text from your handwritten image, organ... | D | unrelated / multi_domain (architecture content, not a candidate application) |  |

## Phase B deterministic-leg closure matrix (per Phase A evidence)

| CASE | phase_that_must_close_this | deterministic leg closed by Phase B | full case closed |
|---|---|---|---|
| cf_R001 | C | n/a (semantic-only) | no |
| cf_R002 | C | n/a (semantic-only) | no |
| cf_R003 | C | n/a (semantic-only) | no |
| cf_R004 | C | n/a (semantic-only) | no |
| cf_R005 | C | n/a (semantic-only) | no |
| cf_R006 | C | n/a (semantic-only) | no |
| cf_R007 | C | n/a (semantic-only) | no |
| cf_R008 | B | YES (age=30 from *বয়স:৩০বছর) | YES |
| cf_R009 | B | YES (_looks_like_dob returned True, age=None) | YES |
| cf_R010 | C | n/a (semantic-only) | no |
| cf_R011 | C | n/a (semantic-only) | no |
| cf_R012 | C | n/a (semantic-only) | no |
| cf_R013 | C | YES for parser, NO for locked-field (Phase C) | PARSER_LEG_FIXED_ONLY |
| cf_W001 | D | n/a (Phase D) | no |

## Summary

- **DETERMINISTIC_CASES_FULLY_CLOSED by Phase B = [cf_R008, cf_R009]**
- **DETERMINISTIC_LEG_FIXED_BUT_CASE_STILL_SEMANTIC = [cf_R013]** (parser leg closed by Phase B; locked-field leg requires Phase C)
- **SEMANTIC_CASES_REMAINING = [cf_R001, cf_R002, cf_R003, cf_R004, cf_R005, cf_R006, cf_R007, cf_R010, cf_R011, cf_R012]** (require Phase C)
- **DOMAIN_ROUTING_CASES_REMAINING = [cf_W001]** (require Phase D; NOT closed by Phase B)
- **NEW_HEURISTIC_SEMANTIC_LAYER_ADDED = no**
- **RECRUITMENT_OPERATIONAL_RESCUE_ADDED = no** (per spec, NOT reintroduced)
