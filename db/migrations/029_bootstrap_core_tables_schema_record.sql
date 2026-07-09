-- Migration: Bootstrap schema record for 27 production tables with no prior
-- migration source of truth (System Consolidation Sprint, Task 5, 2026-07-08).
--
-- Context: SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md Report 4 found 24 tables
-- that exist in production but have no CREATE TABLE anywhere in this repo
-- except a guessed reconstruction in tests/conftest.py (already known to
-- disagree with production). Two of them are confirmed live production
-- writers: fazle_error_log, llm_conversation_log.
--
-- While verifying this migration against a disposable scratch database
-- (see the accompanying implementation report), 3 MORE undocumented tables
-- were discovered — wbom_business_types, wbom_relation_types, and
-- wbom_message_templates — required as live foreign-key targets from
-- wbom_contacts and wbom_whatsapp_messages respectively. Same gap, same fix,
-- included below. The true count of "no schema source of truth" tables was
-- 27, not 24 — this is reported honestly rather than silently expanded.
--
-- Source of truth for every statement below: pg_dump --schema-only run
-- directly against the live production database (docker exec ai-postgres),
-- 2026-07-08 — NOT reconstructed from tests/conftest.py.
--
-- Safety:
--   * Every CREATE TABLE / CREATE SEQUENCE / CREATE INDEX uses IF NOT EXISTS
--     — a no-op against the current production database, where all 24
--     tables already exist. This file is a paper record + a genuine bootstrap
--     for a clean/disaster-recovery environment, not a change to production.
--   * Every PRIMARY KEY / UNIQUE / FOREIGN KEY / CHECK constraint added via
--     ALTER TABLE is wrapped in a guarded DO block that checks pg_constraint
--     first, so it is also a no-op against the current database.
--   * One trigger (wbom_whatsapp_messages) depends on a function,
--     wbom_msg_autofill(), which was ALSO found to have no prior migration
--     source anywhere in this repo — its definition (captured live via
--     pg_get_functiondef) is included below for the same reason. CREATE OR
--     REPLACE FUNCTION is inherently idempotent; the trigger is preceded by
--     DROP TRIGGER IF EXISTS for the same reason.
--   * Nothing in this file drops, renames, or destructively alters anything.
--   * setval()/sequence-value statements from the pg_dump were deliberately
--     omitted — this file establishes schema shape, not current data state.
--
-- This migration WILL be picked up and applied by db/migrate.py the next
-- time it runs (new filename, never before in schema_migrations) — by
-- design, since every statement here is a safe no-op against the current
-- database. Verified end-to-end against a disposable scratch database
-- before being added here (see the accompanying implementation report).

-- Required by idx_wbom_employees_name_trgm below (production already has
-- this enabled; a fresh/disaster-recovery environment would not).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Dependency tables discovered during verification of this migration
-- (wbom_contacts has live FK constraints referencing both of these; neither
-- has any prior migration source anywhere in this repo either — same gap
-- as the 24 tables above, found by validating this file against a fresh
-- scratch database rather than assuming the 24-table list was complete).

CREATE TABLE IF NOT EXISTS public.wbom_business_types (
    business_type_id integer NOT NULL,
    business_name character varying(100) NOT NULL,
    service_category character varying(50),
    default_templates jsonb,
    is_active boolean DEFAULT true
);

CREATE TABLE IF NOT EXISTS public.wbom_relation_types (
    relation_type_id integer NOT NULL,
    relation_name character varying(50) NOT NULL,
    description text,
    greeting_template text,
    is_active boolean DEFAULT true
);

CREATE SEQUENCE IF NOT EXISTS public.wbom_business_types_business_type_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.wbom_business_types_business_type_id_seq OWNED BY public.wbom_business_types.business_type_id;
ALTER TABLE ONLY public.wbom_business_types ALTER COLUMN business_type_id SET DEFAULT nextval('public.wbom_business_types_business_type_id_seq'::regclass);

CREATE SEQUENCE IF NOT EXISTS public.wbom_relation_types_relation_type_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.wbom_relation_types_relation_type_id_seq OWNED BY public.wbom_relation_types.relation_type_id;
ALTER TABLE ONLY public.wbom_relation_types ALTER COLUMN relation_type_id SET DEFAULT nextval('public.wbom_relation_types_relation_type_id_seq'::regclass);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_business_types_pkey'
    ) THEN
        ALTER TABLE public.wbom_business_types ADD CONSTRAINT wbom_business_types_pkey PRIMARY KEY (business_type_id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_relation_types_pkey'
    ) THEN
        ALTER TABLE public.wbom_relation_types ADD CONSTRAINT wbom_relation_types_pkey PRIMARY KEY (relation_type_id);
    END IF;
END $$;

-- One more dependency found the same way: wbom_whatsapp_messages has a live
-- FK to wbom_message_templates, also with no prior migration source anywhere.
CREATE TABLE IF NOT EXISTS public.wbom_message_templates (
    template_id integer NOT NULL,
    template_name character varying(100) NOT NULL,
    template_type character varying(30) NOT NULL,
    template_body text NOT NULL,
    required_fields jsonb,
    optional_fields jsonb,
    extraction_patterns jsonb,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS public.wbom_message_templates_template_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.wbom_message_templates_template_id_seq OWNED BY public.wbom_message_templates.template_id;
ALTER TABLE ONLY public.wbom_message_templates ALTER COLUMN template_id SET DEFAULT nextval('public.wbom_message_templates_template_id_seq'::regclass);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_message_templates_pkey'
    ) THEN
        ALTER TABLE public.wbom_message_templates ADD CONSTRAINT wbom_message_templates_pkey PRIMARY KEY (template_id);
    END IF;
END $$;




--
--

