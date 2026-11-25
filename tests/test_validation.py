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


def numbered_track(number, *, disc=None, folder_disc=None, album="A", name=None):
    tags = {"tracknumber": number}
    if disc is not None:
        tags["discnumber"] = disc
    return {
        "album_dir": f"/music/{album}",
        "path": f"/music/{album}/CD{folder_disc or 1}/{name or number} - Song.flac",
        "metadata": {"tags": tags},
        "disc_from_path": folder_disc,
    }


SEQUENCE_CONFIG = {
    "filename": {"allowed_chars": ""},
    "album_rules": {"consecutive_track_numbers": True},
}


@pytest.mark.parametrize("numbers", [["01"], ["03", "01", "02"], ["1/12", "02/12", "3/12"]])
def test_consecutive_track_numbers_accepts_sorted_and_unsorted_sequences(numbers):
    assert validate([numbered_track(n) for n in numbers], SEQUENCE_CONFIG) == []


@pytest.mark.parametrize(
    ("numbers", "missing"),
    [
        (["01", "02", "04"], "03"),
        (["02", "03"], "01"),
        (["04", "01", "07"], "02-03, 05-06"),
        (["01", "1000000000000"], "02-999999999999"),
    ],
)
def test_consecutive_track_numbers_reports_compact_missing_ranges(numbers, missing):
    rows = [numbered_track(n) for n in numbers]
    issues = validate(rows, SEQUENCE_CONFIG)
    assert len(issues) == 1
    found = issues[0]
    assert found["rule"] == "track_number_gap"
    assert found["severity"] == "error"
    assert found["album"] == "/music/A"
    assert f"Disc 1: missing track numbers {missing} (" in found["message"]
    assert validate(list(reversed(rows)), SEQUENCE_CONFIG) == issues


def test_consecutive_track_numbers_is_opt_in_and_can_be_disabled():
    rows = [numbered_track("02")]
    for album_rules in ({}, {"consecutive_track_numbers": False}):
        assert validate(rows, {**SEQUENCE_CONFIG, "album_rules": album_rules}) == []


def test_consecutive_track_numbers_checks_each_disc_and_album_independently():
    rows = [
        numbered_track("01", folder_disc=1),
        numbered_track("02", folder_disc=1),
        numbered_track("01", folder_disc=2),
        numbered_track("03", folder_disc=2),
        numbered_track("01", album="B"),
        numbered_track("02", album="B"),
    ]
    issues = validate(rows, SEQUENCE_CONFIG)
    assert len(issues) == 1
    assert issues[0]["album"] == "/music/A"
    assert "Disc 2: missing track numbers 02 (" in issues[0]["message"]


def test_consecutive_track_numbers_uses_disc_tags_before_folder_numbers():
    rows = [
        numbered_track("01", disc="1/2", folder_disc=1),
        numbered_track("01", disc="2/2", folder_disc=1),
        numbered_track("02", disc="2/2", folder_disc=1),
    ]
    config = {
        **SEQUENCE_CONFIG,
        "album_rules": {"consecutive_track_numbers": True, "disc_number_mismatch": False},
    }
    assert validate(rows, config) == []


@pytest.mark.parametrize("number", [None, "", " ", "bad", "0", "-1"])
def test_consecutive_track_numbers_reports_unusable_numbers(number):
    row = numbered_track(number)
    if number is None:
        del row["metadata"]["tags"]["tracknumber"]
    issues = validate([row], SEQUENCE_CONFIG)
    assert len(issues) == 1
    assert issues[0]["rule"] == "invalid_tracknumber"
    assert issues[0]["severity"] == "error"


def test_consecutive_track_numbers_keeps_duplicate_and_read_errors_separate():
    rows = [numbered_track("01", name="first"), numbered_track("01", name="copy")]
    unreadable = numbered_track("02")
    unreadable["error"] = "Cannot read tags"
    rows.append(unreadable)
    rows.append(numbered_track("03"))
    config = {
        **SEQUENCE_CONFIG,
        "album_rules": {"consecutive_track_numbers": True, "unique_disc_track": True},
    }
    issues = validate(rows, config)
    assert {i["rule"] for i in issues} == {"duplicate_track", "read_error", "track_number_gap"}
    assert "missing track numbers 02 (" in next(
        i["message"] for i in issues if i["rule"] == "track_number_gap"
    )


SHORT_ALBUM_CONFIG = {
    "album_rules": {"short_album_requires_ep": True},
}


@pytest.mark.parametrize("count", [1, 6, 7, 8])
def test_short_album_requires_ep_only_below_seven_tracks(count):
    rows = [numbered_track(str(n)) for n in range(1, count + 1)]
    issues = validate(rows, SHORT_ALBUM_CONFIG)
    assert len(issues) == (count if count < 7 else 0)
    assert all(i["rule"] == "short_album_requires_ep" for i in issues)
    assert all(i["severity"] == "warning" for i in issues)
    assert all(f"Album has {count} tracks (< 7)" in i["message"] for i in issues)


@pytest.mark.parametrize("value", [None, "", " ", "album", "EP", "ep; live"])
def test_short_album_requires_exact_ep_on_every_readable_track(value):
    rows = [numbered_track("01"), numbered_track("02")]
    rows[0]["metadata"]["tags"]["musicbrainz_albumtype"] = "ep"
    if value is not None:
        rows[1]["metadata"]["tags"]["musicbrainz_albumtype"] = value
    issues = validate(rows, SHORT_ALBUM_CONFIG)
    assert len(issues) == 1
    assert issues[0]["path"] == rows[1]["path"]
    assert "MUSICBRAINZ_ALBUMTYPE='ep'" in issues[0]["message"]


def test_short_album_ep_passes_and_rule_is_opt_in():
    row = numbered_track("01")
    assert validate([row], {}) == []
    assert validate([row], {"album_rules": {"short_album_requires_ep": False}}) == []
    row["metadata"]["tags"]["musicbrainz_albumtype"] = "ep"
    assert validate([row], SHORT_ALBUM_CONFIG) == []
    assert validate([], SHORT_ALBUM_CONFIG) == []


def test_short_album_counts_all_discs_together_and_keeps_albums_separate():
    rows = [numbered_track(str(n), folder_disc=1) for n in range(1, 4)]
    rows += [numbered_track(str(n), folder_disc=2) for n in range(1, 5)]
    rows.append(numbered_track("01", album="B"))
    issues = validate(rows, SHORT_ALBUM_CONFIG)
    assert len(issues) == 1
    assert issues[0]["album"] == "/music/B"
    rows = [r for r in rows if r["album_dir"] == "/music/A"][:-1]
    assert len(validate(rows, SHORT_ALBUM_CONFIG)) == 6


def test_short_album_counts_unreadable_files_but_retains_read_errors():
    rows = [numbered_track(str(n)) for n in range(1, 8)]
    rows[-1]["error"] = "Cannot read metadata"
    rows[-1]["metadata"] = None
    assert [i["rule"] for i in validate(rows, SHORT_ALBUM_CONFIG)] == ["read_error"]
    issues = validate(rows[-2:], SHORT_ALBUM_CONFIG)
    assert {i["rule"] for i in issues} == {"read_error", "short_album_requires_ep"}
    assert "Album has 2 tracks (< 7)" in next(
        i["message"] for i in issues if i["rule"] == "short_album_requires_ep"
    )
