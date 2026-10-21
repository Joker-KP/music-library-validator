"""Static report outputs: HTML (album-oriented), JSON and CSV."""
import csv
import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def write_reports(dest: Path, issues: list, count: int):
    dest.mkdir(parents=True, exist_ok=True)
    grouped = defaultdict(list)
    for item in issues:
        grouped[item["album"]].append(item)
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "tracks": count, "albums_with_issues": len(grouped), "issues": issues}
    (dest / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (dest / "issues.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["album", "path", "rule", "severity", "message"])
        writer.writeheader()
        writer.writerows(issues)
    sections = []
    for album, entries in grouped.items():
        cells = "".join("<tr>" + "".join(f"<td>{html.escape(str(e[k]))}</td>" for k in ("path", "rule", "severity", "message")) + "</tr>" for e in entries)
        sections.append(f'<details><summary>{html.escape(album)} — {len(entries)} issue(s)</summary><table><thead><tr><th>Track</th><th>Rule</th><th>Severity</th><th>Details</th></tr></thead><tbody>{cells}</tbody></table></details>')
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Music library audit</title><style>body{{font:15px system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;line-height:1.5}}details{{border:1px solid #aaa;border-radius:8px;padding:1rem;margin:.8rem 0}}summary{{font-weight:650;cursor:pointer;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%;margin-top:1rem}}td,th{{padding:.5rem;border-bottom:1px solid #aaa;text-align:left;overflow-wrap:anywhere}}td:first-child{{max-width:25rem}}.muted{{color:#666}}</style></head><body><h1>Music library audit</h1><p>{count} tracks scanned / indexed; {len(grouped)} albums needing work; {len(issues)} issues.</p><p class="muted">Generated {html.escape(summary["generated_at"])} · Read-only validation</p>{''.join(sections) or '<p>No validation issues found.</p>'}</body></html>"""
    (dest / "index.html").write_text(document, encoding="utf-8")
    return summary
