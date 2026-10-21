import pytest

from music_validator.characters import DEFAULT_ALLOWED_CHARS, allowed_characters
from music_validator.validation import validate

CONFIG = {
    "required_tags": ["title", "artist", "album", "tracknumber", "comment"],
    "artwork": {"required": True},
    "filename": {"regex": r"^\d{2} - .+$"},
    "album_rules": {"consistent_album": True, "unique_disc_track": True},
}


def test_missing_fields_and_cover():
    row = {
        "album_dir": "/music/A",
        "path": "/music/A/wrong.mp3",
        "metadata": {"tags": {"artist": "A"}, "artwork": []},
        "error": None,
        "disc_from_path": None,
    }
    rules = {i["rule"] for i in validate([row], CONFIG)}
    assert {
        "missing_title",
        "missing_album",
        "missing_tracknumber",
        "missing_comment",
        "missing_artwork",
        "filename_pattern",
    } <= rules


def test_disc_numbers_allow_repeated_track_numbers():
    rows = []
    for disc in (1, 2):
        rows.append(
            {
                "album_dir": "/music/A",
                "path": f"/music/A/CD{disc}/01 - Song.flac",
                "metadata": {
                    "tags": {
                        "title": "Song",
                        "artist": "A",
                        "album": "Album",
                        "tracknumber": "1",
                        "comment": "ok",
                    },
                    "artwork": [{"sha256": "x", "width": 600, "height": 600}],
                },
                "error": None,
                "disc_from_path": disc,
            }
        )
    assert not any(x["rule"] == "duplicate_track" for x in validate(rows, CONFIG))


def test_disc_tag_directory_mismatch_and_opt_out():
    row = {
        "album_dir": "/music/A",
        "path": "/music/A/CD2/01.flac",
        "metadata": {"tags": {"discnumber": "1/2", "tracknumber": "1"}, "artwork": []},
        "error": None,
        "disc_from_path": 2,
    }
    assert any(i["rule"] == "disc_number_mismatch" for i in validate([row], CONFIG))
    config = {**CONFIG, "album_rules": {"disc_number_mismatch": False}}
    assert not any(i["rule"] == "disc_number_mismatch" for i in validate([row], config))


def filename_row(name):
    return {
        "album_dir": "/music/A",
        "path": "/music/A/" + name,
        "metadata": {"tags": {}, "artwork": []},
        "error": None,
    }


def test_default_allowlist_is_exact():
    import string

    assert allowed_characters(DEFAULT_ALLOWED_CHARS) == frozenset(
        string.ascii_letters + string.digits + "[] -()"
    )


def test_custom_ranges_and_literal_regex_metacharacters():
    config = {"filename": {"allowed_chars": "a-c2-4^.*-"}}
    assert not validate([filename_row("ab34^.*-.flac")], config)
    assert any(
        i["rule"] == "filename_allowed_chars" for i in validate([filename_row("d5.mp3")], config)
    )


@pytest.mark.parametrize("name", ["01 - Song [Live] (Mix).mp3", "02 - SONG123.FLAC"])
def test_default_allowed_characters_pass(name):
    assert not any(
        i["rule"] == "filename_allowed_chars" for i in validate([filename_row(name)], {})
    )


@pytest.mark.parametrize(
    "character",
    ["!", '"', "#", "$", "%", "&", "'", "*", "+", ",", ".", ":", ";", "_", "Ł", "\t", "\n"],
)
def test_default_allowed_characters_reject_exactly_other_chars(character):
    result = validate([filename_row("01 - Song" + character + ".flac")], {})
    found = [i for i in result if i["rule"] == "filename_allowed_chars"]
    assert len(found) == 1
    assert f"U+{ord(character):04X}" in found[0]["message"]


@pytest.mark.parametrize("allowed", ["", None])
def test_empty_allowed_characters_allows_unicode_and_punctuation(allowed):
    config = {"filename": {"allowed_chars": allowed}}
    result = validate([filename_row("01 - Łódź_世界!.mp3")], config)
    assert not any(i["rule"] == "filename_allowed_chars" for i in result)


def test_literal_punctuation_and_custom_unicode():
    config = {"filename": {"allowed_chars": "0-9a-zA-Z -[]()Łóź_"}}
    assert not validate([filename_row("01 - Łóź_[Mix] (A).flac")], config)


def test_whitespace_only_allowlist_does_not_disable_rule():
    assert validate([filename_row("Song.mp3")], {"filename": {"allowed_chars": " "}})


def test_allowlist_diagnostic_is_unique_and_deterministic():
    result = validate([filename_row("01 - Song!!_!!.mp3")], {})
    issue = next(i for i in result if i["rule"] == "filename_allowed_chars")
    assert issue["message"].count("U+0021") == 1
    assert issue["message"].index("U+0021") < issue["message"].index("U+005F")
