from pathlib import Path

import pytest

from music_validator.grouping import album_directory, disc_number

ROOT = Path("/music")


def test_multiple_disc_patterns():
    for child, number in [("CD1", 1), ("CD 02", 2), ("Disc-3", 3), ("Disk_04", 4)]:
        f = ROOT / "Artist" / "Album" / child / "01 - Song.flac"
        assert album_directory(f, ROOT) == ROOT / "Artist" / "Album"
        assert disc_number(f, ROOT) == number


def test_regular_album_directory():
    f = ROOT / "Artist" / "Album" / "01 - Song.mp3"
    assert album_directory(f, ROOT) == f.parent
    assert disc_number(f, ROOT) is None


def test_non_disc_folder_not_collapsed():
    f = ROOT / "Artist" / "Album" / "Bonus" / "01 - Song.flac"
    assert album_directory(f, ROOT) == f.parent


@pytest.mark.parametrize("folder,number", [("cD1", 1), ("DISK_003", 3), ("Disc.2", 2)])
def test_case_and_separator_variants(folder, number):
    track = ROOT / "艺术家" / "Żółć" / folder / "01.flac"
    assert album_directory(track, ROOT) == ROOT / "艺术家" / "Żółć"
    assert disc_number(track, ROOT) == number


def test_nested_disc_folders_and_root_tracks():
    track = ROOT / "Album" / "CD1" / "CD2" / "01.mp3"
    assert album_directory(track, ROOT) == ROOT / "Album"
    assert disc_number(track, ROOT) == 2
    assert album_directory(ROOT / "01.mp3", ROOT) == ROOT
    assert disc_number(ROOT / "01.mp3", ROOT) is None


def test_named_disc_capture_with_total():
    track = ROOT / "Album" / "CD 1 of 2" / "01.flac"
    pattern = r"^CD (?P<disc>\d+) of \d+$"
    assert disc_number(track, ROOT, pattern) == 1
    assert album_directory(track, ROOT, pattern) == ROOT / "Album"


@pytest.mark.parametrize(
    "name,pattern",
    [
        ("CD 1 of 2", r"^CD \d+ of \d+$"),
        ("Disc A", r"^Disc [A-Z]$"),
        ("CD0", r"^CD(?P<disc>\d+)$"),
    ],
)
def test_invalid_disc_extraction_is_a_clear_error(name, pattern):
    with pytest.raises(ValueError, match="disc"):
        disc_number(ROOT / "Album" / name / "01.flac", ROOT, pattern)


@pytest.mark.parametrize("track", [Path("/elsewhere/01.flac"), Path("/music/../elsewhere/01.flac")])
def test_outside_root_does_not_loop(track):
    with pytest.raises(ValueError):
        disc_number(track, ROOT)
