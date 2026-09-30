"""Capability-boundary enforcement for the Admin -> Hermes relay.

2026-09-29, Admin Canary audit. This is the fazle-core half of the fix; the
enforcement logic itself lives in the Hermes ``task_action_policy`` plugin
(``~/.hermes/plugins/task_action_policy/``), which runs in a different
process and venv and therefore has its own suite. What belongs *here* is
everything fazle-core owns and can be gate-checked:

  1. ``is_action_type_authorized`` -- the expiry-aware authorization answer
     the plugin now calls. Before this, the plugin treated a non-empty
     ``list_actions(status='approved')`` as authorization, while that
     function documents that it does NOT filter expired rows. Nine
     approvals left over from Aug 2026 were therefore permanently
     authorizing restarts, deploys and migrations.
  2. The new read-only ``/api/actions/authorization-state`` route.
  3. A drift guard for the two byte-identical copies of
     ``action_policy.py`` (fazle-mcp and the Hermes plugin), whose contract
     is "keep both copies byte-identical" but which nothing enforced.
  4. Structural guards that fazle-core's own MCP write path is NOT weakened
     or double-gated by this work.

No test here performs a real mutation: they use the transactional test
database, mocks, and source inspection only.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from modules.hermes_tasks import (
    TaskError,
    is_action_approved,
    is_action_type_authorized,
    list_actions,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.db]

_PLUGIN_DIR = Path("/home/azim/.hermes/plugins/task_action_policy")
_MCP_ACTION_POLICY = Path("/home/azim/fazle-mcp/action_policy.py")
_PLUGIN_ACTION_POLICY = _PLUGIN_DIR / "action_policy.py"
_PLUGIN_INIT = _PLUGIN_DIR / "__init__.py"


def _load_policy_module():
    """Load the shared action_policy by path.

    It lives outside this repository (fazle-mcp's copy, mirrored into the
    Hermes plugin) and has no package of its own, so it is loaded by file
    location rather than import path. The byte-identity test below is what
    guarantees the loaded copy is the same code the enforcement layer runs.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_fazle_mcp_action_policy", _MCP_ACTION_POLICY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _approval(conn, *, action_type, status="approved", expires_in_hours=24,
                    command=None, diff=None, task_id=None):
    """Insert one hermes_action_approvals row directly.

    Written as raw SQL rather than via authorize_action() so a test can
    create states the API refuses to produce (notably status='rejected' and
    rows already past their expiry window) without weakening the API.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO hermes_action_approvals
            (task_id, action_type, summary, files_changed, diff, command, risk,
             expected_effect, rollback_plan, requested_by, status, reviewed_by,
             reviewed_at, created_at, expires_at, executed_at)
        VALUES ($1, $2, 'synthetic capability-boundary test', NULL, $3, $4,
                'high', NULL, NULL, 'synthetic-test', $5, 'synthetic-test',
                NOW(),
                NOW() - (INTERVAL '48 hours' * $6::int),
                NOW() + (INTERVAL '1 hour' * $7::int),
                CASE WHEN $8 THEN NOW() ELSE NULL END)
        RETURNING id
        """,
        task_id, action_type, diff, command, status, 1, expires_in_hours,
        status == "executed",
    )
    return int(row["id"])


@pytest.fixture(autouse=True)
async def _clean_synthetic_approvals(test_db_pool):
    async def _clean():
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM hermes_action_approvals WHERE requested_by = 'synthetic-test'"
            )
    await _clean()
    yield
    await _clean()


# ── 1. Expiry-aware authorization (the actual regression) ─────────────────────
async def test_expired_approval_does_not_authorize_restart(test_db_pool):
    """The live defect, reproduced: an approval that existed and was approved
    but has since expired must not authorize a service restart."""
    async with test_db_pool.acquire() as conn:
        aid = await _approval(conn, action_type="restart", status="approved", expires_in_hours=-1)

    result = await is_action_type_authorized("restart")

    assert result["authorized"] is False
    assert aid not in result["action_ids"]
    assert any(r["id"] == aid for r in result["rejected"])


async def test_expired_approval_does_not_authorize_deploy(test_db_pool):
    async with test_db_pool.acquire() as conn:
        aid = await _approval(conn, action_type="deploy", status="approved", expires_in_hours=-1)
    result = await is_action_type_authorized("deploy")
    assert result["authorized"] is False
    assert aid not in result["action_ids"]


