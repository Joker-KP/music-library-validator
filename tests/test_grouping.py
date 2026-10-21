from pathlib import Path
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