--
-- Name: wbom_attendance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_attendance (
    attendance_id integer NOT NULL,
    employee_id integer NOT NULL,
    attendance_date date NOT NULL,
    status character varying(20) DEFAULT 'Present'::character varying NOT NULL,
    location character varying(100),
    check_in_time timestamp with time zone,
    check_out_time timestamp with time zone,
    remarks text,
    recorded_by character varying(50),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: wbom_employees; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_employees (
    employee_id integer NOT NULL,
    employee_mobile character varying(20) NOT NULL,
    employee_name character varying(100) NOT NULL,
    designation character varying(30) NOT NULL,
    joining_date date,
    status character varying(20) DEFAULT 'Active'::character varying,
    bank_account character varying(50),
    emergency_contact character varying(20),
    address text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    bkash_number character varying(20),
    nagad_number character varying(20),
    basic_salary numeric(10,2) DEFAULT 0,
    nid_number character varying(20)
);

--
-- Name: wbom_contacts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_contacts (
    contact_id integer NOT NULL,
    whatsapp_number character varying(50) NOT NULL,
    display_name character varying(200) DEFAULT ''::character varying NOT NULL,
    company_name character varying(300),
    relation_type_id integer,
    business_type_id integer,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text,
    platform character varying(20) DEFAULT 'whatsapp'::character varying,
    relation character varying(100) DEFAULT 'unknown'::character varying,
    personality_hint character varying(200) DEFAULT ''::character varying,
    interaction_count integer DEFAULT 0,
    interest_level character varying(20) DEFAULT 'unknown'::character varying,
    last_seen timestamp with time zone DEFAULT now(),
    keep_reason text,
    triage_at timestamp with time zone DEFAULT now(),
    keep_tier text,
    marked_for_delete_at timestamp with time zone,
    is_protected boolean DEFAULT false
);

--
-- Name: wbom_escort_programs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_escort_programs (
    program_id integer NOT NULL,
    mother_vessel character varying(100) NOT NULL,
    lighter_vessel character varying(100) NOT NULL,
    master_mobile character varying(20) NOT NULL,
    destination character varying(100),
    escort_employee_id integer,
    escort_mobile character varying(20),
    program_date date NOT NULL,
    shift character varying(1) NOT NULL,
    status character varying(20) DEFAULT 'Assigned'::character varying,
    assignment_time timestamp with time zone DEFAULT now(),
    completion_time timestamp with time zone,
    contact_id integer,
    whatsapp_message_id character varying(100),
    reply_message_id character varying(100),
    remarks text,
    start_date date,
    end_date date,
    end_shift character varying(1),
    release_point character varying(100),
    day_count numeric(6,1) DEFAULT 0,
    conveyance numeric(10,2) DEFAULT 0,
    capacity character varying(20),
    escort_name text,
    release_location text,
    total_days numeric(6,2),
    total_payment numeric(12,2),
    is_historical boolean DEFAULT false NOT NULL,
    food_bill numeric(10,2) DEFAULT 0 NOT NULL
);

--
-- Name: fazle_service_heartbeats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_service_heartbeats (
    service character varying(40) NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_message_id character varying(120),
    queue_depth integer,
    meta_json jsonb DEFAULT '{}'::jsonb NOT NULL
);

--
-- Name: wbom_payroll_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_payroll_runs (
    run_id bigint NOT NULL,
    employee_id integer NOT NULL,
    period_year integer NOT NULL,
    period_month integer NOT NULL,
    status character varying(20) DEFAULT 'draft'::character varying NOT NULL,
    basic_salary numeric(12,2) DEFAULT 0 NOT NULL,
    total_programs integer DEFAULT 0 NOT NULL,
    per_program_rate numeric(10,2) DEFAULT 0 NOT NULL,
    program_allowance numeric(12,2) DEFAULT 0 NOT NULL,
    other_allowance numeric(12,2) DEFAULT 0 NOT NULL,
    total_advances numeric(12,2) DEFAULT 0 NOT NULL,
    total_deductions numeric(12,2) DEFAULT 0 NOT NULL,
    gross_salary numeric(12,2) DEFAULT 0 NOT NULL,
    net_salary numeric(12,2) DEFAULT 0 NOT NULL,
    payout_target_date date,
    payment_method character varying(20),
    payment_reference character varying(80),
    payout_idempotency_key character varying(80),
    paid_at timestamp with time zone,
    computed_by character varying(80),
    submitted_by character varying(80),
    approved_by character varying(80),
    locked_by character varying(80),
    paid_by character varying(80),
    correction_reason text,
    remarks text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_payroll_run_month CHECK (((period_month >= 1) AND (period_month <= 12))),
    CONSTRAINT chk_payroll_run_status CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('reviewed'::character varying)::text, ('approved'::character varying)::text, ('locked'::character varying)::text, ('paid'::character varying)::text, ('cancelled'::character varying)::text]))),
    CONSTRAINT chk_payroll_run_year CHECK (((period_year >= 2020) AND (period_year <= 2099)))
);

--
-- Name: wbom_whatsapp_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_whatsapp_messages (
    message_id integer NOT NULL,
    whatsapp_msg_id character varying(100),
    contact_id integer,
    sender_number character varying(200) DEFAULT ''::character varying,
    message_type character varying(10) DEFAULT 'text'::character varying,
    content_type character varying(10) DEFAULT 'text'::character varying,
    message_body text DEFAULT ''::text,
    classification character varying(20) DEFAULT 'unclassified'::character varying,
    is_processed boolean DEFAULT false,
    template_used_id integer,
    received_at timestamp with time zone DEFAULT now(),
    processed_at timestamp with time zone,
    related_program_id integer,
    related_transaction_id integer,
    platform character varying(20) DEFAULT 'whatsapp'::character varying,
    direction character varying(10),
    contact_identifier character varying(200),
    ai_response text DEFAULT ''::text,
    metadata_json jsonb DEFAULT '{}'::jsonb,
    status character varying(20) DEFAULT 'sent'::character varying,
    identity_role text,
    identity_confidence integer,
    workflow_triggered text,
    canonical_phone text,
    phone_last10 text,
    source_message_ref text,
    source_timestamp timestamp with time zone,
    source_context text,
    message_hash text,
    critical_contact boolean DEFAULT false NOT NULL,
    critical_log_path text,
    original_sender_number text,
    conversation_key text,
    receiver_number text,
    intent_detected text,
    extracted_text text
);

--
-- Name: fazle_admin_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_admin_audit (
    id bigint NOT NULL,
    actor_phone character varying(20),
    actor_user_id bigint,
    actor_label character varying(80),
    channel character varying(20) NOT NULL,
    command character varying(120) NOT NULL,
    args text,
    allowed boolean NOT NULL,
    required_role character varying(40),
    denied_reason text,
    result_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: fazle_admin_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.fazle_admin_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: fazle_admin_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fazle_admin_audit_id_seq OWNED BY public.fazle_admin_audit.id;

--
-- Name: fazle_admin_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_admin_roles (
    admin_id bigint NOT NULL,
    role_name character varying(40) NOT NULL,
    granted_at timestamp with time zone DEFAULT now() NOT NULL,
    granted_by character varying(60)
);

--
-- Name: fazle_admins; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_admins (
    id bigint NOT NULL,
    phone character varying(20),
    name character varying(120),
    api_key_hash character varying(80),
    status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone,
    notes text,
    username text,
    password_hash text,
    login_token_hash text
);

--
-- Name: fazle_admins_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.fazle_admins_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: fazle_admins_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fazle_admins_id_seq OWNED BY public.fazle_admins.id;

--
-- Name: fazle_contact_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_contact_roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    phone character varying(20) NOT NULL,
    name character varying(200) DEFAULT ''::character varying NOT NULL,
    role character varying(30) DEFAULT 'unknown'::character varying NOT NULL,
    sub_role character varying(30) DEFAULT ''::character varying NOT NULL,
    language_pref character varying(10) DEFAULT ''::character varying NOT NULL,
    platform character varying(20) DEFAULT 'whatsapp'::character varying NOT NULL,
    is_active boolean DEFAULT true,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    confidence integer DEFAULT 0 NOT NULL,
    source text DEFAULT 'seed_rule'::text NOT NULL,
    priority integer DEFAULT 50 NOT NULL,
    notes text DEFAULT ''::text NOT NULL
);

--
-- Name: fazle_db_backups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_db_backups (
    id bigint NOT NULL,
    filename character varying(200) NOT NULL,
    path text NOT NULL,
    size_bytes bigint,
    sha256 character varying(80),
    status character varying(20) DEFAULT 'ok'::character varying NOT NULL,
    duration_ms integer,
    error text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    rotated_at timestamp with time zone
);

--
-- Name: fazle_db_backups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.fazle_db_backups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: fazle_db_backups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fazle_db_backups_id_seq OWNED BY public.fazle_db_backups.id;

