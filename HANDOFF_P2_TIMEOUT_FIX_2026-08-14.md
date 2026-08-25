# P2 (opencode_dispatch over WhatsApp) — Handoff, 2026-08-14

**Written:** end of the 2026-08-13/14 session that continued from
`HERMES_FULL_AUTHORITY_NEXT_SESSION_PLAN_2026-08-13.md` (P1, all three
items, is now fully done — see that doc's own checklist, confirmed closed
tonight). This note picks up exactly where P1 left off: P2 item 4,
live-exercising `opencode_dispatch` through WhatsApp for the first time.

**Read this first**, then `HERMES_FULL_AUTHORITY_NEXT_SESSION_PLAN_2026-08-13.md`
for the broader P0–P3 picture — this doc only covers tonight's P2 work.

---

## 1. What got proven tonight

Three real, independently-verified capabilities, not just claims:

- **Mode inheritance works correctly for a *fresh* Hermes session.** Direct
  diagnostic (bypassing WhatsApp, hitting `hermes-runner`'s `/run` on
  `127.0.0.1:8093` with the admin secret) compared two calls under the
  same live RUN-mode window:
  - Fresh session → reported full RUN toolset: `memory, web, todo, skills,
    fazle-core, file, code_execution, terminal`.
  - A session that had been *resumed* from one born earlier under READ →
    reported a stale, narrower set (no `terminal`, no `code_execution`),
    **despite the persisted mode being genuinely RUN at call time.**
  This is the root cause of two earlier-tonight failures where Hermes
  correctly, honestly reported it had no file/terminal tools even though
  `current_mode.txt` said RUN — a resumed session does not pick up a
  mode change made after it was created.

- **The practical fix is already live and works**: `modules/admin_directives/router.py`
  already has reset keywords (`_HERMES_RESET_KEYWORDS`: "reset", "clear",
  "নতুন কথোপকথন", "নতুন চ্যাট", "start over", "new conversation") that
  clear the per-(channel, sender) session-continuity cache. Sending one of
  these before a task forces the next turn onto a brand-new session, which
  the diagnostic above confirms correctly inherits whatever mode is
  currently active. No code change was needed for this — it already
  existed, it just wasn't part of the operating pattern until tonight.

- **The Bridge1→Hermes→OpenCode→Bridge2 wiring is real end-to-end for the
  parts that don't hit the timeout bug below.** After a `reset`, Hermes
  correctly used its now-genuine file/terminal access to verify the real
  state of `fazle-mcp` before doing anything (ahead-6-commits count,
  the one unrelated modified file) — all independently confirmed against
  actual `git status`/`git log` afterward, exact match. It then correctly
  called `mcp__fazle_core__opencode_dispatch`, which correctly created a
  real OpenCode session (`ses_00266566dffe0vK11hstiLJm2k`, created
  2026-08-14T00:08:54 UTC, confirmed via `GET assistant-backend/api/opencode/session`
  sorted by `time.created`). The dispatch call itself fired correctly.
  (For reference: the earlier, simpler task — writing `hermes-runner/README.md`
  directly, no OpenCode involved — succeeded fully end-to-end over WhatsApp,
  commit `d7886554`, verified pushed and clean. That one has no open issue.)

## 2. Structural problem found: timeout budget is mathematically too tight

Three independent timeouts stack in this chain, and the outer one is not
larger than the inner ones — it's the same or smaller:

| Layer | File | Value |
|---|---|---|
| `opencode.js`'s `/session/:id/prompt` poll loop | `assistant-platform/backend/src/routes/opencode.js` | `const deadline = Date.now() + 150000;` → **150s** |
| `opencode_dispatch`'s own client timeout | `fazle-mcp/opencode_tools.py` | `_PROMPT_TIMEOUT_S = 155` → **155s** (comment: "give it a little headroom on this side too") |
| `hermes-runner`'s outer subprocess timeout for the *entire* `hermes chat` call (Hermes's reasoning + tool call + reply, not just the OpenCode leg) | `hermes-runner/server.py:197` | `TIMEOUT_SECONDS = int(os.environ.get("HERMES_RUN_TIMEOUT", "150"))` → **150s** |

The outermost budget (150s) is *smaller than or equal to* the innermost
one (155s) it's supposed to contain, with zero room left for Hermes's own
LLM reasoning time before and after the tool call. This is not a
one-off flake — it's structurally near-guaranteed to time out on any
`opencode_dispatch` call where OpenCode takes anywhere close to its own
allowed window.

**Live evidence tonight:** `subprocess_diag.log` shows
`"event": "subprocess_timeout", "session": "20260814_020210_82e02d",
"elapsed_s": 150.1` at `2026-08-14T00:11:01.846+00:00` — almost exactly
the 150s ceiling. The user got `⚠️ Hermes ত্রুটি: Hermes did not respond in time`.

