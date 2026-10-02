-- Migration 086: K3.2 Recruitment knowledge reconciliation.
--
-- The JSON authority remains canonical. This migration only removes legacy
-- Recruitment rows from current DB consumers and deprecates the dormant
-- governance copy. Values, IDs, creation timestamps, and provenance remain.
-- Do not run against a live database until the owner approves K3.2.

BEGIN;

CREATE TEMP TABLE _k32_recruitment_faq_expected (
    id      BIGINT PRIMARY KEY,
    key     TEXT NOT NULL UNIQUE,
    retain  BOOLEAN NOT NULL DEFAULT FALSE
) ON COMMIT DROP;

INSERT INTO _k32_recruitment_faq_expected (id, key, retain) VALUES
    (107, 'b11_job_details', false),
    (108, 'b11_office_address', false),
    (109, 'b11_apply_intent', false),
    (110, 'b11_anti_fraud', false),
    (111, 'b11_documents', false),
    (112, 'b11_madrasah_ok', false),
    (113, 'b11_vacancy_status', false),
    (114, 'b11_no_fee', false),
    (115, 'b11_salary', false),
    (116, 'b11_salary_complaint', false),
    (117, 'b11_greeting', true),
    (119, 'company_overview_career', false),
    (120, 'available_positions_multi', false),
    (121, 'security_guard_duties', false),
    (122, 'escort_duties_vessel', false),
    (125, 'supervisor_duties', false),
    (126, 'survey_scout_duties', false),
    (127, 'operation_officer_duties', false),
    (128, 'operation_officer_qualification', false),
    (129, 'operation_officer_salary', false),
    (130, 'operation_officer_career_path', false),
    (131, 'marketing_officer_duties', false),
    (132, 'marketing_officer_qualification', false),
    (133, 'marketing_officer_salary_commission', false),
    (134, 'security_incharge_duties', false),
    (135, 'security_incharge_qualification_salary', false),
    (136, 'security_incharge_career_path', false),
    (137, 'ghat_supervisor_duties', false),
    (138, 'ghat_supervisor_qualification_salary', false),
    (139, 'ghat_supervisor_work_type', false),
    (140, 'application_initial_info', false),
    (141, 'office_hours_candidates', false),
    (142, 'required_documents_full', false),
    (143, 'admission_security_deposit_policy', false),
    (144, 'admission_fee_refund_policy', false),
    (145, 'training_duration_content', false),
    (146, 'training_facilities_food', false),
    (147, 'training_salary_may_not_have', false),
    (148, 'probation_period_general_scout', false),
    (149, 'probation_evaluation_criteria', false),
    (150, 'duty_reporting_time', false),
    (151, 'uniform_cleanliness_rules', false),
    (152, 'prohibited_duty_behaviour', false),
    (153, 'post_handover_rule', false),
    (154, 'trainee_salary_range', false),
    (155, 'permanent_salary_package', false),
    (156, 'salary_components', false),
    (157, 'salary_payment_date', false),
    (158, 'salary_payment_method', false),
    (159, 'provident_fund_benefit', false),
    (160, 'insurance_benefit', false),
    (161, 'leave_benefits', false),
    (162, 'emergency_advance', false),
    (163, 'conveyance_allowance', false),
    (164, 'apply_how_to', false),
    (165, 'training_mandatory', false),
    (166, 'joining_formalities', false),
    (167, 'joining_payment_general_safe', false),
    (168, 'resignation_notice', false),
    (169, 'chain_of_command', false),
    (172, 'bkash_nagad_payment_info', false),
    (174, 'career_growth_general', false),
    (175, 'job_eligibility_age', false),
    (176, 'job_eligibility_education', false),
    (177, 'complaint_grievance', false),
    (178, 'interest_intake', false);

CREATE TEMP TABLE _k32_recruitment_governance_expected (
    id       UUID PRIMARY KEY,
    fact_key TEXT NOT NULL UNIQUE
) ON COMMIT DROP;

INSERT INTO _k32_recruitment_governance_expected (id, fact_key) VALUES
    ('33cc9acf-2bff-431a-83a5-9f0b1b3b204b', 'age_range'),
    ('8d30d1be-e191-48ab-b508-caf918f3b376', 'education_required'),
    ('10d7caf7-cd80-4aa0-8da5-eea25aa2fab4', 'joining_fee'),
    ('2edeab73-b3c4-4202-b02d-648864b9d5e5', 'joining_fee_refund'),
    ('b7798e75-8461-4ef4-b7ea-31f80a0e9e60', 'min_upfront_payment'),
    ('3e387cc3-8a2c-4429-9837-410a16b7149b', 'monthly_deduction'),
    ('8d658ca1-52d0-4859-9650-2b6c298817c9', 'no_leave_first'),
    ('b9e64026-97df-492c-928f-5d48b3e1bdac', 'resignation_notice'),
    ('f3972e1d-cb71-42cd-a61a-6a9d282a4457', 'resignation_penalty');

