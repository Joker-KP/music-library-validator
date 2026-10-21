"""Output preflight and advisory ownership locks for Linux/Synology."""

import fcntl
import os
import stat
from contextlib import contextmanager
from pathlib import Path

REPORT_FILES = ("index.html", "issues.csv", "report.json")


def plain_path(path: Path):
    """Reject symlinks at every existing component, including dangling links."""
    path = path.absolute()
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"Unsafe output symlink: {component}")
    return path


def regular_output(path: Path):
    plain_path(path)
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"Output must be a regular file with one link: {path}")


def check_report_directory(dest: Path):
    plain_path(dest)
    plain_path(dest / "generations")
    for directory in (dest, dest / "generations"):
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"Report directory is not a directory: {directory}")
    current = dest / "current"
    if current.is_symlink():
        target = current.readlink()
        if (
            target.is_absolute()
            or len(target.parts) != 2
            or target.parts[0] != "generations"
            or not target.name.startswith("scan-")
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in target.name)
        ):
            raise ValueError(f"Unsafe current report symlink: {current}")
        plain_path(dest / target)
        if not (dest / target).is_dir():
            raise ValueError(f"Missing report generation: {target}")
    elif current.exists():
        raise ValueError(f"Reserved report path must be a managed symlink: {current}")
    for name in REPORT_FILES:
        path = dest / name
        if path.is_symlink():
            if path.readlink() != Path("current") / name:
                raise ValueError(f"Unsafe report output symlink: {path}")
        elif path.exists():
            regular_output(path)


def check_outputs(root: Path, db: Path, reports: Path):
    for output in (db, reports):
        if output.resolve().is_relative_to(root):
            raise ValueError(f"Output must be outside the music library: {output}")
        plain_path(output)
    if root.is_relative_to(reports.resolve()):
        raise ValueError("Report directory must not contain the music library")
    if db.resolve().is_relative_to(reports.resolve()):
        raise ValueError("Database must be outside the report directory")
    for suffix in ("", "-journal", "-wal", "-shm", ".lock"):
        regular_output(Path(str(db) + suffix))
    check_report_directory(reports)
    regular_output(reports / ".scan.lock")


@contextmanager
def exclusive_lock(path: Path):
    path = plain_path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    regular_output(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"Unsafe lock file: {path}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"A scan is already using {path}") from exc
        yield
    finally:
        os.close(fd)