async def test_expired_approval_does_not_authorize_migration(test_db_pool):
    async with test_db_pool.acquire() as conn:
        aid = await _approval(conn, action_type="migration", status="approved", expires_in_hours=-1)
    result = await is_action_type_authorized("migration")
    assert result["authorized"] is False
    assert aid not in result["action_ids"]


async def test_many_expired_approvals_still_authorize_nothing(test_db_pool):
    """The exact live shape: many stale rows, none current. Non-emptiness of
    the discovery listing is not consent."""
    async with test_db_pool.acquire() as conn:
        for _ in range(4):
            await _approval(conn, action_type="restart", status="approved", expires_in_hours=-1)

    # Proof the old code path would have said yes ...
    legacy = await list_actions("approved", None, "restart")
    assert len(legacy) >= 4, "synthetic expired rows should be visible to the listing"
    # ... while the canonical answer is no.
    assert (await is_action_type_authorized("restart"))["authorized"] is False


async def test_current_matching_approval_authorizes(test_db_pool):
    async with test_db_pool.acquire() as conn:
        aid = await _approval(conn, action_type="restart", status="approved", expires_in_hours=1)
    result = await is_action_type_authorized("restart")
    assert result["authorized"] is True
    assert result["action_ids"] == [aid]


async def test_rejected_approval_does_not_authorize(test_db_pool):
    async with test_db_pool.acquire() as conn:
        aid = await _approval(conn, action_type="restart", status="rejected", expires_in_hours=1)
    result = await is_action_type_authorized("restart")
    assert result["authorized"] is False
    assert aid not in result["action_ids"]


async def test_missing_approval_does_not_authorize(test_db_pool):
    """No rows at all is a deny, not a vacuous allow."""
    result = await is_action_type_authorized("restart")
    assert result["authorized"] is False
    assert result["action_ids"] == []


async def test_already_executed_approval_does_not_authorize(test_db_pool):
    """Single-use: once executed, the row's status is no longer 'approved',
    so it stops authorizing a repeat of the same action."""
    async with test_db_pool.acquire() as conn:
        aid = await _approval(conn, action_type="deploy", status="executed", expires_in_hours=1)
    assert (await is_action_type_authorized("deploy"))["authorized"] is False
    assert await is_action_approved(aid) is False


async def test_approval_does_not_authorize_a_different_action_type(test_db_pool):
    """Action binding: a current restart approval must not satisfy a
    migration check, and vice versa."""
    async with test_db_pool.acquire() as conn:
        await _approval(conn, action_type="restart", status="approved", expires_in_hours=1)
    assert (await is_action_type_authorized("restart"))["authorized"] is True
    assert (await is_action_type_authorized("migration"))["authorized"] is False


async def test_expired_file_mutation_does_not_authorize_workspace_edit(test_db_pool):
    """WORKSPACE_MUTATION now routes here, so expiry must bind it too."""
    async with test_db_pool.acquire() as conn:
        await _approval(conn, action_type="file_mutation", status="approved", expires_in_hours=-1)
    assert (await is_action_type_authorized("file_mutation"))["authorized"] is False


async def test_expired_production_write_does_not_authorize_execute_code(test_db_pool):
    """execute_code is gated on this existing action type; expiry must bind."""
    async with test_db_pool.acquire() as conn:
        await _approval(conn, action_type="production_write", status="approved", expires_in_hours=-1)
    assert (await is_action_type_authorized("production_write"))["authorized"] is False


