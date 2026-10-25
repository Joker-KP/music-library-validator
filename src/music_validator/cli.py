"""CLI entry point. No write access is needed to music files."""

import argparse
import json
import sqlite3
import sys
from itertools import groupby
from pathlib import Path

import yaml

from . import database, scanner
from .analysis_modes import report_analysis, select_mode
from .audio_identity import validate_audio_identity
from .catalog import summarize_album
from .config import load_config
from .cross_album import artist_aliases, credit_candidate, validate_cross_album
from .fingerprints import FingerprintBackend
from .grouping import disc_number
from .integrity import IntegrityBackend
from .local_rules import LocalRules
from .metadata import normalize_date_tags
from .progress import Progress
from .reports import write_reports
from .safety import ISSUE_REPORT_FILES, REPORT_FILES, check_outputs, exclusive_lock, plain_path
from .validation import issue, validate


def run(
    config_path: Path,
    db: Path,
    reports: Path,
    full: bool = False,
    *,
    show_progress=True,
    recheck_integrity=False,
    recheck_identity=False,
    audio_integrity_only=False,
    audio_identity_only=False,
    event_callback=None,
):
    config = load_config(config_path)
    mode = select_mode(
        config, audio_integrity_only, audio_identity_only, recheck_integrity, recheck_identity
    )
    root = Path(config["library_root"]).resolve()
    if not root.is_dir():
        raise ValueError(f"Library directory not found: {root}")
    # Reject links before resolving; normalize '..' before mkdir can create
    # any otherwise-unused component inside the library.
    db, reports = plain_path(db).resolve(), plain_path(reports).resolve()
    report_base = reports
    check_outputs(root, db, report_base)
    report_files = REPORT_FILES
    if mode != "configured":
        reports = reports / f"{mode}-report"
        report_files = ISSUE_REPORT_FILES
        check_outputs(root, db, reports, report_files)
    if db == config_path.resolve() or config_path.resolve() in {
        dest / name for dest in (report_base, reports) for name in REPORT_FILES
    }:
        raise ValueError("Outputs must not overwrite the configuration")
    pattern = config["disc_folder_pattern"]
    extensions = set(config["extensions"])
    exclusions = config["exclude_directory_patterns"]
    integrity_options = config["audio_integrity"]
    integrity_backend = (
        IntegrityBackend(integrity_options, extensions) if integrity_options["enabled"] else None
    )
    audio_options = config["audio_identity_rules"]
    backend = FingerprintBackend() if audio_options["enabled"] and audio_options["fields"] else None
    progress = Progress(enabled=show_progress, callback=event_callback)
    local_rules = LocalRules(root, config, primary_path=config_path.resolve())
    # Parse all patches before locks/mkdir/database writes, including empty folders.
    with progress.phase("Counting audio files") as advance:
        estimated = scanner.count_files(
            root,
            extensions,
            progress=advance,
            on_enter=local_rules.collect,
            exclude_directory_patterns=exclusions,
        )
    with exclusive_lock(Path(str(db) + ".lock")), exclusive_lock(reports / ".scan.lock"):
        check_outputs(root, db, report_base)
        check_outputs(root, db, reports, report_files)
        con = database.connect(db)
        try:
            database.bind_library(con, root, extensions)
            with progress.phase("Analyzing tracks", estimated) as advance:
                result = scanner.scan(
                    con,
                    root,
                    extensions,
                    pattern,
                    full,
                    backend=backend,
                    progress=advance,
                    integrity_backend=integrity_backend,
                    recheck_integrity=recheck_integrity,
                    recheck_identity=recheck_identity,
                    mode=mode,
                    on_enter=local_rules.check_directory,
                    exclude_directory_patterns=exclusions,
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
            if mode == "configured":
                with progress.phase("Validating tags", result.seen) as advance:
                    for album, album_rows in groupby(
                        normalized_rows(), key=lambda row: row["album_dir"]
                    ):
                        rows = list(album_rows)
                        issues.extend(
                            validate(
                                rows,
                                local_rules.for_directory(Path(album)),
                                track_config=lambda row: local_rules.for_directory(
                                    Path(row["path"]).parent
                                ),
                            )
                        )
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
            integrity_failed_paths = set()
            if integrity_backend is not None:
                for row in database.iter_integrity_rows(con):
                    if row["check_name"] not in integrity_backend.checks.get(
                        Path(row["path"]).suffix.lower(), ()
                    ):
                        continue
                    checked = json.loads(row["result_json"])
                    for diagnostic in checked["findings"]:
                        issues.append(issue(row, **diagnostic))
                    if any(item["severity"] == "error" for item in checked["findings"]):
                        integrity_failed_paths.add(row["path"])
            if mode == "identity":
                for row in database.iter_identity_activity(con):
                    if row["status"] == "skipped_integrity":
                        integrity_failed_paths.add(row["path"])
                        issues.append(
                            issue(
                                {"path": row["path"], "album_dir": row["album"]},
                                "audio_identity_skipped_integrity",
                                row["reason"],
                            )
                        )
                for row in database.iter_rows(con):
                    if row["error"] and row["path"] not in integrity_failed_paths:
                        issues.append(issue(row, "read_error", row["error"], "error"))
            if backend is not None:
                with progress.phase("Indexing audio", result.seen) as advance:

                    def audio_rows():
                        for row in database.iter_audio_rows(con):
                            if row["path"] in integrity_failed_paths:
                                advance()
                                continue
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
                    if mode == "identity":
                        issues.extend(
                            validate_audio_identity(
                                index,
                                audio_options,
                                progress=advance,
                                on_group=lambda group: database.save_identity_group(con, group),
                            )
                        )
                    else:
                        issues.extend(
                            validate_audio_identity(index, audio_options, progress=advance)
                        )
            issues.sort(key=lambda item: (item["album"], item["path"], item["rule"]))
            count = result.seen
            outstanding = con.execute(
                "SELECT count(*) FROM tracks JOIN files USING(path) WHERE error IS NOT NULL AND tracks.size=files.size AND tracks.mtime_ns=files.mtime_ns"
            ).fetchone()[0]
            analysis = report_analysis(con, mode, result, config) if mode != "configured" else None
            local_rules.verify()
            with progress.phase("Publishing reports", len(report_files)) as advance:
                con.commit()
                if event_callback:
                    event_callback({"type": "cache_committed"})
                if analysis is None:
                    summary = write_reports(reports, issues, count, catalog)
                else:
                    results = (
                        database.iter_integrity_activity
                        if mode == "integrity"
                        else database.iter_identity_activity
                    )
                    summary = write_reports(
                        reports,
                        issues,
                        count,
                        analysis=analysis,
                        results=lambda: results(con),
                        groups=(lambda: database.iter_identity_groups(con))
                        if mode == "identity"
                        else None,
                    )
                advance(len(report_files))
                if event_callback:
                    event_callback(
                        {
                            "type": "reports_published",
                            "mode": mode,
                            "issues": len(issues),
                            "generated_at": summary["generated_at"],
                        }
                    )
        finally:
            con.rollback()
            con.close()
    print(
        f"Indexed {count} tracks; reread {result.read}; read errors this run {result.errors}; "
        f"outstanding read errors {outstanding}; "
        f"{summary['albums_with_issues']} albums with {len(issues)} issues."
    )
    print(f"Reports: {reports.resolve() / 'index.html'}")
    if mode == "configured":
        print(f"Catalog: {reports.resolve() / 'catalog.html'}")
    if integrity_backend is not None:
        print(
            f"Audio integrity: ran {result.integrity_read} checks; "
            f"failed checks {result.integrity_failed}; incomplete checks {result.integrity_incomplete} "
            "(including cached results)."
        )
    if backend is not None:
        print(
            f"Audio fingerprints: decoded {result.audio_read}; fingerprint errors {result.audio_errors}."
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only MP3/FLAC library auditor")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Serve reports and optional browser scan controls")
    serve.add_argument("--config", type=Path, default=Path("config/rules.yaml"))
    serve.add_argument("--db", type=Path, default=Path("data/library.sqlite"))
    serve.add_argument("--reports", type=Path, default=Path("reports"))
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    scan = sub.add_parser("scan", help="Scan and generate reports")
    scan.add_argument("--config", type=Path, default=Path("config/rules.yaml"))
    scan.add_argument("--db", type=Path, default=Path("data/library.sqlite"))
    scan.add_argument("--reports", type=Path, default=Path("reports"))
    modes = scan.add_mutually_exclusive_group()
    modes.add_argument(
        "--audio-integrity-only",
        action="store_true",
        help="Run only integrity checks, overriding their YAML enabled setting",
    )
    modes.add_argument(
        "--audio-identity-only",
        action="store_true",
        help="Run only audio matching/tag comparisons, overriding their YAML enabled setting",
    )
    scan.add_argument(
        "--full-scan",
        action="store_true",
        help="Refresh all source data needed by the selected mode",
    )
    scan.add_argument(
        "--recheck-identity",
        action="store_true",
        help="Re-extract fingerprints while retaining current metadata",
    )
    scan.add_argument(
        "--recheck-integrity",
        action="store_true",
        help="Repeat enabled integrity checks while retaining metadata and fingerprint caches",
    )
    scan.add_argument(
        "--no-progress", action="store_true", help="Suppress progress output on stderr"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .server import serve as serve_reports

            return serve_reports(args.config, args.db, args.reports, args.host, args.port)
        return run(
            args.config,
            args.db,
            args.reports,
            args.full_scan,
            show_progress=not args.no_progress,
            recheck_integrity=args.recheck_integrity,
            recheck_identity=args.recheck_identity,
            audio_integrity_only=args.audio_integrity_only,
            audio_identity_only=args.audio_identity_only,
        )
    except (OSError, ValueError, yaml.YAMLError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
