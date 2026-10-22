"""Streaming discovery and read-only descriptor-based metadata collection."""

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import MutagenError

from . import database
from .fingerprints import FingerprintError
from .grouping import album_directory
from .metadata import read_metadata


@dataclass
class ScanResult:
    scan_id: int
    seen: int = 0
    read: int = 0
    errors: int = 0
    audio_read: int = 0
    audio_errors: int = 0
    folder_sizes: dict[str, int] = field(default_factory=dict)


def discover(root, extensions, on_directory=None):
    """Yield files while their parent descriptor remains open; never follow links.

    Errors are fatal to discovery, so callers must roll back instead of pruning.
    Memory is proportional to directory width/depth, not total library size.
    An optional callback receives recursive regular-file bytes per directory
    after its descendants have been visited; symlinks never contribute bytes.
    """

    def walk(relative, dir_fd):
        total_size = 0
        with os.scandir(dir_fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child_fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
                )
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise OSError(
                            f"Directory changed during discovery: {root / relative / name}"
                        )
                    total_size += yield from walk(relative / name, child_fd)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode):
                total_size += info.st_size
                if Path(name).suffix.lower() in extensions:
                    yield root / relative / name, dir_fd, name, info
        if on_directory is not None:
            on_directory(root / relative, total_size)
        return total_size

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield from walk(Path(), fd)
    finally:
        os.close(fd)


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def count_files(root, extensions, progress=None):
    """Count discoverable audio paths without opening audio or retaining a list."""
    count = 0
    for _ in discover(root, extensions):
        count += 1
        if progress is not None:
            progress(1)
    return count


def scan(con, root: Path, extensions, pattern, full=False, *, backend=None, progress=None):
    scan_id = con.execute("INSERT INTO scans DEFAULT VALUES").lastrowid
    result = ScanResult(scan_id)

    def directory_size(path, size):
        if str(path) in result.folder_sizes:
            result.folder_sizes[str(path)] = size

    for path, dir_fd, name, info in discover(root, extensions, on_directory=directory_size):
        result.seen += 1
        album = album_directory(path, root, pattern)
        result.folder_sizes.setdefault(str(album), 0)
        old = database.get(con, path)
        if (
            not full
            and old
            and not old["error"]
            and old["parser_version"] == database.PARSER_VERSION
            and old["size"] == info.st_size
            and old["mtime_ns"] == info.st_mtime_ns
        ):
            con.execute(
                "UPDATE tracks SET album_dir=?,last_seen_scan=? WHERE path=?",
                (str(album), scan_id, str(path)),
            )
        else:
            try:
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
                with os.fdopen(file_fd, "rb") as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode) or _signature(before) != _signature(info):
                        raise OSError("File changed during discovery; retry on next scan")
                    metadata = read_metadata(path, fileobj=source)
                    after = os.fstat(source.fileno())
                    if _signature(before) != _signature(after):
                        raise OSError("File changed while reading; retry on next scan")
                error = None
            except (OSError, ValueError, MutagenError) as exc:
                metadata = {}
                error = f"{type(exc).__name__}: {exc}"
                result.errors += 1
            database.upsert(con, path, album, info, metadata, error, scan_id=scan_id)
            result.read += 1
        if backend is not None:
            read, errors = _scan_audio_track(con, path, dir_fd, name, info, backend, full)
            result.audio_read += read
            result.audio_errors += errors
        if progress is not None:
            progress(1)
    # Reached only after complete discovery; changes are still uncommitted.
    con.execute("DELETE FROM tracks WHERE last_seen_scan<>?", (scan_id,))
    database.prune_audio_fingerprints(con)
    con.execute(
        """UPDATE scans SET finished_at=CURRENT_TIMESTAMP,
        files_seen=?,files_read=?,errors=? WHERE id=?""",
        (result.seen, result.read, result.errors, scan_id),
    )
    return result


def _scan_audio_track(con, path, dir_fd, name, info, backend, full):
    """Refresh audio independently of tags, within the same discovery traversal."""
    old = database.audio_fingerprint(con, path)
    if (
        not full
        and old
        and not old["error"]
        and old["backend_version"] == backend.version
        and (old["size"], old["mtime_ns"]) == (info.st_size, info.st_mtime_ns)
    ):
        return 0, 0
    duration, fingerprint, error = None, None, None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or _signature(before) != _signature(info):
                raise OSError("File changed during audio discovery; retry on next scan")
            duration, fingerprint = backend.fingerprint(source, path.suffix)
            if _signature(before) != _signature(os.fstat(source.fileno())):
                raise OSError("File changed while fingerprinting; retry on next scan")
    except (OSError, FingerprintError) as exc:
        duration, fingerprint = None, None
        error = f"{type(exc).__name__}: {exc}"
    database.save_audio_fingerprint(con, path, info, backend.version, duration, fingerprint, error)
    return 1, int(error is not None)