async def test_current_production_write_authorizes_execute_code(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await _approval(conn, action_type="production_write", status="approved", expires_in_hours=1)
    assert (await is_action_type_authorized("production_write"))["authorized"] is True


async def test_unknown_action_type_is_rejected(test_db_pool):
    with pytest.raises(TaskError):
        await is_action_type_authorized("not_a_real_action_type")


async def test_target_binding_commands_are_surfaced(test_db_pool):
    """The approval's own `command` column is returned so the caller can bind
    an approval to the concrete command instead of only the action type.
    No schema change was needed for this."""
    async with test_db_pool.acquire() as conn:
        await _approval(conn, action_type="restart", status="approved",
                        expires_in_hours=1, command="systemctl restart hermes-runner.service")
    result = await is_action_type_authorized("restart")
    assert result["commands"] == ["systemctl restart hermes-runner.service"]


async def test_action_approval_lookup_is_read_only(test_db_pool):
    """Structural guarantee: this authorization path must never mutate an
    approval row. A write here would mean the security check changes the
    state it is judging."""
    async with test_db_pool.acquire() as conn:
        aid = await _approval(conn, action_type="restart", status="approved", expires_in_hours=-1)
        before = await conn.fetchrow(
            "SELECT status, executed_at, expires_at FROM hermes_action_approvals WHERE id=$1", aid)

    # Deliberately outside the seeding connection: this exercises the same
    # call path the plugin uses, via the module's own pool.
    await is_action_type_authorized("restart")

    async with test_db_pool.acquire() as conn:
        after = await conn.fetchrow(
            "SELECT status, executed_at, expires_at FROM hermes_action_approvals WHERE id=$1", aid)

    assert dict(before) == dict(after), "authorization decision must not write to the row"


# ── 2. The route the plugin calls ─────────────────────────────────────────────
async def test_authorization_state_route_is_read_only_and_registered():
    """The plugin depends on this exact path. A rename would silently turn
    every gated category into a fail-closed block (safe, but silently
    breaking legitimate admin work), so pin the contract."""
    from modules.hermes_tasks.routes import router

    paths = {r.path for r in router.routes}
    assert "/api/actions/authorization-state" in paths

    # Must be registered before the int-typed /api/actions/{action_id} so the
    # literal path is never shadowed.
    order = [r.path for r in router.routes if r.path.startswith("/api/actions/")]
    assert order.index("/api/actions/authorization-state") < order.index("/api/actions/{action_id}")

    route = next(r for r in router.routes if r.path == "/api/actions/authorization-state")
    assert "GET" in route.methods and "POST" not in route.methods


# ── 3. action_policy.py copy-sync drift guard ────────────────────────────────
def test_action_policy_copies_are_byte_identical():
    """action_policy.py's own docstring requires the fazle-mcp and Hermes
    plugin copies to be byte-identical. Nothing enforced that, so a fix
    applied to one copy could leave the enforcement layer classifying
    differently from the proposal layer. Pin it."""
    assert _MCP_ACTION_POLICY.exists(), "fazle-mcp action_policy.py missing"
    assert _PLUGIN_ACTION_POLICY.exists(), "hermes plugin action_policy.py missing"
    assert _MCP_ACTION_POLICY.read_bytes() == _PLUGIN_ACTION_POLICY.read_bytes(), (
        "action_policy.py copies have drifted -- the module docstring requires "
        "them to be kept byte-identical"
    )


def test_redirection_fix_present_in_both_copies():
    """The redirection classification fix must exist in whichever copy is
    loaded; assert on the shared source so it cannot be dropped from one."""
    source = _PLUGIN_ACTION_POLICY.read_text(encoding="utf-8")
    assert "_has_state_writing_redirection" in source
    assert "WORKSPACE_MUTATION" in source


# ── 3b. Deployed-plugin integrity gate ────────────────────────────────────────
def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_deployed_classifier_is_byte_identical_to_the_canonical_repo_copy():
    """Security-candidate integrity: the Owner must be able to name exactly
    which bytes a restart activates.

    action_policy.py has a genuine canonical tracked source -- the fazle-mcp
    repository, commit 98985c4 "feat(hermes): canonical action-classification
    module ...". The copy the Hermes plugin actually loads is a deployed
    duplicate. This proves the two are byte-identical, so the tests in this
    repository and the tests in the plugin directory run against the same
    bytes that production will load.
    """
    canonical = _MCP_ACTION_POLICY
    deployed = _PLUGIN_ACTION_POLICY
    assert _sha256(canonical) == _sha256(deployed), (
        f"deployed plugin classifier has drifted from its canonical source:\n"
        f"  canonical {_MCP_ACTION_POLICY} sha256={_sha256(canonical)}\n"
        f"  deployed  {_PLUGIN_ACTION_POLICY} sha256={_sha256(deployed)}"
    )


def test_canonical_classifier_source_is_actually_tracked_in_a_repository():
    """A 'canonical source' that is not tracked is not a source of truth.
    Assert the canonical copy is committed in a git repository, so a restart
    approval can point at a commit rather than at a local file."""
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(_MCP_ACTION_POLICY.parent), "ls-files", "--error-unmatch", "action_policy.py"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "fazle-mcp/action_policy.py is not tracked by git -- it cannot serve as "
        f"the canonical source: {result.stderr.strip()}"
    )
    assert result.stdout.strip() == "action_policy.py"


