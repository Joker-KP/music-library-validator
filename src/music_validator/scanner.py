"""Streaming discovery and read-only descriptor-based metadata collection."""

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import MutagenError

from . import database
from .fingerprints import FingerprintError
from .grouping import album_directory
from .integrity import incomplete
from .metadata import read_metadata


@dataclass
class ScanResult:
    scan_id: int
    seen: int = 0
    read: int = 0
    errors: int = 0
    audio_read: int = 0
    audio_errors: int = 0
    integrity_read: int = 0
    integrity_failed: int = 0
    integrity_incomplete: int = 0
    folder_sizes: dict[str, int] = field(default_factory=dict)


def discover(root, extensions, on_directory=None, on_enter=None, *, exclude_directory_patterns=()):
    """Yield files while their parent descriptor remains open; never follow links.

    Errors are fatal to discovery, so callers must roll back instead of pruning.
    Memory is proportional to directory width/depth, not total library size.
    An optional callback receives recursive regular-file bytes per directory
    after its descendants have been visited; symlinks never contribute bytes.
    on_enter receives the directory path and its verified descriptor before
    visiting any entries, including for directories without audio files.
    Directory exclusion regexes search absolute paths before opening a matched
    directory. Excluded subtrees contribute no files, callbacks or bytes.
    """
    exclusions = tuple(re.compile(pattern) for pattern in exclude_directory_patterns)
    absolute_root = root.absolute()

    def excluded(relative):
        return any(pattern.search(str(absolute_root / relative)) for pattern in exclusions)

    def walk(relative, dir_fd):
        total_size = 0
        if on_enter is not None:
            on_enter(root / relative, dir_fd)
        with os.scandir(dir_fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if excluded(relative / name):
                    continue
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

    if excluded(Path()):
        return
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield from walk(Path(), fd)
    finally:
        os.close(fd)


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def count_files(root, extensions, progress=None, *, on_enter=None, exclude_directory_patterns=()):
    """Count without opening audio; on_enter can preflight local configurations."""
    count = 0
    for _ in discover(
        root, extensions, on_enter=on_enter, exclude_directory_patterns=exclude_directory_patterns
    ):
        count += 1
        if progress is not None:
            progress(1)
    return count


def scan(
    con,
    root: Path,
    extensions,
    pattern,
    full=False,
    *,
    backend=None,
    progress=None,
    integrity_backend=None,
    recheck_integrity=False,
    recheck_identity=False,
    mode="configured",
    on_enter=None,
    exclude_directory_patterns=(),
):
    scan_id = con.execute("INSERT INTO scans(mode) VALUES(?)", (mode,)).lastrowid
    database.start_audio_activity(con)
    result = ScanResult(scan_id)

    def directory_size(path, size):
        if str(path) in result.folder_sizes:
            result.folder_sizes[str(path)] = size

    for path, dir_fd, name, info in discover(
        root,
        extensions,
        on_directory=directory_size if mode == "configured" else None,
        on_enter=on_enter,
        exclude_directory_patterns=exclude_directory_patterns,
    ):
        result.seen += 1
        album = album_directory(path, root, pattern)
        if mode == "configured":
            result.folder_sizes.setdefault(str(album), 0)
        database.record_file(con, path, album, info, scan_id)
        if mode != "integrity":
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
                    file_fd = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd
                    )
                    with os.fdopen(file_fd, "rb") as source:
                        before = os.fstat(source.fileno())
                        if not stat.S_ISREG(before.st_mode) or _signature(before) != _signature(
                            info
                        ):
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
        integrity_failed = False
        if integrity_backend is not None:
            for check in integrity_backend.checks.get(path.suffix.lower(), ()):
                refreshed, checked = _scan_integrity_track(
                    con,
                    path,
                    dir_fd,
                    name,
                    info,
                    integrity_backend,
                    check,
                    full or recheck_integrity,
                )
                con.execute(
                    "INSERT INTO integrity_activity VALUES(?,?,?)",
                    (str(path), check, "executed" if refreshed else "cached"),
                )
                result.integrity_read += refreshed
                result.integrity_failed += int(checked["status"] == "failed")
                result.integrity_incomplete += int(checked["status"] == "incomplete")
                integrity_failed |= any(item["severity"] == "error" for item in checked["findings"])
        if mode == "integrity" and not integrity_backend.checks.get(path.suffix.lower()):
            con.execute(
                "INSERT INTO integrity_activity VALUES(?,?,?)", (str(path), "none", "not_checked")
            )
        if mode == "identity":
            integrity_failed = database.cached_integrity_errors(con, path, info)
        if backend is not None:
            if integrity_failed:
                con.execute(
                    "INSERT INTO identity_activity VALUES(?,?,?,?)",
                    (
                        str(path),
                        "skipped_integrity",
                        "not_checked",
                        "Cached integrity error for the current file signature; recheck integrity to reassess.",
                    ),
                )
            else:
                read, errors = _scan_audio_track(
                    con, path, dir_fd, name, info, backend, full or recheck_identity
                )
                result.audio_read += read
                result.audio_errors += errors
                metadata = database.get(con, path)
                fingerprint = database.audio_fingerprint(con, path)
                status = "fingerprint_error" if fingerprint["error"] else "unmatched"
                reason = fingerprint["error"] or ""
                if metadata["error"]:
                    status, reason = "metadata_error", metadata["error"]
                con.execute(
                    "INSERT INTO identity_activity VALUES(?,?,?,?)",
                    (
                        str(path),
                        status,
                        "executed" if read else "cached",
                        reason,
                    ),
                )
        if progress is not None:
            progress(1)
    # Reached only after complete discovery; changes are still uncommitted.
    database.prune_files(con, scan_id)
    con.execute(
        """UPDATE scans SET finished_at=CURRENT_TIMESTAMP,
        files_seen=?,files_read=?,errors=? WHERE id=?""",
        (result.seen, result.read, result.errors, scan_id),
    )
    return result


def _scan_integrity_track(con, path, dir_fd, name, info, backend, check, full):
    old = database.integrity_result(con, path, check)
    if (
        not full
        and old
        and old["backend_version"] == backend.versions[check]
        and (old["size"], old["mtime_ns"]) == (info.st_size, info.st_mtime_ns)
    ):
        checked = json.loads(old["result_json"])
        if checked["status"] != "incomplete":
            return 0, checked
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or _signature(before) != _signature(info):
                raise OSError("File changed during integrity discovery; retry on next scan")
            checked = backend.check(check, source)
            if _signature(before) != _signature(os.fstat(source.fileno())):
                raise OSError("File changed during integrity check; retry on next scan")
    except OSError as exc:
        checked = incomplete(check, str(exc))
    database.save_integrity_result(con, path, check, info, backend.versions[check], checked)
    return 1, checked.as_dict()


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
