# PHASE A — ACTUAL vs EXPECTED BASELINE REPORT

Each row is a single real Recruitment failure case preserved as evidence.
ACTUAL column = what production did today (per real captured conversation).
EXPECTED column = the ground truth every replay acceptance gate will compare against.

CLOSURE PHASE:
- A = deterministic parser correction only (Phase B of rollout)
- B = preserve as regression corpus only (semantic decision still needed)
- C = semantic TurnDecision v2 required (Phase C of rollout)
- D = multi-signal domain routing redesign required (Phase D of rollout)

Closure NOT evaluated here. This is evidence-only.

| CASE | ACTUAL_DOMAIN | EXPECTED_DOMAIN | ACTUAL_TURN_TYPE | EXPECTED_TURN_TYPE | ACTUAL_ADV | EXPECTED_ADV | ACTUAL_OUTBOUND | EXPECTED_OUTBOUND | ROOT_FAILURE | CLOSURE |
|---|---|---|---|---|---|---|---|---|---|---|
| cf_R001 | recruitment | recruitment | mixed (legacy classify_turn_intent classified as mixed: question regex matched 'কি কাজ' AND name regex matched) | pure_question | True (legacy intake_message ran; no value extracted) | False | দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন। আপনি কোন পদে কাজ... | outbound_count=1; source-of-truth expected | Legacy dispatch cannot produce SAVE VALID FIELD + ANSWER + ADVANCE simultaneously; here, no field was supplied, but intake stepped anyway | C |
| cf_R002 | recruitment | recruitment | mixed | pure_question | True (legacy advanced from name) | True | কোন পদের কাজের দায়িত্ব ও ডিটেইলস জানতে চান? Security Guard,... | outbound_count=1; source-of-truth expected | Question regex matched 'জানতে চাই'; name regex matched | C |
| cf_R003 | recruitment | recruitment | mixed | pure_question | True (legacy asked for full name via payment_due workflow) | False | দুঃখিত, বুঝতে পারিনি। দয়া করে শুধু আপনার পুরো নাম লিখুন (যে... | outbound_count=1; source-of-truth expected | Question matched 'কি'; name regex matched; legacy treated as name step | C |
| cf_R004 | recruitment | recruitment | mixed | mixed | False | True | সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 2... | outbound_count=1; source-of-truth expected | AI stuck on age prompt; didn't recognise 'security হলে' as preference | C |
| cf_R005 | recruitment | recruitment | mixed | pure_question | True (legacy advance) | False | সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 2... | outbound_count=1; source-of-truth expected | Question regex matched; AI picked the wrong prompt | C |
| cf_R006 | recruitment | recruitment | pure_intake (no fields, no questions) | acknowledgement | False | False | দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন। আপনি কোন পদে কাজ... | outbound_count=0; no source-of-truth answer expected | Legacy sent another post-list prompt after candidate closure signal | C |
| cf_R007 | recruitment | recruitment | mixed | pure_question | True (legacy advance name -> age) | False | Hi! Sure, I can help. May I know your name and which positio... | outbound_count=1; source-of-truth expected | English opener matched question regex + name regex | C |
| cf_R008 | recruitment | recruitment | pure_question (question regex matched 'বয়স') | pure_intake | False | True | সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 2... | outbound_count=1; no source-of-truth answer expected | _STEP_HAS_VALUE_RE['age'] regex only accepts single-digit Bengali or 1-2 ASCII digits; missing multi-digit Bengali '৩০' | B |
| cf_R009 | recruitment | recruitment | mixed | pure_question (DOB is not a free-form age; clarify) | True (legacy extracted age=31 and advanced step) | False | আপনি কোন জেলায় থাকেন? (বর্তমান ঠিকানা) | outbound_count=1; source-of-truth expected | _parse_age grabbed the first multi-digit Bengali pair '৩১' (day-of-month from DOB) as age; no DOB gate existed | B |
| cf_R010 | recruitment | recruitment | pure_question | frustration | False | False | আপনি কোন পদে কাজ করতে চান? ১. Escort ২. Survey Scout ... | outbound_count=1; no source-of-truth answer expected | Question regex matches '?'; frustration has no special handling | C |
| cf_R011 | recruitment | recruitment | pure_intake (no question or intake markers matched) | frustration | False | False | এই ক্ষেত্রে আপনার কত বছরের অভিজ্ঞতা আছে? (নতুন হলে 0 লিখুন) | outbound_count=1; no source-of-truth answer expected | frustration has no special handling | C |
| cf_R012 | recruitment | recruitment | mixed | mixed | False | True | আপনার কনফার্মেশনের জন্য: আপনার মোবাইল নম্বর কত? (যেটায় কল ক... | outbound_count=1; source-of-truth expected | Legacy is at phone_confirm step and ignores the alternative preference offer | C |
| cf_R013 | recruitment | recruitment | mixed | pure_intake | False (legacy did not advance; AI re-prompted age-limit every time) | True | সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 2... | outbound_count=1; no source-of-truth answer expected | Repeated same value; legacy doesn't carry forward locked value (parser also fails on Bengali multi-digit) | C |
| cf_W001 | attendance (legacy replied with attendance bot template) | multi_domain (recruitment-system architecture discussion; not a candidate application) | classified as payment_due by intent engine (first-match-wins on OCR text containing 'টাকা'/'জাহাজ'/'ডিউটি'/'পেমেন্ট') | unrelated / multi_domain (architecture content, not a candidate application) | False (recruitment side rejected at recruitment_eligibility:919-921 short-circuit) | False | আপনার রিকোয়েস্ট পেয়েছি 🙏 যাচাইয়ের জন্য আপনার **Employee I... | outbound_count=1; no source-of-truth answer expected | intent in OPERATIONAL_INTENTS short-circuit; legacy single-domain dispatch | D |

---
Total real cases: 14
REAL_CASE_COUNT=14
Phase A.0 scope respected: production code NOT modified.
