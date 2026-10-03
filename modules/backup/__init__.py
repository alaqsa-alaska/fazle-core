"""Batch 18 — Database backup & disaster-recovery module.

Responsibilities
----------------
* Run `pg_dump` of the configured Postgres DB via the `ai-postgres` container.
* Persist metadata (`fazle_db_backups`) for every backup attempt.
* Rotate old backups (keep N latest daily + M latest weekly).
* Provide a scheduler entry point and helpers for admin commands / API.

The actual `pg_dump` is executed inside the Postgres container so the binary
version always matches the server. The dump file is written to a host-mounted
path under ``$BACKUP_DIR/fazle/``.

Env vars
--------
BACKUP_DIR              Host directory for backups (default ``/home/azim/backups``)
BACKUP_SUBDIR           Sub-folder for B18 dumps (default ``fazle``)
BACKUP_PG_CONTAINER     Postgres container name (default ``ai-postgres``)
BACKUP_KEEP_DAILY       Number of latest daily dumps to retain (default 14)
BACKUP_KEEP_WEEKLY      Number of weekly (Sunday) dumps to retain (default 8)
DAILY_BACKUP_HOUR       Cron hour for scheduled backup (default 2)
DAILY_BACKUP_MIN        Cron minute for scheduled backup (default 30)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.database import execute, fetch_all, fetch_one

log = logging.getLogger("fazle.backup")

_DUMP_RE = re.compile(r"^fazle_pg_(\d{8})_(\d{6})\.dump$")


# ── helpers ───────────────────────────────────────────────────────────────────
def _backup_dir() -> Path:
    base = Path(os.getenv("BACKUP_DIR", "/home/azim/backups"))
    sub = os.getenv("BACKUP_SUBDIR", "fazle")
    p = base / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def _container() -> str:
    return os.getenv("BACKUP_PG_CONTAINER", "ai-postgres")


def _db_name() -> str:
    url = os.getenv("DATABASE_URL", "")
    # postgresql://user:pass@host:port/dbname
    if "/" in url:
        return url.rsplit("/", 1)[-1].split("?")[0] or "postgres"
    return "postgres"


def _db_user() -> str:
    url = os.getenv("DATABASE_URL", "")
    m = re.match(r"postgres(?:ql)?://([^:/@]+)", url)
    return m.group(1) if m else "postgres"


def _sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _authority_source() -> Path:
    return Path(__file__).resolve().parents[1] / ".." / "knowledge_base" / "00_governance" / "recruitment_fact_authority.json"


def _validate_authority_bytes(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("authority backup is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), list):
        raise ValueError("authority backup has invalid schema")
    valid = {"current_approved", "historical", "unverified", "conflicting", "superseded"}
    keys: set[str] = set()
    for fact in payload["facts"]:
        if not isinstance(fact, dict) or not fact.get("key") or fact["key"] in keys:
            raise ValueError("authority backup has duplicate or invalid fact keys")
        keys.add(fact["key"])
        if fact.get("status") not in valid:
            raise ValueError("authority backup has invalid fact status")
    return payload


def _authority_backup_dir() -> Path:
    path = _backup_dir() / "authority"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def backup_authority_register(source: Path | None = None, backup_dir: Path | None = None) -> dict[str, Any]:
    """Atomically back up the validated authority register.

    The backup is recovery material only; it is stored outside the repository's
    knowledge tree and is never read by RAG or authority resolution.
    """
    source = Path(source or _authority_source())
    if source.is_symlink() or not source.is_file():
        raise FileNotFoundError(f"authority source is not a regular file: {source}")
    raw = source.read_bytes()
    _validate_authority_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    out_dir = Path(backup_dir) if backup_dir else _authority_backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_dir, 0o700)
    target = out_dir / f"recruitment_fact_authority_{digest[:16]}.json"
    fd, temp_name = tempfile.mkstemp(prefix=".authority.", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
        os.chmod(target, 0o600)
        dir_fd = os.open(out_dir, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    if _sha256_of(target) != digest:
        raise IOError("authority backup hash mismatch")
    return {"status": "ok", "source": str(source), "backup": str(target), "sha256": digest}


def validate_authority_backup(backup: Path, source: Path | None = None) -> dict[str, Any]:
    """Validate a backup and, when supplied, prove it matches the source."""
    backup = Path(backup)
    if backup.is_symlink() or not backup.is_file():
        raise FileNotFoundError(f"authority backup is not a regular file: {backup}")
    raw = backup.read_bytes()
    _validate_authority_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if source is not None:
        source = Path(source)
        if _sha256_of(source) != digest:
            raise ValueError("authority backup does not match source")
    return {"status": "ok", "backup": str(backup), "sha256": digest}


def restore_authority_register(backup: Path, target: Path | None = None) -> dict[str, Any]:
    """Atomically restore a validated backup to a test or explicit target.

    Callers must explicitly choose the target; no restart path invokes restore.
    """
    backup = Path(backup)
    validate_authority_backup(backup)
    target = Path(target or _authority_source())
    if target.is_symlink():
        raise ValueError("authority target must not be a symlink")
    raw = backup.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".authority-restore.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
        os.chmod(target, 0o664)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    validate_authority_backup(target)
    return {"status": "ok", "backup": str(backup), "target": str(target), "sha256": _sha256_of(target)}


# ── core ──────────────────────────────────────────────────────────────────────
async def run_backup() -> dict[str, Any]:
    """Execute a pg_dump and record metadata. Returns a result dict."""
    out_dir = _backup_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"fazle_pg_{ts}.dump"
    fpath = out_dir / fname
    started = datetime.now(timezone.utc)
    t0 = time.time()

    cmd = [
        "docker", "exec", _container(),
        "pg_dump", "-U", _db_user(), "-Fc", "--no-owner", "--no-privileges",
        _db_name(),
    ]
    log.info(f"[backup] starting → {fpath}")

    try:
        with fpath.open("wb") as out_f:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=out_f, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        rc = proc.returncode or 0
    except Exception as e:
        dur = int((time.time() - t0) * 1000)
        await execute(
            "INSERT INTO fazle_db_backups (filename, path, status, duration_ms, "
            "error, started_at, finished_at) VALUES ($1,$2,'failed',$3,$4,$5,$6)",
            fname, str(fpath), dur, f"exec: {e}", started, datetime.now(timezone.utc),
        )
        log.error(f"[backup] exec failed: {e}")
        if fpath.exists():
            try: fpath.unlink()
            except OSError: pass
        return {"status": "failed", "error": str(e), "filename": fname}

    dur = int((time.time() - t0) * 1000)
    if rc != 0:
        err = (stderr or b"").decode("utf-8", "replace")[:500]
        await execute(
            "INSERT INTO fazle_db_backups (filename, path, status, duration_ms, "
            "error, started_at, finished_at) VALUES ($1,$2,'failed',$3,$4,$5,$6)",
            fname, str(fpath), dur, err, started, datetime.now(timezone.utc),
        )
        log.error(f"[backup] pg_dump rc={rc}: {err}")
        if fpath.exists():
            try: fpath.unlink()
            except OSError: pass
        return {"status": "failed", "error": err, "rc": rc, "filename": fname}

    size = fpath.stat().st_size
    sha = _sha256_of(fpath)
    await execute(
        "INSERT INTO fazle_db_backups (filename, path, size_bytes, sha256, "
        "status, duration_ms, started_at, finished_at) "
        "VALUES ($1,$2,$3,$4,'ok',$5,$6,$7)",
        fname, str(fpath), size, sha, dur, started, datetime.now(timezone.utc),
    )
    log.info(f"[backup] ok {fname} size={size} dur={dur}ms")
    return {
        "status": "ok",
        "filename": fname,
        "path": str(fpath),
        "size_bytes": size,
        "sha256": sha,
        "duration_ms": dur,
    }


async def rotate_backups(
    keep_daily: Optional[int] = None,
    keep_weekly: Optional[int] = None,
) -> dict[str, Any]:
    """Delete old dumps. Keep last N daily + last M weekly (Sunday) files."""
    keep_daily = keep_daily or int(os.getenv("BACKUP_KEEP_DAILY", "14"))
    keep_weekly = keep_weekly or int(os.getenv("BACKUP_KEEP_WEEKLY", "8"))
    out_dir = _backup_dir()
    files: list[tuple[datetime, Path]] = []
    for p in out_dir.glob("fazle_pg_*.dump"):
        m = _DUMP_RE.match(p.name)
        if not m:
            continue
        try:
            dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        files.append((dt, p))
    files.sort(key=lambda x: x[0], reverse=True)  # newest first

    keep: set[Path] = set()
    # last N daily (any weekday)
    for _, p in files[:keep_daily]:
        keep.add(p)
    # last M Sundays
    sundays = [(d, p) for d, p in files if d.weekday() == 6]
    for _, p in sundays[:keep_weekly]:
        keep.add(p)

    deleted: list[str] = []
    for _, p in files:
        if p in keep:
            continue
        try:
            p.unlink()
            deleted.append(p.name)
            await execute(
                "UPDATE fazle_db_backups SET status='rotated', rotated_at=now() "
                "WHERE filename=$1 AND status='ok'",
                p.name,
            )
        except OSError as e:
            log.warning(f"[backup] rotate could not delete {p}: {e}")
    return {
        "status": "ok",
        "kept": len(keep),
        "deleted": len(deleted),
        "deleted_files": deleted,
        "keep_daily": keep_daily,
        "keep_weekly": keep_weekly,
    }


async def latest_backup() -> Optional[dict[str, Any]]:
    row = await fetch_one(
        "SELECT id, filename, path, size_bytes, sha256, status, duration_ms, "
        "started_at, finished_at FROM fazle_db_backups "
        "WHERE status='ok' ORDER BY started_at DESC LIMIT 1"
    )
    return dict(row) if row else None


async def list_backups(limit: int = 20) -> list[dict[str, Any]]:
    rows = await fetch_all(
        "SELECT id, filename, size_bytes, status, duration_ms, started_at, "
        "finished_at, rotated_at, error FROM fazle_db_backups "
        "ORDER BY started_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def backup_status() -> dict[str, Any]:
    out_dir = _backup_dir()
    latest = await latest_backup()
    on_disk = sorted(out_dir.glob("fazle_pg_*.dump"))
    total_bytes = sum(p.stat().st_size for p in on_disk if p.exists())
    age_h: Optional[float] = None
    if latest and latest.get("started_at"):
        st = latest["started_at"]
        if st.tzinfo is None:
            st = st.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - st).total_seconds() / 3600
    return {
        "latest": latest,
        "files_on_disk": len(on_disk),
        "total_bytes": total_bytes,
        "newest_age_h": round(age_h, 2) if age_h is not None else None,
        "dir": str(out_dir),
    }


# ── scheduler entry ───────────────────────────────────────────────────────────
async def job_daily_db_backup() -> dict[str, Any]:
    """Scheduler job: run a backup, rotate old dumps, alert admins on failure."""
    res = await run_backup()
    rot = await rotate_backups()
    if res.get("status") != "ok":
        try:
            from modules import outbound  # late import
            from modules.scheduler import _admin_number  # type: ignore
            adm = _admin_number()
            if adm:
                day = datetime.utcnow().strftime("%Y%m%d")
                msg = f"⚠️ DB backup FAILED ({res.get('error', '')[:140]})"
                await outbound.enqueue(
                    adm, msg, source_bridge="bridge1", fallback_channel="bridge2",
                    purpose="backup-failed",
                    idempotency_key=f"backup-failed-{day}",
                )
        except Exception as e:
            log.warning(f"[backup] failed-alert error: {e}")
    return {"backup": res, "rotate": rot}


def render_status_text(s: dict[str, Any]) -> str:
    latest = s.get("latest")
    if not latest:
        return (
            "📦 ব্যাকআপ স্ট্যাটাস\n"
            "────────────────\n"
            "❌ এখনো কোনো ব্যাকআপ নেই\n"
            f"ডিরেক্টরি: {s.get('dir')}"
        )
    sz = latest.get("size_bytes") or 0
    sz_mb = sz / (1024 * 1024)
    age = s.get("newest_age_h")
    age_s = f"{age:.1f}h" if age is not None else "?"
    return (
        "📦 ব্যাকআপ স্ট্যাটাস\n"
        "────────────────\n"
        f"• সর্বশেষ: {latest['filename']}\n"
        f"• আকার: {sz_mb:.2f} MB\n"
        f"• বয়স: {age_s}\n"
        f"• স্ট্যাটাস: {latest['status']}\n"
        f"• মোট ফাইল: {s.get('files_on_disk')}\n"
        f"• মোট আকার: {s.get('total_bytes', 0) / (1024 * 1024):.2f} MB"
    )
