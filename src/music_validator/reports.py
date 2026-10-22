"""Atomic issue and album catalog outputs in standalone HTML, JSON and CSV."""

import csv
import html
import json
import os
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .catalog import CATALOG_COLUMNS
from .safety import ISSUE_REPORT_FILES, REPORT_FILES, check_report_directory


def _write_json_report(path, summary, results, groups):
    # Stream per-file details from SQLite rather than collecting the whole library.
    with path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(summary, ensure_ascii=False, indent=2)[:-1])
        for name, records in (("results", results), ("groups", groups)):
            if records is None:
                continue
            stream.write(f',"{name}":[')
            separator = ""
            for record in records():
                stream.write(separator + json.dumps(record, ensure_ascii=False))
                separator = ",\n"
            stream.write("]")
        stream.write("}")


def _analysis_overview(analysis):
    def rows(values, prefix=""):
        for key, value in values.items():
            label = prefix + key.replace("_", " ").capitalize()
            if isinstance(value, dict):
                yield from rows(value, label + " / ")
            else:
                text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
                yield f"<tr><th>{html.escape(label)}</th><td>{html.escape(text)}</td></tr>"

    return (
        '<h2>Scope and coverage</h2><p><a href="issues.csv">Issues CSV</a> · '
        '<a href="report.json">Full JSON report</a></p><table>'
        + "".join(rows(analysis["statistics"]))
        + "</table><h2>Effective settings</h2><table>"
        + "".join(rows(analysis["settings"]))
        + "</table>"
    )


def _render_analysis_details(stream, analysis, results, groups):
    columns = [("path", "Track"), ("status", "Result"), ("source", "Execution")]
    if analysis["mode"] == "integrity":
        columns.insert(1, ("tool", "Tool"))
        columns.extend([("checked_at", "Last checked (UTC)"), ("findings", "Details")])
    else:
        columns.append(("reason", "Details"))
    stream.write("<h2>Track/check results</h2><table><thead><tr>")
    stream.write("".join(f"<th>{label}</th>" for _, label in columns))
    stream.write("</tr></thead><tbody>")
    if results is not None:
        for record in results():
            stream.write("<tr>")
            for key, _ in columns:
                value = record.get(key) or ""
                if key == "findings":
                    value = "\n".join(item["message"] for item in value)
                elif key in ("status", "source"):
                    value = str(value).replace("_", " ").capitalize()
                stream.write("<td>" + html.escape(str(value)) + "</td>")
            stream.write("</tr>")
    stream.write("</tbody></table>")
    if groups is None:
        return
    stream.write("<h2>Matched audio groups</h2>")
    for group in groups():
        fields = analysis["settings"]["fields"]
        conflicts = ", ".join(group["conflicting_fields"]) or "None"
        stream.write(
            "<details><summary>Group "
            + html.escape(group["id"])
            + f" — {len(group['tracks'])} copies</summary>"
            + f"<p>Minimum pair similarity: {group['similarity_min']:.4f}; "
            + f"minimum pair coverage: {group['coverage_min']:.4f}. "
            + "Conflicting fields: "
            + html.escape(conflicts)
            + ".</p>"
            + "<table><thead><tr><th>Track</th>"
            + "".join("<th>" + html.escape(field) + "</th>" for field in fields)
            + "</tr></thead><tbody>"
        )
        for track in group["tracks"]:
            stream.write("<tr><td>" + html.escape(track["path"]) + "</td>")
            for field in fields:
                stream.write("<td>" + html.escape(track["values"][field] or "<missing>") + "</td>")
            stream.write("</tr>")
        stream.write("</tbody></table></details>")