--
-- Name: fazle_error_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_error_log (
    id bigint NOT NULL,
    module character varying(80) NOT NULL,
    error_type character varying(80) NOT NULL,
    message text,
    count integer DEFAULT 1 NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: fazle_error_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.fazle_error_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: fazle_error_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fazle_error_log_id_seq OWNED BY public.fazle_error_log.id;

--
-- Name: fazle_outbound_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_outbound_queue (
    id bigint NOT NULL,
    recipient character varying(40) NOT NULL,
    body text NOT NULL,
    source_bridge character varying(10) DEFAULT 'bridge2'::character varying NOT NULL,
    purpose character varying(40),
    status character varying(15) DEFAULT 'pending'::character varying NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    next_retry_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error text,
    idempotency_key character varying(80),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone,
    meta_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    fallback_channel text,
    external_id text,
    locked_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fazle_outbound_queue_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('sending'::character varying)::text, ('sent'::character varying)::text, ('failed'::character varying)::text, ('dlq'::character varying)::text])))
);

--
-- Name: fazle_outbound_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.fazle_outbound_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: fazle_outbound_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fazle_outbound_queue_id_seq OWNED BY public.fazle_outbound_queue.id;

--
-- Name: fazle_report_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_report_cache (
    cache_key character varying(160) NOT NULL,
    report_name character varying(60) NOT NULL,
    payload_json jsonb NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: fazle_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.fazle_roles (
    name character varying(40) NOT NULL,
    level integer NOT NULL,
    description text
);

--
-- Name: llm_conversation_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.llm_conversation_log (
    id bigint NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    caller character varying(100),
    user_id character varying(200),
    provider character varying(20) NOT NULL,
    model character varying(100) NOT NULL,
    messages jsonb NOT NULL,
    reply text NOT NULL,
    usage_data jsonb,
    latency_ms real,
    is_fallback boolean DEFAULT false,
    trainable boolean DEFAULT false
);

--
-- Name: llm_conversation_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.llm_conversation_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: llm_conversation_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_conversation_log_id_seq OWNED BY public.llm_conversation_log.id;

--
-- Name: wbom_attendance_attendance_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_attendance_attendance_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_attendance_attendance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_attendance_attendance_id_seq OWNED BY public.wbom_attendance.attendance_id;

--
-- Name: wbom_cases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_cases (
    case_id bigint NOT NULL,
    case_type character varying(40) NOT NULL,
    source_platform character varying(20) DEFAULT 'whatsapp'::character varying NOT NULL,
    source_channel character varying(40) DEFAULT 'inbound_message'::character varying NOT NULL,
    contact_id integer,
    employee_id integer,
    related_program_id integer,
    title character varying(200) NOT NULL,
    description text,
    priority character varying(20) DEFAULT 'normal'::character varying NOT NULL,
    severity character varying(20) DEFAULT 'medium'::character varying NOT NULL,
    status character varying(30) DEFAULT 'open'::character varying NOT NULL,
    owner_role character varying(30),
    owner_user character varying(80),
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    first_response_at timestamp with time zone,
    due_at timestamp with time zone,
    resolved_at timestamp with time zone,
    closed_at timestamp with time zone,
    resolution_summary text,
    metadata_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_wbom_cases_priority CHECK (((priority)::text = ANY (ARRAY[('low'::character varying)::text, ('normal'::character varying)::text, ('high'::character varying)::text, ('urgent'::character varying)::text]))),
    CONSTRAINT chk_wbom_cases_severity CHECK (((severity)::text = ANY (ARRAY[('low'::character varying)::text, ('medium'::character varying)::text, ('high'::character varying)::text, ('critical'::character varying)::text]))),
    CONSTRAINT chk_wbom_cases_status CHECK (((status)::text = ANY (ARRAY[('open'::character varying)::text, ('in_progress'::character varying)::text, ('waiting_customer'::character varying)::text, ('waiting_internal'::character varying)::text, ('resolved'::character varying)::text, ('closed'::character varying)::text, ('cancelled'::character varying)::text])))
);

--
-- Name: wbom_cases_case_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_cases_case_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_cases_case_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_cases_case_id_seq OWNED BY public.wbom_cases.case_id;

--
-- Name: wbom_cash_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_cash_transactions (
    transaction_id integer NOT NULL,
    employee_id integer NOT NULL,
    program_id integer,
    transaction_type character varying(20) NOT NULL,
    amount numeric(10,2) NOT NULL,
    payment_method character varying(10) NOT NULL,
    payment_mobile character varying(20),
    transaction_date date DEFAULT CURRENT_DATE NOT NULL,
    transaction_time timestamp with time zone DEFAULT now(),
    status character varying(20) DEFAULT 'Completed'::character varying,
    reference_number character varying(50),
    remarks text,
    whatsapp_message_id character varying(100),
    created_by character varying(50),
    idempotency_key character varying(64),
    approved_by character varying(80),
    approved_at timestamp with time zone,
    source character varying(30) DEFAULT 'web'::character varying,
    is_reversed boolean DEFAULT false,
    reversal_of integer,
    correction_note text,
    employee_phone text,
    payment_number text
);

--
-- Name: COLUMN wbom_cash_transactions.approved_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.wbom_cash_transactions.approved_by IS 'DEPRECATED — 0 rows, no active writer. Not part of Business Constitution. Scheduled for removal in future sprint.';

--
-- Name: COLUMN wbom_cash_transactions.approved_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.wbom_cash_transactions.approved_at IS 'DEPRECATED — 0 rows, no active writer. Not part of Business Constitution. Scheduled for removal in future sprint.';

--
-- Name: wbom_cash_transactions_transaction_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_cash_transactions_transaction_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_cash_transactions_transaction_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_cash_transactions_transaction_id_seq OWNED BY public.wbom_cash_transactions.transaction_id;

--
-- Name: wbom_contacts_contact_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_contacts_contact_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_contacts_contact_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_contacts_contact_id_seq OWNED BY public.wbom_contacts.contact_id;

--
-- Name: wbom_employee_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_employee_requests (
    request_id integer NOT NULL,
    employee_id integer NOT NULL,
    request_type character varying(30) NOT NULL,
    message_body text,
    sender_number character varying(20),
    status character varying(20) DEFAULT 'Pending'::character varying,
    response_text text,
    delay_hours integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    responded_at timestamp with time zone
);

--
-- Name: wbom_employee_requests_request_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_employee_requests_request_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_employee_requests_request_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_employee_requests_request_id_seq OWNED BY public.wbom_employee_requests.request_id;

--
-- Name: wbom_employees_employee_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_employees_employee_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_employees_employee_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_employees_employee_id_seq OWNED BY public.wbom_employees.employee_id;

--
-- Name: wbom_escort_programs_program_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_escort_programs_program_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_escort_programs_program_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_escort_programs_program_id_seq OWNED BY public.wbom_escort_programs.program_id;

--
-- Name: wbom_payroll_approval_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_payroll_approval_log (
    log_id bigint NOT NULL,
    run_id bigint NOT NULL,
    action character varying(30) NOT NULL,
    actor character varying(80) NOT NULL,
    from_status character varying(20),
    to_status character varying(20),
    reason text,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

--
-- Name: wbom_payroll_approval_log_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_payroll_approval_log_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_payroll_approval_log_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_payroll_approval_log_log_id_seq OWNED BY public.wbom_payroll_approval_log.log_id;

--
-- Name: wbom_payroll_run_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_payroll_run_items (
    item_id bigint NOT NULL,
    run_id bigint NOT NULL,
    component_type character varying(40) NOT NULL,
    component_label character varying(100) NOT NULL,
    amount numeric(12,2) NOT NULL,
    sign character varying(1) DEFAULT '+'::character varying NOT NULL,
    source_table character varying(60),
    source_id bigint,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_payroll_item_sign CHECK (((sign)::text = ANY (ARRAY[('+'::character varying)::text, ('-'::character varying)::text])))
);

--
-- Name: wbom_payroll_run_items_item_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_payroll_run_items_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_payroll_run_items_item_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_payroll_run_items_item_id_seq OWNED BY public.wbom_payroll_run_items.item_id;

--
-- Name: wbom_payroll_runs_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_payroll_runs_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_payroll_runs_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_payroll_runs_run_id_seq OWNED BY public.wbom_payroll_runs.run_id;

--
-- Name: wbom_salary_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_salary_records (
    salary_id integer NOT NULL,
    employee_id integer NOT NULL,
    month integer NOT NULL,
    year integer NOT NULL,
    basic_salary numeric(10,2),
    total_programs integer DEFAULT 0,
    program_allowance numeric(10,2) DEFAULT 0,
    other_allowance numeric(10,2) DEFAULT 0,
    total_advances numeric(10,2) DEFAULT 0,
    total_deductions numeric(10,2) DEFAULT 0,
    net_salary numeric(10,2),
    payment_date date,
    payment_status character varying(20) DEFAULT 'Pending'::character varying,
    remarks text
);

--
-- Name: wbom_salary_records_salary_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_salary_records_salary_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_salary_records_salary_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_salary_records_salary_id_seq OWNED BY public.wbom_salary_records.salary_id;

--
-- Name: wbom_staging_payments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.wbom_staging_payments (
    staging_id integer NOT NULL,
    message_id integer,
    sender_number character varying(20),
    extracted_name character varying(100),
    extracted_mobile character varying(20),
    amount numeric(12,2),
    payment_method character varying(20),
    transaction_type character varying(30),
    matched_employee_id integer,
    name_match_ratio numeric(4,2),
    status character varying(20) DEFAULT 'pending'::character varying,
    approved_by character varying(100),
    approved_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now(),
    final_transaction_id integer,
    idempotency_key character varying(64)
);

--
-- Name: wbom_staging_payments_staging_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_staging_payments_staging_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_staging_payments_staging_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_staging_payments_staging_id_seq OWNED BY public.wbom_staging_payments.staging_id;

--
-- Name: wbom_whatsapp_messages_message_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE IF NOT EXISTS public.wbom_whatsapp_messages_message_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

--
-- Name: wbom_whatsapp_messages_message_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wbom_whatsapp_messages_message_id_seq OWNED BY public.wbom_whatsapp_messages.message_id;

--
-- Name: fazle_admin_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fazle_admin_audit ALTER COLUMN id SET DEFAULT nextval('public.fazle_admin_audit_id_seq'::regclass);

--
-- Name: fazle_admins id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fazle_admins ALTER COLUMN id SET DEFAULT nextval('public.fazle_admins_id_seq'::regclass);

--
-- Name: fazle_db_backups id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fazle_db_backups ALTER COLUMN id SET DEFAULT nextval('public.fazle_db_backups_id_seq'::regclass);

--
-- Name: fazle_error_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fazle_error_log ALTER COLUMN id SET DEFAULT nextval('public.fazle_error_log_id_seq'::regclass);

--
-- Name: fazle_outbound_queue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fazle_outbound_queue ALTER COLUMN id SET DEFAULT nextval('public.fazle_outbound_queue_id_seq'::regclass);

--
-- Name: llm_conversation_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_conversation_log ALTER COLUMN id SET DEFAULT nextval('public.llm_conversation_log_id_seq'::regclass);

--
-- Name: wbom_attendance attendance_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_attendance ALTER COLUMN attendance_id SET DEFAULT nextval('public.wbom_attendance_attendance_id_seq'::regclass);

--
-- Name: wbom_cases case_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_cases ALTER COLUMN case_id SET DEFAULT nextval('public.wbom_cases_case_id_seq'::regclass);

--
-- Name: wbom_cash_transactions transaction_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_cash_transactions ALTER COLUMN transaction_id SET DEFAULT nextval('public.wbom_cash_transactions_transaction_id_seq'::regclass);

--
-- Name: wbom_contacts contact_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_contacts ALTER COLUMN contact_id SET DEFAULT nextval('public.wbom_contacts_contact_id_seq'::regclass);

--
-- Name: wbom_employee_requests request_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_employee_requests ALTER COLUMN request_id SET DEFAULT nextval('public.wbom_employee_requests_request_id_seq'::regclass);

--
-- Name: wbom_employees employee_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_employees ALTER COLUMN employee_id SET DEFAULT nextval('public.wbom_employees_employee_id_seq'::regclass);

--
-- Name: wbom_escort_programs program_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_escort_programs ALTER COLUMN program_id SET DEFAULT nextval('public.wbom_escort_programs_program_id_seq'::regclass);

--
-- Name: wbom_payroll_approval_log log_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_payroll_approval_log ALTER COLUMN log_id SET DEFAULT nextval('public.wbom_payroll_approval_log_log_id_seq'::regclass);

--
-- Name: wbom_payroll_run_items item_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_payroll_run_items ALTER COLUMN item_id SET DEFAULT nextval('public.wbom_payroll_run_items_item_id_seq'::regclass);

--
-- Name: wbom_payroll_runs run_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_payroll_runs ALTER COLUMN run_id SET DEFAULT nextval('public.wbom_payroll_runs_run_id_seq'::regclass);

--
-- Name: wbom_salary_records salary_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_salary_records ALTER COLUMN salary_id SET DEFAULT nextval('public.wbom_salary_records_salary_id_seq'::regclass);

--
-- Name: wbom_staging_payments staging_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_staging_payments ALTER COLUMN staging_id SET DEFAULT nextval('public.wbom_staging_payments_staging_id_seq'::regclass);

--
-- Name: wbom_whatsapp_messages message_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wbom_whatsapp_messages ALTER COLUMN message_id SET DEFAULT nextval('public.wbom_whatsapp_messages_message_id_seq'::regclass);

--
-- Name: fazle_admin_audit fazle_admin_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_admin_audit_pkey'
    ) THEN
        ALTER TABLE public.fazle_admin_audit ADD CONSTRAINT fazle_admin_audit_pkey PRIMARY KEY (id);
    END IF;
END $$;

--
-- Name: fazle_admin_roles fazle_admin_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_admin_roles_pkey'
    ) THEN
        ALTER TABLE public.fazle_admin_roles ADD CONSTRAINT fazle_admin_roles_pkey PRIMARY KEY (admin_id, role_name);
    END IF;