**Separately, and worth investigating on its own**: checking the OpenCode
session directly (not through Hermes at all) ~17 minutes after creation,
it still has exactly **one message** — the original prompt — no assistant
reply at all, not even a partial one. `fazle-mcp` is untouched (still
6 ahead of origin, still only `fazle_core_client.py` modified, no
`README.md`). No git lock file. `opencode-serve.service` shows 0 active
tasks and near-zero CPU. This does not look like "still working, just
slow" — it looks stalled. **Fixing the timeout math alone may not be
sufficient** if OpenCode itself genuinely hung on this prompt; the next
session should check this specific session's state first (see §4) before
assuming a bigger timeout alone will fix things.

Nothing was restarted or retried tonight — the Owner explicitly chose to
stop here rather than restart `opencode-serve.service` blind. The stuck
session (`ses_00266566dffe0vK11hstiLJm2k`) is still open as of this
writing.

## 3. Recommended change (not yet made — needs the same review/approval as any config change)

- Raise `HERMES_RUN_TIMEOUT` (env var consumed by `hermes-runner/server.py:197`)
  to **300s or higher** — well above `opencode_tools.py`'s 155s inner
  budget, with real headroom for Hermes's own reasoning/tool-call
  overhead on both sides of the OpenCode leg.
- Keep the whole chain's budgets strictly nested and documented in one
  place (a comment near each of the three constants cross-referencing the
  other two would prevent this from silently drifting out of sync again):
  `opencode.js` poll deadline < `opencode_tools.py`'s `_PROMPT_TIMEOUT_S` <
  `hermes-runner`'s `HERMES_RUN_TIMEOUT`, each with meaningful margin, not
  just a few seconds.
- This is a `.env` change on `hermes-runner`, which needs a service
  restart to take effect — same restart-approval courtesy as any other
  service restart in this project, not something to apply-and-restart
  silently.
- Before raising the timeout and calling it fixed: resolve the "OpenCode
  never produced a single reply message" observation in §2 separately —
  a bigger timeout does nothing if OpenCode itself is actually stuck, not
  just slow.

## 4. Before anything else: check the stuck session first

1. `GET` the assistant-backend OpenCode session-messages endpoint for
   `ses_00266566dffe0vK11hstiLJm2k` (same call pattern as `opencode_check`)
   — has it produced a reply since last night? If yes, read it and check
   whether `fazle-mcp` changed to match (`git log`, `git status`) before
   assuming anything is still broken.
2. If it's still stuck with zero progress, that's independent confirmation
   the timeout math wasn't the only problem — investigate `opencode-serve.service`
   health/logs before retrying anything, and only restart it with an
   explicit go-ahead (it's a live service other things may depend on).

## 5. Small, safe next live test (once §3/§4 are resolved)

Don't re-run the same README task first — it involves multiple file reads
(`server.py`, `opencode_tools.py`, `send_whatsapp_tools.py`, `draft_tools.py`,
`pii_mask.py`, `audit_tools.py`, `monitoring_tools.py`) plus a write plus
git ops, which makes it hard to tell "timeout too short" apart from
"OpenCode is just slow for this much work." Isolate the variable first:

- **Step 1 (read-only, no dispatch needed, no risk):** ask Hermes to call
  `opencode_check` on a *fresh* trivial OpenCode session (or just measure
  how long a minimal `opencode_dispatch` prompt takes) — e.g. "read the
  first 20 lines of `fazle-mcp/server.py` and report its module docstring,
  nothing else, no file writes." This measures real OpenCode round-trip
  latency for near-zero work, cheaply, before trusting a bigger timeout to
  fix a bigger task.
- **Step 2**, only once Step 1 comes back promptly: retry the actual
  `fazle-mcp` README dispatch task from tonight — same prompt is still
  valid and was correctly scoped (explicit `git add README.md` only, do
  not touch `fazle_core_client.py`, do not use `-A`/`.`/`-u`).
- Continue using the `reset` keyword before either of these — tonight's
  fix for the mode-inheritance bug is real but still needs to be invoked
  deliberately each time a task follows mode elevation.

## 6. State at session close (2026-08-14, ~00:26 UTC / 06:26 BD)

- `fazle-mcp`: unchanged — 6 ahead of origin, `fazle_core_client.py`
  still locally modified, no `README.md`. Nothing pushed.
- OpenCode session `ses_00266566dffe0vK11hstiLJm2k`: still open, one
  message, no reply.
- `hermes-runner.service` / `opencode-serve.service`: both untouched,
  both still running the same code/config as before tonight's session.
- Hermes mode: RUN grant from tonight (`23:51:28 UTC` → `00:21:28 UTC`)
  has expired; next call will fail-closed to READ as designed.
- No commits made to `fazle-core`, `hermes-runner`, or `fazle-mcp` by this
  orchestrating session tonight beyond what's already recorded in
  `HERMES_FULL_AUTHORITY_NEXT_SESSION_PLAN_2026-08-13.md`'s own P1 log.
