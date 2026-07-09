# Deployment / Restart Gate — Design

**Status:** DESIGN ONLY — not implemented as a hard-blocking systemd hook yet, per explicit instruction. This document specifies what the gate should check and how, so it can be implemented (as a pre-restart script, then optionally wired into `ExecStartPre=` or a wrapper around `systemctl restart`) once approved.

**Why this exists:** `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 8 Risk #2 ("working-tree deployment") found that `fazle-core.service` runs directly off the git working tree with no build/artifact step — any restart loads whatever is currently on disk, reviewed or not. This gate is the smallest mechanical fix for that risk without requiring a full build/artifact pipeline.

**Design principle:** the current working tree is *routinely* dirty (in-flight release work sits uncommitted for days at a time in this repo's normal workflow) — a gate that demands 100% clean would be unworkable and would just get bypassed. So this gate distinguishes **known, reviewed, currently-dirty files** (an explicit allowlist) from **anything new/unexpected**, and blocks only the latter.

---

## The 9 checks

### 1. Working-tree cleanliness or approved dirty-file manifest
- Maintain a file, `db/.restart_gate_manifest` (plain text, one path per line, comment lines start with `#`), listing every path that is *currently* expected to be dirty (e.g. the REL-2026-07-W1 in-flight files).
- Gate logic: `git status --porcelain` output, minus any path present in the manifest, must be empty. Anything left over is an **unreviewed** change — block.
- Updating the manifest is itself a reviewable, committed change (or at minimum a deliberate edit with a reason in the commit/PR), so "add my new dirty file to the allowlist" can't happen silently.

### 2. No leaked secret patterns in git-tracked files
- Run a secret-scan tool (gitleaks or trufflehog) against the current `git diff` / working tree, not just history.
- Minimum viable version without installing a new tool: a small pattern list (`fk_[A-Za-z0-9_-]{20,}`, common password-field patterns like `_PASSWORD=` followed by a non-empty, non-placeholder value) grepped across all git-tracked files.
- Block on any hit that isn't already in a documented allowlist (e.g. a deliberately-shown example key in a test fixture, if any exist).

### 3. No new `xfail` increase
- `grep -rc "pytest.mark.xfail" tests/` before vs. after — compare against a stored baseline count (`db/.xfail_baseline`, a single integer, updated deliberately alongside any Owner-approved waiver).
- Block if the current count exceeds the stored baseline. A legitimate new waiver requires updating the baseline file as its own reviewed step, not an automatic pass-through.

### 4. Targeted tests pass
- Run the test subset relevant to whatever changed since the last approved restart (at minimum: `tests/unit/test_admin_commands.py`, `tests/unit/test_drafts_router_mounted.py`, `tests/unit/test_rbac.py` — the money/auth-adjacent suites — plus any suite touching a file in the current diff).
- Block on any failure **not** already accounted for in the known-pre-existing-failures list (`TESTING_STATUS.md`'s successor — see Tier 3 Task 8 in `docs/NEXT_IMPLEMENTATION_QUEUE.md`, not yet reconciled as of 2026-07-09).

### 5. No untracked migration files without an inventory entry
- Every file under `db/migrations/*.sql` must have a corresponding line in `db/MIGRATION_INVENTORY.md`.
- Gate logic: diff the `db/migrations/` directory listing against the inventory doc's file list; block on any `.sql` file not mentioned there.
- This directly prevents a repeat of the 2026-07-08 finding (4 migration files sitting untracked and undocumented).

### 6. Current `.env` critical flags acknowledged
- Maintain a short list of safety-critical flags (`AUTO_REPLY_ENABLED`, `RECRUITMENT_AUTOREPLY_ENABLED`, `SOCIAL_AUTO_REPLY_SINGLE_ENGINE`, `AI_SAFE_MODE`, `INTERNAL_NOTIFICATIONS_ENABLED`) and their last-acknowledged values (`db/.env_flags_acknowledged`, key=value pairs).
- Gate logic: read current `.env` values for these keys; if any differs from the acknowledged file, **warn, don't hard-block** (an intentional flag change is a valid reason to restart) — but require the operator to explicitly re-run with an `--ack-flags` step that updates the acknowledged file, so a flag change can never be silently unnoticed the way `AUTO_REPLY_ENABLED` was (see the 2026-07-08/09 KB reconciliation).

### 7. DB backup freshness
- Check the newest file in `/home/azim/backups/fazle/` — block if older than `BACKUP_STALE_HOURS` (already an app-level setting, default 48h; reuse the same threshold here for consistency).
- This is cheap insurance: don't restart into a bad migration/state with no recent recovery point.

### 8. Service health before restart
- Hit `/health` (and, with the internal key, `/health/deep`) on the *currently running* process before touching it. If it's already unhealthy, restarting might be the right call anyway, but the gate should record the before-state so a post-restart comparison is possible (see check 9) — don't block on this one, just capture it.

### 9. Service health after restart
- After `systemctl restart fazle-core.service`, poll `/health` (with backoff, e.g. every 2s up to 30s) until it returns 200, or declare the restart failed.
- If `/health/deep` was healthy before and is unhealthy after, this is a strong signal to roll back (see `RECOVERY.md`'s existing git-tag/tar-snapshot rollback procedure) rather than leave a broken restart running.

---

## Suggested implementation shape (for when this is approved to build)

A single script, `scripts/restart_gate.sh`, run as:
```bash
scripts/restart_gate.sh --check-only   # checks 1-7, prints PASS/BLOCK/WARN per item, exits non-zero on any BLOCK
scripts/restart_gate.sh --restart      # runs checks 1-7, then (if PASS) does checks 8-9 around an actual restart
```
Not wired into `ExecStartPre=` yet (that would make it hard-blocking at the systemd level, which the instructions explicitly said not to do without further approval) — for now, it's a script the operator (or an AI agent acting under explicit instruction) runs manually before requesting a restart.

## Verdict format
Each run should produce one line per check:
```
[PASS ] 1. Working-tree cleanliness ... 0 unreviewed dirty files
[PASS ] 2. Secret scan ................ 0 matches
[PASS ] 3. xfail count ................ 13 (baseline 26, no increase)
[PASS ] 4. Targeted tests ............. 5/5 passed
[BLOCK] 5. Migration inventory ........ 1 file not in inventory: db/migrations/030_foo.sql
[WARN ] 6. .env critical flags ........ AUTO_REPLY_ENABLED changed (false -> true), not yet acknowledged
[PASS ] 7. Backup freshness ........... newest dump 14h old (threshold 48h)
--------------------------------------------------------------
OVERALL: BLOCK (1 blocking item, 1 warning)
```
