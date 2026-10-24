"""Read published immutable report snapshots, independent of the live SQLite cache."""

import json
import re
import threading
from collections import Counter, OrderedDict
from pathlib import Path

from .safety import ISSUE_REPORT_FILES, REPORT_FILES, check_report_directory, regular_output

MODES = ("configured", "integrity", "identity")


class ReportStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.cache = OrderedDict()

    def directory(self, mode):
        if mode not in MODES:
            raise ValueError("Unknown report family")
        return self.root if mode == "configured" else self.root / f"{mode}-report"

    def generation_path(self, mode, generation=None):
        directory = self.directory(mode)
        files = REPORT_FILES if mode == "configured" else ISSUE_REPORT_FILES
        check_report_directory(directory, files)
        if generation is None:
            try:
                generation = (directory / "current").readlink().name
            except FileNotFoundError:
                raise FileNotFoundError("No published report yet") from None
        if not re.fullmatch(r"scan-[a-z0-9-]+", generation):
            raise ValueError("Invalid report generation")
        path = directory / "generations" / generation
        for name in files:
            regular_output(path / name)
            if not (path / name).is_file():
                raise FileNotFoundError("Report generation is missing or incomplete")
        return path

    def load(self, mode, generation=None):
        path = self.generation_path(mode, generation)
        key = (mode, path.name)
        with self.lock:
            if key not in self.cache:
                report = json.loads((path / "report.json").read_text(encoding="utf-8"))
                catalog = (
                    json.loads((path / "catalog.json").read_text(encoding="utf-8"))["albums"]
                    if mode == "configured"
                    else []
                )
                counts = Counter(item["severity"] for item in report["issues"])
                rules = Counter(item["rule"] for item in report["issues"])
                self.cache[key] = {
                    "report": report,
                    "albums": catalog,
                    "summary": {
                        "mode": mode,
                        "available": True,
                        "generation": path.name,
                        "generated_at": report["generated_at"],
                        "tracks": report["tracks"],
                        "albums": len(catalog) if mode == "configured" else None,
                        "albums_with_issues": report["albums_with_issues"],
                        "issues": len(report["issues"]),
                        "affected_tracks": len({i["path"] for i in report["issues"]}),
                        "severities": dict(sorted(counts.items())),
                        "rules": dict(sorted(rules.items())),
                        "analysis": report.get("analysis"),
                    },
                }
                while len(self.cache) > 6:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            return self.cache[key]

    def summaries(self):
        summaries = {}
        for mode in MODES:
            try:
                summaries[mode] = self.load(mode)["summary"]
            except FileNotFoundError:
                summaries[mode] = {"mode": mode, "available": False}
        return summaries

    def query(self, mode, resource, params):
        snapshot = self.load(mode, params.get("generation"))
        if resource == "summary":
            return snapshot["summary"]
        report = snapshot["report"]
        if resource == "albums" and mode == "configured":
            rows = snapshot["albums"]
        elif resource == "issues":
            rows = report["issues"]
        elif resource in ("results", "groups") and mode != "configured":
            rows = report.get(resource, [])
        else:
            raise ValueError("Unknown report resource")
        total = len(rows)
        q = params.get("q", "").casefold()
        severity, rule, album = (params.get(key, "") for key in ("severity", "rule", "album"))
        album_matches = None
        if resource == "albums" and (severity or rule):
            album_matches = {
                i["album"]
                for i in report["issues"]
                if (not severity or i["severity"] == severity) and (not rule or i["rule"] == rule)
            }

        def contains(value):
            if isinstance(value, dict):
                return any(contains(item) for item in value.values())
            if isinstance(value, list):
                return any(contains(item) for item in value)
            return value is not None and q in str(value).casefold()

        def matches(row):
            if q and not contains(row):
                return False
            if resource == "issues":
                if severity and row["severity"] != severity:
                    return False
                if rule and row["rule"] != rule:
                    return False
                if album and row["album"] != album:
                    return False
            if resource == "albums":
                if album_matches is not None and row["album_dir"] not in album_matches:
                    return False
                if params.get("issues_only") == "true" and not row["number_of_issues"]:
                    return False
            if resource == "results":
                for key in ("status", "source"):
                    if params.get(key) and row.get(key) != params[key]:
                        return False
            if resource == "groups" and params.get("conflicts_only") == "true":
                return bool(row["conflicting_fields"])
            return True

        rows = [row for row in rows if matches(row)]
        allowed_sort = {
            "albums": {
                "album_artist",
                "year",
                "album",
                "quality",
                "folder_size_mb",
                "total_tracks",
                "number_of_issues",
                "album_dir",
            },
            "issues": {"album", "path", "rule", "severity"},
            "results": {"path", "status", "source", "tool", "checked_at"},
            "groups": {"id", "similarity_min", "coverage_min"},
        }
        sort = params.get("sort")
        if sort:
            if sort not in allowed_sort[resource]:
                raise ValueError("Unsupported sort field")
            numeric = {
                "folder_size_mb",
                "total_tracks",
                "number_of_issues",
                "similarity_min",
                "coverage_min",
            }
            rows.sort(
                key=lambda row: (
                    (row.get(sort) or 0) if sort in numeric else str(row.get(sort) or "").casefold()
                ),
                reverse=params.get("descending") == "true",
            )
        try:
            page, size = int(params.get("page", "1")), int(params.get("page_size", "50"))
        except ValueError:
            raise ValueError("Pagination must use integers") from None
        if page < 1 or not 1 <= size <= 200:
            raise ValueError("Page must be positive; page_size must be between 1 and 200")
        start = (page - 1) * size
        return {
            "generation": snapshot["summary"]["generation"],
            "total": total,
            "matching": len(rows),
            "page": page,
            "page_size": size,
            "items": rows[start : start + size],
        }