END $$;

--
-- Name: fazle_admins fazle_admins_phone_key; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_admins_phone_key'
    ) THEN
        ALTER TABLE public.fazle_admins ADD CONSTRAINT fazle_admins_phone_key UNIQUE (phone);
    END IF;
END $$;

--
-- Name: fazle_admins fazle_admins_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_admins_pkey'
    ) THEN
        ALTER TABLE public.fazle_admins ADD CONSTRAINT fazle_admins_pkey PRIMARY KEY (id);
    END IF;
END $$;

--
-- Name: fazle_contact_roles fazle_contact_roles_phone_platform_key; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_contact_roles_phone_platform_key'
    ) THEN
        ALTER TABLE public.fazle_contact_roles ADD CONSTRAINT fazle_contact_roles_phone_platform_key UNIQUE (phone, platform);
    END IF;
END $$;

--
-- Name: fazle_contact_roles fazle_contact_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_contact_roles_pkey'
    ) THEN
        ALTER TABLE public.fazle_contact_roles ADD CONSTRAINT fazle_contact_roles_pkey PRIMARY KEY (id);
    END IF;
END $$;

--
-- Name: fazle_db_backups fazle_db_backups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_db_backups_pkey'
    ) THEN
        ALTER TABLE public.fazle_db_backups ADD CONSTRAINT fazle_db_backups_pkey PRIMARY KEY (id);
    END IF;
