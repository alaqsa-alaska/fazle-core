---
title: Recruitment Scoring
owner: Fazle Core Admin
status: active
last_verified: 2026-07-02
runtime_index: true
---

# নিয়োগ স্কোরিং সিস্টেম

## উৎস
`modules/recruitment_flow/__init__.py` — `_compute_score()` ফাংশন থেকে সরাসরি।

---

## স্কোর বাকেট (Buckets)

| মোট স্কোর | বাকেট | অর্থ |
|-----------|-------|------|
| ৭০–১০০ | `hot` | সর্বোচ্চ অগ্রাধিকার |
| ৪০–৬৯ | `warm` | মধ্যম অগ্রাধিকার |
| ০–৩৯ | `cold` | নিম্ন অগ্রাধিকার |

> সর্বোচ্চ স্কোর: ১০০ (কোড: `score = min(score, 100)`)

---

## স্কোর পয়েন্ট বিভাজন

| শর্ত | পয়েন্ট |
|------|--------|
| অভিজ্ঞতা ≥ ৬ বছর | +৬০ |
| অভিজ্ঞতা ৩–৫ বছর | +৪০ |
| অভিজ্ঞতা ১–২ বছর | +২০ |
| পদ পছন্দ: Escort / Survey Scout / Security Guard | +২০ |
| সব প্রয়োজনীয় ফিল্ড পূর্ণ (নাম, বয়স, এলাকা, পদ) | +২০ |

> মোট সম্ভাব্য: ৬০ + ২০ + ২০ = ১০০

---

## বয়স সীমা

- সর্বনিম্ন: **১৮ বছর**
- সর্বোচ্চ: **৫৫ বছর**
- যাচাই: `val if 18 <= val <= 55 else None` (কোড থেকে)
- এই সীমার বাইরে বয়স দিলে ফিল্ড গ্রহণযোগ্য হয় না।

---

## সেশন মেয়াদ

- `SESSION_TTL = timedelta(hours=24)`
- ২৪ ঘণ্টার বেশি নিষ্ক্রিয় সেশন মেয়াদোত্তীর্ণ বলে গণ্য হয়।
- মেয়াদোত্তীর্ণ সেশন: পুনরায় শুরু করতে হবে।

---

## সংগ্রহ ধাপ (Collection Steps)

ক্রমানুসারে:

| ধাপ | ফিল্ড | প্রশ্ন |
|-----|-------|-------|
| ১ | `name` | পুরো নাম কি? |
| ২ | `age` | বয়স কত বছর? (১৮–৫৫) |
| ৩ | `area` | কোন জেলায় থাকেন? |
| ৪ | `job_preference` | কোন পদে কাজ করতে চান? (১–৯) |
| ৫ | `experience` | কত বছরের অভিজ্ঞতা? (নতুন = ০) |
| ৬ | `phone_confirm` | মোবাইল নম্বর নিশ্চিত করুন |

---

## পদ তালিকা (Job Preferences — কোড থেকে)

| কোড | পদের নাম |
|-----|----------|
| ১ | Escort |
| ২ | Survey Scout |
| ৩ | Security Guard |
| ৪ | Security Supervisor |
| ৫ | Assistant Supervisor |
| ৬ | Operation Officer |
| ৭ | Security In-Charge |
| ৮ | Marketing Officer |
| ৯ | Ghat Supervisor |

---

## কমপ্লিশন মেসেজ

সেশন সম্পন্ন হলে:
"আপনার তথ্য সফলভাবে সংগ্রহ করা হয়েছে। আমাদের টিম শীঘ্রই আপনার সাথে যোগাযোগ করবে। আরো জানতে: 01958 122322"

## Cross References
- `01_employee_knowledge/recruitment_policy.md`
- `06_developer_system/developer_notes.md`
