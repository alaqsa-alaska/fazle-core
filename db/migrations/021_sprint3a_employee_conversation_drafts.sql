-- ============================================================================
-- Migration 021: Sprint-3A — Employee Conversation + Verification + Draft
-- ============================================================================
-- PHILOSOPHY: Additive only. No existing columns touched. No financial writes.
-- All statements guarded with IF NOT EXISTS / DO NOTHING.
--
-- Adds the mandatory draft fields required by Sprint-3A so that an employee
-- payment-request conversation can produce a fully-validated, 24-hour-expiry
-- draft WITHOUT any financial transaction, ledger update, or balance change.
--
-- Protected components (NOT modified by this migration):
--   • create_transaction()
--   • _upsert_ledger()
--   • accounting_worker()
--   • parse_message()
--   • wbom_cash_transactions (no new columns, no writes)
--   • Existing Payroll Transaction Pipeline
-- ============================================================================

-- ── 1. Extend fazle_payment_drafts with Sprint-3A mandatory fields ───────────
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS payout_mobile        TEXT,
    ADD COLUMN IF NOT EXISTS purpose              TEXT,
    ADD COLUMN IF NOT EXISTS verification_summary JSONB,
    ADD COLUMN IF NOT EXISTS source_message       TEXT,
    ADD COLUMN IF NOT EXISTS conversation_summary  JSONB,
    ADD COLUMN IF NOT EXISTS draft_created_by     TEXT DEFAULT 'ai_conversation',
    ADD COLUMN IF NOT EXISTS conversation_id       TEXT;

-- expires_at already exists from migration 009; ensure it is present for safety.
ALTER TABLE fazle_payment_drafts
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

-- Backfill: give existing pending drafts a 24-hour TTL from creation if missing.
UPDATE fazle_payment_drafts
SET    expires_at = created_at + INTERVAL '24 hours'
WHERE  expires_at IS NULL
  AND  status = 'pending';

-- Index for conversation lookup (one active conversation per employee phone).
CREATE INDEX IF NOT EXISTS idx_pdraft_conversation
    ON fazle_payment_drafts (employee_mobile, status)
    WHERE status = 'pending';

-- ── 2. Knowledge Base seed — employee policy categories ─────────────────────
-- These entries power STEP 3 (inactive employee) and STEP 5 (KB integration).
-- The AI conversation MUST use these; it must never fabricate policy answers.

-- Ensure reply_short column exists (additive; some prod schemas lack it).
ALTER TABLE fazle_knowledge_base
    ADD COLUMN IF NOT EXISTS reply_short TEXT;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'leave_policy', 'leave_policy',
   ARRAY['ছুটি','ছুটির নিয়ম','leave policy','সাপ্তাহিক ছুটি','বার্ষিক ছুটি','চিকিৎসা ছুটি'],
   E'ছুটির নীতি:\n\n• সাপ্তাহিক ছুটি: প্রতি সপ্তাহে ১ দিন (ডিউটি রোস্টার অনুযায়ী)\n• বার্ষিক ছুটি: স্থায়ী কর্মীদের জন্য বছরে ১০ দিন\n• চিকিৎসা ছুটি: মেডিকেল রিপোর্ট সহ আবেদন করতে হবে\n• ছুটির আবেদন অন্তত ৩ দিন আগে লিখিতভাবে দিতে হবে\n• ডিউটি চলাকালীন অনুমতি ছাড়া ছুটি নেওয়া যাবে না',
   'ছুটির নীতি: সাপ্তাহিক ১ দিন, বার্ষিক ১০ দিন, চিকিৎসা ছুটির জন্য রিপোর্ট লাগবে।')
