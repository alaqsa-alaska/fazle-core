"""Fazle Core — log setup with file rotation (Batch 15.7)."""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_initialized = False
_DEFAULT_LOG_DIR = str((Path(__file__).resolve().parents[1] / "logs"))

# Owner directive (2026-08-27, Facebook token exposure containment):
# httpx's own INFO-level request logging (logger "httpx") writes the full
# request URL, including query parameters -- several Meta/Graph API call
# sites in this codebase used to pass access_token as a URL query param
# (params={"access_token": ...}), which meant the live Facebook Page
# Access Token was written to logs/fazle-core.log in cleartext on every
# Messenger/comment send and every backlog scan (confirmed live: 2500+
# occurrences across the current log + 5 rotations). Those call sites are
# now fixed to use Authorization: Bearer headers instead (see
# modules/social_auto_reply/send_queue.py, app/main.py, modules/
# social_auto_reply/backlog_processor.py) -- httpx does NOT log header
# contents at INFO level, only the request line, so that alone closes the
# leak. This filter is defense-in-depth on top of that fix: it redacts any
# access_token=<value> (or token=<value>) query-string-shaped substring
# from every record the "httpx" logger emits, so a future call site that
# accidentally reintroduces the query-param pattern still can't leak a
# real token into this file. Redacts in place; never raises (a logging
# filter must never crash the app it's instrumenting).
_TOKEN_QUERY_PARAM_RE = re.compile(
    r"((?:access_token|api_key|apikey)=)[^&\s\"'\\]+", re.IGNORECASE
)


class _RedactTokenQueryParamsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str) and "token" in record.msg.lower():
                record.msg = _TOKEN_QUERY_PARAM_RE.sub(r"\1***REDACTED***", record.msg)
            if record.args:
                record.args = tuple(
                    _TOKEN_QUERY_PARAM_RE.sub(r"\1***REDACTED***", a)
                    if isinstance(a, str) else a
                    for a in record.args
                )
        except Exception:
            pass
        return True


def setup_logging() -> None:
    global _initialized
    if _initialized:
        return
    log_dir = Path(os.getenv("FAZLE_LOG_DIR", _DEFAULT_LOG_DIR))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "fazle-core.log"

    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(str(log_path), maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)

    root = logging.getLogger()
    # Avoid double-add on reload
    for h in root.handlers:
        if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path):
            _initialized = True
            return
    root.addHandler(fh)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    logging.getLogger("httpx").addFilter(_RedactTokenQueryParamsFilter())
    _initialized = True
