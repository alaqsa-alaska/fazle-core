-- Migration 058: correct company name in already-seeded fazle_knowledge_base
-- rows to the current brand (2026-07-22 rebrand: "Al-Rifa'i Integrated
-- Services", replacing "Al-Manaratayn Skill Solutions" / আল-মানারাতাইন
-- স্কিল সলিউশনস, and the older pre-rebrand-1 "Al-Aqsa" name still present
-- in some rows seeded before that first rebrand).
--
-- Content-only UPDATE, no schema change. Idempotent (safe to re-run): each
-- REPLACE() is a no-op once the text no longer contains the old string, and
-- the whole statement is scoped to rows that actually contain one of the
-- old names via the WHERE clause. Covers both reply_text and reply_short.
--
-- Order matters: replace the longer, more specific bilingual form first
-- (Bangla name + parenthetical English name together) so it doesn't leave
-- an orphaned empty "()" behind, then the remaining bare-Bangla and
-- bare-English forms, then the older pre-rebrand-1 name.

BEGIN;

UPDATE fazle_knowledge_base
SET
    reply_text = REPLACE(
        REPLACE(
            REPLACE(
                REPLACE(
                    REPLACE(reply_text,
                        'আল-মানারাতাইন স্কিল সলিউশনস (Al-Manaratayn Skill Solutions)', 'Al-Rifa''i Integrated Services'),
                    'আল-মানারাতাইন স্কিল সলিউশনস', 'Al-Rifa''i Integrated Services'),
                'Al-Manaratayn Skill Solutions', 'Al-Rifa''i Integrated Services'),
            'আল-আকসা সিকিউরিটি সার্ভিস অ্যান্ড ট্রেডিং সেন্টার', 'Al-Rifa''i Integrated Services'),
        'আল-আকসা সিকিউরিটি', 'Al-Rifa''i Integrated Services'
    ),
    reply_short = CASE WHEN reply_short IS NULL THEN NULL ELSE REPLACE(
        REPLACE(
            REPLACE(
                REPLACE(
                    REPLACE(reply_short,
                        'আল-মানারাতাইন স্কিল সলিউশনস (Al-Manaratayn Skill Solutions)', 'Al-Rifa''i Integrated Services'),
                    'আল-মানারাতাইন স্কিল সলিউশনস', 'Al-Rifa''i Integrated Services'),
                'Al-Manaratayn Skill Solutions', 'Al-Rifa''i Integrated Services'),
            'আল-আকসা সিকিউরিটি সার্ভিস অ্যান্ড ট্রেডিং সেন্টার', 'Al-Rifa''i Integrated Services'),
        'আল-আকসা সিকিউরিটি', 'Al-Rifa''i Integrated Services'
    ) END,
    updated_at = NOW()
WHERE reply_text LIKE '%আল-মানারাতাইন%'
   OR reply_text LIKE '%Al-Manaratayn%'
   OR reply_text LIKE '%আল-আকসা%'
   OR (reply_short IS NOT NULL AND (
        reply_short LIKE '%আল-মানারাতাইন%'
        OR reply_short LIKE '%Al-Manaratayn%'
        OR reply_short LIKE '%আল-আকসা%'
   ));

COMMIT;
