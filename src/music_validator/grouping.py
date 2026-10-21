"""Filesystem grouping: disc subfolders belong to their parent album."""
import re
from pathlib import Path

DEFAULT_DISC_PATTERN = r"^(?:cd|disc|disk)[ _.-]*0*[1-9][0-9]*$"


def album_directory(track: Path, root: Path, pattern: str = DEFAULT_DISC_PATTERN) -> Path:
    """Return the album directory, grouping nested disc folders at any depth."""
    directory = track.parent
    while directory != root and re.fullmatch(pattern, directory.name, re.IGNORECASE):
        directory = directory.parent
    return directory


def disc_number(track: Path, root: Path, pattern: str = DEFAULT_DISC_PATTERN) -> int | None:
    """Use closest disc directory to track; prefer explicit discnumber tag later."""
    directory = track.parent
    while directory != root:
        if re.fullmatch(pattern, directory.name, re.IGNORECASE):
            return int(re.search(r"(\d+)$", directory.name).group(1))
        directory = directory.parent
    return None
