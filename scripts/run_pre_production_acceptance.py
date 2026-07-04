#!/usr/bin/env python3
"""Script wrapper for the Phase 8.5 pre-production acceptance runner."""

from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


_load_env_file(ROOT / ".env")
_load_env_file(ROOT.parent / "secure-env-backup" / "runtime-services.env")

from modules.ai_console.acceptance_runner import main


if __name__ == "__main__":
    raise SystemExit(main())
