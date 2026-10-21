import pytest

from music_validator.catalog import summarize_album


def track(format="flac", bitrate=None, tags=None, error=None):
    return {
        "album_dir": "/music/Artist/Album",
        "metadata": {"format": format, "bitrate": bitrate, "tags": tags or {}},
        "error": error,
    }


@pytest.mark.parametrize(
    "tracks,quality",
    [
        ([track()], "FLAC"),
        ([track("mp3", 320000)], "MP3 320"),
        ([track(), track("mp3", 320000), track("mp3", 128000)], "MP3 128"),
        ([track("mp3", 127997)], "MP3 128"),
        ([track(), track("mp3")], "MP3 (unknown bitrate)"),
        ([track(), track(error="unreadable")], "Unknown"),
    ],
)
def test_worst_album_quality(tracks, quality):
    result = summarize_album(tracks, 1234567)
    assert result["quality"] == quality
    assert result["total_tracks"] == len(tracks)
    assert result["folder_size_mb"] == 1.23
    assert result["album_dir"] == "/music/Artist/Album"


def test_catalog_preserves_conflicts_and_falls_back_for_missing_tags():
    rows = [
        track(tags={"albumartist": "Żółć", "artist": "Ignored", "year": "2002", "album": "B"}),
        track(tags={"artist": "Alice", "year": "2001", "album": "A"}),
        track(tags={"albumartist": " ", "year": ""}),
    ]
    result = summarize_album(rows, 0)
    assert result["album_artist"] == "Alice; Unknown; Żółć"
    assert result["year"] == "2001; 2002; Unknown"
    assert result["album"] == "A; Album; B"