CANONICAL_PLUGIN_REPO = "/home/azim/fazle-mcp"


def test_enforcement_plugin_has_one_tracked_canonical_source():
    """SECURITY_POLICY_INTEGRITY_BLOCKER: CLOSED 2026-09-29.

    This test was previously an ``xfail(strict=True)`` gap marker asserting
    that ``task_action_policy/__init__.py`` -- the module that makes every
    authorization decision -- was tracked by NO repository. It has now been
    brought under the fazle-mcp repository, which already owned the sibling
    classifier ``action_policy.py``, so the enforcement and classifier
    implementations are governed together in one repo rather than split
    between a tracked half and an untracked half.

    The strict xfail fired (XPASS) when the source was added, which is what it
    was designed to do: force a human to come back and replace the gap marker
    with a real assertion rather than let the gap become invisible.

    It is now a HARD assertion of the three properties that actually matter:
    exactly one tracked canonical source, the classifier still tracked in the
    same repo, and deployed bytes equal to canonical bytes.
    """
    import hashlib
    import subprocess
    from pathlib import Path

    plugin_file = "task_action_policy/__init__.py"

    owners = []
    for repo in ("/home/azim/core", "/home/azim/fazle-mcp", "/home/azim/hermes-runner"):
        result = subprocess.run(
            ["git", "-C", repo, "ls-files", "--error-unmatch", plugin_file],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            owners.append(repo)

    assert owners == [CANONICAL_PLUGIN_REPO], (
        f"the enforcement plugin must have exactly ONE tracked canonical "
        f"source, expected [{CANONICAL_PLUGIN_REPO}], found {owners} -- "
        f"two independently editable enforcement implementations is the "
        f"failure mode this test exists to prevent"
    )

    # classifier still tracked in the same repo as its enforcement sibling
    result = subprocess.run(
        ["git", "-C", CANONICAL_PLUGIN_REPO, "ls-files", "--error-unmatch",
         "action_policy.py"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "action_policy.py must stay tracked in fazle-mcp: "
        f"{result.stderr.strip()}"
    )

    # deployed bytes must equal canonical bytes (no drift)
    canonical = Path(CANONICAL_PLUGIN_REPO) / plugin_file
    deployed = _PLUGIN_DIR / "__init__.py"
    assert deployed.is_file(), f"deployed plugin file missing: {deployed}"
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    assert digest(canonical) == digest(deployed), (
        "deployed enforcement plugin has drifted from its canonical source: "
        f"canonical={digest(canonical)} deployed={digest(deployed)}"
    )


def test_plugin_is_loaded_from_the_user_plugin_directory():
    """Pins the deployment location so a moved plugin cannot silently stop
    being loaded while its files still look correct on disk."""
    assert _PLUGIN_DIR.is_dir(), f"deployed plugin directory missing: {_PLUGIN_DIR}"
    assert (_PLUGIN_DIR / "__init__.py").exists()
    config = Path("/home/azim/.hermes/config.yaml").read_text(encoding="utf-8")
    assert "task_action_policy" in config, (
        "config.yaml no longer enables the task_action_policy plugin"
    )
    assert "TASK_ACTION_POLICY_DISABLE" not in Path(
        "/home/azim/.hermes/.env"
    ).read_text(encoding="utf-8"), "plugin is disabled in ~/.hermes/.env"


# ── 4. The plugin's own enforcement wiring (source-level, no import needed) ───
def test_plugin_does_not_use_the_non_expiry_checking_listing_as_a_decision():
    """Regression guard for the exact bug: /api/actions?status=approved is a
    discovery helper that does not filter expiry. It may still be used for
    git-commit diff lookup, but never as the authorization answer."""
    source = _PLUGIN_INIT.read_text(encoding="utf-8")
    assert "/api/actions/authorization-state" in source
    # The only remaining /api/actions usage must be the git_commit diff
    # comparison, which is content-bound and not an expiry decision.
    for line in source.splitlines():
        if '"/api/actions"' in line:
            assert "status" in line and "approved" in line, (
                f"unexpected /api/actions use in plugin: {line.strip()}"
            )


def test_plugin_gates_workspace_mutation_and_execute_code():
    source = _PLUGIN_INIT.read_text(encoding="utf-8")
    assert "_check_workspace_mutation_command" in source
    assert "_CODE_EXECUTION_TOOL" in source
    # execute_code must reuse an existing action type, not invent one.
    assert '"production_write"' in source


def test_workspace_mutation_uses_existing_file_mutation_vocabulary():
    """The terminal path must reuse the classifier's existing category ->
    action_type mapping rather than hardcoding a new name."""
    assert _load_policy_module().action_type_for("WORKSPACE_MUTATION") == "file_mutation"


def test_requires_gated_approval_now_includes_workspace_mutation():
    requires_gated_approval = _load_policy_module().requires_gated_approval
    assert requires_gated_approval("WORKSPACE_MUTATION") is True
    assert requires_gated_approval("READ_ONLY") is False
    assert requires_gated_approval("SAFE_EXECUTION") is False
    assert requires_gated_approval("DESTRUCTIVE") is False


def test_classifier_categories_unchanged_in_shape():
    """The taxonomy itself must not grow a new category for this audit --
    CODE_EXECUTION reuses the existing generic mutation bucket."""
    assert set(_load_policy_module().CATEGORIES) == {
        "READ_ONLY", "SAFE_EXECUTION", "WORKSPACE_MUTATION", "REPOSITORY_MUTATION",
        "SERVICE_MUTATION", "DATABASE_MUTATION", "DEPLOYMENT", "DESTRUCTIVE",
    }


def test_shell_redirection_is_never_classified_read_only():
    """The boundary the audit found broken: the classifier looked only at
    command names, so `cat /etc/passwd > /tmp/leak` was READ_ONLY."""
    classify = _load_policy_module().classify_terminal_command
    for cmd in (
        "cat /etc/passwd > /tmp/leak",
        "cat /etc/shadow >> /tmp/leak",
        "echo x > /home/azim/core/app/main.py",
    ):
        assert classify(cmd) == "WORKSPACE_MUTATION", cmd
    # Reading is untouched, and routine non-writing redirect forms stay free.
    for cmd in ("cat /etc/passwd", "ls -la", "git status", "pytest 2>&1", "ls > /dev/null"):
        assert classify(cmd) != "WORKSPACE_MUTATION", cmd
    # A destructive command keeps its stricter label even with a redirect.
    assert classify("rm -rf /tmp/x > log") == "DESTRUCTIVE"


# ── 5. fazle-core's own MCP write path is neither weakened nor double-gated ──
def test_send_whatsapp_gate_still_requires_run_mode_and_confirm():
    """The relay force-grants the RUN *toolset*, but fazle-mcp's send gate
    reads the persisted mode FILE, and confirm is a separate required
    argument. This work must not have relaxed either."""
    src = Path("/home/azim/fazle-mcp/send_whatsapp_tools.py").read_text(encoding="utf-8")
    body = src.split("def send_whatsapp_message", 1)[1]
    assert '_read_mode()' in body
    assert 'mode != "RUN"' in body
    assert "if not confirm:" in body


def test_plugin_does_not_gate_mcp_tools():
    """fazle-core's MCP tools keep their own canonical authorization; the
    plugin must not double-gate them (which would be a second framework)."""
    source = _PLUGIN_INIT.read_text(encoding="utf-8")
    assert "mcp__fazle_core" not in source


def test_hermes_runner_forces_run_toolset_for_the_relay_key():
    """Pins the capability contract this audit was asked to preserve rather
    than change: the Owner explicitly kept RUN for the Admin relay."""
    src = Path("/home/azim/hermes-runner/server.py").read_text(encoding="utf-8")
    assert 'readonly_key == WHATSAPP_ADMIN_READONLY_KEY' in src
    assert 'MODE_TOOLSETS["RUN"]' in src
