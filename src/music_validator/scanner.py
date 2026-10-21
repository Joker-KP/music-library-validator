"""Streaming discovery and read-only descriptor-based metadata collection."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from mutagen import MutagenError

from . import database
from .grouping import album_directory
from .metadata import read_metadata


@dataclass
class ScanResult:
    scan_id: int
    seen: int = 0
    read: int = 0
    errors: int = 0


def discover(root, extensions):
    """Yield files while their parent descriptor remains open; never follow links.

    Errors are fatal to discovery, so callers must roll back instead of pruning.
    Memory is proportional to directory width/depth, not total library size.
    """

    def walk(relative, dir_fd):
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
                    yield from walk(relative / name, child_fd)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode) and Path(name).suffix.lower() in extensions:
                yield root / relative / name, dir_fd, name, info

    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield from walk(Path(), fd)
    finally:
        os.close(fd)


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def scan(con, root: Path, extensions, pattern, full=False):
    scan_id = con.execute("INSERT INTO scans DEFAULT VALUES").lastrowid
    result = ScanResult(scan_id)
    for path, dir_fd, name, info in discover(root, extensions):
        result.seen += 1
        album = album_directory(path, root, pattern)
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
            continue
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
    # Reached only after complete discovery; changes are still uncommitted.
    con.execute("DELETE FROM tracks WHERE last_seen_scan<>?", (scan_id,))
    con.execute(
        """UPDATE scans SET finished_at=CURRENT_TIMESTAMP,
        files_seen=?,files_read=?,errors=? WHERE id=?""",
        (result.seen, result.read, result.errors, scan_id),
    )
    return result
