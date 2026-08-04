"""
One-time setup: create a dedicated fazle-core admin identity for Hermes'
fazle-mcp integration (metrics/scheduler tools), separate from the master
INTERNAL_API_KEY. Uses the existing, already-audited rbac.add_admin() /
rbac.issue_api_key() functions — no raw SQL/DDL, no direct table writes.

Run once, on fazle-core's own host:
    cd /home/azim/core && python3 scripts/create_hermes_mcp_admin.py

Prints the plaintext API key ONCE (it's stored only as a hash, per
rbac.issue_api_key's own design) — copy it straight into
~/.hermes/config.yaml's FAZLE_CORE_API_KEY, then don't keep this output
lying around in shell history/scrollback/a log file.

Idempotent by default: re-running is safe and a no-op if the admin
identity already exists — it will NOT create a duplicate admin, will NOT
touch the admin's name/role, and will NOT reissue (and thereby silently
invalidate) an already-issued API key. Pass --rotate to explicitly
generate and store a brand-new key for the existing identity (invalidates
the old one immediately — only do this if you actually intend to rotate
it, e.g. suspected exposure).
"""

import asyncio
import os
import sys
from pathlib import Path

# Add core to path so we can import app/modules — same pattern as
# scripts/setup_hr_officers.py (this file lives in scripts/, one level
# below repo root, so its own directory alone isn't enough).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Load .env, then overlay secure-env-backup/runtime-services.env (holds
# DATABASE_URL — split out of the main .env, only merged automatically by
# systemd's two EnvironmentFile= directives when the real service runs;
# a standalone script needs to do it itself). Same pattern as
# scripts/rebuild_qdrant.py.
_ENV_LAYERS = [
    (Path(ROOT) / ".env", os.environ.setdefault),
    (Path("/home/azim/secure-env-backup/runtime-services.env"), os.environ.__setitem__),
]
for env_path, setter in _ENV_LAYERS:
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            setter(k.strip(), v.strip())

from app.database import init_db, close_db
from modules import rbac

PHONE = "01958122304"  # Owner-designated, API-key/authorization use only
NAME = "hermes-mcp-service"
ROLE = "viewer"


async def main():
    rotate = "--rotate" in sys.argv
    await init_db()
    try:
        result = await rbac.add_admin(
            phone=PHONE, name=NAME, role=ROLE,
            granted_by="hermes-foundational-capabilities-2026-08-04",
        )
        print(f"add_admin: {result}")

        # Only issue an API key on first creation, or when --rotate is
        # explicitly passed. rbac.issue_api_key() unconditionally
        # overwrites the stored key hash (fazle_admins.api_key_hash is a
        # single column, not a list) — calling it on every run would
        # silently invalidate whatever key is already deployed in
        # ~/.hermes/config.yaml, with nothing on screen to warn about it.
        if result["status"] == "created":
            key_result = await rbac.issue_api_key(PHONE)
            print(
                "\nAPI key (copy this into ~/.hermes/config.yaml's "
                f"FAZLE_CORE_API_KEY, shown once):\n{key_result['api_key']}\n"
            )
        elif rotate:
            key_result = await rbac.issue_api_key(PHONE)
            print(
                "\n--rotate passed: issued a NEW API key — the previous one "
                "is now invalid. New API key (copy this into "
                f"~/.hermes/config.yaml's FAZLE_CORE_API_KEY, shown once):\n"
                f"{key_result['api_key']}\n"
            )
        else:
            print(
                "\nAdmin identity already exists — NOT reissuing an API key "
                "(that would silently invalidate whatever key is already in "
                "~/.hermes/config.yaml's FAZLE_CORE_API_KEY). Re-run with "
                "--rotate if you actually intend to rotate it.\n"
            )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