ON CONFLICT DO NOTHING;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'salary_policy', 'salary_policy',
   ARRAY['বেতন','বেতন নীতি','salary policy','বেতন চক্র','payment cycle','বেতন কবে','বেতন পাব'],
   E'বেতন নীতি:\n\n• বেতন চক্র: প্রতি মাসের ১-৩০ তারিখ\n• বেতন পরিশোধ: পরের মাসের ৫-৭ তারিখের মধ্যে\n• এস্কর্ট ডিউটি: ডিউটি সমাপ্তির ৭ দিনের মধ্যে\n• পেমেন্ট পদ্ধতি: বিকাশ / নগদ / নগদ (হাতে)\n• বেতন কর্তন: অগ্রিম, খাবার বিল, কনভেয়েন্স প্রযোজ্য',
   'বেতন চক্র: মাসের ১-৩০, পরিশোধ ৫-৭ তারিখে। পদ্ধতি: বিকাশ/নগদ/নগদ।')
ON CONFLICT DO NOTHING;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'exit_policy', 'exit_policy',
   ARRAY['চাকরি ছাড়','রিজাইন','exit policy','নোটিশ','resignation','ছাড়ব','চাকরি ছাড়ব'],
   E'চাকরি ছাড়ার নীতি (Exit Policy):\n\n• ৩০ দিনের লিখিত নোটিশ দিতে হবে\n• দায়িত্ব হস্তান্তর সম্পন্ন করতে হবে\n• জয়েনিং ফি ৬ মাস পূর্ণ হলে ফেরত পাওয়া যাবে\n• বকেয়া পাওনা চূড়ান্ত হিসাবে মিটিয়ে দেওয়া হবে\n• অনুমতি ছাড়া ছাড়লে জয়েনিং ফি ফেরত পাওয়া যাবে না',
   'Exit: ৩০ দিন নোটিশ, দায়িত্ব হস্তান্তর, জয়েনিং ফি ৬ মাস পর ফেরত।')
ON CONFLICT DO NOTHING;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'advance_policy', 'advance_policy',
   ARRAY['অগ্রিম','অগ্রীম','advance policy','অগ্রিম নিয়ম','জরুরি টাকা','salary advance'],
   E'অগ্রিম নীতি:\n\n• বিশেষ পরিস্থিতিতে সীমিত অগ্রিম সুবিধা\n• সর্বোচ্চ: মাসিক বেতনের ৫০%\n• অনুমোদনের জন্য যাচাই বাধ্যতামূলক (সেলফি + স্লিপ)\n• অগ্রিম পরবর্তী বেতন থেকে কর্তন হবে\n• একই মাসে একাধিক অগ্রিম সাধারণত দেওয়া হয় না',
   'অগ্রিম: সর্বোচ্চ বেতনের ৫০%, যাচাই বাধ্যতামূলক, পরের বেতন থেকে কর্তন।')
ON CONFLICT DO NOTHING;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'food_bill_policy', 'food_bill_policy',
   ARRAY['খাবারের বিল','খাবার বিল','food bill','food bill policy','খাবার খরচ'],
   E'খাবারের বিল নীতি:\n\n• এস্কর্ট ডিউটির সময় খাবার খরচ কর্মীর নিজ দায়িত্বে\n• তবে প্রোগ্রাম অনুযায়ী নির্দিষ্ট ফুড বিল ভাউচার গ্রহণযোগ্য\n• ভাউচার/রসিদ সহ দাবি করতে হবে\n• পেমেন্ট সেটেলমেন্টের সময় কর্তন হিসেবে প্রযোজ্য\n• সর্বোচ্চ সীমা প্রোগ্রাম ভেদে নির্ধারিত',
   'ফুড বিল: ভাউচার সহ দাবি, সেটেলমেন্টে কর্তন।')
