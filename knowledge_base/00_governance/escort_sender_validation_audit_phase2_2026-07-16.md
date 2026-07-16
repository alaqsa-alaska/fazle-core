---
title: Escort Program Audit — Phase 2 (Legacy Drafts, S/N 777, Historical Backfill Review, Duplicate Review)
owner: Fazle Core Admin
status: completed
last_verified: 2026-07-16
runtime_index: true
---

# Escort Program Audit — Phase 2

Follow-up to [escort_sender_validation_audit_2026-07-16.md](escort_sender_validation_audit_2026-07-16.md)
(Session 16). Owner requested 5 items; this document records findings and actions for
all 5.

---

## 1. The 10 known-sender expired legacy drafts

All from a *registered* client (`8801836743754`) or admin (`8801958122300`) number,
all predating the 2026-06-27 sender-validation gate, all abandoned mid-order (vessel
named, never followed up with lighter/master details) — genuinely different in kind
from the 18 unauthorized-sender rows removed in Session 16, but the same in effect
(financially inert, never confirmed, cluttering the roster).

Pre-delete verification (read-only):
- `escort_employee_id IS NULL` on all 10 (`wbom_escort_programs` and
  `escort_roster_entries`)
- `fpe_cash_transactions` references: **0** across all 10
- `net_payable = 0.00` on all 10 roster projections

**Action taken:** deleted, per Owner's Phase 2 instruction. Audit-logged first (action
`manual_legacy_expired_cleanup`), `escort_roster_entries` deleted alongside
`wbom_escort_programs` so no orphaned projection rows remain. See §6 for the exact
program_id list including S/N 777 (folded in after the §2 investigation below
concluded it was safe to include).

---

## 2. S/N 777 — reproducibility investigation

**Original stored row:** `mother_vessel=''`, `lighter_vessel='Mv: kafela--01.mobile:'`,
`master_mobile='01820413051'`, `destination=''`.

**Original raw message** (recovered from `wbom_whatsapp_messages`, message_id 7939,
received 2026-05-23 20:30:15):

```
Mv: kafela--01.mobile:01820413051
Destination: rupshi
```

A compact, non-standard shorthand: vessel name and master mobile packed onto one line
with no space (`kafela--01.mobile:01820413051`), no `Mother Vessel:` label at all — the
client never actually stated a mother vessel in this message.

**Replayed through the current parser** (`modules.escort.parse_escort_message()`,
isolated call, no DB write):

```python
>>> parse_escort_message("Mv: kafela--01.mobile:01820413051\nDestination: rupshi")
{
  'mother_vessel': None,
  'lighters': [{'lighter_vessel': 'kafela--01', 'master_mobile': '01820413051',
                'destination': 'Narayanganj', ...}],
  ...
}
>>> is_minimum_escort_order(order)
False
```

**Finding: NOT reproducible.** The label-leakage symptom (raw field-name text like
`"Mv: ...mobile:"` ending up inside `lighter_vessel`) does not happen today —
`lighter_vessel` extracts cleanly as `'kafela--01'`. The current parser also picked up
`destination` correctly (normalized `"rupshi"` → `"Narayanganj"`), which the May-2026
stored row lost entirely. `mother_vessel` is still `None` — but correctly so: the
message never contained one, and today's `is_minimum_escort_order()` check means this
exact message would be rejected outright (no draft created at all) rather than saved in
a corrupted form. That's a strictly better outcome than what happened in May, achieved
by the same 2026-06-27 hardening already covered in Session 16 — no separate code change
needed.

**Conclusion:** legacy-only. No KB-first proposal required (no code change needed).
Folded into the §1 deletion batch.

---

## 3. Historical backfill (`is_historical=true`) — the 9 flagged cases

Compared `remarks.raw_block` (original bridge SQLite message text) against the parsed
columns for each. Recommendation is per-row; **no rows have been changed** — this is a
report only, per Owner's instruction ("do not mass-edit the other historical rows").

