-- Batch 11 — seed recruitment KB rows so admins can edit triggers/replies
-- without touching code. Idempotent (insert by key).

BEGIN;

-- K3.2 leaves legacy rows as inactive historical records. Do not delete them
-- or overwrite/reactivate them when this seed is rerun.

INSERT INTO fazle_knowledge_base
    (category, key, value, language, trigger_keywords, reply_text, is_active)
VALUES
('recruitment', 'b11_job_details',
 'Survey Scout details + salary',
 'bn',
 ARRAY['চাকরি কি','কাজ কী','কাজ কি','details','ডিউটি কত','কত ঘণ্টা','বিস্তারিত জান','survey scout','job details'],
 E'ধন্যবাদ আমাদের সাথে যোগাযোগ করার জন্য 🙏\n\n👉 পদ: Survey Scout (সার্ভে স্কট)\n📌 কাজ: লাইটার জাহাজে মালামাল তদারকি, লোড–আনলোড হিসাব, চুরি প্রতিরোধ\n⏰ ডিউটি: গড়ে ৬–৮ ঘণ্টা | 🏠 থাকা: জাহাজেই (ফ্রি)\n💰 বেতন: ১০,০০০–১৮,০০০ টাকা | ✅ অভিজ্ঞতা লাগবে না\n\n📄 আবেদন করতে পাঠান: নাম, বয়স, শিক্ষা, ঠিকানা\n📲 WhatsApp: 01958 122322',
 true),

('recruitment', 'b11_office_address',
 'Office address',
 'bn',
 ARRAY['ঠিকানা','লোকেশন','অফিস কোথায়','একে খান','google map','কোথায় যাব'],
 E'📍 আমাদের অফিস:\nআল-আকসা সিকিউরিটি সার্ভিস\nভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n🕘 সকাল ৯টা – বিকাল ৫টা (শুক্রবার বন্ধ)\n📌 আনুন: NID ফটোকপি + ২ কপি ছবি\n📲 WhatsApp: 01958 122322',
 true),

('recruitment', 'b11_apply_intent',
 'How to apply',
 'bn',
 ARRAY['করতে চাই','আগ্রহী','interested','আমাকে নিবেন','জয়েন করতে চাই','apply'],
 E'ধন্যবাদ আগ্রহ দেখানোর জন্য 🙏\n\nআবেদন করতে নিচের তথ্যগুলো পাঠান:\n1️⃣ পূর্ণ নাম\n2️⃣ বয়স\n3️⃣ শিক্ষাগত যোগ্যতা\n4️⃣ বর্তমান ঠিকানা (জেলাসহ)\n5️⃣ মোবাইল নম্বর\n\n📲 WhatsApp: 01958 122322\n✅ কোনো ভর্তি ফি বা জামানত লাগবে না',
 true),

('recruitment', 'b11_anti_fraud',
 'Anti-fraud reassurance',
 'bn',
 ARRAY['বাটপার','ভুয়া','fake','প্রতারক','ধোঁকাবাজ','fraud'],
 E'আল-আকসা সিকিউরিটি সার্ভিস একটি নিবন্ধিত প্রতিষ্ঠান।\n✅ কোনো ফি বা জামানত নেওয়া হয় না\n✅ বেতন প্রতি মাসে নিয়মিত প্রদান করা হয়\n📍 ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n📲 01958 122322 — সরাসরি অফিসে এসে যাচাই করুন।',
 true),

('recruitment', 'b11_documents',
 'Documents required',
 'bn',
 ARRAY['কী কী লাগবে','কাগজপত্র','NID','ছবি','বয়সসীমা','certificate','কী নিয়ে আসব','কি কি লাগবে'],
 E'📌 আবেদনের জন্য যা লাগবে:\n✔ বয়স: ১৮–৪৫ বছর | ✔ শিক্ষা: ন্যূনতম অষ্টম শ্রেণি\n✔ অভিজ্ঞতা লাগবে না — ৪৫ দিন ট্রেনিং দেওয়া হবে\n\n📄 অফিসে আনুন:\n1. NID / জন্ম নিবন্ধন (ফটোকপি)\n2. পাসপোর্ট সাইজ ছবি (২ কপি)\n✅ সার্টিফিকেট না থাকলেও আবেদন করা যাবে',
 true),

