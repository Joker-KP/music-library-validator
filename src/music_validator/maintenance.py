"""Archive managed cache/report state without replacing mounts or lock inodes."""

import os
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from .config import load_config
from .safety import ISSUE_REPORT_FILES, REPORT_FILES, check_outputs, exclusive_lock, plain_path


def _plan(config, db, reports):
    root = Path(load_config(config)["library_root"]).resolve()
    db, reports = plain_path(db).resolve(), plain_path(reports).resolve()
    families = [(reports, REPORT_FILES)] + [
        (reports / f"{mode}-report", ISSUE_REPORT_FILES) for mode in ("integrity", "identity")
    ]
    for directory, files in families:
        check_outputs(root, db, directory, files)
    cache = [Path(str(db) + suffix) for suffix in ("", "-journal", "-wal", "-shm")]
    cache = [path for path in cache if path.exists()]
    generated = [
        directory / name
        for directory, files in families
        for name in (*files, "current", "generations")
        if os.path.lexists(directory / name)
    ]
    for source in (*cache, *generated):
        target = source.resolve()
        if config.resolve() == target or (
            source.is_dir() and config.resolve().is_relative_to(target)
        ):
            raise ValueError(
                "Fresh start must not archive the configuration; move it outside managed outputs"
            )
    return db, reports, families, cache, generated


def _sync(directories):
    for directory in sorted(directories):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def archive_state(config, db, reports):
    """Move only managed outputs; reverse completed moves on ordinary I/O failures.

    Each move is within its own filesystem. There is no cross-volume atomic
    commit; archives retain the files if a machine stops between renames.
    """
    db, reports, families, _, _ = _plan(config, db, reports)
    with ExitStack() as locks:
        locks.enter_context(exclusive_lock(Path(str(db) + ".lock")))
        for directory, _ in families:
            locks.enter_context(exclusive_lock(directory / ".scan.lock"))
        # Revalidate after locking: a scan may have published during preflight.
        _, _, _, cache, generated = _plan(config, db, reports)
        now = datetime.now(UTC)
        identifier = now.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
        data_archive = db.parent / f"previous-data-{identifier}" if cache else None
        report_archive = reports / f"previous-reports-{identifier}" if generated else None
        created, moved = [], []

        def make(directory):
            directory.mkdir(mode=0o700)
            created.append(directory)

        try:
            if data_archive:
                make(data_archive)
            if report_archive:
                make(report_archive)
                for mode in ("integrity", "identity"):
                    if any(p.parent == reports / f"{mode}-report" for p in generated):
                        make(report_archive / f"{mode}-report")
            transfers = [(p, data_archive / p.name) for p in cache]
            transfers.extend((p, report_archive / p.relative_to(reports)) for p in generated)
            for source, destination in transfers:
                source.rename(destination)
                moved.append((source, destination))
            _sync({p.parent for pair in moved for p in pair} | set(created))
        except OSError as exc:
            failures = []
            for source, destination in reversed(moved):
                try:
                    if os.path.lexists(source):
                        raise OSError(f"Cannot restore occupied path: {source}")
                    destination.rename(source)
                except OSError as restore_error:
                    failures.append(str(restore_error))
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass  # Never remove an archive containing files we could not restore.
            if failures:
                raise OSError(
                    f"Fresh start failed ({exc}); recovery is incomplete. Files are preserved in "
                    f"{data_archive} and {report_archive}. Inspect these before scanning again: "
                    + "; ".join(failures)
                ) from exc
            raise OSError(f"Fresh start failed; completed moves were rolled back: {exc}") from exc
        return {
            "id": identifier,
            "created_at": now.isoformat(),
            "data_archive": str(data_archive) if data_archive else None,
            "reports_archive": str(report_archive) if report_archive else None,
            "cache_files": len(cache),
            "report_entries": len(generated),
        }