END $$;

--
-- Name: fazle_error_log fazle_error_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_error_log_pkey'
    ) THEN
        ALTER TABLE public.fazle_error_log ADD CONSTRAINT fazle_error_log_pkey PRIMARY KEY (id);
    END IF;
END $$;

--
-- Name: fazle_outbound_queue fazle_outbound_queue_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_outbound_queue_idempotency_key_key'
    ) THEN
        ALTER TABLE public.fazle_outbound_queue ADD CONSTRAINT fazle_outbound_queue_idempotency_key_key UNIQUE (idempotency_key);
    END IF;
END $$;

--
-- Name: fazle_outbound_queue fazle_outbound_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_outbound_queue_pkey'
    ) THEN
        ALTER TABLE public.fazle_outbound_queue ADD CONSTRAINT fazle_outbound_queue_pkey PRIMARY KEY (id);
    END IF;
END $$;

--
-- Name: fazle_report_cache fazle_report_cache_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_report_cache_pkey'
    ) THEN
        ALTER TABLE public.fazle_report_cache ADD CONSTRAINT fazle_report_cache_pkey PRIMARY KEY (cache_key);
    END IF;
END $$;

--
-- Name: fazle_roles fazle_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_roles_pkey'
    ) THEN
        ALTER TABLE public.fazle_roles ADD CONSTRAINT fazle_roles_pkey PRIMARY KEY (name);
    END IF;
END $$;

--
-- Name: fazle_service_heartbeats fazle_service_heartbeats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_service_heartbeats_pkey'
    ) THEN
        ALTER TABLE public.fazle_service_heartbeats ADD CONSTRAINT fazle_service_heartbeats_pkey PRIMARY KEY (service);
    END IF;
END $$;

--
-- Name: llm_conversation_log llm_conversation_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'llm_conversation_log_pkey'
    ) THEN
        ALTER TABLE public.llm_conversation_log ADD CONSTRAINT llm_conversation_log_pkey PRIMARY KEY (id);
    END IF;
END $$;

--
-- Name: wbom_employees uq_wbom_employees_mobile; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_wbom_employees_mobile'
    ) THEN
        ALTER TABLE public.wbom_employees ADD CONSTRAINT uq_wbom_employees_mobile UNIQUE (employee_mobile);
    END IF;
END $$;

--
-- Name: wbom_attendance wbom_attendance_employee_id_attendance_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_attendance_employee_id_attendance_date_key'
    ) THEN
        ALTER TABLE public.wbom_attendance ADD CONSTRAINT wbom_attendance_employee_id_attendance_date_key UNIQUE (employee_id, attendance_date);
    END IF;
END $$;

--
-- Name: wbom_attendance wbom_attendance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_attendance_pkey'
    ) THEN
        ALTER TABLE public.wbom_attendance ADD CONSTRAINT wbom_attendance_pkey PRIMARY KEY (attendance_id);
    END IF;
END $$;

--
-- Name: wbom_cases wbom_cases_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cases_pkey'
    ) THEN
        ALTER TABLE public.wbom_cases ADD CONSTRAINT wbom_cases_pkey PRIMARY KEY (case_id);
    END IF;
END $$;

--
-- Name: wbom_cash_transactions wbom_cash_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cash_transactions_pkey'
    ) THEN
        ALTER TABLE public.wbom_cash_transactions ADD CONSTRAINT wbom_cash_transactions_pkey PRIMARY KEY (transaction_id);
    END IF;
END $$;

--
-- Name: wbom_contacts wbom_contacts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_contacts_pkey'
    ) THEN
        ALTER TABLE public.wbom_contacts ADD CONSTRAINT wbom_contacts_pkey PRIMARY KEY (contact_id);
    END IF;
END $$;

--
-- Name: wbom_contacts wbom_contacts_whatsapp_platform_uniq; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_contacts_whatsapp_platform_uniq'
    ) THEN
        ALTER TABLE public.wbom_contacts ADD CONSTRAINT wbom_contacts_whatsapp_platform_uniq UNIQUE (whatsapp_number, platform);
    END IF;
END $$;

--
-- Name: wbom_employee_requests wbom_employee_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_employee_requests_pkey'
    ) THEN
        ALTER TABLE public.wbom_employee_requests ADD CONSTRAINT wbom_employee_requests_pkey PRIMARY KEY (request_id);
    END IF;
END $$;

--
-- Name: wbom_employees wbom_employees_employee_mobile_key; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_employees_employee_mobile_key'
    ) THEN
        ALTER TABLE public.wbom_employees ADD CONSTRAINT wbom_employees_employee_mobile_key UNIQUE (employee_mobile);
    END IF;
END $$;

--
-- Name: wbom_employees wbom_employees_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_employees_pkey'
    ) THEN
        ALTER TABLE public.wbom_employees ADD CONSTRAINT wbom_employees_pkey PRIMARY KEY (employee_id);
    END IF;
END $$;

--
-- Name: wbom_escort_programs wbom_escort_programs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_escort_programs_pkey'
    ) THEN
        ALTER TABLE public.wbom_escort_programs ADD CONSTRAINT wbom_escort_programs_pkey PRIMARY KEY (program_id);
    END IF;
END $$;

--
-- Name: wbom_payroll_approval_log wbom_payroll_approval_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_payroll_approval_log_pkey'
    ) THEN
        ALTER TABLE public.wbom_payroll_approval_log ADD CONSTRAINT wbom_payroll_approval_log_pkey PRIMARY KEY (log_id);
    END IF;
END $$;

--
-- Name: wbom_payroll_run_items wbom_payroll_run_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_payroll_run_items_pkey'
    ) THEN
        ALTER TABLE public.wbom_payroll_run_items ADD CONSTRAINT wbom_payroll_run_items_pkey PRIMARY KEY (item_id);
    END IF;
END $$;

--
-- Name: wbom_payroll_runs wbom_payroll_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_payroll_runs_pkey'
    ) THEN
        ALTER TABLE public.wbom_payroll_runs ADD CONSTRAINT wbom_payroll_runs_pkey PRIMARY KEY (run_id);
    END IF;
END $$;