('recruitment', 'b11_madrasah_ok',
 'Madrasah/low-education OK',
 'bn',
 ARRAY['মাদ্রাসা','সার্টিফিকেট নেই','দাখিল','আলিম','ক্লাস ৮','পড়তে পারি'],
 E'জি ভাই, আবেদন করতে পারবেন 😊\n✅ মাদ্রাসার ছাত্র — চলবে | ✅ দাখিল/আলিম — চলবে\n✅ শুধু পড়তে ও লিখতে পারলেই যথেষ্ট\n📄 পাঠান: নাম, বয়স, শিক্ষা (যা আছে), ঠিকানা\n📲 WhatsApp: 01958 122322',
 true),

('recruitment', 'b11_vacancy_status',
 'Vacancy still open',
 'bn',
 ARRAY['vacancy','আসন আছে','এখনো নিচ্ছেন','লোক নিচ্ছেন','আর কত জন'],
 E'⚠️ সীমিত আসন — এখনো নিয়োগ চলছে\n👉 আগ্রহী হলে দেরি না করে এখনই আবেদন করুন\n📌 পাঠান: নাম, বয়স, শিক্ষা, ঠিকানা\n📲 WhatsApp: 01958 122322\n✔ আগে এলে আগে সুযোগ',
 true),

('recruitment', 'b11_no_fee',
 'No joining fee',
 'bn',
 ARRAY['টাকা লাগবে','ভর্তি ফি','জামানত','joining fee','deposit','ট্রেনিং ফি'],
 E'❌ কোনো ভর্তি ফি নেই | ❌ কোনো জামানত নেই\n❌ কোনো ট্রেনিং ফি নেই | ❌ কোনো ইউনিফর্ম ফি নেই\n\n✅ আল-আকসা সিকিউরিটি সার্ভিস কখনো টাকা চায় না\nযদি কেউ আমাদের নামে টাকা চায় — সেটি প্রতারণা।\n📲 01958 122322 এ জানান।',
 true),

('recruitment', 'b11_salary',
 'Salary structure',
 'bn',
 ARRAY['বেতন কত','salary','মাসে কত','কত পাব','বেতন কাঠামো'],
 E'💰 বেতন কাঠামো:\n🔹 প্রশিক্ষণকাল (৪৫ দিন): ১০,০০০–১৫,০০০ টাকা\n🔹 পরে: ১২,০০০–১৮,০০০ টাকা\n✔ দক্ষতার উপর বেতন বাড়ে\n✔ ভবিষ্যতে পদোন্নতির সুযোগ',
 true),

('recruitment', 'b11_salary_complaint',
 'Unpaid salary complaint',
 'bn',
 ARRAY['বেতন মেরে','বেতন পাই না','পাওনা দেয়নি'],
 E'আমরা আপনার অভিযোগ গুরুত্বের সাথে নিচ্ছি।\n📲 WhatsApp: 01958 122322\n📍 ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n✅ সব বৈধ পাওনা মিটিয়ে দিতে আমরা প্রতিশ্রুতিবদ্ধ',
 true),

('recruitment', 'b11_greeting',
 'Greeting / menu',
 'bn',
 ARRAY['সালাম','আস্সালামু','hello','হ্যালো','menu','মেনু','start'],
 E'ওয়ালাইকুম আস্সালাম 🙏\n\nআমি ফজলে — আল-আকসা সিকিউরিটি সার্ভিসের ডিজিটাল সহকারী।\n\nকীভাবে সাহায্য করতে পারি?\n1️⃣ চাকরি সম্পর্কে জানতে\n2️⃣ অফিসের ঠিকানা\n3️⃣ বেতন সম্পর্কে\n4️⃣ ডিউটি/পেমেন্ট\n\nযেকোনো প্রশ্ন করুন 👇',
 true)
ON CONFLICT (key) DO NOTHING;

COMMIT;

SELECT category, count(*) FROM fazle_knowledge_base GROUP BY category ORDER BY 1;
