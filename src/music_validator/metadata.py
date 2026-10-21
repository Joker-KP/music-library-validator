"""Mutagen-based normalization for ID3 MP3 and Vorbis-comment FLAC."""

import hashlib
import re
import warnings
from io import BytesIO
from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from PIL import Image


def _first(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def normalize_date_tags(tags: dict[str, str]) -> None:
    """Expose date/year consistently, including metadata from existing caches."""
    year = tags.get("year", "").strip()
    date = tags.get("date", "").strip()
    if year:
        if not date:
            tags["date"] = tags["year"]
    elif date:
        match = re.match(r"^([0-9]{4})(?:$|[-/.Tt\s])", date)
        if match:
            tags["year"] = match[1]


def _flac_artwork(data):
    # FLAC PICTURE dimensions may be zero or stale. Read the encoded image
    # header itself, without loading pixels or modifying the PICTURE block.
    artwork = {"width": None, "height": None, "sha256": hashlib.sha256(data).hexdigest()}
    try:
        with warnings.catch_warnings(), BytesIO(data) as source:
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError("Image dimensions must be positive")
                artwork.update(width=width, height=height)
    except (
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        # Do not include BytesIO reprs: their memory addresses are nondeterministic.
        artwork["dimension_error"] = f"Cannot read embedded image dimensions ({type(exc).__name__})"
    return artwork


def read_metadata(path: Path, fileobj=None) -> dict:
    suffix = path.suffix.lower()
    source = fileobj if fileobj is not None else path
    tags: dict[str, str] = {}
    art: list[dict] = []
    bitrate = None
    if suffix == ".flac":
        audio = FLAC(source)
        for key, value in audio.tags.items() if audio.tags else []:
            tags[key.lower()] = _first(value)
        for pic in audio.pictures:
            art.append(_flac_artwork(pic.data))
    elif suffix == ".mp3":
        try:
            audio = ID3(source)
        except ID3NoHeaderError:
            audio = ID3()
        frames = {
            "TIT2": "title",
            "TPE1": "artist",
            "TALB": "album",
            "TPE2": "albumartist",
            "TRCK": "tracknumber",
            "TPOS": "discnumber",
            "TDRC": "date",
            "TYER": "date",
            "TEXT": "lyricist",
            "TOLY": "origlyricist",
            "TCMP": "compilation",
        }
        for fid, dest in frames.items():
            f = audio.get(fid)
            if f is not None:
                tags[dest] = _first(getattr(f, "text", []))
        comments = audio.getall("COMM")
        if comments:
            tags["comment"] = _first(comments[0].text)
        for pic in audio.getall("APIC"):
            # ID3 APIC doesn't carry dimensions. Pillow can optionally enrich these later.
            art.append(
                {"width": None, "height": None, "sha256": hashlib.sha256(pic.data).hexdigest()}
            )
        # Stream headers provide bitrate, not a full decoding/integrity check.
        # Keep usable ID3 tags even for tag-only files or unreadable MPEG headers.
        try:
            if fileobj is not None:
                fileobj.seek(0)
            bitrate = MP3(source).info.bitrate
        except (MutagenError, ValueError):
            pass
    else:
        raise ValueError(f"Unsupported format: {suffix}")
    # FLAC sometimes names these with underscores or spaces.
    aliases = {
        "track": "tracknumber",
        "track_number": "tracknumber",
        "album_artist": "albumartist",
        "disc": "discnumber",
        "disc_number": "discnumber",
        "description": "comment",
        "originallyricist": "origlyricist",
        "original_lyricist": "origlyricist",
    }
    for old, new in aliases.items():
        if old in tags and new not in tags:
            tags[new] = tags[old]
    normalize_date_tags(tags)
    return {"tags": tags, "artwork": art, "format": suffix.lstrip("."), "bitrate": bitrate}