ON CONFLICT DO NOTHING;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'conveyance_policy', 'conveyance_policy',
   ARRAY['কনভেয়েন্স','যাতায়াত ভাতা','conveyance','conveyance policy','ভাড়া','ভাড়া','transport allowance'],
   E'কনভেয়েন্স নীতি:\n\n• ডিউটি লোকেশন পরিবর্তনের সময় যাতায়াত ভাতা প্রযোজ্য\n• সর্বোচ্চ সীমা প্রোগ্রাম অনুযায়ী নির্ধারিত\n• রসিদ/টিকিট সহ দাবি করতে হবে\n• পেমেন্ট সেটেলমেন্টে কর্তন হিসেবে প্রযোজ্য\n• নিয়মিত যাতায়াত এর আওতায় পড়ে না',
   'কনভেয়েন্স: লোকেশন পরিবর্তনে ভাতা, রসিদ সহ দাবি।')
ON CONFLICT DO NOTHING;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'rejoining_policy', 'rejoining_policy',
   ARRAY['রিজয়েন','rejoining','পুনরায় যোগ','rejoining policy','ফিরে আসব','আবার জয়েন'],
   E'পুনর্যোগদান নীতি:\n\n• চাকরি ছাড়ার পর ৯০ দিনের মধ্যে পুনরায় যোগ দিতে পারবেন\n• পূর্বের সার্ভিস সময় গণনায় ধরা হবে\n• নতুন ভেরিফিকেশন প্রয়োজন হতে পারে\n• জয়েনিং ফি পুনরায় দিতে হবে না (৯০ দিনের মধ্যে)\n• অনুমোদন ম্যানেজমেন্টের উপর নির্ভরশীল',
   'রিজয়েন: ৯০ দিনের মধ্যে পুনরায় যোগ, পূর্ব সার্ভিস গণনায়।')
ON CONFLICT DO NOTHING;

INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'escort_duty_policy', 'escort_duty_policy',
   ARRAY['এস্কর্ট ডিউটি','escort duty policy','ডিউটি নিয়ম','escort duty','এসকর্ট ডিউটি'],
   E'এস্কর্ট ডিউটি নীতি:\n\n• ডিউটি: লাইটার জাহাজে মালামাল তদারকি\n• সময়: গড়ে ৬-৮ ঘণ্টা, প্রোগ্রাম অনুযায়ী ৭-২০ দিন\n• থাকা: জাহাজেই (ফ্রি)\n• ডিউটি সমাপ্তি: রিলিজ স্লিপ সহ রিপোর্ট করতে হবে\n• পেমেন্ট: ডিউটি সমাপ্তির ৭ দিনের মধ্যে\n• শিফট: দিন (D) / রাত (N)',
   'এস্কর্ট ডিউটি: জাহাজে তদারকি, ৭-২০ দিন, রিলিজ স্লিপ সহ রিপোর্ট।')
ON CONFLICT DO NOTHING;

-- ── 3. Inactive employee guidance (used when status != Active) ──────────────
INSERT INTO fazle_knowledge_base (category, key, value, trigger_keywords, reply_text, reply_short)
VALUES
  ('employee_policy', 'inactive_employee_guidance', 'inactive_employee_guidance',
   ARRAY['inactive','চাকরি ছাড়া','অব্যাহত','resigned','status inactive'],
   E'আপনার কর্মচারী স্ট্যাটাস বর্তমানে সক্রিয় নয়।\n\nপ্রযোজ্য নীতি:\n• চাকরি ছাড়ার নিয়ম: ৩০ দিন লিখিত নোটিশ\n• বকেয়া পাওনা: চূড়ান্ত হিসাবে মিটিয়ে দেওয়া হবে\n• জয়েনিং ফি: ৬ মাস পূর্ণ হলে ফেরত\n• পুনর্যোগদান: ৯০ দিনের মধ্যে সম্ভব\n\nবিস্তারিতের জন্য অফিসে যোগাযোগ করুন।',
   'স্ট্যাটাস সক্রিয় নয়। নোটিশ, বকেয়া, জয়েনিং ফি নীতি প্রযোজ্য।')
ON CONFLICT DO NOTHING;

-- ── 4. Audit marker — Sprint-3A migration applied ───────────────────────────
-- No financial tables touched. No transaction pipeline modified.
-- This migration is purely additive schema + KB seed data.