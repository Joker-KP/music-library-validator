"""Mutagen-based normalization for ID3 MP3 and Vorbis-comment FLAC."""
import hashlib
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError


def _first(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def read_metadata(path: Path) -> dict:
    suffix = path.suffix.lower()
    tags: dict[str, str] = {}
    art: list[dict] = []
    if suffix == ".flac":
        audio = FLAC(path)
        for key, value in audio.tags.items() if audio.tags else []:
            tags[key.lower()] = _first(value)
        for pic in audio.pictures:
            art.append({"width": pic.width, "height": pic.height, "sha256": hashlib.sha256(pic.data).hexdigest()})
    elif suffix == ".mp3":
        try:
            audio = ID3(path)
        except ID3NoHeaderError:
            audio = ID3()
        frames = {"TIT2": "title", "TPE1": "artist", "TALB": "album", "TPE2": "albumartist", "TRCK": "tracknumber", "TPOS": "discnumber", "TDRC": "date", "TYER": "date"}
        for fid, dest in frames.items():
            f = audio.get(fid)
            if f is not None:
                tags[dest] = _first(getattr(f, "text", []))
        comments = audio.getall("COMM")
        if comments:
            tags["comment"] = _first(comments[0].text)
        for pic in audio.getall("APIC"):
            # ID3 APIC doesn't carry dimensions. Pillow can optionally enrich these later.
            art.append({"width": None, "height": None, "sha256": hashlib.sha256(pic.data).hexdigest()})
    else:
        raise ValueError(f"Unsupported format: {suffix}")
    # FLAC sometimes names these with underscores or spaces.
    aliases = {"track": "tracknumber", "track_number": "tracknumber", "album_artist": "albumartist", "disc": "discnumber", "disc_number": "discnumber", "description": "comment"}
    for old, new in aliases.items():
        if old in tags and new not in tags:
            tags[new] = tags[old]
    return {"tags": tags, "artwork": art, "format": suffix.lstrip(".")}
