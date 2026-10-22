"""CLI entry point. No write access is needed to music files."""

import argparse
import json
import sqlite3
import sys
from itertools import groupby
from pathlib import Path

import yaml

from . import database, scanner
from .audio_identity import validate_audio_identity
from .catalog import summarize_album
from .config import load_config
from .cross_album import artist_aliases, credit_candidate, validate_cross_album
from .fingerprints import FingerprintBackend
from .grouping import disc_number
from .metadata import normalize_date_tags
from .progress import Progress
from .reports import write_reports
from .safety import REPORT_FILES, check_outputs, exclusive_lock, plain_path
from .validation import issue, validate


def run(config_path: Path, db: Path, reports: Path, full: bool = False, *, show_progress=True):
    config = load_config(config_path)
    root = Path(config["library_root"]).resolve()
    if not root.is_dir():
        raise ValueError(f"Library directory not found: {root}")
    # Reject links before resolving; normalize '..' before mkdir can create
    # any otherwise-unused component inside the library.
    db, reports = plain_path(db).resolve(), plain_path(reports).resolve()
    check_outputs(root, db, reports)
    if db == config_path.resolve() or config_path.resolve() in {
        reports / name for name in REPORT_FILES
    }:
        raise ValueError("Outputs must not overwrite the configuration")
    pattern = config["disc_folder_pattern"]
    extensions = set(config["extensions"])
    audio_options = config["audio_identity_rules"]
    backend = FingerprintBackend() if audio_options["enabled"] and audio_options["fields"] else None
    progress = Progress(enabled=show_progress)
    with exclusive_lock(Path(str(db) + ".lock")), exclusive_lock(reports / ".scan.lock"):
        check_outputs(root, db, reports)
        with progress.phase("Counting audio files") as advance:
            estimated = scanner.count_files(root, extensions, progress=advance)
        con = database.connect(db)
        try:
            database.bind_library(con, root, extensions)
            with progress.phase("Analyzing tracks", estimated) as advance:
                result = scanner.scan(
                    con, root, extensions, pattern, full, backend=backend, progress=advance
                )
            if show_progress and result.seen != estimated:
                print(
                    f"File count changed: counted {estimated}, processed {result.seen}.",
                    file=sys.stderr,
                )

            def normalized_rows():
                for row in database.iter_rows(con):
                    row["metadata"] = json.loads(row.pop("metadata_json"))
                    normalize_date_tags(row["metadata"].get("tags", {}))
                    row["disc_from_path"] = disc_number(Path(row["path"]), root, pattern)
                    yield row

            # Validate one album at a time, avoiding another full metadata copy.
            issues = []
            catalog = []
            with progress.phase("Validating tags", result.seen) as advance:
                for album, album_rows in groupby(
                    normalized_rows(), key=lambda row: row["album_dir"]
                ):
                    rows = list(album_rows)
                    issues.extend(validate(rows, config))
                    catalog.append(summarize_album(rows, result.folder_sizes[album]))
                    advance(len(rows))
            cross_album = config["cross_album_rules"]
            fields = cross_album["fields"]
            if cross_album["enabled"] and fields:
                aliases = artist_aliases(cross_album["artist_alias_groups"])
                with progress.phase("Cross-album rules", result.seen) as advance:

                    def candidates():
                        for row in normalized_rows():
                            candidate = credit_candidate(row, aliases, fields)
                            if candidate is not None:
                                yield candidate
                            advance()

                    issues.extend(
                        validate_cross_album(
                            database.iter_cross_album_rows(con, candidates()),
                            cross_album["severity"],
                            fields,
                        )
                    )
            if backend is not None:
                with progress.phase("Indexing audio", result.seen) as advance:

                    def audio_rows():
                        for row in database.iter_audio_rows(con):
                            if row["fingerprint_error"]:
                                issues.append(
                                    issue(
                                        row,
                                        "audio_fingerprint_error",
                                        row["fingerprint_error"],
                                        "warning",
                                    )
                                )
                            elif not row["error"]:
                                row["metadata"] = json.loads(row.pop("metadata_json"))
                                normalize_date_tags(row["metadata"].get("tags", {}))
                                row["fingerprint"] = json.loads(row.pop("fingerprint_json"))
                                yield row
                            advance()

                    index = database.AudioIndex(con, audio_rows(), audio_options["fields"])
                with progress.phase("Matching audio", index.count) as advance:
                    issues.extend(validate_audio_identity(index, audio_options, progress=advance))
            issues.sort(key=lambda item: (item["album"], item["path"], item["rule"]))
            count = con.execute("SELECT count(*) FROM tracks").fetchone()[0]
            outstanding = con.execute(
                "SELECT count(*) FROM tracks WHERE error IS NOT NULL"
            ).fetchone()[0]
            with progress.phase("Publishing reports", len(REPORT_FILES)) as advance:
                con.commit()
                summary = write_reports(reports, issues, count, catalog)
                advance(len(REPORT_FILES))
        finally:
            con.rollback()
            con.close()
    print(
        f"Indexed {count} tracks; reread {result.read}; read errors this run {result.errors}; "
        f"outstanding read errors {outstanding}; "
        f"{summary['albums_with_issues']} albums with {len(issues)} issues."
    )
    print(f"Reports: {reports.resolve() / 'index.html'}")
    print(f"Catalog: {reports.resolve() / 'catalog.html'}")
    if backend is not None:
        print(
            f"Audio fingerprints: decoded {result.audio_read}; fingerprint errors {result.audio_errors}."
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only MP3/FLAC library auditor")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="Scan and generate reports")
    scan.add_argument("--config", type=Path, default=Path("config/rules.yaml"))
    scan.add_argument("--db", type=Path, default=Path("data/library.sqlite"))
    scan.add_argument("--reports", type=Path, default=Path("reports"))
    scan.add_argument("--full-scan", action="store_true", help="Ignore SQLite size/mtime cache")
    scan.add_argument(
        "--no-progress", action="store_true", help="Suppress progress output on stderr"
    )
    args = parser.parse_args(argv)
    try:
        return run(
            args.config, args.db, args.reports, args.full_scan, show_progress=not args.no_progress
        )
    except (OSError, ValueError, yaml.YAMLError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