DO $$
DECLARE
    missing_faq INTEGER;
    mismatched_faq INTEGER;
    active_policy_faq INTEGER;
    unexpected_active_faq INTEGER;
    missing_governance INTEGER;
    mismatched_governance INTEGER;
    active_governance INTEGER;
    deprecated_governance INTEGER;
BEGIN
    SELECT count(*) INTO missing_faq
    FROM _k32_recruitment_faq_expected e
    LEFT JOIN fazle_knowledge_base k ON k.id = e.id
    WHERE k.id IS NULL;
    IF missing_faq <> 0 THEN
        RAISE EXCEPTION 'K3.2 precondition failed: % expected FAQ IDs are missing', missing_faq;
    END IF;

    SELECT count(*) INTO mismatched_faq
    FROM _k32_recruitment_faq_expected e
    JOIN fazle_knowledge_base k ON k.id = e.id
    WHERE k.category <> 'recruitment' OR k.key <> e.key;
    IF mismatched_faq <> 0 THEN
        RAISE EXCEPTION 'K3.2 precondition failed: % FAQ IDs have unexpected category/key', mismatched_faq;
    END IF;

    SELECT count(*) INTO unexpected_active_faq
    FROM fazle_knowledge_base k
    WHERE k.category = 'recruitment'
      AND k.is_active IS TRUE
      AND NOT EXISTS (SELECT 1 FROM _k32_recruitment_faq_expected e WHERE e.id = k.id);
    IF unexpected_active_faq <> 0 THEN
        RAISE EXCEPTION 'K3.2 precondition failed: % unexpected active Recruitment FAQ rows exist', unexpected_active_faq;
    END IF;

    SELECT count(*) INTO active_policy_faq
    FROM _k32_recruitment_faq_expected e
    JOIN fazle_knowledge_base k ON k.id = e.id
    WHERE NOT e.retain AND k.is_active IS TRUE;
    IF active_policy_faq NOT IN (0, 65) THEN
        RAISE EXCEPTION 'K3.2 precondition failed: expected 65 policy FAQ rows active or already suppressed, found %', active_policy_faq;
    END IF;

    SELECT count(*) INTO missing_governance
    FROM _k32_recruitment_governance_expected e
    LEFT JOIN fazle_knowledge_governance g ON g.id = e.id
    WHERE g.id IS NULL;
    IF missing_governance <> 0 THEN
        RAISE EXCEPTION 'K3.2 precondition failed: % expected governance IDs are missing', missing_governance;
    END IF;

    SELECT count(*) INTO mismatched_governance
    FROM _k32_recruitment_governance_expected e
    JOIN fazle_knowledge_governance g ON g.id = e.id
    WHERE g.category <> 'recruitment' OR g.fact_key <> e.fact_key;
    IF mismatched_governance <> 0 THEN
        RAISE EXCEPTION 'K3.2 precondition failed: % governance IDs have unexpected category/key', mismatched_governance;
    END IF;

    SELECT count(*) INTO active_governance
    FROM _k32_recruitment_governance_expected e
    JOIN fazle_knowledge_governance g ON g.id = e.id
    WHERE g.status = 'active';
    SELECT count(*) INTO deprecated_governance
    FROM _k32_recruitment_governance_expected e
    JOIN fazle_knowledge_governance g ON g.id = e.id
    WHERE g.status = 'deprecated' AND g.deprecated_at IS NOT NULL;
    IF active_governance NOT IN (0, 9)
       OR (active_governance = 0 AND deprecated_governance <> 9) THEN
        RAISE EXCEPTION 'K3.2 precondition failed: governance rows are partially reconciled';
    END IF;
END $$;

UPDATE fazle_knowledge_base k
SET is_active = FALSE
FROM _k32_recruitment_faq_expected e
WHERE k.id = e.id
  AND e.retain IS FALSE
  AND k.is_active IS TRUE;

UPDATE fazle_knowledge_governance g
SET status = 'deprecated',
    deprecated_at = COALESCE(g.deprecated_at, NOW()),
    deprecation_reason = COALESCE(
        g.deprecation_reason,
        'K3.2: legacy Recruitment governance record is not the canonical JSON authority'
    )
FROM _k32_recruitment_governance_expected e
WHERE g.id = e.id
  AND g.status = 'active';

COMMIT;
