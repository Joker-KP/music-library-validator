"""Pure validation rules over normalized metadata."""

import re
from collections import defaultdict
from pathlib import Path


def issue(row, rule, message, severity="warning"):
    return {
        "album": row["album_dir"],
        "path": row["path"],
        "rule": rule,
        "severity": severity,
        "message": message,
    }


def _tracknum(value):
    try:
        return int(str(value).split("/", 1)[0])
    except (ValueError, TypeError):
        return None


def validate(rows, config):
    issues = []
    albums = defaultdict(list)
    for row in rows:
        albums[row["album_dir"]].append(row)
        if row.get("error"):
            issues.append(issue(row, "read_error", row["error"], "error"))
            continue
        meta = row["metadata"]
        tags = meta.get("tags", {})
        for field in config.get("required_tags", []):
            if not str(tags.get(field, "")).strip():
                issues.append(issue(row, "missing_" + field, f"Missing required tag: {field}"))
        artwork = meta.get("artwork", [])
        ac = config.get("artwork", {})
        if ac.get("required") and not artwork:
            issues.append(issue(row, "missing_artwork", "No embedded cover art"))
        for pic in artwork:
            for dimension, minimum in [
                ("width", ac.get("min_width")),
                ("height", ac.get("min_height")),
            ]:
                if minimum and pic.get(dimension) is not None and pic[dimension] < minimum:
                    issues.append(
                        issue(
                            row, "small_artwork", f"Cover {dimension} {pic[dimension]} < {minimum}"
                        )
                    )
        filename = Path(row["path"]).stem
        fc = config.get("filename", {})
        if fc.get("regex") and not re.fullmatch(fc["regex"], filename):
            issues.append(
                issue(row, "filename_pattern", f"Filename does not match regex: {filename}")
            )
        if fc.get("match_template"):
            try:
                vars = dict(tags)
                vars["tracknumber"] = _tracknum(tags.get("tracknumber"))
                expected = fc["match_template"].format_map(vars)
                if expected != filename:
                    issues.append(
                        issue(
                            row, "filename_mismatch", f"Expected '{expected}', found '{filename}'"
                        )
                    )
            except (KeyError, ValueError, TypeError, AttributeError, IndexError) as exc:
                issues.append(
                    issue(row, "filename_template_error", f"Cannot render filename template: {exc}")
                )
        for rule in config.get("custom_rules", []):
            try:
                actual = str(tags.get(rule["field"], ""))
                expected = rule["equals_template"].format_map(tags)
                if actual != expected:
                    issues.append(
                        issue(
                            row,
                            rule["id"],
                            f"Expected '{expected}', found '{actual}'",
                            rule.get("severity", "warning"),
                        )
                    )
            except (KeyError, ValueError, TypeError, AttributeError, IndexError) as exc:
                issues.append(
                    issue(
                        row, rule.get("id", "custom_rule"), f"Rule evaluation error: {exc}", "error"
                    )
                )
    ar = config.get("album_rules", {})
    for tracks in albums.values():
        good = [r for r in tracks if not r.get("error")]
        if ar.get("disc_number_mismatch", True):
            for row in good:
                tagged = _tracknum(row["metadata"].get("tags", {}).get("discnumber"))
                folder = row.get("disc_from_path")
                if tagged is not None and folder is not None and tagged != folder:
                    issues.append(
                        issue(
                            row,
                            "disc_number_mismatch",
                            f"Disc tag {tagged} disagrees with directory disc {folder}",
                        )
                    )
        for field, enabled in [
            ("album", ar.get("consistent_album")),
            ("albumartist", ar.get("consistent_albumartist")),
        ]:
            if enabled:
                values = {
                    r["metadata"]["tags"].get(field, "").strip()
                    for r in good
                    if r["metadata"]["tags"].get(field, "").strip()
                }
                if len(values) > 1:
                    issues.append(
                        issue(
                            tracks[0],
                            "inconsistent_" + field,
                            f"Multiple {field} values in album: {sorted(values)}",
                        )
                    )
        if ar.get("unique_disc_track"):
            seen = {}
            for row in good:
                tags = row["metadata"]["tags"]
                num = _tracknum(tags.get("tracknumber"))
                disc = _tracknum(tags.get("discnumber")) or row.get("disc_from_path") or 1
                if num is None:
                    continue
                key = (disc, num)
                if key in seen:
                    issues.append(
                        issue(
                            row,
                            "duplicate_track",
                            f"Duplicate disc/track {disc}/{num}; also {seen[key]}",
                        )
                    )
                else:
                    seen[key] = row["path"]
        if ar.get("consistent_artwork"):
            fingerprints = {
                pic["sha256"] for row in good for pic in row["metadata"].get("artwork", [])
            }
            if len(fingerprints) > 1:
                issues.append(
                    issue(
                        tracks[0],
                        "inconsistent_artwork",
                        "Different embedded cover images within album",
                    )
                )
    return sorted(issues, key=lambda x: (x["album"], x["path"], x["rule"]))
