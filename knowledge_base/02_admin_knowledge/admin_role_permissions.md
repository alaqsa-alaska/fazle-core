---
title: Admin Role Permissions
owner: Fazle Core Admin
status: active
last_verified: 2026-07-02
runtime_index: true
---

# অ্যাডমিন রোল এবং অনুমতি

## উৎস
এই ডকুমেন্ট `modules/rbac/__init__.py`-এর `COMMAND_ROLE` ডিকশনারি থেকে সরাসরি তৈরি।
কোড পড়ে লেখা হয়েছে — অনুমান নয়।

## রোল হায়ারার্কি

| রোল | লেভেল | বিবরণ |
|-----|-------|-------|
| `viewer` | 1 | শুধু পড়া — রিপোর্ট, স্ট্যাটাস |
| `operator` | 2 | ড্রাফট ম্যানেজমেন্ট, পেমেন্ট, রিলিজ |
| `accountant` | 3 | পেরোল ম্যানেজমেন্ট, পেমেন্ট ইমপোর্ট |
| `admin` | 4 | সিস্টেম জব, ব্যাকআপ, ইউজার তালিকা |
| `superadmin` | 5 | ইউজার তৈরি, রোল পরিবর্তন, API কী |

> নিয়ম: কমান্ডের required_role-এর লেভেল ≤ অ্যাডমিনের সর্বোচ্চ রোল লেভেল হলে অনুমতি মেলে।
> অজানা কমান্ড: ডিফল্ট `admin` রোল প্রয়োজন।

---

## রোল অনুযায়ী কমান্ড অ্যাক্সেস

### Viewer (লেভেল ১) — শুধু পড়া

| কমান্ড | বিবরণ |
|--------|-------|
| `STATUS` / `DRAFTS` / `PENDING` | পেন্ডিং ড্রাফটের সংখ্যা ও তালিকা |
| `SCHEDULE STATUS` | সময়সূচী স্ট্যাটাস |
| `REPORT LIST` | রিপোর্ট তালিকা |
| `REPORT DAILY` | দৈনিক রিপোর্ট |
| `REPORT PAYROLL` | পেরোল রিপোর্ট |
| `REPORT CASH` | ক্যাশ রিপোর্ট |
| `REPORT RECON` | রিকনসিলিয়েশন রিপোর্ট |
| `REPORT ESCORT` | এসকর্ট রিপোর্ট |
| `BACKUP STATUS` | ব্যাকআপ স্ট্যাটাস |
| `BACKUP LIST` | ব্যাকআপ তালিকা |
| `PAYROLL LIST` | পেরোল রান তালিকা |

### Operator (লেভেল ২) — ড্রাফট ও পেমেন্ট

| কমান্ড | বিবরণ |
|--------|-------|
| `APPROVE <id>` | ড্রাফট অনুমোদন ও পাঠানো |
| `REJECT <id>` | ড্রাফট প্রত্যাখ্যান |
| `EDIT <id> <text>` | ড্রাফট সম্পাদনা |
| `PAID <id> <amount> [method]` | পেমেন্ট চিহ্নিত করুন |
| `ADVANCE <id> <amount> [method]` | অ্যাডভান্স অনুমোদন |
| `RELEASE <program_id> <date> <D\|N> <point>` | রিলিজ স্লিপ তৈরি |
| `ESCORTCONFIRM <id> \| <name> \| <mobile> \| <date> \| <D\|N>` | এসকর্ট নিশ্চিতকরণ |

> Operator কমান্ডে Viewer সব কমান্ডও চালাতে পারে।

### Accountant (লেভেল ৩) — পেরোল ও ফাইন্যান্স

| কমান্ড | বিবরণ |
|--------|-------|
| `PAY-IMPORT <text>` | পেমেন্ট ডেটা ইমপোর্ট |
| `PAYROLL COMPUTE <YYYY-MM>` | পেরোল রান তৈরি |
| `PAYROLL SUBMIT/APPROVE/LOCK <run_id>` | পেরোল অবস্থা পরিবর্তন |
| `PAYROLL PAID <run_id> <amount> <method>` | পেরোল পেমেন্ট চিহ্নিত |
| `PAYROLL CANCEL <run_id> <reason>` | পেরোল বাতিল |
| `APPROVED <id> <amount> [method]` | ক্যানোনিক্যাল পেমেন্ট অনুমোদন |
| `DREDIT <id> <amount> [method]` | পেমেন্ট ড্রাফট সম্পাদনা |
| `DREJECT <id> [reason]` | পেমেন্ট ড্রাফট প্রত্যাখ্যান |
| `REVERSE <transaction_id> <reason>` | লেনদেন রিভার্স |
| `ADJUST <draft_id> <amount> <method>` | পেমেন্ট সমন্বয় |

> Accountant কমান্ডে Operator ও Viewer সব কমান্ডও চালাতে পারে।

### Admin (লেভেল ৪) — সিস্টেম ম্যানেজমেন্ট

| কমান্ড | বিবরণ |
|--------|-------|
| `RUN JOB <job_name>` | সময়সূচী জব ম্যানুয়ালি চালু |
| `BACKUP NOW` | তাৎক্ষণিক ব্যাকআপ |
| `USER LIST` | ইউজার তালিকা দেখুন |

> অজানা কমান্ড: ডিফল্ট `admin` প্রয়োজন।

### Superadmin (লেভেল ৫) — সর্বোচ্চ অ্যাক্সেস

| কমান্ড | বিবরণ |
|--------|-------|
| `USER ADD <phone> <name> [role]` | নতুন অ্যাডমিন ইউজার যোগ |
| `USER ROLE <phone> <role>` | রোল পরিবর্তন |
| `USER REMOVE <phone>` | ইউজার নিষ্ক্রিয় করুন |
| `USER APIKEY <phone>` | API কী তৈরি |

---

## রোল পরিবর্তনের নিয়ম

- শুধুমাত্র **Superadmin** রোল পরিবর্তন করতে পারে (`USER ROLE` কমান্ড)।
- `ADMIN_NUMBERS` env-এ থাকা নম্বর স্বয়ংক্রিয়ভাবে `superadmin` হিসেবে বুটস্ট্র্যাপ হয়।
- রোল শুধু `viewer`, `operator`, `accountant`, `admin`, `superadmin`-এর মধ্যে সীমাবদ্ধ।
- একজন ইউজারের একাধিক রোল থাকতে পারে — সর্বোচ্চ লেভেল কার্যকর হয়।

## অ্যাকসেস ডিনায়াল

রোল অপর্যাপ্ত হলে: `` `{required_role}` রোল প্রয়োজন। ``
প্রতিটি denied attempt `fazle_admin_audit` টেবিলে লগ হয়।

## Cross References
- `02_admin_knowledge/admin_command_reference.md`
- `06_developer_system/security_rules.md`