| S/N | Raw text (key excerpt) | Parsed | Recommendation |
|---|---|---|---|
| 699 | `MV Meghna Victory / Shirin Akter / Escort: Abdul Majid / Mobile: No` | mv/lv correct, master_mobile blank | **Keep** — "Mobile: No" literally means no mobile was given; blank is correct, not missing data. |
| 702 | `Lighter: Jobayer / Escort Mobile: ... / MV Meghna Victory` (MV stated last) | mv/lv correctly matched despite reversed layout, master_mobile blank | **Keep** — message only gives an Escort Mobile, no Master Mobile; blank is correct. |
| 703 | `Lighter: Al-Amin-4 / 0189437992 / Escort Mobile: 01894371992 / MV Pindes` | mv/lv correct, **master_mobile blank** | **Correct — needs admin input.** A phone-shaped number (`0189437992`) sits directly under the lighter name, distinct from the Escort Mobile below it, and never made it into `master_mobile`. The two numbers differ by one digit (`0189437992` vs `01894371992`) — genuinely ambiguous which is right; recommend admin confirm before filling in. |
| 704 | `Lighter: Mansib-1 / Escort Mobile: ... / MV Sharqi /` | mv has a harmless trailing `/` artifact, lv correct, master_mobile blank | **Keep** (cosmetic-only mv artifact, no master mobile in message so blank is correct). |
| 707 | `Lighter: Europe-2 / 0171301386 / Escort Mobile: 01713322226 / MV Atlantic Ocean /` | mv trailing-`/` artifact, lv correct, **master_mobile blank** | **Correct — needs admin input.** Same pattern as 703: an unclaimed phone-like number sits right under the lighter name. |
| 718 | `MV Atlantic Ocean / Shah Amir / Escort: Shahabuddin / Mobile: 01636129117` | mv/lv correct, master_mobile blank | **Keep** — "Mobile:" here is paired with "Escort:" immediately above it (same pattern as 699), i.e. it's the escort's mobile, not a master mobile; blank is correct. |
| 727 | `MV Ocean Star / Prince of Shimanto - 3 / Escort Mobile: 01616691788` | mv/lv correct, master_mobile blank | **Keep** — only an Escort Mobile given, no Master Mobile; blank is correct. |
| 734 | `GM Fortune / Rafiq Mansur-1 / Escort: Shahin / Mobile: 01302857234` | **mother_vessel blank**, lv=`'GM Fortune'` | **Needs admin review — most ambiguous of the 9.** This is the only one of the 9 with no `MV`/`Mother Vessel` label anywhere in the raw text, so the extractor had nothing to anchor on. It read the first line (`GM Fortune`) as the lighter vessel and dropped the second line (`Rafiq Mansur-1`, which looks like it was meant to be the lighter) entirely. Whether `GM Fortune` is actually the mother vessel (label just omitted) or something else needs someone who knows this vessel to decide — I can't resolve it from text alone. |
| 933 / 934 | See below | | **Merge-delete 933, keep 934.** |

### 933 / 934 — not a normal duplicate

`933`'s `raw_block` is not a client/admin escort message at all — it's the **system's own internal admin-review alert text**, captured verbatim:

```
⚠️ ESCORT COMPLAINT/ASSIGNMENT-LIKE MESSAGE — ADMIN REVIEW
Sender: 8801864127930
Role: blocked

Mother Vessel: MV TRUONG MINH PROSPERITY
...
Note: Client message contains escort assignment/slip/release/food/conveyance context.
Do not create escort order automatically; admin must verify against DB/roster.
```

The SQLite historical backfill parsed this internal alert as if it were a genuine
confirmation and extracted a program row from it — ironically creating exactly the kind
of automatic record its own embedded text says not to create. `934` is the clean,
genuine confirmation for the same assignment (near-identical fields, `escort_name`
`"Rony Banik"` vs 933's `"Md Rony Banik"`). **Recommendation: delete 933, keep 934** —
awaiting sign-off per Owner's instruction (no deletion without explicit approval for
duplicate pairs).

---

## 4. Duplicate review sheet — 7 non-historical + 1 historical pair

**Correction: most of these are not duplicates.** The original detection query grouped
by `mother_vessel + lighter_vessel + master_mobile` only, without `program_date` — for
6 of the 8 pairs, the two rows have **different `program_date`s**, meaning they're
legitimate separate escort runs for the same recurring vessel/lighter combination, not
duplicate entries. Corrected below.

