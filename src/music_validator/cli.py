"""CLI entry point. No write access is needed to music files."""

import argparse
import json
import sqlite3
import sys
from itertools import groupby
from pathlib import Path

import yaml

from . import database, scanner
from .catalog import summarize_album
from .config import load_config
from .cross_album import artist_aliases, credit_candidate, validate_cross_album
from .grouping import disc_number
from .metadata import normalize_date_tags
from .reports import write_reports
from .safety import REPORT_FILES, check_outputs, exclusive_lock, plain_path
from .validation import validate


def run(config_path: Path, db: Path, reports: Path, full: bool = False):
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
    with exclusive_lock(Path(str(db) + ".lock")), exclusive_lock(reports / ".scan.lock"):
        check_outputs(root, db, reports)
        con = database.connect(db)
        try:
            database.bind_library(con, root, extensions)
            result = scanner.scan(con, root, extensions, pattern, full)

            def normalized_rows():
                for row in database.iter_rows(con):
                    row["metadata"] = json.loads(row.pop("metadata_json"))
                    normalize_date_tags(row["metadata"].get("tags", {}))
                    row["disc_from_path"] = disc_number(Path(row["path"]), root, pattern)
                    yield row

            # Validate one album at a time, avoiding another full metadata copy.
            issues = []
            catalog = []
            for album, album_rows in groupby(normalized_rows(), key=lambda row: row["album_dir"]):
                rows = list(album_rows)
                issues.extend(validate(rows, config))
                catalog.append(summarize_album(rows, result.folder_sizes[album]))
            cross_album = config["cross_album_rules"]
            fields = cross_album["fields"]
            if cross_album["enabled"] and fields:
                aliases = artist_aliases(cross_album["artist_alias_groups"])
                candidates = (
                    candidate
                    for row in normalized_rows()
                    if (candidate := credit_candidate(row, aliases, fields)) is not None
                )
                issues.extend(
                    validate_cross_album(
                        database.iter_cross_album_rows(con, candidates),
                        cross_album["severity"],
                        fields,
                    )
                )
            issues.sort(key=lambda item: (item["album"], item["path"], item["rule"]))
            count = con.execute("SELECT count(*) FROM tracks").fetchone()[0]
            outstanding = con.execute(
                "SELECT count(*) FROM tracks WHERE error IS NOT NULL"
            ).fetchone()[0]
            con.commit()
            summary = write_reports(reports, issues, count, catalog)
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
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only MP3/FLAC library auditor")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="Scan and generate reports")
    scan.add_argument("--config", type=Path, default=Path("config/rules.yaml"))
    scan.add_argument("--db", type=Path, default=Path("data/library.sqlite"))
    scan.add_argument("--reports", type=Path, default=Path("reports"))
    scan.add_argument("--full-scan", action="store_true", help="Ignore SQLite size/mtime cache")
    args = parser.parse_args(argv)
    try:
        return run(args.config, args.db, args.reports, args.full_scan)
    except (OSError, ValueError, yaml.YAMLError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
