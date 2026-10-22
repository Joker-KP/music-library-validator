"""Cross-album field comparisons over normalized, identity-sorted metadata."""

from itertools import groupby

from .validation import issue

DEFAULT_FIELDS = ("composer", "lyricist", "origlyricist")


def artist_aliases(groups):
    """Use each configured group's first name as its matching identity only."""
    return {name.strip(): group[0].strip() for group in groups for name in group}


def credit_candidate(row, aliases, fields=DEFAULT_FIELDS):
    """Project readable, identified tracks without changing their cached tags."""
    if row.get("error"):
        return None
    tags = row["metadata"].get("tags", {})
    title, artist = tags.get("title", "").strip(), tags.get("artist", "").strip()
    if not title or not artist:
        return None
    return {
        "title": title,
        "artist": aliases.get(artist, artist),
        "album_dir": row["album_dir"],
        "path": row["path"],
        "values": {field: tags.get(field, "").strip() for field in fields},
    }


def validate_cross_album(rows, severity="warning", fields=DEFAULT_FIELDS):
    """Compare one identity at a time; input is sorted by title and artist."""
    for (title, artist), matches in groupby(rows, key=lambda row: (row["title"], row["artist"])):
        tracks = list(matches)
        if len({row["album_dir"] for row in tracks}) < 2:
            continue
        for field in fields:
            references = {}
            for row in tracks:
                references.setdefault(row["values"][field], row["path"])
            if len(references) < 2:
                continue
            details = "\n".join(
                f"- {value!r} at {references[value]}"
                if value
                else f"- <missing> at {references[value]}"
                for value in sorted(references)
            )
            for row in tracks:
                value = row["values"][field]
                actual = repr(value) if value else "<missing>"
                yield issue(
                    row,
                    "cross_album_" + field,
                    f"Conflicting {field} for title {title!r}, artist {artist!r}\n"
                    f"This track: {actual}\nValues across albums:\n{details}",
                    severity,
                )