--
-- Name: wbom_salary_records wbom_salary_records_employee_id_month_year_key; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_salary_records_employee_id_month_year_key'
    ) THEN
        ALTER TABLE public.wbom_salary_records ADD CONSTRAINT wbom_salary_records_employee_id_month_year_key UNIQUE (employee_id, month, year);
    END IF;
END $$;

--
-- Name: wbom_salary_records wbom_salary_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_salary_records_pkey'
    ) THEN
        ALTER TABLE public.wbom_salary_records ADD CONSTRAINT wbom_salary_records_pkey PRIMARY KEY (salary_id);
    END IF;
END $$;

--
-- Name: wbom_staging_payments wbom_staging_payments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_staging_payments_pkey'
    ) THEN
        ALTER TABLE public.wbom_staging_payments ADD CONSTRAINT wbom_staging_payments_pkey PRIMARY KEY (staging_id);
    END IF;
END $$;

--
-- Name: wbom_whatsapp_messages wbom_whatsapp_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_whatsapp_messages_pkey'
    ) THEN
        ALTER TABLE public.wbom_whatsapp_messages ADD CONSTRAINT wbom_whatsapp_messages_pkey PRIMARY KEY (message_id);
    END IF;
END $$;

--
-- Name: wbom_whatsapp_messages wbom_whatsapp_messages_whatsapp_msg_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_whatsapp_messages_whatsapp_msg_id_key'
    ) THEN
        ALTER TABLE public.wbom_whatsapp_messages ADD CONSTRAINT wbom_whatsapp_messages_whatsapp_msg_id_key UNIQUE (whatsapp_msg_id);
    END IF;
END $$;

--
-- Name: idx_admins_apikey; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_admins_apikey ON public.fazle_admins USING btree (api_key_hash);

--
-- Name: idx_admins_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_admins_phone ON public.fazle_admins USING btree (phone);

--
-- Name: idx_admins_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_admins_status ON public.fazle_admins USING btree (status);

--
-- Name: idx_attendance_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_attendance_date ON public.wbom_attendance USING btree (attendance_date);

--
-- Name: idx_attendance_status_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_attendance_status_date ON public.wbom_attendance USING btree (status, attendance_date);

--
-- Name: idx_audit_command; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_audit_command ON public.fazle_admin_audit USING btree (command);

--
-- Name: idx_b19_audit_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_b19_audit_actor ON public.fazle_admin_audit USING btree (actor_phone, created_at DESC);

--
-- Name: idx_b19_audit_command; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_b19_audit_command ON public.fazle_admin_audit USING btree (command);

--
-- Name: idx_b19_audit_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_b19_audit_created ON public.fazle_admin_audit USING btree (created_at DESC);

--
-- Name: idx_contact_roles_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_contact_roles_phone ON public.fazle_contact_roles USING btree (phone);

--
-- Name: idx_contact_roles_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_contact_roles_role ON public.fazle_contact_roles USING btree (role);

--
-- Name: idx_db_backups_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_db_backups_started_at ON public.fazle_db_backups USING btree (started_at DESC);

--
-- Name: idx_db_backups_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_db_backups_status ON public.fazle_db_backups USING btree (status);

--
-- Name: idx_employees_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_employees_status ON public.wbom_employees USING btree (status);

--
-- Name: idx_fazle_admins_login_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_fazle_admins_login_token_hash ON public.fazle_admins USING btree (login_token_hash);

--
-- Name: idx_fazle_admins_username; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_fazle_admins_username ON public.fazle_admins USING btree (lower(username));

--
-- Name: idx_fazle_contact_roles_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_fazle_contact_roles_phone ON public.fazle_contact_roles USING btree (phone);

--
-- Name: idx_llm_log_trainable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_llm_log_trainable ON public.llm_conversation_log USING btree (trainable) WHERE (trainable = true);

--
-- Name: idx_llm_log_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_llm_log_ts ON public.llm_conversation_log USING btree (ts);

--
-- Name: idx_outbound_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_outbound_pending ON public.fazle_outbound_queue USING btree (next_retry_at) WHERE ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('failed'::character varying)::text]));

--
-- Name: idx_outbound_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_outbound_status_created ON public.fazle_outbound_queue USING btree (status, created_at DESC);

--
-- Name: idx_payroll_approval_log_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_approval_log_actor ON public.wbom_payroll_approval_log USING btree (actor);

--
-- Name: idx_payroll_approval_log_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_approval_log_run ON public.wbom_payroll_approval_log USING btree (run_id);

--
-- Name: idx_payroll_approval_log_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_approval_log_ts ON public.wbom_payroll_approval_log USING btree (created_at DESC);

--
-- Name: idx_payroll_run_items_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_run_items_run ON public.wbom_payroll_run_items USING btree (run_id);

--
-- Name: idx_payroll_run_items_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_run_items_type ON public.wbom_payroll_run_items USING btree (component_type);

--
-- Name: idx_payroll_runs_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_runs_employee ON public.wbom_payroll_runs USING btree (employee_id);

--
-- Name: idx_payroll_runs_payout_idem; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_payroll_runs_payout_idem ON public.wbom_payroll_runs USING btree (payout_idempotency_key) WHERE (payout_idempotency_key IS NOT NULL);

--
-- Name: idx_payroll_runs_payout_target; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_runs_payout_target ON public.wbom_payroll_runs USING btree (payout_target_date) WHERE ((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('reviewed'::character varying)::text, ('approved'::character varying)::text, ('locked'::character varying)::text]));

--
-- Name: idx_payroll_runs_period; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_runs_period ON public.wbom_payroll_runs USING btree (period_year, period_month);

--
-- Name: idx_payroll_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_payroll_runs_status ON public.wbom_payroll_runs USING btree (status);

--
-- Name: idx_payroll_runs_unique_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_payroll_runs_unique_active ON public.wbom_payroll_runs USING btree (employee_id, period_year, period_month) WHERE ((status)::text <> 'cancelled'::text);

--
-- Name: idx_programs_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_programs_date ON public.wbom_escort_programs USING btree (program_date);

--
-- Name: idx_programs_status_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_programs_status_date ON public.wbom_escort_programs USING btree (status, program_date);

--
-- Name: idx_report_cache_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_report_cache_expires ON public.fazle_report_cache USING btree (expires_at);

--
-- Name: idx_staging_payments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_staging_payments_status ON public.wbom_staging_payments USING btree (status);

--
-- Name: idx_tx_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_tx_date ON public.wbom_cash_transactions USING btree (transaction_date);

--
-- Name: idx_tx_type_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_tx_type_date ON public.wbom_cash_transactions USING btree (transaction_type, transaction_date);

--
-- Name: idx_txn_idempotency; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_txn_idempotency ON public.wbom_cash_transactions USING btree (idempotency_key) WHERE (idempotency_key IS NOT NULL);

--
-- Name: idx_wbom_attendance_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_attendance_date ON public.wbom_attendance USING btree (attendance_date);

--
-- Name: idx_wbom_attendance_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_attendance_employee ON public.wbom_attendance USING btree (employee_id);

--
-- Name: idx_wbom_cases_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_cases_contact ON public.wbom_cases USING btree (contact_id);

--
-- Name: idx_wbom_cases_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_cases_employee ON public.wbom_cases USING btree (employee_id);

