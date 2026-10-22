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
from .safety import REPORT_FILES, check_report_directory


def _render_catalog(dest: Path, albums: list[dict], generated_at: str, issues: list):
    issue_counts = Counter(item["album"] for item in issues)
    albums = [
        {"no": number, **album, "number_of_issues": issue_counts[album["album_dir"]] or ""}
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
    headers = "".join(f'<th scope="col">{label}</th>' for _, label in CATALOG_COLUMNS)
    rows = []
    for album in albums:
        cells = "".join(f"<td>{html.escape(str(album[key]))}</td>" for key, _ in CATALOG_COLUMNS)
        rows.append(f'<tr title="{html.escape(album["album_dir"], quote=True)}">{cells}</tr>')
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Music library catalog</title><style>
body{{font:15px system-ui,sans-serif;margin:2rem;line-height:1.5}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:.5rem;border-bottom:1px solid #aaa;text-align:left;overflow-wrap:anywhere}}
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


def _render(dest: Path, issues: list, count: int):
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
    (dest / "report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Music library audit</title><style>body{{font:15px system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;line-height:1.5}}details{{border:1px solid #aaa;border-radius:8px;padding:1rem;margin:.8rem 0}}summary{{font-weight:650;cursor:pointer;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;margin-top:1rem}}td,th{{padding:.5rem;border-bottom:1px solid #aaa;text-align:left;overflow-wrap:anywhere}}td:first-child{{max-width:25rem}}.muted{{color:#666}}</style></head><body><h1>Music library audit</h1><p>{count} tracks scanned / indexed; {len(grouped)} albums needing work; {len(issues)} issues.</p><p class="muted">Generated {html.escape(summary["generated_at"])} · Read-only validation</p>{"".join(sections) or "<p>No validation issues found.</p>"}</body></html>"""
    (dest / "index.html").write_text(document, encoding="utf-8")
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


def _aliases(dest):
    # Migrate old regular outputs into a preserved generation before replacing
    # any names. Until publication, every converted alias still reads old bytes.
    regular = [
        name for name in REPORT_FILES if not (dest / name).is_symlink() and (dest / name).exists()
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
    for name in REPORT_FILES:
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


def write_reports(dest: Path, issues: list, count: int, catalog: list | None = None):
    """Publish all six outputs with one atomic `current` pointer replacement.

    Callers hold the report-directory lock for the entire scan/publication.
    Multi-file consumers can resolve `current` once to pin one generation.
    """
    check_report_directory(dest)
    dest.mkdir(parents=True, exist_ok=True)
    generations = dest / "generations"
    generations.mkdir(exist_ok=True)
    generation = generations / ("scan-" + uuid.uuid4().hex)
    generation.mkdir()
    published = False
    try:
        summary = _render(generation, issues, count)
        _render_catalog(generation, catalog or [], summary["generated_at"], issues)
        for name in REPORT_FILES:
            with (generation / name).open("rb") as source:
                os.fsync(source.fileno())
        _sync_directory(generation)
        _sync_directory(generations)
        check_report_directory(dest)
        _aliases(dest)
        _switch_current(dest, generation.relative_to(dest))
        published = True
        _sync_directory(dest)
        return summary
    finally:
        if not published:
            shutil.rmtree(generation)
