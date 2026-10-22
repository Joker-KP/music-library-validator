from copy import deepcopy

import pytest

from music_validator.cross_album import (
    DEFAULT_FIELDS,
    artist_aliases,
    credit_candidate,
    validate_cross_album,
)


def track(album="A", name="01.flac", **tags):
    return {
        "album_dir": f"/music/{album}",
        "path": f"/music/{album}/{name}",
        "metadata": {"tags": {"title": "Łódź", "artist": "Artysta", "composer": "Anna", **tags}},
        "error": None,
    }


def check(rows, groups=(), severity="warning", fields=DEFAULT_FIELDS):
    aliases = artist_aliases(groups)
    candidates = [c for row in rows if (c := credit_candidate(row, aliases, fields)) is not None]
    candidates.sort(key=lambda row: (row["title"], row["artist"], row["album_dir"], row["path"]))
    return list(validate_cross_album(candidates, severity, fields))


@pytest.mark.parametrize("field", ["composer", "lyricist", "origlyricist"])
def test_each_credit_conflict_is_reported_for_every_copy(field):
    rows = [track(**{field: "Jan Łódzki"}), track("B", **{field: "Żaneta"})]
    found = check(rows)
    assert {i["path"] for i in found} == {row["path"] for row in rows}
    assert len(found) == 2
    for item in found:
        assert item["rule"] == "cross_album_" + field
        assert item["severity"] == "warning"
        assert "Jan Łódzki" in item["message"] and "Żaneta" in item["message"]
        assert all(row["path"] in item["message"] for row in rows)
        lines = item["message"].splitlines()
        assert lines[1].startswith("This track: ")
        assert lines[2] == "Values across albums:"
        assert lines[3:] == [
            "- 'Jan Łódzki' at /music/A/01.flac",
            "- 'Żaneta' at /music/B/01.flac",
        ]


def test_all_conflicting_fields_are_reported_even_for_majority_copies():
    rows = [
        track("A"),
        track("B"),
        track("C", composer="Other", lyricist="Jan", origlyricist="Anna"),
    ]
    found = check(rows, severity="info")
    assert len(found) == 9
    assert {i["rule"] for i in found} == {
        "cross_album_composer",
        "cross_album_lyricist",
        "cross_album_origlyricist",
    }
    assert all(i["severity"] == "info" for i in found)
    assert any("<missing>" in i["message"] for i in found)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_and_missing_credits_are_equal_but_differ_from_populated(blank):
    assert check([track(lyricist=blank), track("B")]) == []
    found = check([track(lyricist=blank), track("B", lyricist="Jan")])
    assert len(found) == 2
    assert all(i["rule"] == "cross_album_lyricist" for i in found)


def test_alias_groups_match_all_alternatives_without_changing_tags():
    groups = [
        ["Wolna Grupa Bukowina", "Wojciech Bellon"],
        ["U Pana Boga za Piecem", "Sylwek Szweda", "Towarzstwo Wzajemnej Adoracji"],
    ]
    rows = [
        track("A", artist="Wolna Grupa Bukowina"),
        track("B", artist="Wojciech Bellon", composer="Other"),
        track("C", artist="U Pana Boga za Piecem"),
        track("D", artist="Sylwek Szweda", composer="Other"),
        track("E", artist="Towarzstwo Wzajemnej Adoracji"),
    ]
    before = deepcopy(rows)
    assert check(rows) == []
    assert len(check(rows, groups)) == 5
    assert rows == before


def test_whitespace_is_trimmed_but_identity_and_credit_case_remain_significant():
    assert check([track(), track("B", title=" Łódź ", artist=" Artysta ", composer=" Anna ")]) == []
    assert check([track(), track("B", title="łódź", composer="Other")]) == []
    assert check([track(), track("B", artist="artysta", composer="Other")]) == []
    assert len(check([track(), track("B", composer="anna")])) == 2
    assert (
        len(
            check(
                [track(artist="First"), track("B", artist=" Second ", composer="Other")],
                [[" First ", "Second"]],
            )
        )
        == 2
    )


def test_single_album_different_keys_and_missing_identity_are_excluded():
    assert check([track(), track(name="02.mp3", composer="Other")]) == []
    assert (
        check([track(title="AB", artist="C"), track("B", title="A", artist="BC", composer="Other")])
        == []
    )
    for field in ("title", "artist"):
        rows = [track(**{field: ""}), track("B", composer="Other", **{field: "  "})]
        assert check(rows) == []
    unreadable = track("B", composer="Other")
    unreadable["error"] = "unreadable"
    assert check([track(), unreadable]) == []


def test_matching_copies_and_empty_input_produce_no_issues():
    assert check([]) == []
    assert check([track(), track("B")]) == []


def test_configured_fields_replace_defaults_and_empty_list_disables_comparisons():
    rows = [track(genre="Folk"), track("B", composer="Other", genre="Rock")]
    assert check(rows, fields=["lyricist"]) == []
    assert check(rows, fields=[]) == []
    found = check(rows, fields=["genre"])
    assert len(found) == 2
    assert all(i["rule"] == "cross_album_genre" for i in found)
    assert all("Folk" in i["message"] and "Rock" in i["message"] for i in found)


@pytest.mark.parametrize("field", ["path", "album_dir", "values", "values_json"])
def test_compared_tag_names_do_not_overwrite_identity_or_reference_fields(field):
    rows = [track(**{field: "First"}), track("B", **{field: "Second"})]
    found = check(rows, fields=[field])
    assert len(found) == 2
    assert {i["path"] for i in found} == {row["path"] for row in rows}
    assert {i["album"] for i in found} == {row["album_dir"] for row in rows}
    assert all("First" in i["message"] and "Second" in i["message"] for i in found)


def test_comparing_original_artist_values_with_aliases_keeps_matching_identity_separate():
    found = check(
        [track(artist="First"), track("B", artist="Second")],
        groups=[["First", "Second"]],
        fields=["artist"],
    )
    assert len(found) == 2
    assert all(i["rule"] == "cross_album_artist" for i in found)
