import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen.flac import Picture
from PIL import Image

from music_validator import metadata
from music_validator.validation import validate


def image_bytes(size, format="PNG"):
    output = BytesIO()
    Image.new("RGB", size, color="navy").save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize("format", ["JPEG", "PNG"])
@pytest.mark.parametrize(
    "size,declared",
    [
        ((600, 600), (0, 0)),
        ((800, 800), (0, 0)),
        ((600, 800), (600, 0)),
        ((800, 600), (0, 600)),
        ((800, 800), (100, 100)),
        ((480, 600), (0, 0)),
        ((600, 480), (0, 0)),
        ((400, 300), (1000, 1000)),
        ((500, 500), (0, 0)),
    ],
)
def test_flac_uses_image_header_dimensions(monkeypatch, format, size, declared):
    picture = Picture()
    picture.data = image_bytes(size, format)
    picture.width, picture.height = declared
    original = picture.data
    audio = SimpleNamespace(tags={"title": ["Song"]}, pictures=[picture])
    monkeypatch.setattr(metadata, "FLAC", lambda source: audio)
    result = metadata.read_metadata(Path("01 - Song.flac"))
    artwork = result["artwork"][0]
    assert (artwork["width"], artwork["height"]) == size
    assert artwork["sha256"] == hashlib.sha256(original).hexdigest()
    assert picture.data == original
    assert (picture.width, picture.height) == declared
    row = {
        "path": "/music/Album/01 - Song.flac",
        "album_dir": "/music/Album",
        "metadata": result,
        "error": None,
    }
    issues = validate([row], {"artwork": {"min_width": 500, "min_height": 500}})
    small = [issue for issue in issues if issue["rule"] == "small_artwork"]
    assert len(small) == int(size[0] < 500) + int(size[1] < 500)
    assert not any(" 0 < " in issue["message"] for issue in issues)


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n"])
def test_unreadable_flac_image_is_unknown_not_zero(monkeypatch, data):
    picture = Picture()
    picture.data = data
    monkeypatch.setattr(
        metadata, "FLAC", lambda source: SimpleNamespace(tags={}, pictures=[picture])
    )
    result = metadata.read_metadata(Path("01 - Song.flac"))
    assert result["artwork"][0]["width"] is None
    assert result["artwork"][0]["height"] is None
    assert result["artwork"][0]["dimension_error"]
    row = {
        "path": "/music/Album/01 - Song.flac",
        "album_dir": "/music/Album",
        "metadata": result,
        "error": None,
    }
    assert [
        i["rule"] for i in validate([row], {"artwork": {"min_width": 500, "min_height": 500}})
    ] == ["artwork_dimensions_unavailable"]
    assert not validate([row], {"artwork": {"min_width": 0, "min_height": None}})


@pytest.mark.parametrize("limit", [10, 60])
def test_large_image_header_is_handled_without_losing_tags(monkeypatch, limit):
    picture = Picture()
    picture.data = image_bytes((10, 10))
    monkeypatch.setattr(
        metadata,
        "FLAC",
        lambda source: SimpleNamespace(tags={"title": ["Song"]}, pictures=[picture]),
    )
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", limit)
    result = metadata.read_metadata(Path("01 - Song.flac"))
    assert result["tags"]["title"] == "Song"
    assert result["artwork"][0]["width"] is None
    assert result["artwork"][0]["dimension_error"]


def test_flac_reads_image_header_without_loading_pixels(monkeypatch):
    picture = Picture()
    picture.data = image_bytes((800, 800), "JPEG")
    monkeypatch.setattr(
        metadata, "FLAC", lambda source: SimpleNamespace(tags={}, pictures=[picture])
    )

    def no_pixel_load(*args, **kwargs):
        raise AssertionError("Artwork dimension checks must not load pixels")

    monkeypatch.setattr(Image.Image, "load", no_pixel_load)
    result = metadata.read_metadata(Path("01 - Song.flac"))
    assert result["artwork"][0]["width"] == result["artwork"][0]["height"] == 800
