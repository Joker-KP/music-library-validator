"""Album catalog aggregation using normalized metadata and discovered sizes."""

from pathlib import Path

CATALOG_COLUMNS = (
    ("album_artist", "Album artist"),
    ("year", "Year"),
    ("album", "Album"),
    ("quality", "Quality"),
    ("folder_size_mb", "Folder size (MB)"),
    ("total_tracks", "Total number of tracks"),
)


def _quality(row):
    metadata = row["metadata"]
    if row.get("error"):
        return (0, 0, "Unknown")
    format = metadata.get("format")
    if format == "flac":
        return (2, 0, "FLAC")
    if format == "mp3":
        bitrate = metadata.get("bitrate")
        if isinstance(bitrate, (int, float)) and bitrate > 0:
            kbps = round(bitrate / 1000)
            return (1, kbps, f"MP3 {kbps}")
        return (0, 1, "MP3 (unknown bitrate)")
    return (0, 0, "Unknown")


def summarize_album(rows: list[dict], folder_size: int) -> dict:
    """Keep distinct values visible rather than choosing an arbitrary track."""
    album_dir = rows[0]["album_dir"]
    artists, years, albums = set(), set(), set()
    for row in rows:
        tags = row["metadata"].get("tags", {})
        artists.add(
            tags.get("albumartist", "").strip() or tags.get("artist", "").strip() or "Unknown"
        )
        years.add(tags.get("year", "").strip() or "Unknown")
        albums.add(tags.get("album", "").strip() or Path(album_dir).name)
    return {
        "album_dir": album_dir,
        "album_artist": "; ".join(sorted(artists)),
        "year": "; ".join(sorted(years)),
        "album": "; ".join(sorted(albums)),
        "quality": min(_quality(row) for row in rows)[2],
        "folder_size_mb": round(folder_size / 1_000_000, 2),
        "total_tracks": len(rows),
    }
