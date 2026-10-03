"""Hermes task_action_policy plugin integrity gate (Part 9 / Part 10).

Fails closed. Establishes that the security-critical Hermes enforcement
plugin has exactly ONE tracked canonical source in the fazle-mcp repository
and that the deployed copy Hermes actually loads has not drifted from it.

Source-of-truth relationship:
    fazle-mcp/task_action_policy/__init__.py  (canonical ENFORCEMENT)
    fazle-mcp/action_policy.py                (canonical CLASSIFIER)
       -> deterministic copy -> ~/.hermes/plugins/task_action_policy/
       -> Hermes loads the deployed copy

This gate is INDEPENDENT of the Fazle-Core restart gate. It is one term of
the composite security gate; it does not widen the meaning of the existing
Fazle-Core gate.
"""

import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

FAZLE_MCP = Path(os.environ.get("FAZLE_MCP_REPO", "/home/azim/fazle-mcp"))
DEPLOYED = Path(
    os.environ.get("HERMES_PLUGIN_DIR", "~/.hermes/plugins/task_action_policy")
).expanduser()

# canonical source -> deployed filename
PAIRS = [
    ("task_action_policy/__init__.py", "__init__.py"),
    ("action_policy.py", "action_policy.py"),
]

CHECK_SCRIPT = FAZLE_MCP / "scripts" / "install_task_action_policy_plugin.sh"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestPluginCanonicalSourceIsTracked(unittest.TestCase):
    def test_canonical_sources_exist(self):
        for src, _ in PAIRS:
            self.assertTrue((FAZLE_MCP / src).is_file(), f"missing canonical {src}")

    def test_canonical_sources_are_tracked_by_git(self):
        out = subprocess.run(
            ["git", "-C", str(FAZLE_MCP), "ls-files", "--error-unmatch"] + [s for s, _ in PAIRS],
            capture_output=True, text=True,
        )
        self.assertEqual(
            out.returncode, 0,
            f"canonical plugin source is not tracked by git: {out.stderr.strip()}",
        )


class TestDeployedPluginMatchesCanonical(unittest.TestCase):
    def test_deployed_target_is_the_hermes_load_path(self):
        self.assertTrue(DEPLOYED.is_dir(), f"deployed plugin dir missing: {DEPLOYED}")
        self.assertEqual(
            DEPLOYED, Path("~/.hermes/plugins/task_action_policy").expanduser(),
            "deployed target is not the expected Hermes plugin load path",
        )

    def test_deployed_bytes_are_identical_to_canonical(self):
        for src, dst_name in PAIRS:
            dst = DEPLOYED / dst_name
            self.assertTrue(dst.is_file(), f"deployed file missing: {dst}")
            self.assertEqual(
                _sha256(FAZLE_MCP / src), _sha256(dst),
                f"DRIFT: {dst} does not match canonical {src}",
            )

    def test_check_script_passes(self):
        self.assertTrue(CHECK_SCRIPT.is_file(), f"missing {CHECK_SCRIPT}")
        proc = subprocess.run(
            ["bash", str(CHECK_SCRIPT), "--check"], capture_output=True, text=True,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"plugin integrity check failed:\n{proc.stdout}\n{proc.stderr}",
        )
        self.assertIn("TASK_ACTION_POLICY_PLUGIN_INTEGRITY: PASS", proc.stdout)


class TestQualifiedEnforcementBehaviorPreserved(unittest.TestCase):
    """The tracked source must preserve the previously qualified gates."""

    @classmethod
    def setUpClass(cls):
        cls.src = (FAZLE_MCP / "task_action_policy" / "__init__.py").read_text()

    def test_expiry_aware_approvals(self):
        self.assertIn("expires_at", self.src)

    def test_workspace_mutation_gate(self):
        self.assertIn("_check_workspace_mutation_command", self.src)

    def test_redirection_classification_integration(self):
        self.assertIn("_normalize_command", self.src)

    def test_service_database_deployment_gating(self):
        for cat in ("SERVICE_MUTATION", "DATABASE_MUTATION", "DEPLOYMENT"):
            self.assertIn(cat, self.src)

    def test_execute_code_mutation_gating(self):
        self.assertIn("_check_code_execution", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
