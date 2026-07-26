"""
tests/unit/test_backup_pipeline.py

Comprehensive tests for:
  - B18: modules/backup/__init__.py
    (run_backup, rotate_backups, backup_status, latest_backup, job_daily_db_backup)
  - B16.6: modules/scheduler/__init__.py
    (job_backup_staleness — the stale-alert cron job)

Design:
  - Filesystem: pytest tmp_path (never touches /home/azim/backups/)
  - pg_dump: mocked subprocess (no real Docker required)
  - DB: test DB pool from root conftest (fazle_db_backups added to schema)
  - env vars: monkeypatch (all reads are at call-time via os.getenv())
  - outbound.enqueue: AsyncMock to suppress real WhatsApp sends

The key regression tests (TestJobBackupStaleness) verify that
job_backup_staleness() scans BACKUP_DIR/BACKUP_SUBDIR (not BACKUP_DIR root),
which was the original bug causing daily false-positive stale alerts.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── Mock helpers ─────────────────────────────────────────────────────────────

def _fake_pg_dump_ok(write_bytes: bytes = b"PGDMP" * 200):
    """Return an async callable that mimics a successful pg_dump.

    When called as asyncio.create_subprocess_exec, it writes fake dump bytes
    to the stdout file handle and returns a process mock with returncode=0.
    """
    async def _fake_exec(*args, stdout=None, **kwargs):
        if stdout is not None:
            stdout.write(write_bytes)
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b"", b"")
        return proc
    return _fake_exec


def _fake_pg_dump_fail(stderr_msg: bytes = b"pg_dump: error: disk full"):
    """Return an async callable that mimics a failed pg_dump (rc=1)."""
    async def _fake_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 1
        proc.communicate.return_value = (b"", stderr_msg)
        return proc
    return _fake_exec


def _fake_pg_dump_raises(exc: Exception = FileNotFoundError("docker: not found")):
    """Return an async callable that raises an exception (e.g. docker missing)."""
    async def _fake_exec(*args, **kwargs):
        raise exc
    return _fake_exec


def _set_mtime(path: Path, age_seconds: float) -> None:
    """Set file mtime to (now - age_seconds)."""
    t = time.time() - age_seconds
    os.utime(path, (t, t))


def _make_fake_dump(backup_dir: Path, dt: datetime, age_seconds: float = 0,
                    content: bytes = b"PGDMP_FAKE") -> Path:
    """Create a fake fazle_pg_YYYYMMDD_HHMMSS.dump file in backup_dir."""
    fname = f"fazle_pg_{dt.strftime('%Y%m%d_%H%M%S')}.dump"
    p = backup_dir / fname
    p.write_bytes(content)
    if age_seconds:
        _set_mtime(p, age_seconds)
    return p


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    """Redirect all backup I/O to tmp_path. Returns {"root", "subdir"} paths."""
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    monkeypatch.setenv("BACKUP_DIR", str(backup_root))
    monkeypatch.setenv("BACKUP_SUBDIR", "fazle")
    monkeypatch.setenv("BACKUP_PG_CONTAINER", "test-container")
    monkeypatch.setenv("BACKUP_STALE_HOURS", "48")
    monkeypatch.setenv("ADMIN_NUMBERS", "8801900000001")
    return {"root": backup_root, "subdir": backup_root / "fazle"}


@pytest.fixture
def db_ready(test_db_pool):
    """Patch app.database._pool so backup module uses the test DB."""
    import app.database as db_module
    db_module._pool = test_db_pool
    yield test_db_pool


# ── 1. TestBackupDirPath ──────────────────────────────────────────────────────

class TestBackupDirPath:
    """_backup_dir() must always incorporate BACKUP_SUBDIR."""

    async def test_path_includes_subdir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
        monkeypatch.setenv("BACKUP_SUBDIR", "myfazle")
        from modules.backup import _backup_dir
        p = _backup_dir()
        assert p == tmp_path / "myfazle"

    async def test_creates_directory_if_missing(self, monkeypatch, tmp_path):
        target = tmp_path / "deep" / "subdir"
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "deep"))
        monkeypatch.setenv("BACKUP_SUBDIR", "subdir")
        from modules.backup import _backup_dir
        p = _backup_dir()
        assert p.exists() and p.is_dir()

    async def test_default_subdir_is_fazle(self, monkeypatch, tmp_path):
        monkeypatch.delenv("BACKUP_SUBDIR", raising=False)
        monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
        from modules.backup import _backup_dir
        p = _backup_dir()
        assert p.name == "fazle"
        assert p.parent == tmp_path


# ── 2. TestRunBackupSuccess ───────────────────────────────────────────────────

class TestRunBackupSuccess:
    """run_backup() happy-path: file created, DB row inserted, correct metadata."""

    async def test_file_created_in_subdir(self, backup_env, db_ready):
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            result = await b.run_backup()

        assert result["status"] == "ok"
        subdir = backup_env["subdir"]
        dump_files = list(subdir.glob("fazle_pg_*.dump"))
        assert len(dump_files) == 1
        assert dump_files[0].name == result["filename"]

    async def test_no_file_in_root_dir(self, backup_env, db_ready):
        """Dump must land in subdir, NOT at BACKUP_DIR root."""
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            await b.run_backup()

        root_dumps = list(backup_env["root"].glob("*.dump"))
        assert root_dumps == []

    async def test_db_row_inserted_status_ok(self, backup_env, db_ready):
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            result = await b.run_backup()

        async with db_ready.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM fazle_db_backups WHERE filename=$1",
                result["filename"],
            )

        assert row is not None
        assert row["status"] == "ok"
        assert row["started_at"] is not None
        assert row["finished_at"] is not None

    async def test_sha256_matches_file_content(self, backup_env, db_ready):
        import hashlib
        fake_content = b"PGDMP_CONTENT" * 100

        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok(fake_content)):
            from modules import backup as b
            result = await b.run_backup()

        expected = hashlib.sha256(fake_content).hexdigest()
        assert result["sha256"] == expected

        async with db_ready.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT sha256 FROM fazle_db_backups WHERE filename=$1",
                result["filename"],
            )
        assert row["sha256"] == expected

    async def test_size_bytes_correct(self, backup_env, db_ready):
        fake_content = b"X" * 2048

        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok(fake_content)):
            from modules import backup as b
            result = await b.run_backup()

        assert result["size_bytes"] == 2048

    async def test_duration_ms_nonnegative(self, backup_env, db_ready):
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            result = await b.run_backup()

        assert result["duration_ms"] >= 0


# ── 3. TestRunBackupFailure ───────────────────────────────────────────────────

class TestRunBackupFailure:
    """run_backup() failure paths: pg_dump error and subprocess exception."""

    async def test_nonzero_rc_returns_failed(self, backup_env, db_ready):
        with patch("asyncio.create_subprocess_exec",
                   _fake_pg_dump_fail(b"pg_dump: error: disk full")):
            from modules import backup as b
            result = await b.run_backup()

        assert result["status"] == "failed"

    async def test_failed_file_is_cleaned_up(self, backup_env, db_ready):
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_fail()):
            from modules import backup as b
            result = await b.run_backup()

        assert not (backup_env["subdir"] / result["filename"]).exists()

    async def test_failure_recorded_in_db_with_error(self, backup_env, db_ready):
        err_msg = b"pg_dump: error: connection refused"
        with patch("asyncio.create_subprocess_exec",
                   _fake_pg_dump_fail(err_msg)):
            from modules import backup as b
            result = await b.run_backup()

        async with db_ready.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, error FROM fazle_db_backups WHERE filename=$1",
                result["filename"],
            )

        assert row is not None
        assert row["status"] == "failed"
        assert "connection refused" in (row["error"] or "")

    async def test_exec_exception_returns_failed(self, backup_env, db_ready):
        """FileNotFoundError (e.g. docker not found) → status=failed, file cleaned."""
        with patch("asyncio.create_subprocess_exec",
                   _fake_pg_dump_raises(FileNotFoundError("docker: not found"))):
            from modules import backup as b
            result = await b.run_backup()

        assert result["status"] == "failed"
        assert "docker" in result.get("error", "").lower()
        assert not (backup_env["subdir"] / result["filename"]).exists()


# ── 4. TestRotateBackups ──────────────────────────────────────────────────────

class TestRotateBackups:
    """rotate_backups() retention policy: keeps newest N daily + M weekly."""

    async def test_keeps_latest_n_daily(self, backup_env, db_ready):
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        base = datetime(2026, 4, 14)  # Tuesday
        for i in range(5):
            _make_fake_dump(subdir, base + timedelta(days=i))

        from modules import backup as b
        result = await b.rotate_backups(keep_daily=3, keep_weekly=0)

        remaining = list(subdir.glob("fazle_pg_*.dump"))
        assert len(remaining) == 3
        assert result["kept"] == 3
        assert result["deleted"] == 2

    async def test_deletes_oldest_first(self, backup_env, db_ready):
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        base = datetime(2026, 4, 14)
        dts = [base + timedelta(days=i) for i in range(4)]
        for dt in dts:
            _make_fake_dump(subdir, dt)

        from modules import backup as b
        await b.rotate_backups(keep_daily=2, keep_weekly=0)

        remaining = {p.name for p in subdir.glob("fazle_pg_*.dump")}
        newest_two = {
            f"fazle_pg_{dts[3].strftime('%Y%m%d_%H%M%S')}.dump",
            f"fazle_pg_{dts[2].strftime('%Y%m%d_%H%M%S')}.dump",
        }
        assert remaining == newest_two

    async def test_marks_rotated_in_db(self, backup_env, db_ready):
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        base = datetime(2026, 4, 14)
        old_p = _make_fake_dump(subdir, base)
        _make_fake_dump(subdir, base + timedelta(days=5))

        async with db_ready.acquire() as conn:
            await conn.execute(
                "INSERT INTO fazle_db_backups (filename, path, status) VALUES ($1,$2,'ok')",
                old_p.name, str(old_p),
            )

        from modules import backup as b
        await b.rotate_backups(keep_daily=1, keep_weekly=0)

        async with db_ready.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, rotated_at FROM fazle_db_backups WHERE filename=$1",
                old_p.name,
            )

        assert row["status"] == "rotated"
        assert row["rotated_at"] is not None

    async def test_keeps_weekly_sunday(self, backup_env, db_ready):
        """A Sunday dump is retained even outside the keep_daily window."""
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        sunday = datetime(2026, 4, 12)  # weekday() == 6
        assert sunday.weekday() == 6
        _make_fake_dump(subdir, sunday)
        for i in range(1, 4):
            _make_fake_dump(subdir, sunday + timedelta(days=i))  # Mon–Wed

        from modules import backup as b
        await b.rotate_backups(keep_daily=2, keep_weekly=1)

        remaining = {p.name for p in subdir.glob("fazle_pg_*.dump")}
        sunday_fname = f"fazle_pg_{sunday.strftime('%Y%m%d_%H%M%S')}.dump"
        assert sunday_fname in remaining

    async def test_empty_dir_no_error(self, backup_env, db_ready):
        """Empty backup subdir: rotate_backups returns ok, zero deleted."""
        backup_env["subdir"].mkdir(parents=True, exist_ok=True)

        from modules import backup as b
        result = await b.rotate_backups(keep_daily=14, keep_weekly=8)

        assert result["status"] == "ok"
        assert result["kept"] == 0
        assert result["deleted"] == 0


# ── 5. TestLatestBackup ───────────────────────────────────────────────────────

class TestLatestBackup:
    """latest_backup() returns most recent status='ok' row from fazle_db_backups."""

    async def test_returns_most_recent_ok(self, backup_env, db_ready):
        async with db_ready.acquire() as conn:
            await conn.execute("""
                INSERT INTO fazle_db_backups (filename, path, status, started_at)
                VALUES
                  ('fazle_pg_20260501_023000.dump', '/tmp/a.dump', 'ok',
                   '2026-05-01 02:30:00+00'),
                  ('fazle_pg_20260508_023000.dump', '/tmp/b.dump', 'ok',
                   '2026-05-08 02:30:00+00')
            """)

        from modules import backup as b
        latest = await b.latest_backup()

        assert latest is not None
        assert latest["filename"] == "fazle_pg_20260508_023000.dump"

    async def test_ignores_failed_rows(self, backup_env, db_ready):
        async with db_ready.acquire() as conn:
            await conn.execute("""
                INSERT INTO fazle_db_backups (filename, path, status, started_at)
                VALUES
                  ('fazle_pg_20260507_023000.dump', '/tmp/a.dump', 'ok',
                   '2026-05-07 02:30:00+00'),
                  ('fazle_pg_20260508_023000.dump', '/tmp/b.dump', 'failed',
                   '2026-05-08 02:30:00+00')
            """)

        from modules import backup as b
        latest = await b.latest_backup()

        assert latest["filename"] == "fazle_pg_20260507_023000.dump"

    async def test_returns_none_when_empty(self, backup_env, db_ready):
        from modules import backup as b
        latest = await b.latest_backup()
        assert latest is None


# ── 6. TestBackupStatus ───────────────────────────────────────────────────────

class TestBackupStatus:
    """backup_status() returns age_h, files_on_disk, dir using correct subdir."""

    async def test_no_backup_shows_none_age(self, backup_env, db_ready):
        from modules import backup as b
        status = await b.backup_status()

        assert status["latest"] is None
        assert status["newest_age_h"] is None
        assert status["files_on_disk"] == 0

    async def test_age_h_fresh_after_backup(self, backup_env, db_ready):
        """Immediately after a backup, age_h must be very small (< 0.1h)."""
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            await b.run_backup()
            status = await b.backup_status()

        assert status["newest_age_h"] is not None
        assert status["newest_age_h"] < 0.1

    async def test_files_on_disk_count(self, backup_env, db_ready):
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        base = datetime(2026, 5, 1)
        for i in range(3):
            _make_fake_dump(subdir, base + timedelta(days=i))

        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            await b.run_backup()
            status = await b.backup_status()

        assert status["files_on_disk"] == 4  # 3 fake + 1 fresh

    async def test_dir_uses_backup_subdir(self, backup_env, db_ready):
        from modules import backup as b
        status = await b.backup_status()

        assert status["dir"] == str(backup_env["subdir"])


# ── 7. TestJobBackupStaleness — THE CRITICAL TESTS ───────────────────────────

class TestJobBackupStaleness:
    """
    Regression tests for the stale-alert cron job.

    Root cause of original bug:
      job_backup_staleness() scanned BACKUP_DIR/*.dump (root dir),
      but run_backup() writes to BACKUP_DIR/BACKUP_SUBDIR/fazle_pg_*.dump.
      The root dir contained a 21-day-old legacy manual dump, causing daily
      false-positive stale alerts while the real backups were perfectly fresh.

    After fix:
      job_backup_staleness() scans BACKUP_DIR/BACKUP_SUBDIR/fazle_pg_*.dump
      (same directory as run_backup()). Root-level files are ignored.
    """

    async def test_fresh_backup_no_alert(self, backup_env):
        """Fresh dump (< 48h) in correct subdir → no alert fires."""
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        p = _make_fake_dump(subdir, datetime(2026, 5, 8, 2, 30))
        _set_mtime(p, 3600)  # 1 hour old

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result["status"] == "ok"
        assert result.get("newest_age_h", 0) < 48
        assert result.get("alerted", False) is False
        mock_enqueue.assert_not_called()

    async def test_stale_backup_fires_alert(self, backup_env):
        """Backup older than BACKUP_STALE_HOURS (48h) → alert fires."""
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        p = _make_fake_dump(subdir, datetime(2026, 5, 5, 2, 30))
        _set_mtime(p, 72 * 3600)  # 72 hours old → stale

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result["status"] == "ok"
        assert result["alerted"] is True
        mock_enqueue.assert_called_once()

    async def test_no_dumps_fires_no_backups_alert(self, backup_env):
        """Empty subdir → alert fires with 'no backups' message."""
        backup_env["subdir"].mkdir(parents=True, exist_ok=True)
        # No dump files created

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result["status"] == "ok"
        assert result["alerted"] is True
        mock_enqueue.assert_called_once()
        msg = mock_enqueue.call_args_list[0][0][1]
        assert "No DB backups" in msg or "backup" in msg.lower()

    async def test_dir_missing_returns_skipped(self, monkeypatch):
        """Non-existent backup dir → returns skipped, no exception."""
        monkeypatch.setenv("BACKUP_DIR", "/tmp/does_not_exist_xyz_123456")
        monkeypatch.setenv("BACKUP_SUBDIR", "fazle")
        monkeypatch.setenv("ADMIN_NUMBERS", "8801900000001")
        monkeypatch.setenv("BACKUP_STALE_HOURS", "48")

        from modules.scheduler import job_backup_staleness
        result = await job_backup_staleness()

        assert result["status"] == "ok"
        assert result.get("skipped") == "no backup dir"

    async def test_correct_subdir_fresh_wins_over_stale_root(self, backup_env):
        """
        THE KEY REGRESSION TEST.

        Setup:
          - Root dir has a 30-day-old legacy .dump (like the real Apr-18 file)
          - Correct subdir has a fresh fazle_pg_*.dump (1h old)

        After the fix: stale check looks in subdir → finds fresh file → no alert.
        Before the fix: stale check looked in root → found 30-day-old file → false alert.
        """
        root = backup_env["root"]
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)

        # Stale legacy file at root (mirrors /home/azim/backups/fazle_db_backup_20260419.dump)
        legacy = root / "fazle_db_backup_20260419.dump"
        legacy.write_bytes(b"OLD_LEGACY_BACKUP")
        _set_mtime(legacy, 30 * 24 * 3600)  # 30 days old

        # Fresh managed backup in correct subdir
        fresh = _make_fake_dump(subdir, datetime(2026, 5, 8, 2, 30))
        _set_mtime(fresh, 3600)  # 1 hour old

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result["status"] == "ok"
        assert result.get("alerted", False) is False, (
            "Stale alert fired! job_backup_staleness() must scan "
            "BACKUP_DIR/BACKUP_SUBDIR (fazle_pg_*.dump), not the root "
            "BACKUP_DIR. The 30-day-old root file should be invisible to it."
        )
        mock_enqueue.assert_not_called()

    async def test_root_only_dump_not_counted(self, backup_env):
        """
        A .dump file at BACKUP_DIR root is NOT a valid managed backup.
        Subdir is empty → stale alert must fire (correct after fix).
        """
        root = backup_env["root"]
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)

        # "Fresh" file placed in wrong location (root, not subdir)
        stray = root / "stray_backup.dump"
        stray.write_bytes(b"WRONG_LOCATION")
        _set_mtime(stray, 1800)  # 30 min old — would be "fresh" if scanned

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result["alerted"] is True, (
            "Root-level dump was counted as a valid backup! "
            "job_backup_staleness() must look in BACKUP_DIR/BACKUP_SUBDIR only."
        )

    async def test_only_fazle_pg_pattern_counted(self, backup_env):
        """
        Non-matching filenames in subdir are ignored.
        Only fazle_pg_YYYYMMDD_HHMMSS.dump counts as a managed backup.
        """
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)

        # Files that do NOT match fazle_pg_*.dump
        for name in ["manual.dump", "test.dump", "old_db.dump", "fazle_backup.dump"]:
            (subdir / name).write_bytes(b"WRONG_PATTERN")

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        # No matching managed backups → alert fires
        assert result["alerted"] is True

    async def test_newest_file_wins_when_multiple(self, backup_env):
        """When multiple dumps exist, the newest mtime determines the age reported."""
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)

        old = _make_fake_dump(subdir, datetime(2026, 5, 1, 2, 30))
        fresh = _make_fake_dump(subdir, datetime(2026, 5, 8, 2, 30))
        _set_mtime(old, 7 * 24 * 3600)    # 7 days old
        _set_mtime(fresh, 2 * 3600)        # 2 hours old

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        # Newest is 2h old < 48h threshold → no alert
        assert result.get("alerted", False) is False
        assert result.get("newest_age_h", 999) < 48


# ── 8. TestStaleAlertThreshold ────────────────────────────────────────────────

class TestStaleAlertThreshold:
    """BACKUP_STALE_HOURS env var controls the threshold precisely."""

    async def test_under_threshold_no_alert(self, backup_env, monkeypatch):
        monkeypatch.setenv("BACKUP_STALE_HOURS", "24")
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        p = _make_fake_dump(subdir, datetime(2026, 5, 8, 2, 30))
        _set_mtime(p, 23 * 3600)  # 23h < 24h threshold

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result.get("alerted", False) is False
        mock_enqueue.assert_not_called()

    async def test_over_threshold_alerts(self, backup_env, monkeypatch):
        monkeypatch.setenv("BACKUP_STALE_HOURS", "24")
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        p = _make_fake_dump(subdir, datetime(2026, 5, 7, 2, 30))
        _set_mtime(p, 25 * 3600)  # 25h > 24h threshold → stale

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result["alerted"] is True

    async def test_custom_stale_hours_env(self, backup_env, monkeypatch):
        """BACKUP_STALE_HOURS=6 makes a 7h-old backup trigger the alert."""
        monkeypatch.setenv("BACKUP_STALE_HOURS", "6")
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        p = _make_fake_dump(subdir, datetime(2026, 5, 8, 2, 30))
        _set_mtime(p, 7 * 3600)  # 7h > 6h threshold

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        assert result["alerted"] is True

    async def test_no_admin_number_suppresses_enqueue(self, backup_env, monkeypatch):
        """With empty ADMIN_NUMBERS, result still shows alerted=True but enqueue is skipped."""
        monkeypatch.setenv("BACKUP_STALE_HOURS", "1")
        monkeypatch.setenv("ADMIN_NUMBERS", "")  # no admin configured
        subdir = backup_env["subdir"]
        subdir.mkdir(parents=True, exist_ok=True)
        p = _make_fake_dump(subdir, datetime(2026, 5, 1))
        _set_mtime(p, 72 * 3600)  # well over threshold

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.enqueue", mock_enqueue):
            from modules.scheduler import job_backup_staleness
            result = await job_backup_staleness()

        # Stale is detected but no admin to notify
        mock_enqueue.assert_not_called()


# ── 9. TestJobDailyDbBackup ───────────────────────────────────────────────────

class TestJobDailyDbBackup:
    """job_daily_db_backup() runs backup + rotation and reports both results."""

    async def test_returns_backup_and_rotate_keys(self, backup_env, db_ready):
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            result = await b.job_daily_db_backup()

        assert "backup" in result
        assert "rotate" in result
        assert result["backup"]["status"] == "ok"
        assert result["rotate"]["status"] == "ok"

    async def test_rotate_runs_after_successful_backup(self, backup_env, db_ready):
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_ok()):
            from modules import backup as b
            result = await b.job_daily_db_backup()

        assert result["rotate"]["kept"] >= 1  # at least the backup we just made

    async def test_failure_sends_admin_alert(self, backup_env, db_ready):
        """On pg_dump failure, job_daily_db_backup must alert admin."""
        mock_enqueue = AsyncMock()
        with patch("asyncio.create_subprocess_exec", _fake_pg_dump_fail()), \
             patch("modules.outbound.enqueue", mock_enqueue):
            from modules import backup as b
            result = await b.job_daily_db_backup()

        assert result["backup"]["status"] == "failed"
        mock_enqueue.assert_called_once()

    async def test_failure_does_not_raise(self, backup_env, db_ready):
        """job_daily_db_backup must never propagate an exception."""
        mock_enqueue = AsyncMock()
        with patch("asyncio.create_subprocess_exec",
                   _fake_pg_dump_raises(RuntimeError("unexpected"))), \
             patch("modules.outbound.enqueue", mock_enqueue):
            from modules import backup as b
            # Should not raise
            result = await b.job_daily_db_backup()

        assert result["backup"]["status"] == "failed"
