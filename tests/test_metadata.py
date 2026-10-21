import hashlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen.flac import Picture
from mutagen.id3 import ID3, TCMP, TDRC, TEXT, TOLY, TYER
from PIL import Image

from music_validator import metadata
from music_validator.validation import validate


def test_mp3_stream_bitrate_is_normalized_without_changing_file():
    path = Path(__file__).parent / "fixtures/silence.mp3"
    before = path.read_bytes()
    with path.open("rb") as source:
        result = metadata.read_metadata(path, fileobj=source)
    assert round(result["bitrate"] / 1000) == 64
    assert path.read_bytes() == before


def test_id3_only_file_keeps_tags_when_bitrate_is_unavailable():
    source = BytesIO()
    ID3().save(source)
    source.seek(0)
    result = metadata.read_metadata(Path("01 - Song.mp3"), fileobj=source)
    assert result["bitrate"] is None
    assert result["format"] == "mp3"


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


def test_mp3_lyricist_original_lyricist_and_compilation_normalization():
    tags = ID3()
    tags.add(TEXT(encoding=3, text=["Żaneta Nowak"]))
    tags.add(TOLY(encoding=3, text=["Jan Łódzki"]))
    tags.add(TCMP(encoding=3, text=["1"]))
    source = BytesIO()
    tags.save(source)
    source.seek(0)
    result = metadata.read_metadata(Path("01 - Song.mp3"), fileobj=source)
    assert result["tags"]["lyricist"] == "Żaneta Nowak"
    assert result["tags"]["origlyricist"] == "Jan Łódzki"
    assert result["tags"]["compilation"] == "1"


@pytest.mark.parametrize("key", ["origlyricist", "originallyricist", "original_lyricist"])
def test_flac_lyricist_original_lyricist_and_compilation_normalization(monkeypatch, key):
    tags = {"LYRICIST": ["Żaneta Nowak"], key: ["Jan Łódzki"], "COMPILATION": ["yes"]}
    monkeypatch.setattr(metadata, "FLAC", lambda source: SimpleNamespace(tags=tags, pictures=[]))
    result = metadata.read_metadata(Path("01 - Song.flac"))
    assert result["tags"]["lyricist"] == "Żaneta Nowak"
    assert result["tags"]["origlyricist"] == "Jan Łódzki"
    assert result["tags"]["compilation"] == "yes"


@pytest.mark.parametrize("date", ["1999", "2001-02-03", "2001-02-03T12:34:56"])
def test_mp3_derives_year_preserving_date(date):
    tags = ID3()
    frame = TDRC(encoding=3, text=[date])
    expected_date = str(frame.text[0])
    tags.add(frame)
    source = BytesIO()
    tags.save(source)
    source.seek(0)
    result = metadata.read_metadata(Path("01 - Song.mp3"), fileobj=source)
    assert result["tags"]["year"] == date[:4]
    assert result["tags"]["date"] == expected_date


def test_mp3_legacy_tyer_exposes_year():
    tags = ID3()
    tags.add(TYER(encoding=3, text=["1999"]))
    source = BytesIO()
    tags.save(source, v2_version=3)
    source.seek(0)
    result = metadata.read_metadata(Path("01 - Song.mp3"), fileobj=source)
    assert result["tags"]["year"] == result["tags"]["date"] == "1999"


@pytest.mark.parametrize("tags,expected", [
    ({"DATE": ["1999"]}, {"date": "1999", "year": "1999"}),
    ({"DATE": ["2001-02-03"]}, {"date": "2001-02-03", "year": "2001"}),
    ({"DATE": [" 2001-02-03 "]}, {"date": " 2001-02-03 ", "year": "2001"}),
    ({"DATE": ["2001/02/03"]}, {"date": "2001/02/03", "year": "2001"}),
    ({"DATE": ["2001.02.03"]}, {"date": "2001.02.03", "year": "2001"}),
    ({"YEAR": ["2000"]}, {"date": "2000", "year": "2000"}),
    ({"YEAR": ["2000"], "DATE": [""]}, {"date": "2000", "year": "2000"}),
    ({"YEAR": [""], "DATE": ["2001-02-03"]}, {"date": "2001-02-03", "year": "2001"}),
    ({"YEAR": ["2000"], "DATE": ["2001-02-03"]}, {"date": "2001-02-03", "year": "2000"}),
])
def test_flac_normalizes_year_and_date(monkeypatch, tags, expected):
    monkeypatch.setattr(metadata, "FLAC", lambda source: SimpleNamespace(tags=tags, pictures=[]))
    result = metadata.read_metadata(Path("01 - Song.flac"))
    assert {key: result["tags"][key] for key in ("year", "date")} == expected


@pytest.mark.parametrize("date", ["", "unknown", "19999", "1999junk", "recorded in 1999"])
def test_unrecognized_date_does_not_invent_year(monkeypatch, date):
    monkeypatch.setattr(metadata, "FLAC", lambda source: SimpleNamespace(tags={"date": [date]}, pictures=[]))
    result = metadata.read_metadata(Path("01 - Song.flac"))
    assert not result["tags"].get("year")
    row = {"album_dir": "/music/A", "path": "/music/A/01 - Song.flac", "metadata": result}
    assert any(i["rule"] == "missing_year" for i in validate([row], {"required_tags": ["year"]}))
