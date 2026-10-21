"""CLI entry point. No write access is needed to music files."""
import argparse
import json
import sys
from pathlib import Path

import yaml

from . import database
from .grouping import album_directory, disc_number
from .metadata import read_metadata
from .reports import write_reports
from .validation import validate


def run(config_path: Path, db: Path, reports: Path, full: bool = False):
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    root = Path(config["library_root"]).resolve()
    if not root.is_dir():
        raise ValueError(f"Library directory not found: {root}")
    con = database.connect(db)
    pattern = config.get("disc_folder_pattern")
    extensions = {str(x).lower() for x in config.get("extensions", [".mp3", ".flac"])}
    seen = set()
    changed = 0
    errors = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        # Avoid symlinks escaping the read-only music root.
        if path.is_symlink():
            continue
        seen.add(str(path))
        stat = path.stat()
        old = database.get(con, path)
        if not full and old and old["size"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns:
            continue
        album = album_directory(path, root, pattern) if pattern else album_directory(path, root)
        try:
            metadata = read_metadata(path)
            error = None
        except Exception as exc:
            metadata = {}
            error = f"{type(exc).__name__}: {exc}"
            errors += 1
        database.upsert(con, path, album, stat, metadata, error)
        changed += 1
    # Delete cached records for files absent from this successful full directory walk.
    for row in database.rows(con):
        if row["path"] not in seen:
            con.execute("DELETE FROM tracks WHERE path=?", (row["path"],))
    con.commit()
    indexed = database.rows(con)
    rows = []
    for row in indexed:
        row["metadata"] = json.loads(row.pop("metadata_json"))
        path = Path(row["path"])
        row["disc_from_path"] = disc_number(path, root, pattern) if pattern else disc_number(path, root)
        rows.append(row)
    issues = validate(rows, config)
    summary = write_reports(reports, issues, len(rows))
    print(f"Indexed {len(rows)} tracks; reread {changed}; read errors this run {errors}; "
          f"{summary['albums_with_issues']} albums with {len(issues)} issues.")
    print(f"Reports: {reports.resolve() / 'index.html'}")
    con.close()
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
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