| Pair | S/N | program_date | status | Verdict |
|---|---|---|---|---|
| MV AM Princess / Nowraj-10 | 3, 10 | **same** (2026-01-13) both | completed | **Likely genuine duplicate.** Both created 10 minutes apart (20:22 / 20:32) on 2026-04-18, in a batch of 10 rows (S/N 1,3,9,10,58,60,62,93,107,136) all created within a single 22-minute window that day — looks like a one-time bulk data-seed script that ran/inserted twice. No `remarks.sender_phone` on any of these 10 (not from the message pipeline at all). Recommend merge-delete, pending sign-off. |
| MV Queen Trader / Glove Mariners-1 | 1, 9 | **same** (2026-02-02) both | completed | Same batch, same pattern as above. **Likely genuine duplicate**, recommend merge-delete pending sign-off. |
| Mv Valkyrie / ANJ Karnofully | 58, 136 | **different** (2026-02-01 vs 2026-01-29), different destinations (Ctg vs E. Chng) | completed | **Not a duplicate** — two separate real runs. No action. |
| MV Valkyrie / ANJ Karnafully (note: different spelling from the pair above — same river, "Karnaphuli", transliterated differently) | 60, 107 | **different** (2026-02-01 vs 2026-01-31) | running | **Not a duplicate** — two separate real runs (also a different record from the 58/136 pair above, just the same vessel). No action. |
| Mv Nord Agano / Iftekharul Islam | 62, 93 | **different** (2026-02-01 vs 2026-01-29) | completed | **Not a duplicate** — two separate real runs. No action. |
| MV DUBAI ECO / MV HABIBULLAH | 900, 905 | **different** (2026-06-25 vs 2026-06-26) | confirmed | **Not a duplicate** — two separate real runs, different escort each day. *See new finding below.* |
| MV TRUONG MINH PROSPERITY / MV AL Morium | 901, 906 | **different** (2026-06-25 vs 2026-06-26) | confirmed | **Not a duplicate** — two separate real runs, different escort each day. *See new finding below.* |
| MV TRUONG MINH PROSPERITY / ANJ -7 (historical) | 933, 934 | same | confirmed | **Genuine duplicate** — see §3 above. Recommend merge-delete (933), pending sign-off. |

**No deletions made** for any of these 4 recommended-duplicate pairs (3/10, 1/9, 933/934)
— holding for your explicit sign-off as instructed.

### New finding surfaced by this review (out of the original 5-item scope)

900/901/905/906 all share the same defect: their `destination` column contains text
like `"Escort Name: Sohedul Karim"` / `"Escort Name: Ferdous"` / `"Escort Name: Md
Helal"` / `"Escort Name: Md Rubel"` instead of an actual place name — the escort's name
is leaking into the destination field. All 4 rows are `auto_created=true` (the
`_create_confirmed_from_admin()` direct-create path). I checked whether this is still
live: I could not locate the original admin message in `wbom_whatsapp_messages` for
these (unlike S/N 777, no matching row found in that window), so I could not run the
same replay-and-compare test I ran for §2 — **reproducibility is unconfirmed, not ruled
out**. All 4 rows predate 2026-06-27 by a few hours to two days, so this may or may not
be the same era of bug as everything else in this audit. This is genuinely outside the
5 items you asked for — flagging it and stopping here rather than chasing it further
without your go-ahead. Worth its own follow-up pass if you want one.

---

## 5. Are blank `master_mobile` fields expected-optional or missing?

Answer from KB (`knowledge_base/04_business_rules/escort_business_rules.md`,
`last_verified: 2026-07-05`) — **it depends on which creation path the row went
through**, and the KB is explicit on both:

- **Draft creation (client → bridge):** *"Minimum required fields: Mother Vessel,
  Lighter Vessel, **Master Mobile**"* — required. A row that went through the normal
  draft pipeline should have had a master_mobile at draft time (code enforces this too:
  `is_minimum_escort_order()` requires master_mobile on at least one lighter before any
  draft is even saved).
- **Confirmation (bridge → admin → client):** *"Required confirmation fields: Mother
  Vessel, Lighter Vessel, **Escort Name, Escort Mobile, Start Date**"* — Master Mobile is
  **not** in this list. The admin's confirmation message is about the escort assignment;
  it doesn't need to restate the master mobile already captured on the linked draft.

**So:** a blank `master_mobile` on a row created via `_create_confirmed_from_admin()`
(`auto_created=true`, no draft ever existed) is expected/legitimate — the admin's direct
message was never required to include it. A blank `master_mobile` on a row that went
through a real draft-then-confirm cycle would be more surprising, since it was required
at draft time — that would point at either a value getting dropped during the
confirm-merge, or (as with the 703/707 cases in §3) the field genuinely being present in
the source text but not extracted.

I have not classified all 189 rows individually against this rule — you asked for
findings only, not a per-row pass. If useful, splitting the 189 into
"auto_created (expected-blank)" vs "went-through-a-draft (worth checking)" would be a
quick, cheap next query — say the word and I'll run it.

---

## 6. Program IDs deleted this session

`141, 759, 760, 761, 763, 764, 768, 770, 771, 777` — see §1. Audit trail:
`escort_roster_audit_logs`, action `manual_legacy_expired_cleanup`.

**Not deleted, pending sign-off:** 933 (duplicate of 934), 3 (duplicate of 10), 1
(duplicate of 9).

**Not touched, per instruction:** the 189 blank-field rows; the other 65
non-flagged historical rows; the destination-leak finding in §4.