def _render_catalog(dest: Path, albums: list[dict], generated_at: str, issues: list):
    issue_counts = Counter(item["album"] for item in issues)
    albums = [
        {
            "no": number,
            **album,
            "number_of_issues": issue_counts[album["album_dir"]] or "",
            "parent_folder": Path(album["album_dir"]).parent.name,
        }
        for number, album in enumerate(albums, start=1)
    ]
    (dest / "catalog.json").write_text(
        json.dumps({"generated_at": generated_at, "albums": albums}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (dest / "catalog.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=[key for key, _ in CATALOG_COLUMNS], extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(albums)
    alignment = {
        "folder_size_mb": ' class="numeric"',
        "total_tracks": ' class="centered"',
        "number_of_issues": ' class="centered"',
    }
    headers = "".join(
        f'<th scope="col"{alignment.get(key, "")}>{label}</th>' for key, label in CATALOG_COLUMNS
    )
    rows = []
    for album in albums:
        cells = "".join(
            f"<td{alignment.get(key, '')}>{html.escape(str(album[key]))}</td>"
            for key, _ in CATALOG_COLUMNS
        )
        rows.append(f'<tr title="{html.escape(album["album_dir"], quote=True)}">{cells}</tr>')
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Music library catalog</title><style>
body{{font:15px system-ui,sans-serif;margin:2rem;line-height:1.5}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:.5rem;border-bottom:1px solid #aaa;text-align:left;overflow-wrap:anywhere}}
.numeric{{text-align:right}}
.centered{{text-align:center}}
.table{{overflow-x:auto}}
</style></head><body><h1>Music library catalog</h1>
<p>{len(albums)} albums · Generated {html.escape(generated_at)}</p>
<p>Quality is the lowest in each album; MP3 bitrate is in kbit/s.
Folder sizes include all regular files recursively (1 MB = 1,000,000 bytes).</p>
<div class="table"><table><thead><tr>{headers}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
{"<p>No albums found.</p>" if not albums else ""}
</body></html>"""
    (dest / "catalog.html").write_text(document, encoding="utf-8")


def _render(dest: Path, issues: list, count: int, *, analysis=None, results=None, groups=None):
    dest.mkdir(parents=True, exist_ok=True)
    grouped = defaultdict(list)
    for item in issues:
        grouped[item["album"]].append(item)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tracks": count,
        "albums_with_issues": len(grouped),
        "issues": issues,
    }
    if analysis is not None:
        summary["analysis"] = analysis
    _write_json_report(dest / "report.json", summary, results, groups)
    with (dest / "issues.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["album", "path", "rule", "severity", "message"])
        writer.writeheader()
        writer.writerows(issues)
    sections = []
    for album, entries in grouped.items():
        cells = "".join(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(e[k]))}</td>"
                for k in ("path", "rule", "severity", "message")
            )
            + "</tr>"
            for e in entries
        )
        sections.append(
            f"<details><summary>{html.escape(album)} — {len(entries)} issue(s)</summary><table><thead><tr><th>Track</th><th>Rule</th><th>Severity</th><th>Details</th></tr></thead><tbody>{cells}</tbody></table></details>"
        )
    title = "Music library audit"
    analysis_html = ""
    if analysis is not None:
        title = (
            "Audio integrity report" if analysis["mode"] == "integrity" else "Audio identity report"
        )
        analysis_html = _analysis_overview(analysis)
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>body{{font:15px system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;line-height:1.5}}details{{border:1px solid #aaa;border-radius:8px;padding:1rem;margin:.8rem 0}}summary{{font-weight:650;cursor:pointer;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;margin-top:1rem}}td,th{{padding:.5rem;border-bottom:1px solid #aaa;text-align:left;overflow-wrap:anywhere}}td:first-child{{max-width:25rem}}td:last-child{{white-space:pre-wrap}}.muted{{color:#666}}</style></head><body><h1>{html.escape(title)}</h1><p>{count} tracks scanned / indexed; {len(grouped)} albums needing work; {len(issues)} issues.</p><p class="muted">Generated {html.escape(summary["generated_at"])} · Read-only validation</p>{analysis_html}{"".join(sections) or "<p>No validation issues found.</p>"}</body></html>"""
    if analysis is None:
        (dest / "index.html").write_text(document, encoding="utf-8")
        return summary
    with (dest / "index.html").open("w", encoding="utf-8") as stream:
        stream.write(document.removesuffix("</body></html>"))
        _render_analysis_details(stream, analysis, results, groups)
        stream.write("</body></html>")
    return summary


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _switch_current(dest, target):
    temporary = dest / (".current-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, dest / "current")
    finally:
        temporary.unlink(missing_ok=True)


def _aliases(dest, report_files=REPORT_FILES):
    # Migrate old regular outputs into a preserved generation before replacing
    # any names. Until publication, every converted alias still reads old bytes.
    regular = [
        name for name in report_files if not (dest / name).is_symlink() and (dest / name).exists()
    ]
    if regular and not (dest / "current").exists():
        legacy = dest / "generations" / ("scan-legacy-" + uuid.uuid4().hex)
        legacy.mkdir()
        for name in regular:
            shutil.copyfile(dest / name, legacy / name)
            with (legacy / name).open("rb") as source:
                os.fsync(source.fileno())
        _sync_directory(legacy)
        _sync_directory(dest / "generations")
        _switch_current(dest, legacy.relative_to(dest))
        _sync_directory(dest)
    for name in report_files:
        path = dest / name
        if path.is_symlink():
            continue
        temporary = dest / (".alias-" + uuid.uuid4().hex)
        try:
            temporary.symlink_to(Path("current") / name)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    _sync_directory(dest)


def write_reports(
    dest: Path,
    issues: list,
    count: int,
    catalog: list | None = None,
    *,
    analysis=None,
    results=None,
    groups=None,
):
    """Publish a report family with one atomic `current` pointer replacement.

    Callers hold the report-directory lock for the entire scan/publication.
    Multi-file consumers can resolve `current` once to pin one generation.
    """
    report_files = ISSUE_REPORT_FILES if analysis is not None else REPORT_FILES
    check_report_directory(dest, report_files)
    dest.mkdir(parents=True, exist_ok=True)
    generations = dest / "generations"
    generations.mkdir(exist_ok=True)
    generation = generations / ("scan-" + uuid.uuid4().hex)
    generation.mkdir()
    published = False
    try:
        if analysis is None:
            summary = _render(generation, issues, count)
            _render_catalog(generation, catalog or [], summary["generated_at"], issues)
        else:
            summary = _render(
                generation, issues, count, analysis=analysis, results=results, groups=groups
            )
        for name in report_files:
            with (generation / name).open("rb") as source:
                os.fsync(source.fileno())
        _sync_directory(generation)
        _sync_directory(generations)
        check_report_directory(dest, report_files)
        _aliases(dest, report_files)
        _switch_current(dest, generation.relative_to(dest))
        published = True
        _sync_directory(dest)
        return summary
    finally:
        if not published:
            shutil.rmtree(generation)