--
-- Name: idx_wbom_cases_opened_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_cases_opened_at ON public.wbom_cases USING btree (opened_at DESC);

--
-- Name: idx_wbom_cases_status_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_cases_status_due ON public.wbom_cases USING btree (status, due_at);

--
-- Name: idx_wbom_cases_type_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_cases_type_status ON public.wbom_cases USING btree (case_type, status);

--
-- Name: idx_wbom_contacts_last_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_contacts_last_seen ON public.wbom_contacts USING btree (last_seen DESC);

--
-- Name: idx_wbom_contacts_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_contacts_name ON public.wbom_contacts USING btree (display_name);

--
-- Name: idx_wbom_contacts_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_contacts_phone ON public.wbom_contacts USING btree (whatsapp_number);

--
-- Name: idx_wbom_contacts_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_contacts_platform ON public.wbom_contacts USING btree (platform);

--
-- Name: idx_wbom_contacts_whatsapp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_contacts_whatsapp ON public.wbom_contacts USING btree (whatsapp_number);

--
-- Name: idx_wbom_employee_requests_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_employee_requests_employee ON public.wbom_employee_requests USING btree (employee_id);

--
-- Name: idx_wbom_employees_bkash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_employees_bkash ON public.wbom_employees USING btree (bkash_number);

--
-- Name: idx_wbom_employees_mobile; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_employees_mobile ON public.wbom_employees USING btree (employee_mobile);

--
-- Name: idx_wbom_employees_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_employees_name ON public.wbom_employees USING btree (employee_name);

--
-- Name: idx_wbom_employees_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_employees_name_trgm ON public.wbom_employees USING gin (employee_name public.gin_trgm_ops);

--
-- Name: idx_wbom_messages_canonical_phone; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_messages_canonical_phone ON public.wbom_whatsapp_messages USING btree (canonical_phone, received_at DESC);

--
-- Name: idx_wbom_messages_class; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_messages_class ON public.wbom_whatsapp_messages USING btree (classification);

--
-- Name: idx_wbom_messages_identity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_messages_identity ON public.wbom_whatsapp_messages USING btree (identity_role, received_at DESC) WHERE (identity_role IS NOT NULL);

--
-- Name: idx_wbom_messages_phone_last10; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_messages_phone_last10 ON public.wbom_whatsapp_messages USING btree (phone_last10, received_at DESC);

--
-- Name: idx_wbom_messages_received; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_messages_received ON public.wbom_whatsapp_messages USING btree (received_at);

--
-- Name: idx_wbom_messages_sender; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_messages_sender ON public.wbom_whatsapp_messages USING btree (sender_number);

--
-- Name: idx_wbom_msg_contact_identifier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_contact_identifier ON public.wbom_whatsapp_messages USING btree (contact_identifier);

--
-- Name: idx_wbom_msg_conv_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_conv_key ON public.wbom_whatsapp_messages USING btree (conversation_key, received_at DESC);

--
-- Name: idx_wbom_msg_direction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_direction ON public.wbom_whatsapp_messages USING btree (direction);

--
-- Name: idx_wbom_msg_intent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_intent ON public.wbom_whatsapp_messages USING btree (intent_detected) WHERE (intent_detected IS NOT NULL);

--
-- Name: idx_wbom_msg_platform; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_platform ON public.wbom_whatsapp_messages USING btree (platform);

--
-- Name: idx_wbom_msg_platform_direction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_platform_direction ON public.wbom_whatsapp_messages USING btree (platform, direction);

--
-- Name: idx_wbom_msg_received_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_received_at ON public.wbom_whatsapp_messages USING btree (received_at DESC);

--
-- Name: idx_wbom_msg_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_msg_status ON public.wbom_whatsapp_messages USING btree (status);

--
-- Name: idx_wbom_programs_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_programs_date ON public.wbom_escort_programs USING btree (program_date);

--
-- Name: idx_wbom_programs_end_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_programs_end_date ON public.wbom_escort_programs USING btree (end_date);

--
-- Name: idx_wbom_programs_lighter; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_programs_lighter ON public.wbom_escort_programs USING btree (lighter_vessel);

--
-- Name: idx_wbom_programs_mother; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_programs_mother ON public.wbom_escort_programs USING btree (mother_vessel);

--
-- Name: idx_wbom_programs_start_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_programs_start_date ON public.wbom_escort_programs USING btree (start_date);

--
-- Name: idx_wbom_programs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_programs_status ON public.wbom_escort_programs USING btree (status);

--
-- Name: idx_wbom_transactions_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_transactions_date ON public.wbom_cash_transactions USING btree (transaction_date);

--
-- Name: idx_wbom_transactions_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_wbom_transactions_dedup ON public.wbom_cash_transactions USING btree (employee_id, transaction_date, amount, transaction_type, payment_method) WHERE ((status)::text = 'Completed'::text);

--
-- Name: idx_wbom_transactions_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_transactions_employee ON public.wbom_cash_transactions USING btree (employee_id);

--
-- Name: idx_wbom_transactions_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_wbom_transactions_type ON public.wbom_cash_transactions USING btree (transaction_type);

--
-- Name: idx_wbom_transactions_wa_msg_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_wbom_transactions_wa_msg_dedup ON public.wbom_cash_transactions USING btree (whatsapp_message_id) WHERE ((whatsapp_message_id IS NOT NULL) AND ((whatsapp_message_id)::text <> ''::text));

--
-- Name: idx_wbom_tx_dedup_migration; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS idx_wbom_tx_dedup_migration ON public.wbom_cash_transactions USING btree (employee_id, amount, transaction_date, created_by) WHERE ((created_by)::text = 'migration_ops'::text);

--
-- Name: uq_error_log_dedup; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_error_log_dedup ON public.fazle_error_log USING btree (module, error_type, md5(COALESCE(message, ''::text)));

--
-- Name: uq_wbom_escort_confirmed_exact; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_wbom_escort_confirmed_exact ON public.wbom_escort_programs USING btree (COALESCE(start_date, program_date), upper(TRIM(BOTH FROM mother_vessel)), upper(TRIM(BOTH FROM lighter_vessel)), upper(TRIM(BOTH FROM escort_name))) WHERE (((status)::text = 'confirmed'::text) AND (COALESCE(TRIM(BOTH FROM mother_vessel), ''::text) <> ''::text) AND (COALESCE(TRIM(BOTH FROM lighter_vessel), ''::text) <> ''::text) AND (COALESCE(TRIM(BOTH FROM escort_name), ''::text) <> ''::text));

--
-- Name: uq_wbom_messages_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_wbom_messages_hash ON public.wbom_whatsapp_messages USING btree (message_hash);

--
-- Name: wbom_whatsapp_messages trg_wbom_msg_autofill; Type: TRIGGER; Schema: public; Owner: -
--

