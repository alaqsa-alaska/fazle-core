# -*- coding: utf-8 -*-
"""
Author Phase A regression fixtures for the 14 real Recruitment
classification + wrong-domain cases.

Inputs:
  - real conversation data captured earlier in /tmp/threads_b5.txt
  - real outbound rows captured earlier in /tmp/compare.log

Outputs (under tests/unit/data/recruitment_regression_corpus/cases/<id>/):
  - input.json:     the inbound + minimal context for replay
  - expected.json:  per-case EXPECTED ground truth
  - conversation.jsonl: chronological canonical history (role-labeled)
  - _meta.json:     per-case privacy/admin notes

Privacy:
  - candidate phones salted deterministic hash ('8801' + sha1[:7]) per
    _meta/corpus_meta.json#mask_algorithm
  - admin phones masked to 'admin_<hash>'
  - message-body phone numbers PRESERVED VERBATIM (the candidate typed them
    themselves -- they are part of the failure-bearing signal, not PII of
    the recorder).

Usage:
  python tests/unit/data/recruitment_regression_corpus/_tools/author_corpus.py
"""
from __future__ import annotations
import hashlib, json, os, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent  # corpus root, not _tools
TOOLS = ROOT / "_tools"
TOOLS.mkdir(parents=True, exist_ok=True)

PHONE_SALT = open(ROOT / "_meta" / "salt.txt").read().strip()

def mask_phone(real_phone: str) -> str:
    last9 = (real_phone or "").strip()[-9:]
    h = hashlib.sha1((PHONE_SALT + "|" + last9).encode("utf-8")).hexdigest()[:7]
    return "8801" + h

def is_admin(real_phone: str) -> bool:
    return real_phone.strip() in ("8801958122300", "8801880446111", "8801958122304")

def mask_admin(real_phone: str) -> str:
    if is_admin(real_phone):
        return "admin_" + mask_phone(real_phone)
    return mask_phone(real_phone)


