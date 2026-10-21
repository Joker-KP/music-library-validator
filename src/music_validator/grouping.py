"""Filesystem grouping: disc subfolders belong to their parent album."""

import re
from pathlib import Path

DEFAULT_DISC_PATTERN = r"^(?:cd|disc|disk)[ _.-]*0*[1-9][0-9]*$"


def _disc(name, pattern):
    match = re.fullmatch(pattern, name, re.IGNORECASE)
    if not match:
        return None
    if "disc" in match.re.groupindex:
        value = match.group("disc")
    else:
        numbers = re.findall(r"\d+", name)
        value = numbers[0] if len(numbers) == 1 else None
    if value is None or not re.fullmatch(r"\d+", value) or int(value) < 1:
        raise ValueError(
            f"Cannot extract positive disc number from {name!r}; "
            "use a numeric (?P<disc>...) group in disc_folder_pattern"
        )
    return int(value)


def _check_root(track, root):
    if ".." in track.parts or ".." in root.parts or track == root or not track.is_relative_to(root):
        raise ValueError(f"Track {track} is outside library root {root}")


def album_directory(track: Path, root: Path, pattern: str = DEFAULT_DISC_PATTERN) -> Path:
    """Return the album directory, grouping nested disc folders at any depth."""
    _check_root(track, root)
    directory = track.parent
    while directory != root and _disc(directory.name, pattern) is not None:
        directory = directory.parent
    return directory


def disc_number(track: Path, root: Path, pattern: str = DEFAULT_DISC_PATTERN) -> int | None:
    """Use closest disc directory to track; prefer explicit discnumber tag later."""
    _check_root(track, root)
    directory = track.parent
    while directory != root:
        number = _disc(directory.name, pattern)
        if number is not None:
            return number
        directory = directory.parent
    return None