-- The trigger below depends on this function, which (like the 24 tables
-- above) has no prior definition anywhere in this repository's migrations —
-- discovered while verifying wbom_whatsapp_messages's live schema for this task.
CREATE OR REPLACE FUNCTION public.wbom_msg_autofill()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.sender_number IS NULL OR NEW.sender_number = '' THEN
        NEW.sender_number := COALESCE(NEW.contact_identifier, '');
    END IF;
    IF NEW.contact_identifier IS NULL OR NEW.contact_identifier = '' THEN
        NEW.contact_identifier := COALESCE(NEW.sender_number, '');
    END IF;
    IF NEW.message_type IS NULL OR NEW.message_type = '' THEN
        NEW.message_type := COALESCE(NEW.direction, 'text');
    END IF;
    IF NEW.direction IS NULL OR NEW.direction = '' THEN
        NEW.direction := COALESCE(NEW.message_type, 'incoming');
    END IF;
    IF NEW.received_at IS NULL THEN
        NEW.received_at := NOW();
    END IF;
    IF NEW.message_body IS NULL THEN
        NEW.message_body := '';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_wbom_msg_autofill ON public.wbom_whatsapp_messages;
CREATE TRIGGER trg_wbom_msg_autofill BEFORE INSERT ON public.wbom_whatsapp_messages FOR EACH ROW EXECUTE FUNCTION public.wbom_msg_autofill();

--
-- Name: fazle_admin_roles fazle_admin_roles_admin_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_admin_roles_admin_id_fkey'
    ) THEN
        ALTER TABLE public.fazle_admin_roles ADD CONSTRAINT fazle_admin_roles_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES public.fazle_admins(id) ON DELETE CASCADE;
    END IF;
END $$;

--
-- Name: fazle_admin_roles fazle_admin_roles_role_name_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fazle_admin_roles_role_name_fkey'
    ) THEN
        ALTER TABLE public.fazle_admin_roles ADD CONSTRAINT fazle_admin_roles_role_name_fkey FOREIGN KEY (role_name) REFERENCES public.fazle_roles(name);
    END IF;
END $$;

--
-- Name: wbom_attendance wbom_attendance_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_attendance_employee_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_attendance ADD CONSTRAINT wbom_attendance_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

--
-- Name: wbom_cases wbom_cases_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cases_contact_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_cases ADD CONSTRAINT wbom_cases_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.wbom_contacts(contact_id);
    END IF;
END $$;

--
-- Name: wbom_cases wbom_cases_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cases_employee_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_cases ADD CONSTRAINT wbom_cases_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id) ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;
END $$;

--
-- Name: wbom_cases wbom_cases_related_program_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cases_related_program_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_cases ADD CONSTRAINT wbom_cases_related_program_id_fkey FOREIGN KEY (related_program_id) REFERENCES public.wbom_escort_programs(program_id);
    END IF;
END $$;

--
-- Name: wbom_cash_transactions wbom_cash_transactions_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cash_transactions_employee_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_cash_transactions ADD CONSTRAINT wbom_cash_transactions_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

--
-- Name: wbom_cash_transactions wbom_cash_transactions_program_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cash_transactions_program_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_cash_transactions ADD CONSTRAINT wbom_cash_transactions_program_id_fkey FOREIGN KEY (program_id) REFERENCES public.wbom_escort_programs(program_id);
    END IF;
END $$;

--
-- Name: wbom_cash_transactions wbom_cash_transactions_reversal_of_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_cash_transactions_reversal_of_fkey'
    ) THEN
        ALTER TABLE public.wbom_cash_transactions ADD CONSTRAINT wbom_cash_transactions_reversal_of_fkey FOREIGN KEY (reversal_of) REFERENCES public.wbom_cash_transactions(transaction_id);
    END IF;
END $$;

--
-- Name: wbom_contacts wbom_contacts_business_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_contacts_business_type_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_contacts ADD CONSTRAINT wbom_contacts_business_type_id_fkey FOREIGN KEY (business_type_id) REFERENCES public.wbom_business_types(business_type_id);
    END IF;
END $$;

--
-- Name: wbom_contacts wbom_contacts_relation_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_contacts_relation_type_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_contacts ADD CONSTRAINT wbom_contacts_relation_type_id_fkey FOREIGN KEY (relation_type_id) REFERENCES public.wbom_relation_types(relation_type_id);
    END IF;
END $$;

--
-- Name: wbom_employee_requests wbom_employee_requests_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_employee_requests_employee_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_employee_requests ADD CONSTRAINT wbom_employee_requests_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

--
-- Name: wbom_escort_programs wbom_escort_programs_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_escort_programs_contact_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_escort_programs ADD CONSTRAINT wbom_escort_programs_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.wbom_contacts(contact_id);
    END IF;
END $$;

--
-- Name: wbom_escort_programs wbom_escort_programs_escort_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_escort_programs_escort_employee_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_escort_programs ADD CONSTRAINT wbom_escort_programs_escort_employee_id_fkey FOREIGN KEY (escort_employee_id) REFERENCES public.wbom_employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

--
-- Name: wbom_payroll_approval_log wbom_payroll_approval_log_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_payroll_approval_log_run_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_payroll_approval_log ADD CONSTRAINT wbom_payroll_approval_log_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.wbom_payroll_runs(run_id);
    END IF;
END $$;

--
-- Name: wbom_payroll_run_items wbom_payroll_run_items_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_payroll_run_items_run_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_payroll_run_items ADD CONSTRAINT wbom_payroll_run_items_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.wbom_payroll_runs(run_id) ON DELETE CASCADE;
    END IF;
END $$;

--
-- Name: wbom_payroll_runs wbom_payroll_runs_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_payroll_runs_employee_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_payroll_runs ADD CONSTRAINT wbom_payroll_runs_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

--
-- Name: wbom_salary_records wbom_salary_records_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_salary_records_employee_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_salary_records ADD CONSTRAINT wbom_salary_records_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id) ON UPDATE CASCADE ON DELETE RESTRICT;
    END IF;
END $$;

--
-- Name: wbom_staging_payments wbom_staging_payments_final_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_staging_payments_final_transaction_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_staging_payments ADD CONSTRAINT wbom_staging_payments_final_transaction_id_fkey FOREIGN KEY (final_transaction_id) REFERENCES public.wbom_cash_transactions(transaction_id);
    END IF;
END $$;

--
-- Name: wbom_whatsapp_messages wbom_whatsapp_messages_contact_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_whatsapp_messages_contact_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_whatsapp_messages ADD CONSTRAINT wbom_whatsapp_messages_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES public.wbom_contacts(contact_id) ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;
END $$;

--
-- Name: wbom_whatsapp_messages wbom_whatsapp_messages_related_program_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_whatsapp_messages_related_program_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_whatsapp_messages ADD CONSTRAINT wbom_whatsapp_messages_related_program_id_fkey FOREIGN KEY (related_program_id) REFERENCES public.wbom_escort_programs(program_id);
    END IF;
END $$;

--
-- Name: wbom_whatsapp_messages wbom_whatsapp_messages_related_transaction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_whatsapp_messages_related_transaction_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_whatsapp_messages ADD CONSTRAINT wbom_whatsapp_messages_related_transaction_id_fkey FOREIGN KEY (related_transaction_id) REFERENCES public.wbom_cash_transactions(transaction_id);
    END IF;
END $$;

--
-- Name: wbom_whatsapp_messages wbom_whatsapp_messages_template_used_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'wbom_whatsapp_messages_template_used_id_fkey'
    ) THEN
        ALTER TABLE public.wbom_whatsapp_messages ADD CONSTRAINT wbom_whatsapp_messages_template_used_id_fkey FOREIGN KEY (template_used_id) REFERENCES public.wbom_message_templates(template_id);
    END IF;
END $$;

--
--