def conv_for(thread: str):
    """Return chronological canonical-history list for a thread."""

    if thread == "8801904291594":
        P = "8801393d879"
        return [
            {"ts": "2026-08-23T09:43:26Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "হ্যালো! আমি কি এই ব্যাপারে আরও তথ্য জানতে পারি?"},
            {"ts": "2026-08-23T09:43:49Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "হ্যাঁ, অবশ্যই। আপনার নাম কী এবং কোন পদে আগ্রহী -- জানালে বিস্তারিত বলতে পারব।"},
            {"ts": "2026-08-23T09:44:38Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-নাম- মোঃ আশিকুর রহমান"},
            {"ts": "2026-08-23T09:44:43Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "*এস এস সি পাস।।  ভোকেশনাল"},
            {"ts": "2026-08-23T09:44:51Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "*বয়স:৩০বছর"},
            {"ts": "2026-08-23T09:45:32Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-উচ্চতা :৬ফিট"},
            {"ts": "2026-08-23T09:45:32Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-ওজন:৭৫কেজি"},
            {"ts": "2026-08-23T09:46:22Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-আমি এখন টেক্সটাইলে ফায়ার এন্ড সেইফটি ডিপার্টমেন্টো,,,ফায়ার ম্যান হিসাবে জব করছি।।"},
            {"ts": "2026-08-23T09:46:22Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-৩০"},
            {"ts": "2026-08-23T09:47:29Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)"},
            {"ts": "2026-08-23T09:47:29Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "[media:image]"},
            {"ts": "2026-08-23T09:53:14Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-ভাই আমার জন্ম তারিখ : ৩১/১২/১৯৯৬ সাল,,,সেই হিসাবে আমার বয়স এখন ৩০বছর।।"},
            {"ts": "2026-08-23T09:53:30Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "আপনি কোন জেলায় থাকেন? (বর্তমান ঠিকানা)"},
            {"ts": "2026-08-23T09:54:02Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-আমি এখন নারায়ণগঞ্জ জেলায় থাকি।।"},
            {"ts": "2026-08-23T10:00:20Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-চাকরি কি হবে??"},
            {"ts": "2026-08-23T10:00:45Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন। আপনি কোন পদে কাজ করতে চান?"},
            {"ts": "2026-08-23T10:09:14Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "-ধন্যবাদ 🌹🌹"},
        ]
    if thread == "8801317110473":
        P = "880126078c4"
        return [
            {"ts": "2026-08-23T05:25:42Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "Hello"},
            {"ts": "2026-08-23T05:26:26Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "নমস্কার, আমি ফজলে -- Al-Rifa'i Integrated Services-এর নিয়োগ সহকারী। আপনি কি চাকরিতে আগ্রহী? আপনার নামটা জানতে পারি?"},
            {"ts": "2026-08-23T05:26:26Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "Hello! Can I get more info on this?"},
            {"ts": "2026-08-23T05:28:41Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "মোঃশাহা আলম"},
            {"ts": "2026-08-23T05:29:26Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "20 বছর"},
            {"ts": "2026-08-23T05:35:03Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)"},
            {"ts": "2026-08-23T05:36:18Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "21 বছর"},
            {"ts": "2026-08-23T05:36:29Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "আপনি কোন জেলায় থাকেন? (বর্তমান ঠিকানা)"},
            {"ts": "2026-08-23T05:37:18Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "গাইবান্ধা জেলা"},
            {"ts": "2026-08-23T05:38:32Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "আপনি কোন পদে কাজ করতে চান? ১. Escort ২. Survey Scout (জাহাজে) ৩. Security Guard ৪. Security Supervisor ৫. Asst Supervisor ৬. Operation Officer ৭. Security In-Charge ৮. Marketing Officer"},
            {"ts": "2026-08-23T05:46:55Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "৬ operation officer"},
            {"ts": "2026-08-23T05:41:44Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "সার্টিফিকেট লাগবে না"},
            {"ts": "2026-08-23T05:50:50Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "আপনার কনফার্মেশনের জন্য: আপনার মোবাইল নম্বর কত?"},
            {"ts": "2026-08-23T05:50:13Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "আর না হলে ৭ নাম্বার হলে হবে সিকিউরিটি ইনচাজ"},
        ]
    if thread == "8801832365139":
        P = "88013899d3c"
        return [
            {"ts": "2026-08-23T07:05:09Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "Hello! Can I get more info on this?"},
            {"ts": "2026-08-23T07:05:32Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "Hi! This is Fazle from Al-Rifa'i Integrated Services -- we are currently hiring for Security Guard, Escort, Survey Scout, Operation Officer, Marketing Officer."},
            {"ts": "2026-08-23T07:07:21Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "কাজের দায়িত্ব ও ডিটেইলস জানতে চায়"},
            {"ts": "2026-08-23T07:08:07Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "অপারেশন অফিসার"},
            {"ts": "2026-08-23T07:10:46Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "আপনার বয়স কত বছর?"},
            {"ts": "2026-08-23T07:12:22Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "24"},
            {"ts": "2026-08-23T07:12:31Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "আপনি কোন জেলায় থাকেন?"},
            {"ts": "2026-08-23T07:13:17Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "ময়মনসিংহ"},
            {"ts": "2026-08-23T07:26:34Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "???"},
            {"ts": "2026-08-23T07:34:21Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "??"},
            {"ts": "2026-08-23T07:34:37Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন। আপনি কোন পদে কাজ করতে চান?"},
            {"ts": "2026-08-23T07:41:15Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "Apni dekhen na nki"},
            {"ts": "2026-08-23T07:41:31Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "Koibar bolsi"},
            {"ts": "2026-08-23T07:41:31Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "?"},
            {"ts": "2026-08-23T07:41:32Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "6 number  Operation officer"},
            {"ts": "2026-08-23T07:42:11Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "বাল নাম্বার"},
        ]
    if thread == "8801931562049":
        P = "8801cc40a7d"
        return [
            {"ts": "2026-08-22T23:33:17Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "Can you tell me more about shipping job"},
            {"ts": "2026-08-22T23:35:25Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "Survey Scout/Escort কাজে জাহাজে মালামাল তদারকি, লোড-আনলোড হিসাব, চুরি/ক্ষতি রোধ ও রিপোর্টিং।"},
            {"ts": "2026-08-22T23:37:10Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "আমি shipping a security job খুজছি"},
            {"ts": "2026-08-22T23:37:43Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "আমাদের কাছে shipping আর security দুটোই আছে। Escort পদে জাহাজের মালামাল River Route Escort..."},
            {"ts": "2026-08-22T23:40:03Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "এটা কি সুধু বাংলাদেশ এ থাকতে হবে ?"},
            {"ts": "2026-08-22T23:40:20Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "দুঃখিত, বুঝতে পারিনি। দয়া করে শুধু আপনার পুরো নাম লিখুন (যেমনঃ Md Karim)।"},
            {"ts": "2026-08-22T23:41:35Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "Syed Mustafa musabbir"},
            {"ts": "2026-08-22T23:42:20Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "আমি শিপ বিভিন্ন দেশে যাওয়া আসার কাজ খুজছি"},
            {"ts": "2026-08-22T23:44:35Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "সিকিওরিটি হলে করবো"},
            {"ts": "2026-08-22T23:44:50Z", "role": "ai_outbound", "actor": "ai_recruitment",
             "speaker_phone": P, "text": "সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)"},
            {"ts": "2026-08-22T23:45:22Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P, "text": "আমার বয়স 43"},
        ]
    if thread == "8801805529211":
        P = "880127ebf60"
        return [
            {"ts": "2026-08-23T17:51:07Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P,
             "text": "Here's the extracted text from your handwritten image, organized for clarity:Whatsapp Message Board Functions Zero Employee Creation Daily Cash Payment Escort Orders and Escort Order Confirmations Ship Escort Attendance Guest Attendance Hallium Attendance Security Guard Attendance Supervisor Attendance Office Staff Attendance Employee WhatsApp Communication Need Money Program Completion / Escort Release Irregular Employee Regular Employee Regular → Long Jobs (Escort, Security Guard, Office) Recruitment Conversations Is the person an employee? Is the person a client? Does the person have no data based on their mobile number? Does the person want to know about available Career Opportunities, vacant posts, duties, responsibilities, salary, etc., based on a specific job position or all available positions? Office address, company name, contact number, training facility for deposit, etc. ✅ The text has been fully extracted and structured into clear sections. Would you like me to now format this into a structured table (with columns like Function, Sub-category, Notes) so it's easier to compare and refine?"},
            {"ts": "2026-08-23T17:51:18Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P,
             "text": "Here's the extracted text from your second handwritten image, organized into clear sections:Soh Glam -- AI Fabrication -- Scorpios Island Date: 01.07.2024 0. Whatsapp Bridge Whatsapp-mep-bridge + Meta-whatsapp-api 1. Employee Office Security Escort Management 2. Attendance Clients based Attendance Escort-orders-buyers-Escort-Clients Security Guards & Supervisors Ship Escort, Ghent Tally-man Office Staff 3. Advance and Payroll Daily Cash Payment Daily Salary Calculation Daily Payroll Sheet update Report: Incident-Employee Report Client based Expenditure/Salary Client based Bill/Invoice Generation 4. Media Processing Image + audio + voice 5. AI-Agent 'Hermes' Cloud AI Model → omninate → Local small model → 6. Database 7. HTML 8. Monitoring + Security ✅ The text has been fully extracted and structured."},
            {"ts": "2026-08-23T17:51:58Z", "role": "ai_outbound", "actor": "ai_unrouted_fallback",
             "speaker_phone": P,
             "text": "আপনার রিকোয়েস্ট পেয়েছি 🙏 যাচাইয়ের জন্য আপনার **Employee ID** বা **নাম** লিখুন। (workflow_triggered=attendance, classified as unknown send to attendance bot)"},
            {"ts": "2026-08-23T17:52:57Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P,
             "text": "Here's a structured comparative table combining the extracted information from both handwritten images..."},
            {"ts": "2026-08-23T17:53:47Z", "role": "candidate_inbound", "actor": "candidate",
             "speaker_phone": P,
             "text": "Here's the refined comparative table with a summary row added at the bottom to highlight overlaps and differences..."},
        ]
    raise ValueError("unknown thread " + repr(thread))


# ----- Case definitions ------
CASES = []

# cf_R001
CASES.append({
    "case_id": "cf_R001",
    "title": "Question misclassified mixed; intake advanced; question ignored",
    "thread_phone_real": "8801904291594",
    "target_message": "-চাকরি কি হবে??",
    "source_case_type": "C",
    "inbound_step_at_turn": "name",
    "session_fields_at_turn": {
        "name": "Mohd Ashikur Rahman",
        "age": None,
        "area": "Narayanganj",
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed (legacy classify_turn_intent classified as mixed: question regex matched 'কি কাজ' AND name regex matched)",
        "should_advance_intake": "True (legacy intake_message ran; no value extracted)",
        "outbound_text_seen": "দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন। আপনি কোন পদে কাজ করতে চান?",
        "rule_that_failed": "Legacy dispatch cannot produce SAVE VALID FIELD + ANSWER + ADVANCE simultaneously; here, no field was supplied, but intake stepped anyway",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_question",
        "expected_intake_step_before": "name",
        "expected_intake_fields_proposed": {
            "name": None,
            "age": None,
            "dob": None,
            "phone": None,
            "job_preference": None,
            "question_markers": ["কি হবে", "?"],
            "fee_markers": []
        },
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {"name": "question text matched name regex but _looks_like_name returned False"},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "name",
        "expected_questions": [
            {"topic": "available_posts_or_job_nature", "must_answer_from_source_of_truth": True,
             "evidence": "candidate explicitly asks 'চাকরি কি হবে??' = what job will it be?"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": True,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do NOT silent-route to other workflows; do NOT fabricate a specific post or salary",
        "phase_that_must_close_this": "C",
        "reason_evidence": "ACTUAL replay: AI replied with post-list prompt. EXPECTED: answer the question with the source-of-truth available-post section; do NOT advance intake (name already supplied).",
    },
})

# cf_R002
CASES.append({
    "case_id": "cf_R002",
    "title": "Question misclassified mixed (job duties ask, name-step)",
    "thread_phone_real": "8801832365139",
    "target_message": "কাজের দায়িত্ব ও ডিটেইলস জানতে চায়",
    "source_case_type": "C",
    "inbound_step_at_turn": "name",
    "session_fields_at_turn": {
        "name": None,
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "True (legacy advanced from name)",
        "outbound_text_seen": "কোন পদের কাজের দায়িত্ব ও ডিটেইলস জানতে চান? Security Guard, Escort, Operation Officer, Marketing Officer কিংবা অন্য কিছু?",
        "rule_that_failed": "Question regex matched 'জানতে চাই'; name regex matched",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_question",
        "expected_intake_step_before": "name",
        "expected_intake_fields_proposed": {"name": None, "question_markers": ["জানতে চাই", "কাজের"]},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {"name": "phrasing was a question, not a name"},
        "expected_should_advance_intake": True,
        "expected_intake_step_after": "job_preference",
        "expected_questions": [
            {"topic": "job_duties_per_post", "must_answer_from_source_of_truth": True,
             "evidence": "candidate asks about duties per post; 4 posts have detailed duty sections in source-of-truth"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": True,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": True,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "ask which post if ambiguous; or summarise duties per post from source-of-truth",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: cite duty sections of source-of-truth (Operation Officer, Security Guard, Marketing Officer, Escort/Survey Scout); ask which post if ambiguous; advance step on provided answer.",
    },
})

# cf_R003
CASES.append({
    "case_id": "cf_R003",
    "title": "Question misclassified mixed (cross-border ask at name-step)",
    "thread_phone_real": "8801931562049",
    "target_message": "এটা কি সুধু বাংলাদেশ এ থাকতে হবে ?",
    "source_case_type": "C",
    "inbound_step_at_turn": "name",
    "session_fields_at_turn": {
        "name": None,
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "True (legacy asked for full name via payment_due workflow)",
        "outbound_text_seen": "দুঃখিত, বুঝতে পারিনি। দয়া করে শুধু আপনার পুরো নাম লিখুন (যেমনঃ Md Karim)।",
        "rule_that_failed": "Question matched 'কি'; name regex matched; legacy treated as name step",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_question",
        "expected_intake_step_before": "name",
        "expected_intake_fields_proposed": {"name": None, "question_markers": ["কি", "থাকতে হবে", "?"]},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {"name": "question, not a name"},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "name",
        "expected_questions": [
            {"topic": "geographic_scope_or_domestic_only", "must_answer_from_source_of_truth": True,
             "evidence": "candidate asks if work stays within Bangladesh"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": True,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do not route to payment_due workflow",
        "phase_that_must_close_this": "C",
        "reason_evidence": "Source-of-truth line 'সাধারণত বাংলাদেশের অভ্যন্তরে (Mother Vessel/Lighter Vessel/Ship Stay/River Route) কাজ।' should be in answer.",
    },
})

# cf_R004
CASES.append({
    "case_id": "cf_R004",
    "title": "Conditional preference update at age-step",
    "thread_phone_real": "8801931562049",
    "target_message": "সিকিওরিটি হলে করবো",
    "source_case_type": "C",
    "inbound_step_at_turn": "age",
    "session_fields_at_turn": {
        "name": "Syed Mustafa musabbir",
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "False",
        "outbound_text_seen": "সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)",
        "rule_that_failed": "AI stuck on age prompt; didn't recognise 'security হলে' as preference",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "mixed",
        "expected_intake_step_before": "age",
        "expected_intake_fields_proposed": {"age": None, "job_preference": "Security"},
        "expected_intake_fields_accepted": {"job_preference": "Security (conditional)"},
        "expected_intake_fields_rejected": {"age": "no age value present"},
        "expected_should_advance_intake": True,
        "expected_intake_step_after": "age",
        "expected_questions": [
            {"topic": "age_for_eligibility", "must_answer_from_source_of_truth": False,
             "evidence": "still need age to proceed"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {"job_preference": "candidate hinted Security preference; record pending age confirmation"},
        "expected_outbound_count": 1,
        "expected_factcheck_required": True,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do not infer age; do not ignore conditional preference",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: acknowledge 'Security (প্রাথমিক পছন্দ)'; still ask for age once; do not finalise preference without confirmation.",
    },
})

# cf_R005
CASES.append({
    "case_id": "cf_R005",
    "title": "Recommendation-request classified mixed at job_preference",
    "thread_phone_real": "8801904291594",
    "target_message": "-আমার বডিফিটনেস দেখে বলেন,,আমাকে কোন পদে দেওয়া যায়??",
    "source_case_type": "C",
    "inbound_step_at_turn": "job_preference",
    "session_fields_at_turn": {
        "name": "Mohd Ashikur Rahman",
        "age": 30,
        "area": "Narayanganj",
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "True (legacy advance)",
        "outbound_text_seen": "সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)",
        "rule_that_failed": "Question regex matched; AI picked the wrong prompt",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_question",
        "expected_intake_step_before": "job_preference",
        "expected_intake_fields_proposed": {"job_preference": None, "question_markers": ["কোন পদে", "দেওয়া যায়", "??"]},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {"job_preference": "no specific post; recommendation question"},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "job_preference",
        "expected_questions": [
            {"topic": "post_recommendation_for_body_fitness", "must_answer_from_source_of_truth": True,
             "evidence": "candidate asks which post suits his body fitness"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": True,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do not invent post-suitability answers; defer to office per 'নির্দিষ্ট পদে আপনার যোগ্যতা প্রযোজ্য কিনা তা অফিসে সরাসরি যাচাই করে জানানো হবে।'",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: cite source-of-truth post-suitability caveat; do NOT advance (no value supplied).",
    },
})

# cf_R006
CASES.append({
    "case_id": "cf_R006",
    "title": "Final-acknowledgement mis-acknowledged at done-step",
    "thread_phone_real": "8801904291594",
    "target_message": "-ধন্যবাদ 🌹🌹",
    "source_case_type": "C",
    "inbound_step_at_turn": "done",
    "session_fields_at_turn": {
        "name": "Mohd Ashikur Rahman",
        "age": 30,
        "area": "Narayanganj",
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "pure_intake (no fields, no questions)",
        "should_advance_intake": "False",
        "outbound_text_seen": "দয়া করে ১ থেকে ৮ নম্বর বা পদের নাম লিখুন। আপনি কোন পদে কাজ করতে চান?",
        "rule_that_failed": "Legacy sent another post-list prompt after candidate closure signal",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "acknowledgement",
        "expected_intake_step_before": "done",
        "expected_intake_fields_proposed": {},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "done",
        "expected_questions": [],
        "expected_should_answer_question": False,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 0,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do NOT re-send post-list prompt after closure; no outbound at all",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: silent acknowledgement (no outbound). Legacy sent an unwanted post-list re-prompt.",
    },
})

# cf_R007
CASES.append({
    "case_id": "cf_R007",
    "title": "Meta/Facebook ad opener at name-step",
    "thread_phone_real": "8801317110473",
    "target_message": "Hello! Can I get more info on this?",
    "source_case_type": "C",
    "inbound_step_at_turn": "name",
    "session_fields_at_turn": {
        "name": None,
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "True (legacy advance name -> age)",
        "outbound_text_seen": "Hi! Sure, I can help. May I know your name and which position you're interested in -- for example, Security Guard, Escort, ...",
        "rule_that_failed": "English opener matched question regex + name regex",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_question",
        "expected_intake_step_before": "name",
        "expected_intake_fields_proposed": {"name": None, "question_markers": ["more info", "?"]},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {"name": "this is the standard Meta-WhatsApp ad opener; not a name"},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "name",
        "expected_questions": [
            {"topic": "general_recruitment_intro", "must_answer_from_source_of_truth": True,
             "evidence": "candidate asks generically for more info"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": True,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do NOT classify as candidate name; do NOT advance intake",
        "phase_that_must_close_this": "C",
        "reason_evidence": "Source-of-truth available-post section + office contact should be the reply, not a name question.",
    },
})

# cf_R008 - deterministic parser (Phase B)
CASES.append({
    "case_id": "cf_R008",
    "title": "Bengali multi-digit age parser fails",
    "thread_phone_real": "8801904291594",
    "target_message": "*বয়স:৩০বছর",
    "source_case_type": "A",
    "inbound_step_at_turn": "age",
    "session_fields_at_turn": {
        "name": "Mohd Ashikur Rahman",
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "pure_question (question regex matched 'বয়স')",
        "should_advance_intake": "False",
        "outbound_text_seen": "সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)",
        "rule_that_failed": "_STEP_HAS_VALUE_RE['age'] regex only accepts single-digit Bengali or 1-2 ASCII digits; missing multi-digit Bengali '৩০'",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_intake",
        "expected_intake_step_before": "age",
        "expected_intake_fields_proposed": {"age": 30},
        "expected_intake_fields_accepted": {"age": 30},
        "expected_intake_fields_rejected": {},
        "expected_should_advance_intake": True,
        "expected_intake_step_after": "area",
        "expected_questions": [],
        "expected_should_answer_question": False,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "extract age=30; advance step",
        "phase_that_must_close_this": "B",
        "reason_evidence": "EXPECTED: parser recognizes '৩০বছর' as age=30; intake advances to area; no question reply required.",
    },
})

# cf_R009
CASES.append({
    "case_id": "cf_R009",
    "title": "DOB string mistakenly parsed as age 31",
    "thread_phone_real": "8801904291594",
    "target_message": "-ভাই আমার জন্ম তারিখ : ৩১/১২/১৯৯৬ সাল,,,সেই হিসাবে আমার বয়স এখন ৩০বছর।।",
    "source_case_type": "A",
    "inbound_step_at_turn": "age",
    "session_fields_at_turn": {
        "name": "Mohd Ashikur Rahman",
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "True (legacy extracted age=31 and advanced step)",
        "outbound_text_seen": "আপনি কোন জেলায় থাকেন? (বর্তমান ঠিকানা)",
        "rule_that_failed": "_parse_age grabbed the first multi-digit Bengali pair '৩১' (day-of-month from DOB) as age; no DOB gate existed",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_question (DOB is not a free-form age; clarify)",
        "expected_intake_step_before": "age",
        "expected_intake_fields_proposed": {"age": None, "dob": "31/12/1996"},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {"age": "_looks_like_dob(text)=True -> age not extracted from DOB format"},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "age",
        "expected_questions": [
            {"topic": "age_value_to_use", "must_answer_from_source_of_truth": False,
             "evidence": "candidate gave DOB; clarify"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": True,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do NOT extract age=31 from DOB; ask for plain age value",
        "phase_that_must_close_this": "B",
        "reason_evidence": "EXPECTED: detect DOB format, refuse to parse as age, ask for the bare age value. Do NOT advance step.",
    },
})

# cf_R010
CASES.append({
    "case_id": "cf_R010",
    "title": "Frustration marker '???' classified as question",
    "thread_phone_real": "8801832365139",
    "target_message": "???",
    "source_case_type": "C",
    "inbound_step_at_turn": "job_preference",
    "session_fields_at_turn": {
        "name": None,
        "age": 24,
        "area": "Mymensingh",
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "pure_question",
        "should_advance_intake": "False",
        "outbound_text_seen": "আপনি কোন পদে কাজ করতে চান? ১. Escort ২. Survey Scout ...",
        "rule_that_failed": "Question regex matches '?'; frustration has no special handling",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "frustration",
        "expected_intake_step_before": "job_preference",
        "expected_intake_fields_proposed": {"frustration_marker": "explicit ???", "job_preference": None},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "job_preference",
        "expected_questions": [],
        "expected_should_answer_question": False,
        "expected_needs_clarification": True,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "treat as frustration; offer concise recap of intake state so far + what is still needed; do not re-ask post-list",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: short acknowledgement that intake= name=? age=24 area=Mymensingh job_preference=? -- what is still needed",
    },
})

# cf_R011 - Banglish frustration
CASES.append({
    "case_id": "cf_R011",
    "title": "Banglish frustration-marker treated as pure intake",
    "thread_phone_real": "8801832365139",
    "target_message": "Koibar bolsi",
    "source_case_type": "C",
    "inbound_step_at_turn": "experience",
    "session_fields_at_turn": {
        "name": None,
        "age": 24,
        "area": "Mymensingh",
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "pure_intake (no question or intake markers matched)",
        "should_advance_intake": "False",
        "outbound_text_seen": "এই ক্ষেত্রে আপনার কত বছরের অভিজ্ঞতা আছে? (নতুন হলে 0 লিখুন)",
        "rule_that_failed": "frustration has no special handling",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "frustration",
        "expected_intake_step_before": "experience",
        "expected_intake_fields_proposed": {"frustration_marker": "Koibar bolsi"},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": "experience",
        "expected_questions": [],
        "expected_should_answer_question": False,
        "expected_needs_clarification": True,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "treat as Banglish frustration; offer recap of intake state + clear instruction on next step (job_preference is still missing, NOT experience)",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: recap that name=?, age=24, area=Mymensingh, job_preference=? -- ask the missing field, not the experience field (which would be the next step after job_preference).",
    },
})

# cf_R012
CASES.append({
    "case_id": "cf_R012",
    "title": "Alternative job preference at phone_confirm-step ignored",
    "thread_phone_real": "8801317110473",
    "target_message": "আর না হলে ৭ নাম্বার হলে হবে সিকিউরিটি ইনচাজ",
    "source_case_type": "C",
    "inbound_step_at_turn": "phone_confirm",
    "session_fields_at_turn": {
        "name": "Mohd Shaha Alam",
        "age": 21,
        "area": "Gaibandha",
        "job_preference": "Operation Officer (supplied earlier as '৬ operation officer')",
        "experience": 0,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "False",
        "outbound_text_seen": "আপনার কনফার্মেশনের জন্য: আপনার মোবাইল নম্বর কত? (যেটায় কল করতে পারব)",
        "rule_that_failed": "Legacy is at phone_confirm step and ignores the alternative preference offer",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "mixed",
        "expected_intake_step_before": "phone_confirm",
        "expected_intake_fields_proposed": {
            "phone_confirm": None,
            "job_preference": "Security In-Charge (alternative offered)"
        },
        "expected_intake_fields_accepted": {"job_preference": "candidate offered Security In-Charge (conditional on Operation Officer not available)"},
        "expected_intake_fields_rejected": {"phone_confirm": "no phone value supplied"},
        "expected_should_advance_intake": True,
        "expected_intake_step_after": "phone_confirm",
        "expected_questions": [
            {"topic": "which_post_final_choice", "must_answer_from_source_of_truth": False,
             "evidence": "candidate offers conditional preference"}
        ],
        "expected_should_answer_question": True,
        "expected_needs_clarification": True,
        "expected_changes_or_corrections": {"job_preference": "Operation Officer -> alternative Security In-Charge if Operation Officer unavailable"},
        "expected_outbound_count": 1,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "acknowledge both Operation Officer and Security In-Charge as valid posts; ask candidate to confirm; do NOT silently fix the preference",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: record the alternative preference; acknowledge both posts are valid; ask for phone confirmation; do not jump ahead",
    },
})

# cf_R013
CASES.append({
    "case_id": "cf_R013",
    "title": "Repeated age-value not recognized as field-locked; AI re-asks 4x",
    "thread_phone_real": "8801904291594",
    "target_message": "-আমার বয়স: ৩০বছর।।",
    "source_case_type": "C",
    "inbound_step_at_turn": "age",
    "session_fields_at_turn": {
        "name": "Mohd Ashikur Rahman",
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "recruitment",
        "turn_type": "mixed",
        "should_advance_intake": "False (legacy did not advance; AI re-prompted age-limit every time)",
        "outbound_text_seen": "সাধারণ বয়সসীমা ১৮–৫৫ বছর। দয়া করে সঠিক বয়স লিখুন (যেমন: 25)",
        "rule_that_failed": "Repeated same value; legacy doesn't carry forward locked value (parser also fails on Bengali multi-digit)",
    },
    "expected": {
        "expected_domain": "recruitment",
        "expected_turn_type": "pure_intake",
        "expected_intake_step_before": "age",
        "expected_intake_fields_proposed": {"age": 30},
        "expected_intake_fields_accepted": {"age": 30},
        "expected_intake_fields_rejected": {},
        "expected_should_advance_intake": True,
        "expected_intake_step_after": "area",
        "expected_questions": [],
        "expected_should_answer_question": False,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "if age already accepted once, accept again silently without re-asking; advance step; no question reply",
        "phase_that_must_close_this": "C",
        "reason_evidence": "EXPECTED: parser extracts age=30; intake advances age -> area; brief acknowledgement that age accepted. The pattern also requires cf_R008 parser fix (Phase B closes parser leg, Phase C closes the locked-field leg).",
    },
})

# cf_W001
CASES.append({
    "case_id": "cf_W001",
    "title": "Recruitment-domain OCR'd text misrouted to operational workflows",
    "thread_phone_real": "8801805529211",
    "target_message": "Here's the extracted text from your handwritten image, organized for clarity: ... Recruitment Conversations ... Office address, company name, contact number, training facility for deposit, etc. ✅ The text has been fully extracted and structured into clear sections. Would you like me to now format this into a structured table ...",
    "source_case_type": "D",
    "inbound_step_at_turn": None,
    "session_fields_at_turn": {
        "name": None,
        "age": None,
        "area": None,
        "job_preference": None,
        "experience": None,
        "phone_confirm": None,
    },
    "actual_outcome_at_turn": {
        "domain": "attendance (legacy replied with attendance bot template)",
        "turn_type": "classified as payment_due by intent engine (first-match-wins on OCR text containing 'টাকা'/'জাহাজ'/'ডিউটি'/'পেমেন্ট')",
        "should_advance_intake": "False (recruitment side rejected at recruitment_eligibility:919-921 short-circuit)",
        "outbound_text_seen": "আপনার রিকোয়েস্ট পেয়েছি 🙏 যাচাইয়ের জন্য আপনার **Employee ID** বা **নাম** লিখুন।",
        "rule_that_failed": "intent in OPERATIONAL_INTENTS short-circuit; legacy single-domain dispatch",
    },
    "expected": {
        "expected_domain": "multi_domain (recruitment-system architecture discussion; not a candidate application)",
        "expected_turn_type": "unrelated / multi_domain (architecture content, not a candidate application)",
        "expected_intake_step_before": None,
        "expected_intake_fields_proposed": {},
        "expected_intake_fields_accepted": {},
        "expected_intake_fields_rejected": {},
        "expected_should_advance_intake": False,
        "expected_intake_step_after": None,
        "expected_questions": [],
        "expected_should_answer_question": False,
        "expected_needs_clarification": False,
        "expected_changes_or_corrections": {},
        "expected_outbound_count": 1,
        "expected_factcheck_required": False,
        "expected_no_fabricated_fact": True,
        "expected_protected_domain_behavior": "do NOT route to payment/attendance/slip/client_order/leave; do NOT fabricate that the sender is an employee needing ID lookup; defer to office or HR channel",
        "phase_that_must_close_this": "D",
        "reason_evidence": "EXPECTED: detect that OCR text discusses recruitment-system architecture (not a candidate application); route-off-recruitment with a safe pointer to office (e.g. 'আপনার প্রশ্নটি recruitment-system-এর design বিষয়ে -- architecture আলোচনায় পারদর্শী নই। অফিস: 01958-122300।'). Must NOT be hijacked to attendance/payment_due.",
    },
})

# ---- Writer ----

def write_case_files(case):
    phone_real = case["thread_phone_real"]
    phone_masked = mask_admin(phone_real)
    case_id = case["case_id"]
    title_slug = (case["title"].lower()
                  .replace(" ", "_").replace("-", "_").replace("'", "")
                  .replace("__", "_").replace("?", "").replace("/", "_"))
    title_slug = title_slug[:50]
    case_dir = ROOT / "cases" / f"{case_id}_{title_slug}"
    case_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "_schema": "recruitment-regression-case-meta/v1",
        "case_id": case_id,
        "thread_phone_real": phone_real,
        "thread_phone_masked": phone_masked,
        "is_admin_thread": is_admin(phone_real),
        "actual_outcome_notes": case["actual_outcome_at_turn"],
        "privacy_notes": (
            "Real candidate phone numbers are salted-hash masked "
            "(see _meta/corpus_meta.json#mask_algorithm). Admin phone numbers "
            "are prefixed 'admin_' and salted-hash masked. Inbound message text "
            "and AI outbound message text are PRESERVED VERBATIM because typos, "
            "bilingual mixing, and punctuation are part of the failure-bearing signal."
        ),
    }
    (case_dir / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    inp = {
        "_schema": "recruitment-regression-case-input/v1",
        "case_id": case_id,
        "thread_phone_real": phone_real,
        "thread_phone_masked": phone_masked,
        "inbound_step_at_turn": case["inbound_step_at_turn"],
        "session_fields_at_turn": case["session_fields_at_turn"],
        "inbound_message": case["target_message"],
    }
    (case_dir / "input.json").write_text(json.dumps(inp, ensure_ascii=False, indent=2) + "\n")

    full_conv = conv_for(phone_real)
    with (case_dir / "conversation.jsonl").open("w") as f:
        for m in full_conv:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    expected = {"_schema": "recruitment-regression-case-expected/v1",
                "case_id": case_id,
                "source_case_type": case["source_case_type"],
                **case["expected"]}
    (case_dir / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n")

    return case_dir

written = []
for c in CASES:
    d = write_case_files(c)
    written.append((c["case_id"], d))

with (ROOT / "index.jsonl").open("w") as f:
    for cid, d in written:
        f.write(json.dumps({"case_id": cid, "path": str(d.relative_to(ROOT))}, ensure_ascii=False) + "\n")

print("Wrote %d cases:" % len(written))
for cid, d in written:
    print("  %s -> %s" % (cid, d.relative_to(ROOT)))
